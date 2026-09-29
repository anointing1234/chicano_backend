# Chicano Cruise API image (used for both the API and the dispatch worker).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# libpq for psycopg, libjpeg/zlib for Pillow (document photos)
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

# Build the dashboard CSS/JS/favicon + WhiteNoise manifest into the image.
# No secrets are needed for this step; it only copies and hashes files.
RUN python manage.py collectstatic --noinput

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
# Dev default (docker compose). On Render, set the Docker Command to gunicorn (see below).
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]