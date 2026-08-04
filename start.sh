#!/bin/sh

date
echo "migrate"
python manage.py migrate

date
echo "starting gunicorn"

gunicorn \
  bookreader_core.wsgi:application \
  --bind 0.0.0.0:$PORT \
  --timeout 60