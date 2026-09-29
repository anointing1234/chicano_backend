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

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
# Dev default. Production: gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
