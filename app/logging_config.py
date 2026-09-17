"""Configurazione del logging.

Senza init esplicito, Python mostra di default solo WARNING+ (via
"handler of last resort") — ogni logger.info() sparso nel codice viene
scartato in silenzio, anche se la libreria è configurata correttamente.
Qui impostiamo un handler su stdout (Dozzle/`docker logs` lo leggono da
lì) con soglia configurabile via env LOG_LEVEL (default INFO)."""

import logging
import os


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    root.handlers = [handler]

    # httpx/httpcore a DEBUG loggerebbero ogni singola richiesta/risposta
    # HTTP per esteso (comprese le chiamate a TMDB/tracker): mai più
    # verbosi di INFO anche se l'utente alza il livello generale a DEBUG.
    logging.getLogger("httpx").setLevel(max(level, logging.INFO))
    logging.getLogger("httpcore").setLevel(max(level, logging.WARNING))
