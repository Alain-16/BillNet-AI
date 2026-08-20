
#syntax=docker/dockerfile:1.7

FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# runtime
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.production

RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libmagic1 curl && rm -rf /var/lib/apt/lists/*

RUN groupadd --system billnet && useradd --system --gid billnet --create-home billnet

COPY --from=builder /wheels /wheels/
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels requirements.txt

WORKDIR /app
COPY --chown=billnet:billnet . /app
COPY --chown=billnet:billnet docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /data/objects /app/staticfiles && chown -R billnet:billnet /data /app/staticfiles

USER billnet

EXPOSE 8000
ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]
CMD [ "web" ]

