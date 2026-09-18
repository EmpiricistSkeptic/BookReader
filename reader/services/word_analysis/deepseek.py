import logging
import requests
import json

from django.conf import settings

from ...models import Translation
from .user_prompt import USER_PROMPT
from .system_prompt import BASE_PROMPT


logger = logging.getLogger(__name__)

class DeepSeekWordAnalyzer:
    def __init__(self):
        self.api_key = getattr(settings, "DEEPSEEK_API_KEY", None)
        self.base_url = getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
        self.model = "deepseek-flash"
        self.session = requests.Session()

    def _parse_response(self, raw_response: dict) -> dict:
        content = raw_response["choices"][0]["message"]["content"]

        if not content:
            raise ValueError("Empty content from DeepSeek")

        content = content.strip()

        if content.startswith("```"):
            content = (
                content
                .replace("```json", "", 1)
                .replace("```", "", 1)
                .strip()
            )

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning("DeepSeek returned invalid JSON")
            raise ValueError("Invalid JSON returned by DeepSeek") from e

        if not isinstance(data, dict):
            logger.warning("DeepSeek returned non-dict JSON")
            raise ValueError("DeepSeek response must be a JSON object")

        # -------------------------
        # lemma
        # -------------------------
        lemma = data.get("lemma")

        if not isinstance(lemma, str) or not lemma.strip():
            raise ValueError("Invalid 'lemma' field")

        lemma = lemma.strip()

        # -------------------------
        # ipa
        # -------------------------
        ipa = data.get("ipa")

        if not isinstance(ipa, str) or not ipa.strip():
            raise ValueError("Invalid 'ipa' field")

        ipa = ipa.strip()

        # -------------------------
        # part_of_speech
        # -------------------------
        part_of_speech = data.get("part_of_speech")

        if not isinstance(part_of_speech, str) or not part_of_speech.strip():
            raise ValueError("Invalid 'part_of_speech' field")

        part_of_speech = part_of_speech.strip()

        # -------------------------
        # grammar
        # -------------------------
        grammar = data.get("grammar")

        if not isinstance(grammar, dict):
            raise ValueError("'grammar' must be a dict")

        for key, value in grammar.items():
            if not isinstance(key, str):
                raise ValueError("Grammar keys must be strings")

            if value is not None and not isinstance(value, (str, int)):
                raise ValueError(
                    f"Invalid grammar value for '{key}': "
                    f"expected string, integer or null"
                )

            if isinstance(value, str):
                grammar[key] = value.strip()

        # -------------------------
        # explanation
        # -------------------------
        explanation = data.get("explanation")

        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError("Invalid 'explanation' field")

        explanation = explanation.strip()

        # -------------------------
        # synonyms
        # -------------------------
        synonyms = data.get("synonyms")

        if not isinstance(synonyms, list):
            raise ValueError("'synonyms' must be a list")

        if not all(
            isinstance(item, str) and item.strip()
            for item in synonyms
        ):
            raise ValueError(
                "'synonyms' must contain only non-empty strings"
            )

        synonyms = [item.strip() for item in synonyms]

        # -------------------------
        # examples
        # -------------------------
        examples = data.get("examples")

        if not isinstance(examples, list):
            raise ValueError("'examples' must be a list")

        if len(examples) != 3:
            raise ValueError("'examples' must contain exactly 3 examples")

        cleaned_examples = []

        for example in examples:
            if not isinstance(example, dict):
                raise ValueError(
                    "Each example must be a JSON object"
                )

            source = example.get("source")
            translation = example.get("translation")

            if not isinstance(source, str) or not source.strip():
                raise ValueError(
                    "Each example must contain a non-empty 'source'"
                )

            if (
                not isinstance(translation, str)
                or not translation.strip()
            ):
                raise ValueError(
                    "Each example must contain a non-empty 'translation'"
                )

            cleaned_examples.append(
                {
                    "source": source.strip(),
                    "translation": translation.strip(),
                }
            )

        # -------------------------
        # etymology
        # -------------------------
        etymology = data.get("etymology")

        if etymology is not None and not isinstance(etymology, str):
            raise ValueError(
                "'etymology' must be a string or null"
            )

        if isinstance(etymology, str):
            etymology = etymology.strip()

            if not etymology:
                etymology = None

        # -------------------------
        # final result
        # -------------------------
        return {
            "lemma": lemma,
            "ipa": ipa,
            "part_of_speech": part_of_speech,
            "grammar": grammar,
            "explanation": explanation,
            "synonyms": synonyms,
            "examples": cleaned_examples,
            "etymology": etymology,
        }

    def build_system_prompt(self) -> str:
        return BASE_PROMPT

    def build_user_prompt(self, translation: Translation) -> str:
        word = translation.original_text
        translated = translation.translated_text
        source_language = translation.source_language
        target_language = translation.target_language

        return USER_PROMPT.format(
            word=word,
            translated=translated,
            source_language=source_language,
            target_language=target_language,
        )


    def analyze(self, translation: Translation) -> dict:

        if not self.api_key:
            raise ValueError("DeepSeek API ключ не найден")

        try:
            messages = [
                {
                    "role": "system",
                    "content": self.build_system_prompt(),
                },
                {
                    "role": "user", 
                    "content": self.build_user_prompt(translation),
                }
            ]

            data = {
                "model": self.model,
                "messages": messages,
                "max_tokens": 1500,
                "temperature": 0.3,
                "thinking": {
                    "type": "disabled"
                },
                "response_format": {
                    "type": "json_object"
                },
            }
            logger.info(
                "DeepSeek request: user_id=%s, translation_id=%s",
                translation.user_id,
                translation.id,
            )

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            response = self.session.post(
                self.base_url,
                json=data,
                headers=headers,
                timeout=(5, 30),
            )
            response.raise_for_status()
            raw_response = response.json()
            logger.info(
                "DeepSeek response received: user_id=%s, translation_id=%s",
                translation.user_id,
                translation.id,
            )
            return self._parse_response(raw_response)
        except requests.exceptions.Timeout:
            logger.error(
                "DeepSeek API timeout for user_id=%s, translation_id=%s",
                translation.user_id,
                translation.id,
            )
            raise
                            
        except requests.exceptions.RequestException as e:
            logger.exception(
                "HTTP error during DeepSeek request: user_id=%s, translation_id=%s",
                translation.user_id,
                translation.id,
            )
            raise
                            
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            logger.exception(
                "Unexpected DeepSeek response format: user_id=%s, translation_id=%s",
                translation.user_id,
                translation.id,
            )
            raise 
