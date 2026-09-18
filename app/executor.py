"""Esecutore hardlink + integrazione client torrent.

Vedi docs/SPEC.md sezione 10. Ordine dei passi, mai bypassabile:
1. verifica "già in seeding" (nlink + ricerca nella cartella torrent) -> skip
2. verifica st_dev sorgente/destinazione (mai un cross-device silente)
3. hardlink con il/i nome/i esatto/i atteso/i dal tracker
4. add_torrent sul client, sempre con force_recheck=True (mai skip_checking)

Season pack (candidate.folder valorizzato, più file video in file_list):
l'associazione file-del-pack -> media_item locale è calcolata al volo
(guessit sul nome di ciascun file del pack, confrontato con
media_item.episode_number), MAI persistita in schema — evita di dover
modificare candidate per una relazione molti-a-molti. Eseguito solo con
copertura totale: se anche un solo episodio del pack manca localmente,
l'esecuzione viene rifiutata esplicitamente (mai un hardlink parziale,
mai un seeding parziale silenzioso — vedi docs/SPEC.md sezione 8 punto 4).
"""

import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

import httpx

from app.adapters.torrent_client.base import TorrentClientAdapter
from app.fs_scope import ScopeViolation, resolve_scoped
from app.models import Candidate, Disk, MediaItem, SeedJob
from app.scanner import VIDEO_EXTENSIONS
from app.season_pack import map_pack_files_by_episode
from app.torrent_file import TorrentMetainfoError, fetch_root_folder

logger = logging.getLogger(__name__)

_torrent_http_client = httpx.Client(timeout=15.0)


class ExecutionError(Exception):
    """Errore esplicito che impedisce l'esecuzione — mai un fallimento silente."""


def execute_candidate(
    session: Session,
    candidate: Candidate,
    torrent_client_adapter: TorrentClientAdapter,
) -> SeedJob | None:
    """Ritorna il SeedJob creato, o None se il file era già in seeding
    (nulla da fare). Solleva ExecutionError per ogni precondizione mancante
    (mai un fallimento silente, mai un tentativo "best effort" ambiguo)."""
    media_item = candidate.media_item
    media_path = media_item.media_path
    disk = media_path.disk

    # scan_root: dove cercare "già in seeding" — SEMPRE l'intera cartella
    # torrent del disco, mai ristretta alla sottocartella per-libreria: un
    # client può separare i completed in più sottocartelle che vanno
    # comunque scansionate tutte, o si generano doppioni/falsi negativi.
    if not disk.torrents_rel_path:
        raise ExecutionError(f"Disco '{disk.label}' non ha torrents_rel_path configurato")
    try:
        scan_root = resolve_scoped(disk.root_path, disk.torrents_rel_path)
    except ScopeViolation as exc:
        raise ExecutionError(str(exc)) from exc
    if not os.path.isdir(scan_root):
        raise ExecutionError(f"torrents_rel_path non esiste su disco: {scan_root}")

    # target_root: dove creare il NUOVO hardlink e quale save_path dare al
    # client — la sottocartella per-libreria se configurata, altrimenti la
    # stessa scan_root (vedi MediaPath.effective_new_torrent_rel_path).
    target_rel_path = media_path.effective_new_torrent_rel_path
    try:
        target_root = resolve_scoped(disk.root_path, target_rel_path)
    except ScopeViolation as exc:
        raise ExecutionError(str(exc)) from exc
    if not os.path.isdir(target_root):
        raise ExecutionError(f"Cartella di destinazione per i nuovi hardlink non trovata: {target_root}")

    file_list = json.loads(candidate.file_list_json) if candidate.file_list_json else []
    video_files = [f for f in file_list if _is_video(f)]
    if not video_files:
        raise ExecutionError(f"Candidate {candidate.id} non ha file video nel file_list")

    # Limitato ai pack (>1 file video): un file singolo con folder=None è
    # quasi sempre correttamente piatto (nessuna cartella), sottoporlo
    # comunque a un fetch di rete (fino a 15s di timeout se il tracker non
    # risponde) rallenterebbe inutilmente ogni singola approvazione. Per un
    # pack invece una cartella è pressoché certa, e sbagliarla causa un
    # recheck fallito — vale la spesa di un fetch mirato.
    if len(video_files) > 1:
        _ensure_folder_known(session, candidate)

    # Un solo file video non è per forza un torrent "piatto": molte release
    # (specie film) impacchettano comunque il file dentro una cartella con
    # lo stesso nome della release (candidate.folder valorizzato pur con un
    # solo video) — un dettaglio di posizionamento, non un pack TV da
    # riconciliare episodio per episodio. Il dispacciamento verso la
    # riconciliazione multi-episodio si basa solo sul numero di file video,
    # MAI sulla sola presenza di folder (bug: prima instradava qui anche i
    # film con un solo file ma una cartella, facendo fallire guessit che
    # cerca un numero di episodio inesistente).
    if len(video_files) > 1:
        return _execute_season_pack(
            session, candidate, media_item, disk, scan_root, target_root, video_files, torrent_client_adapter
        )
    return _execute_single_file(
        session, candidate, media_item, disk, scan_root, target_root, video_files[0], torrent_client_adapter
    )


