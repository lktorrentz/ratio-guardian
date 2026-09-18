"""Indice (st_dev, inode) dei file già presenti nella cartella torrent di
un disco — usato per riconoscere "già in seeding" SENZA dover chiamare
TMDB o il tracker. Vedi docs/SPEC.md sezione 10 punto 1: è pensato apposta
come scorciatoia PRIMA del resolver/matching, non solo prima dell'hardlink
(dove viene comunque riverificato in app/executor.py — qui è solo per
decidere se vale la pena chiamare TMDB/tracker, mai per saltare i
controlli di sicurezza dell'esecuzione vera e propria)."""

import os


def build_seeding_index(disk_root_path: str, torrents_rel_path: str | None) -> set[tuple[int, int]]:
    if not torrents_rel_path:
        return set()
    torrents_root = os.path.join(disk_root_path, torrents_rel_path)
    if not os.path.isdir(torrents_root):
        return set()

    index: set[tuple[int, int]] = set()
    for dirpath, _dirnames, filenames in os.walk(torrents_root):
        for name in filenames:
            try:
                stat = os.stat(os.path.join(dirpath, name))
            except OSError:
                continue
            index.add((stat.st_dev, stat.st_ino))
    return index


def build_hardlink_path_index(disk_root_path: str, torrents_rel_path: str | None) -> dict[tuple[int, int], list[str]]:
    """Come build_seeding_index, ma tiene anche i path (non solo l'insieme
    (st_dev, inode)) — usata dalla preview della coda di revisione
    (app/web/reviews.py) per mostrare DOVE sono già gli altri collegamenti.
    Un solo walk per disco, mai uno per ogni file mostrato: fare altrimenti
    rendeva la pagina lentissima su una cartella torrent grande (un
    os.walk indipendente per riga)."""
    if not torrents_rel_path:
        return {}
    torrents_root = os.path.join(disk_root_path, torrents_rel_path)
    if not os.path.isdir(torrents_root):
        return {}

    index: dict[tuple[int, int], list[str]] = {}
    for dirpath, _dirnames, filenames in os.walk(torrents_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            index.setdefault((stat.st_dev, stat.st_ino), []).append(path)
    return index
