#!/bin/sh

python manage.py migrate

python manage.py collectstatic --noinput

python manage.py import_dictionary dictionary.csv

gunicorn bookreader_core.wsgi:application --bind 0.0.0.0:$PORT