#!/bin/sh
# Waits for Postgres, applies migrations, then runs the container command.
set -e

echo "Waiting for the database..."
python - <<'PY'
import os, time
import psycopg
url = os.environ.get("DATABASE_URL", "")
if url.startswith("postgres"):
    for i in range(60):
        try:
            psycopg.connect(url.replace("postgres://", "postgresql://", 1)).close()
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("Database never became available")
PY

# Migration files ship in apps/*/migrations/. Create new ones yourself with
# `python manage.py makemigrations` when you change a model, and review them in code review.
# (AUTO_MAKEMIGRATIONS=true is only a convenience for local experiments; never on deploy.)
if [ "${AUTO_MAKEMIGRATIONS:-false}" = "true" ]; then
  python manage.py makemigrations core accounts customers pricing providers rides payments support --noinput
fi
if [ "${SKIP_MIGRATE:-false}" != "true" ]; then
  python manage.py migrate --noinput
fi

exec "$@"
