"""Associazione episodio -> nome file per un season pack, condivisa tra il
motore di matching (app/matching.py, per confrontare la dimensione del
singolo episodio dentro il pack) e l'esecutore (app/executor.py, per sapere
quale file del pack hardlinkare per ogni episodio locale) — mai duplicata,
vedi CLAUDE.md."""

from guessit import guessit


def map_pack_files_by_episode(video_files: list[str]) -> dict[int, str]:
    """Solleva ValueError se un file non permette di determinare l'episodio
    (mai un'associazione ambigua o silenziosamente scartata)."""
    result: dict[int, str] = {}
    for filename in video_files:
        episode = _first(guessit(filename).get("episode"))
        if episode is None:
            raise ValueError(f"Impossibile determinare l'episodio dal nome file del pack: {filename!r}")
        result[episode] = filename
    return result


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value
