"""Funzione di scoping condivisa per ogni endpoint che tocca il filesystem.

Vedi docs/SPEC.md sezione 5 e CLAUDE.md: non va mai duplicata, va sempre
riusata (browse, mkdir, e in futuro la creazione degli hardlink).
"""

import os


class ScopeViolation(Exception):
    def __init__(self, candidate: str):
        self.candidate = candidate
        super().__init__(f"Percorso fuori dallo scope consentito: {candidate}")


def resolve_scoped(root_path: str, relative: str) -> str:
    candidate = os.path.realpath(os.path.join(root_path, relative))
    root_real = os.path.realpath(root_path)
    if not (candidate == root_real or candidate.startswith(root_real + os.sep)):
        raise ScopeViolation(candidate)
    return candidate
