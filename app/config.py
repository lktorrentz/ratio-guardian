"""Caricamento della configurazione statica (config.yaml).

Vedi docs/SPEC.md sezione 4: config.yaml contiene SOLO i mount point fisici
dei dischi e il data_dir. Tutto il resto vive nel DB (tabella app_settings
e le altre tabelle di configurazione) ed è editabile da UI senza restart.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class Settings(BaseModel):
    disks: list[str]
    data_dir: str

    @field_validator("disks")
    @classmethod
    def _disks_must_be_absolute(cls, value: list[str]) -> list[str]:
        for disk in value:
            if not os.path.isabs(disk):
                raise ValueError(f"disks: il path '{disk}' deve essere assoluto")
        return value

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "ratio_guardian.db")


def load_settings(config_path: str | None = None) -> Settings:
    path = config_path or os.environ.get("CONFIG_PATH", "config.yaml")
    resolved = Path(path)

    if resolved.is_dir():
        # Bind mount di un file su un host dove quel file non esiste ancora:
        # Docker (e Unraid allo stesso modo) crea silenziosamente una
        # directory vuota al suo posto invece di dare errore. Capita spesso
        # al primo avvio se config.yaml non è stato creato prima sull'host.
        raise NotADirectoryError(
            f"{path} è una directory, non un file: probabile bind mount di un "
            "config.yaml che non esisteva ancora sull'host. Crea il file "
            "config.yaml (copiando config.example.yaml) sull'host PRIMA di "
            "avviare il container, poi ricrea il container."
        )
    if not resolved.exists():
        raise FileNotFoundError(
            f"File di configurazione non trovato: {path}. "
            "Copia config.example.yaml in config.yaml e adattalo ai mount reali."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Settings(**raw)
