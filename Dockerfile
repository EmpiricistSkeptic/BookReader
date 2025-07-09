FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/bookreader
COPY requirements.txt /app/

RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . /srv/bookreader

CMD ["gunicorn", "bookreader_core.wsgi:application", "--bind", "0.0.0.0:8000"]