def _ensure_folder_known(session: Session, candidate: Candidate) -> None:
    """Se il tracker non ha riportato una cartella (né direttamente né
    annidata nei nomi file — vedi Unit3dTrackerAdapter._normalize_pack_structure),
    l'unica fonte davvero affidabile è il .torrent stesso: legge
    info.name dal bencode (BEP3), mai attributes.name del tracker (un
    titolo "leggibile" con spazi, spesso diverso dal vero nome di
    release con i punti — usarlo produceva un path che il client non
    riconosceva, causando un mismatch e quindi un recheck fallito).
    Backfilla candidate.folder una volta sola (persistito, mai
    ri-scaricato ai retry successivi). Mai fatale: un fallimento qui
    lascia folder invariato, e le validazioni esplicite già esistenti
    (es. _execute_season_pack) segnalano poi l'assenza di una cartella,
    mai un nome indovinato."""
    if candidate.folder or not candidate.download_link:
        return
    try:
        folder = fetch_root_folder(_torrent_http_client, candidate.download_link)
    except TorrentMetainfoError:
        logger.warning(
            "Impossibile determinare la cartella dal .torrent per candidate %s", candidate.id, exc_info=True
        )
        return
    if folder:
        candidate.folder = folder
        session.commit()
        logger.info("Cartella determinata dal .torrent per candidate %s: %r", candidate.id, folder)


def _execute_single_file(
    session: Session,
    candidate: Candidate,
    media_item: MediaItem,
    disk: Disk,
    scan_root: str,
    target_root: str,
    expected_filename: str,
    adapter: TorrentClientAdapter,
) -> SeedJob | None:
    source_path = media_item.file_path
    if not os.path.isfile(source_path):
        raise ExecutionError(f"File locale non trovato: {source_path}")

    existing = _already_seeding(source_path, scan_root)
    if existing is not None:
        logger.info("File già in seeding (%s), skip: %s", existing, source_path)
        return None

    # Anche un torrent a file singolo può avere una cartella contenitore
    # (candidate.folder) — molte release la usano pure per i film, non solo
    # per i pack TV. Va ricreata, mai appiattita nella root della cartella
    # torrent.
    relative_target = os.path.join(candidate.folder, expected_filename) if candidate.folder else expected_filename
    try:
        target_path = resolve_scoped(target_root, relative_target)
    except ScopeViolation as exc:
        raise ExecutionError(str(exc)) from exc

    _check_same_filesystem(source_path, target_root)

    if os.path.exists(target_path):
        raise ExecutionError(f"Il path di destinazione esiste già: {target_path}")

    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    seed_job = SeedJob(candidate_id=candidate.id, final_status="in_progress")
    session.add(seed_job)
    session.commit()

    return _link_and_seed(session, seed_job, adapter, [(source_path, target_path)], candidate, target_root)


