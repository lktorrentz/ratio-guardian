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

EXPOSE 8080

CMD ["supervisord", "-c", "docker/supervisord.conf"]
