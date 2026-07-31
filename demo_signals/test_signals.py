import os
import sys
import django

# --- ИСПРАВЛЕНИЕ 1: ДОБАВЛЕНИЕ ПУТИ К ПРОЕКТУ ---
# Это необходимо, чтобы Python мог найти ваш модуль 'bookreader_core'
# при запуске скрипта из подпапки.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)


# Теперь Django сможет правильно найти настройки
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookreader_core.settings")
django.setup()


# Все импорты из Django теперь будут работать корректно
from django.contrib.auth.models import User
from django.db.models.signals import pre_save, post_save
from django.core.signals import request_started
from django.dispatch import receiver

# --- ВАШИ СИГНАЛЫ (ОСТАЮТСЯ БЕЗ ИЗМЕНЕНИЙ) ---

@receiver(pre_save, sender=User)
def before_user_save(sender, instance, **kwargs):
    # Этот сигнал сработает и при создании, и при обновлении
    print(f"🟡 pre_save: Сохранение пользователя '{instance.username}'...")

@receiver(post_save, sender=User)
def after_user_save(sender, instance, created, **kwargs):
    if created:
        print(f"🟢 post_save: Пользователь '{instance.username}' успешно СОЗДАН (created=True).")
    else:
        print(f"🔵 post_save: Пользователь '{instance.username}' успешно ОБНОВЛЕН (created=False).")

@receiver(request_started)
def on_request(sender, **kwargs):
    # Этот сигнал не сработает, так как мы не делаем HTTP-запросов
    print("🌐 Новый HTTP-запрос!")


# --- ОСНОВНОЙ БЛОК ЗАПУСКА ---

if __name__ == "__main__":
    print("--- Начинаю выполнение скрипта ---")

    # --- ИСПРАВЛЕНИЕ 2: ИСПОЛЬЗОВАНИЕ get_or_create ---
    # Это позволяет запускать скрипт много раз без ошибок.
    # Если пользователь 'signal_test' существует, он будет получен.
    # Если нет - он будет создан.
    user, created = User.objects.get_or_create(username="signal_test")

    if created:
        print("Действие: Пользователь 'signal_test' не найден, создаю нового.")
    else:
        print("Действие: Пользователь 'signal_test' найден, буду его обновлять.")

    # Изменяем какое-нибудь поле, чтобы запустить сохранение и сигналы
    user.email = "test.email@example.com"
    user.save()

    print("--- Скрипт успешно завершен ---")