def _execute_season_pack(
    session: Session,
    candidate: Candidate,
    media_item: MediaItem,
    disk: Disk,
    scan_root: str,
    target_root: str,
    video_files: list[str],
    adapter: TorrentClientAdapter,
) -> SeedJob | None:
    if not candidate.folder:
        raise ExecutionError(
            f"Candidate {candidate.id} ha più file video ma nessuna cartella pack (folder) indicata dal tracker"
        )

    try:
        pack_files_by_episode = map_pack_files_by_episode(video_files)
    except ValueError as exc:
        raise ExecutionError(str(exc)) from exc

    local_items = (
        session.query(MediaItem)
        .filter(
            MediaItem.tmdb_id == media_item.tmdb_id,
            MediaItem.season_number == media_item.season_number,
            MediaItem.episode_number.in_(pack_files_by_episode.keys()),
        )
        .all()
    )
    local_by_episode = {item.episode_number: item for item in local_items}

    missing = sorted(set(pack_files_by_episode) - set(local_by_episode))
    if missing:
        raise ExecutionError(
            f"Pack incompleto localmente: mancano gli episodi {missing} — "
            "niente hardlink/seeding parziale automatico, vedi docs/SPEC.md sezione 8"
        )

    # Tutti i file locali devono stare sullo stesso disco (l'hardlink non
    # attraversa dischi diversi) — verifica esplicita, mai un fallimento
    # cross-device silente.
    for episode, item in local_by_episode.items():
        if item.media_path.disk_id != disk.id:
            raise ExecutionError(
                f"Episodio {episode} risiede su un disco diverso da quello del candidate ({disk.label})"
            )
        if not os.path.isfile(item.file_path):
            raise ExecutionError(f"File locale non trovato per episodio {episode}: {item.file_path}")

    try:
        pack_dir = resolve_scoped(target_root, candidate.folder)
    except ScopeViolation as exc:
        raise ExecutionError(str(exc)) from exc

    # "Già in seeding": basta controllare un file rappresentativo del pack.
    any_source = next(iter(local_by_episode.values())).file_path
    existing = _already_seeding(any_source, scan_root)
    if existing is not None:
        logger.info("Pack già in seeding (%s), skip", existing)
        return None

    _check_same_filesystem(any_source, target_root)

    links: list[tuple[str, str]] = []
    for episode, filename in pack_files_by_episode.items():
        source_path = local_by_episode[episode].file_path
        target_path = os.path.join(pack_dir, filename)
        if os.path.exists(target_path):
            raise ExecutionError(f"Il path di destinazione esiste già: {target_path}")
        links.append((source_path, target_path))

    os.makedirs(pack_dir, exist_ok=True)

    seed_job = SeedJob(candidate_id=candidate.id, final_status="in_progress", hardlink_path=pack_dir)
    session.add(seed_job)
    session.commit()

    return _link_and_seed(session, seed_job, adapter, links, candidate, target_root, hardlink_path=pack_dir)


