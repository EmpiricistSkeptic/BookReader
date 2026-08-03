FROM python:3.12-slim

# Окружение
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Установка системных зависимостей
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Рабочая директория — всё здесь
WORKDIR /srv/bookreader

# Копируем только requirements сначала (для layer‑кеша)
COPY requirements.txt .

# Обновляем pip и ставим зависимости
RUN pip install --upgrade pip --root-user-action=ignore \
 && pip install --no-cache-dir -r requirements.txt

# Копируем весь проект в /srv/bookreader
COPY . .

RUN chmod +x start.sh

CMD ["sh", "start.sh"]