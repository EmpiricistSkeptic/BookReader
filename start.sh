#!/bin/sh

date
python manage.py migrate

date
python manage.py collectstatic --noinput

date
python manage.py import_dictionary dictionary.csv

date
gunicorn bookreader_core.wsgi:application --bind 0.0.0.0:$PORT