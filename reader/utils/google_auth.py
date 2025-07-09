import logging
from google.auth.transport import requests
from google.oauth2 import id_token
from django.conf import settings
from django.contrib.auth.models import User
from ..models import UserProfile

logger = logging.getLogger(__name__)


class GoogleAuthService:
    """Сервис для работы с Google OAuth"""
    
    @staticmethod
    def verify_google_token(token):
        """
        Верификация Google ID токена
        """
        try:
            # Верифицируем токен
            idinfo = id_token.verify_oauth2_token(
                token, 
                requests.Request(), 
                settings.GOOGLE_OAUTH2_CLIENT_ID
            )
            
            # Проверяем, что токен выпущен Google
            if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
                raise ValueError('Wrong issuer.')
            
            return idinfo
            
        except ValueError as e:
            logger.error(f"Invalid Google token: {e}")
            return None
        except Exception as e:
            logger.error(f"Error verifying Google token: {e}")
            return None
    
    @staticmethod
    def get_or_create_user_from_google(google_data):
        """
        Создание или получение пользователя на основе Google данных
        """
        try:
            google_id = google_data.get('sub')
            email = google_data.get('email')
            first_name = google_data.get('given_name', '')
            last_name = google_data.get('family_name', '')
            picture = google_data.get('picture', '')
            
            if not google_id or not email:
                return None, "Invalid Google data"
            
            # Ищем пользователя по Google ID
            try:
                profile = UserProfile.objects.get(google_id=google_id)
                return profile.user, None
            except UserProfile.DoesNotExist:
                pass
            
            # Ищем пользователя по email
            try:
                user = User.objects.get(email=email)
                # Связываем существующего пользователя с Google
                profile = user.profile
                profile.google_id = google_id
                profile.is_google_user = True
                profile.avatar_url = picture
                profile.save()
                return user, None
                
            except User.DoesNotExist:
                # Создаем нового пользователя
                username = email.split('@')[0]
                
                # Проверяем уникальность username
                counter = 1
                original_username = username
                while User.objects.filter(username=username).exists():
                    username = f"{original_username}{counter}"
                    counter += 1
                
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name
                )
                
                # Обновляем профиль
                profile = user.profile
                profile.google_id = google_id
                profile.is_google_user = True
                profile.avatar_url = picture
                profile.save()
                
                return user, None
                
        except Exception as e:
            logger.error(f"Error creating user from Google data: {e}")
            return None, str(e)
                





            





            



    