def _link_and_seed(
    session: Session,
    seed_job: SeedJob,
    adapter: TorrentClientAdapter,
    links: list[tuple[str, str]],
    candidate: Candidate,
    target_root: str,
    hardlink_path: str | None = None,
) -> SeedJob:
    if not candidate.download_link:
        seed_job.final_status = "failed"
        seed_job.error_message = "Candidate senza download_link: impossibile aggiungere il torrent al client"
        session.commit()
        raise ExecutionError(seed_job.error_message)

    try:
        created: list[str] = []
        for source_path, target_path in links:
            os.link(source_path, target_path)
            created.append(target_path)

        seed_job.hardlink_path = hardlink_path or created[0]
        seed_job.hardlink_created_at = datetime.now(timezone.utc)
        session.commit()
        logger.info("Hardlink creato per candidate %s: %s", candidate.id, seed_job.hardlink_path)

        disk = candidate.media_item.media_path.disk
        client_save_path = _client_visible_path(disk, target_root)
        info_hash = adapter.add_torrent(candidate.download_link, save_path=client_save_path, force_recheck=True)
        seed_job.info_hash = info_hash
        seed_job.torrent_added_at = datetime.now(timezone.utc)
        seed_job.recheck_status = "pending"
        session.commit()
        logger.info("Torrent aggiunto al client (info_hash=%s), recheck in corso", info_hash)
    except Exception as exc:
        seed_job.final_status = "failed"
        seed_job.error_message = str(exc)
        session.commit()
        raise ExecutionError(str(exc)) from exc

    return seed_job


def _client_visible_path(disk: Disk, local_path: str) -> str:
    """Traduce un path lato Ratio Guardian nel path equivalente visto dal
    client torrent, quando i due girano in container/mount diversi per lo
    stesso disco fisico (disk.torrent_client_root_path configurato). Se non
    configurato, assume che client e Ratio Guardian vedano lo stesso path
    (comportamento invariato per chi non ne ha bisogno)."""
    if not disk.torrent_client_root_path:
        return local_path
    root_real = os.path.realpath(disk.root_path)
    local_real = os.path.realpath(local_path)
    if local_real != root_real and not local_real.startswith(root_real + os.sep):
        return local_path  # fuori dal disco: non dovrebbe succedere, non tocchiamo nulla
    relative = os.path.relpath(local_real, root_real)
    return disk.torrent_client_root_path if relative == "." else os.path.join(disk.torrent_client_root_path, relative)


def retry_seed_job(session: Session, seed_job: SeedJob, adapter: TorrentClientAdapter) -> SeedJob:
    """Ritenta un seed_job failed, riprendendo dal punto giusto invece di
    ripartire ciecamente da execute_candidate() (che tratterebbe un
    hardlink già creato come "già in seeding" e non farebbe nulla, anche
    se il torrent non è mai stato aggiunto al client — il fallimento più
    comune, es. client irraggiungibile al momento della prima esecuzione).

    - hardlink_path nullo -> mai arrivato a creare l'hardlink, si riparte
      da zero con execute_candidate().
    - hardlink_path presente, info_hash nullo -> hardlink ok, torrent mai
      aggiunto: si riprende da lì.
    - info_hash già presente -> probabilmente solo il recheck non è mai
      stato confermato: si reinterroga il client."""
    if seed_job.final_status != "failed":
        raise ExecutionError(f"SeedJob {seed_job.id} non è in stato failed (attuale: {seed_job.final_status})")

    candidate = seed_job.candidate

    if not seed_job.hardlink_path:
        logger.info("Retry seed_job %s: hardlink mai creato, riparto da zero", seed_job.id)
        result = execute_candidate(session, candidate, adapter)
        if result is None:
            raise ExecutionError("Il file risulta già in seeding: nessuna nuova esecuzione necessaria")
        return result

    if seed_job.info_hash:
        logger.info("Retry seed_job %s: hardlink e torrent già presenti, reinterrogo il client", seed_job.id)
        return reconcile_seed_job(session, seed_job, adapter)

    logger.info("Retry seed_job %s: hardlink presente, riprendo dall'aggiunta al client", seed_job.id)
    if not candidate.download_link:
        seed_job.error_message = "Candidate senza download_link: impossibile aggiungere il torrent al client"
        session.commit()
        raise ExecutionError(seed_job.error_message)

    media_path = candidate.media_item.media_path
    disk = media_path.disk
    target_rel_path = media_path.effective_new_torrent_rel_path
    if not target_rel_path:
        raise ExecutionError(f"Disco '{disk.label}' non ha torrents_rel_path configurato")
    try:
        target_root = resolve_scoped(disk.root_path, target_rel_path)
    except ScopeViolation as exc:
        raise ExecutionError(str(exc)) from exc

    try:
        client_save_path = _client_visible_path(disk, target_root)
        info_hash = adapter.add_torrent(candidate.download_link, save_path=client_save_path, force_recheck=True)
        seed_job.info_hash = info_hash
        seed_job.torrent_added_at = datetime.now(timezone.utc)
        seed_job.recheck_status = "pending"
        seed_job.final_status = "in_progress"
        seed_job.error_message = None
        session.commit()
        logger.info("Retry riuscito: torrent aggiunto (info_hash=%s) per seed_job %s", info_hash, seed_job.id)
    except Exception as exc:
        seed_job.error_message = str(exc)
        session.commit()
        raise ExecutionError(str(exc)) from exc

    return seed_job


