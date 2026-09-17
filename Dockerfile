FROM python:3.12-slim

# mediainfo: fornisce sia la CLI che libmediainfo, usate per calcolare
# l'Unique ID (vedi docs/SPEC.md sezione 8 e CLAUDE.md).
RUN apt-get update \
    && apt-get install -y --no-install-recommends mediainfo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY docs docs
COPY docker docker
COPY config.example.yaml .
RUN chmod +x docker/entrypoint.sh

# /app/config va montato come cartella (mai un file), vedi docker/entrypoint.sh:
# se manca config.yaml al suo interno viene seminato da config.example.yaml.
ENV CONFIG_PATH=/app/config/config.yaml

EXPOSE 8080

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["supervisord", "-c", "docker/supervisord.conf"]
