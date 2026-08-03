#!/bin/sh

python manage.py migrate

python manage.py import_dictionary fixtures/dictionary.csv

gunicorn bookreader_core.wsgi:application --bind 0.0.0.0:$PORT