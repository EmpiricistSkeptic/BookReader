#!/bin/sh
set -e

date
echo "migrate"
python manage.py migrate --noinput

date
echo "starting gunicorn"

exec gunicorn \
  bookreader_core.wsgi:application \
  --bind 0.0.0.0:$PORT \
  --worker-class gthread \
  --workers 1 \
  --threads 8 \
  --timeout 90 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -