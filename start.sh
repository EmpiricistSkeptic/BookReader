#!/bin/sh

date
echo "migrate"
python manage.py migrate

date
echo "collectstatic"
python manage.py collectstatic --noinput

date
echo "import"
python manage.py import_dictionary dictionary.csv

date
echo "starting gunicorn"

gunicorn bookreader_core.wsgi:application --bind 0.0.0.0:$PORT