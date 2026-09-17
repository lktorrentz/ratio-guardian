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
    if not Path(path).exists():
        raise FileNotFoundError(
            f"File di configurazione non trovato: {path}. "
            "Copia config.example.yaml in config.yaml e adattalo ai mount reali."
        )
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Settings(**raw)
