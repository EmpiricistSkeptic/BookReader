import textwrap
import logging
import requests
from typing import Dict, List

from django.conf import settings

from ..models import Message, UserProfile, Conversation
from ..exceptions import AIServiceError

from .config import AI_MODES
from .prompts.base import BASE_PROMPT


logger = logging.getLogger("ai_service")

class AITeacherService:
    def __init__(self):
        self.api_key = getattr(settings, "DEEPSEEK_API_KEY", None)
        self.base_url = getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
        self.model = "deepseek-v4-flash"
        self.session = requests.Session()

    def build_system_prompt(
        self,
        user_profile: UserProfile,
        mode_config,
    ) -> str:
        language_names = {
            "ru": "russian",
            "en": "english",
            "es": "spanish",
            "fr": "french",
            "de": "german",
            "zh": "chinese",
            "ja": "japanese",
        }
        native_lang = language_names.get(
            user_profile.native_language, user_profile.native_language
        )
        learning_lang = language_names.get(
            user_profile.language_to_learn, user_profile.language_to_learn
        )
        current_level = getattr(user_profile, "current_level", "Intermediate (B1)")


        system_prompt = BASE_PROMPT.format(
            native_lang=native_lang,

            learning_lang=learning_lang,

            current_level=current_level,
        )

        if mode_config.prompt:
            system_prompt += "\n\n" + mode_config.prompt
        
        return system_prompt

    def get_conversation_history(self, messages: List[Message]) -> List[Dict[str, str]]:
        return [{"role": msg.role, "content": msg.content} for msg in messages[-10:]]

    def generate_response(
        self,
        conversation: Conversation,
        user_profile: UserProfile,
        conversation_messages: List[Message],
        user_message: str,
    ) -> str:
        try:
            mode_config = AI_MODES[conversation.mode]
            system_prompt = self.build_system_prompt(user_profile, mode_config)
            messages_payload = [{"role": "system", "content": system_prompt}]
            messages_payload.extend(
                self.get_conversation_history(conversation_messages)
            )
            messages_payload.append({"role": "user", "content": user_message})

            data = {
                "model": self.model,
                "messages": messages_payload,
                "max_tokens": 2000,
                "temperature": mode_config.temperature,
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            response = self.session.post(
                self.base_url,
                json=data,
                headers=headers,
                timeout=(5, 25)
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            logger.error(f"DeepSeek API timeout for user_id: {getattr(user_profile, 'user_id', 'N/A')}")
            raise AIServiceError("Превышено время ожидания ответа от ИИ. Попробуйте еще раз.")
            
        except requests.exceptions.RequestException as e:
            logger.exception(f"HTTP error during AI generation: {e}")
            raise AIServiceError("Ошибка связи с сервером ИИ. Попробуйте позже.")
            
        except (KeyError, IndexError) as e:
            logger.exception(f"Unexpected response format from DeepSeek API: {e}")
            raise AIServiceError("Получен некорректный ответ от ИИ.")
            
        except Exception as e:
            logger.exception(f"Unexpected error in AITeacherService: {e}")
            raise AIServiceError("Произошла непредвиденная ошибка при обработке запроса.")
