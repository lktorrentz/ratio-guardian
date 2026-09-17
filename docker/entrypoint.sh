#!/bin/sh
# Entrypoint: prepara utente/gruppo PUID/PGID. Pattern standard per i tool
# self-hosted (Sonarr, Radarr, qBittorrent su Unraid girano tutti così,
# tipicamente 99:100) — evita che le cartelle create dal tool
# (torrents_rel_path, pack season) finiscano possedute da root sull'array.
#
# NB: supervisord resta root (necessario per gestire correttamente i
# segnali come PID 1 e per aprire /dev/stdout/stderr dei figli — un
# supervisord non-root che prova a riaprire quei path per path va in
# EACCES, problema noto). Il drop dei privilegi avviene per-programma
# dentro supervisord.conf (user=%(ENV_RUN_AS_USER)s), non qui.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" ratioguardian
fi
GROUP_NAME=$(getent group "$PGID" | cut -d: -f1)

if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$GROUP_NAME" -M -s /usr/sbin/nologin ratioguardian
fi
USER_NAME=$(getent passwd "$PUID" | cut -d: -f1)

mkdir -p /app/data
chown -R "$PUID:$PGID" /app/data

export RUN_AS_USER="$USER_NAME"
exec "$@"
