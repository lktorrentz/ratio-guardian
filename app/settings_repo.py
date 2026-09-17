"""Accesso alla tabella app_settings (impostazioni dinamiche editabili da UI
senza restart — vedi docs/SPEC.md sezione 4)."""

from sqlalchemy.orm import Session

from app.models import AppSettings


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(AppSettings, key)
    return row.value if row is not None else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(AppSettings, key)
    if row is None:
        session.add(AppSettings(key=key, value=value))
    else:
        row.value = value
    session.commit()
