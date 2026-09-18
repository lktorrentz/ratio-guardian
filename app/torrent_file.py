"""Parser bencode minimale per estrarre la cartella radice REALE di un
.torrent multi-file dal suo download_link — l'unica fonte davvero
affidabile quando il tracker non riporta esplicitamente una cartella.

Perché serve: `attributes.name` di UNIT3D è un titolo "leggibile" pensato
per la UI (spazi), non necessariamente il vero nome di release scritto
nel file .torrent (punti, es. "Stranger.Things.S05...."). Usarlo come
fallback per la cartella produce un path che il client torrent NON
riconosce — lui calcola sempre `save_path/info.name/...` dal .torrent
reale, mai da quello che gli passiamo noi — causando un mismatch che fa
fallire il recheck (osservato: risalita fino al 99% e poi fallimento).
Il bencode del .torrent (BEP3) riporta invece sempre il nome vero in
`info.name` per un torrent multi-file, mai un titolo cosmetico."""

import httpx


class TorrentMetainfoError(Exception):
    """Mai fatale per l'esecuzione: il chiamante deve trattarlo come
    "cartella non determinabile", lasciando che la validazione esplicita
    già esistente (es. app/executor.py::_execute_season_pack) segnali
    l'assenza di una cartella — mai indovinare un nome sbagliato."""


def _decode(data: bytes, i: int):
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c == b"l":
        i += 1
        result = []
        while data[i:i + 1] != b"e":
            item, i = _decode(data, i)
            result.append(item)
        return result, i + 1
    if c == b"d":
        i += 1
        result = {}
        while data[i:i + 1] != b"e":
            key, i = _decode(data, i)
            value, i = _decode(data, i)
            result[key] = value
        return result, i + 1
    if c.isdigit():
        colon = data.index(b":", i)
        length = int(data[i:colon])
        start = colon + 1
        return data[start:start + length], start + length
    raise TorrentMetainfoError(f"Formato bencode non valido a offset {i}")


def decode(data: bytes):
    try:
        value, _ = _decode(data, 0)
    except (IndexError, ValueError) as exc:
        raise TorrentMetainfoError("Impossibile decodificare il .torrent (bencode malformato)") from exc
    return value


def extract_root_folder(torrent_bytes: bytes) -> str | None:
    """None per un torrent a file singolo (info.name è il nome del file,
    non una cartella)."""
    metainfo = decode(torrent_bytes)
    if not isinstance(metainfo, dict) or b"info" not in metainfo:
        raise TorrentMetainfoError("Struttura .torrent inattesa: manca il dizionario 'info'")
    info = metainfo[b"info"]
    if b"files" not in info:
        return None
    name = info.get(b"name")
    if name is None:
        raise TorrentMetainfoError("Struttura .torrent inattesa: 'info.name' mancante per un torrent multi-file")
    try:
        return name.decode("utf-8")
    except UnicodeDecodeError:
        return name.decode("latin-1")


def fetch_root_folder(client: httpx.Client, download_link: str) -> str | None:
    """Scarica il .torrent da download_link e ne estrae la cartella radice
    reale (None per un torrent a file singolo). Solleva TorrentMetainfoError
    su qualunque problema (rete, formato) — il chiamante deve trattarlo
    come "cartella non determinabile", mai come errore fatale."""
    try:
        response = client.get(download_link)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TorrentMetainfoError(f"Download del .torrent fallito: {exc}") from exc
    return extract_root_folder(response.content)
