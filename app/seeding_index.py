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
