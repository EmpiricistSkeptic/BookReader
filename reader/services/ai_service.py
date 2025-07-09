import openai
from django.conf import settings
from ..models import UserProfile, Message
from typing import Dict, List
import textwrap


class AITeacherService:
    def __init__(self):
        self.client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

    def get_system_prompt(self, user_profile: UserProfile) -> str:
        language_names = {
            'ru': 'русский',
            'en': 'английский',
            'es': 'испанский',
            'fr': 'французский',
            'de': 'немецкий',
            'zh': 'китайский',
            'ja': 'японский',
        }
        native_lang = language_names.get(
            user_profile.native_language,
            user_profile.native_language
        )
        learning_lang = language_names.get(
            user_profile.language_to_learn,
            user_profile.language_to_learn
        )

        prompt = f"""
        Ты - опытный преподаватель иностранных языков и искусственный интеллект‑помощник.

        Информация о студенте:
        - Родной язык: {native_lang}
        - Изучаемый язык: {learning_lang}
        - Уровень владения: {user_profile.current_level}

        Твоя задача:
        1. Помогать изучать {learning_lang}
        2. Отвечать на вопросы понятно и структурированно
        3. Давать примеры и упражнения
        4. Исправлять ошибки деликатно
        5. Адаптировать сложность объяснений под уровень {user_profile.current_level}
        6. При необходимости переводить на {native_lang} сложные концепции

        Стиль общения: дружелюбный, терпеливый, мотивирующий.
        """
        return textwrap.dedent(prompt).strip()

    def get_conversation_history(
        self,
        messages: List[Message]
    ) -> List[Dict[str, str]]:
        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages[-10:]
        ]

    def generate_response(
        self,
        user_profile: UserProfile,
        conversation_messages: List[Message],
        user_message: str
    ) -> str:
        try:
            system_prompt = self.get_system_prompt(user_profile)
            messages_payload = [{"role": "system", "content": system_prompt}]
            messages_payload.extend(self.get_conversation_history(conversation_messages))
            messages_payload.append({"role": "user", "content": user_message})

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages_payload,
                max_tokens=1000,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            return f"Извините, произошла ошибка при обработке вашего запроса: {e}"