def reconcile_seed_job(session: Session, seed_job: SeedJob, adapter: TorrentClientAdapter) -> SeedJob:
    """Aggiorna recheck_status/final_status interrogando lo stato reale nel
    client. Il recheck è asincrono lato client: va richiamata finché non
    raggiunge uno stato definitivo (mai un'attesa bloccante qui dentro)."""
    if seed_job.info_hash is None:
        raise ExecutionError(f"SeedJob {seed_job.id} non ha ancora un info_hash")

    status = adapter.get_torrent_status(seed_job.info_hash)
    seed_job.recheck_status = status.recheck_status
    if status.recheck_status == "ok":
        seed_job.final_status = "seeding"
        logger.info("Recheck ok, seed_job %s in seeding (info_hash=%s)", seed_job.id, seed_job.info_hash)
    elif status.recheck_status == "failed":
        seed_job.final_status = "failed"
        seed_job.error_message = f"Recheck fallito, stato client: {status.state}"
        logger.warning("Recheck fallito per seed_job %s: stato client %s", seed_job.id, status.state)
    session.commit()
    return seed_job


def _walk_matching_inode(source_stat: os.stat_result, search_root: str) -> list[str]:
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(search_root):
        for name in filenames:
            candidate_path = os.path.join(dirpath, name)
            try:
                other_stat = os.stat(candidate_path)
            except OSError:
                continue
            if other_stat.st_dev == source_stat.st_dev and other_stat.st_ino == source_stat.st_ino:
                found.append(candidate_path)
    return found


def _already_seeding(source_path: str, torrents_root: str) -> str | None:
    """Vedi docs/SPEC.md sezione 10 punto 1: se nlink>1, cerca dentro
    torrents_root un file con lo stesso (st_dev, st_ino) del sorgente."""
    try:
        source_stat = os.stat(source_path)
    except OSError:
        return None
    if source_stat.st_nlink <= 1:
        return None
    matches = _walk_matching_inode(source_stat, torrents_root)
    return matches[0] if matches else None


def find_existing_hardlinks(source_path: str, search_root: str) -> list[str]:
    """Tutti i path dentro search_root che condividono lo stesso inode del
    file locale — sola lettura, usata dalla preview della coda di revisione
    (app/web/reviews.py) per mostrare dove sono già gli altri collegamenti.
    NON sostituisce _already_seeding(): quella resta l'unica verifica usata
    prima di un vero hardlink."""
    try:
        source_stat = os.stat(source_path)
    except OSError:
        return []
    if source_stat.st_nlink <= 1:
        return []
    return _walk_matching_inode(source_stat, search_root)


def _check_same_filesystem(source_path: str, torrents_root: str) -> None:
    source_dev = os.stat(source_path).st_dev
    target_dev = os.stat(torrents_root).st_dev
    if source_dev != target_dev:
        raise ExecutionError(
            f"Sorgente e destinazione su device diversi ({source_dev} != {target_dev}): "
            "l'hardlink non può attraversare filesystem diversi, vedi docs/SPEC.md sezione 3"
        )


def _is_video(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)
