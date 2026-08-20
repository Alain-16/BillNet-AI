#!/usr/bin/env bash

set -euo pipefail

python - <<'PY'
import os, sys, time
import psycopg
dsn = (f"host={os.environ['DB_HOST']} port={os.environ.get('DB_PORT','5432')}"
       f"dbname={os.environ['DB_NAME']} user={os.environ['DB_USER']} "
       f"password={os.environ['DB_PASSWORD']}" 
        )
for attempt in range(60):
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
        print("database ready"); sys.exit(0)
    except Exception as exc:
        print(f"waiting for database ({attempt+1}/60): {exc}", flush=True)
        time.sleep(2)
sys.exit("database never became available")
PY

case "${1:-web}" in 
    web)
    
        python manage.py migrate --noinput
        python manage.py collectstatic --noinput

        exec gunicorn config.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${GUNICORN_WORKERS:-3}" \
            --timeout "${GUNICORN_TIMEOUT:-120}" \
            --graceful-timeout 30 \
            --access-logfile - --error-logfile -
         
        ;;

   worker)
          
          exec celery -A config worker \
               --loglevel="${CELERY_LOGLEVEL:-info}" \
               --concurrency="${CELERY_CONCURRENCY:-2}"
          ;;
    
    beat)
         
         exec celery -A config beat --loglevel=info

         ;;
    
    shell) exec python manage.py shell ;;
    migrate) exec python manage.py migrate --noinput ;;
    *) exec "$@" ;;
    esac
