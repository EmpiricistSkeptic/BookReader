import json
import logging
import re

import requests
from django.conf import settings
from pydantic import ValidationError

from ...models import Translation
from .prompts import (
    MULTI_TOKEN_SYSTEM_PROMPT,
    MULTI_TOKEN_USER_PROMPT,
    SENTENCE_SYSTEM_PROMPT,
    SENTENCE_USER_PROMPT,
    WORD_SECURITY_SUFFIX,
)
from .schemas import (
    ANALYSIS_RESULT_ADAPTER,
    SENTENCE_DATA_ADAPTER,
    WORD_DATA_ADAPTER,
)
from .selection import InvalidSelectionError, count_tokens, detect_selection_form
from .system_prompt import BASE_PROMPT as WORD_SYSTEM_PROMPT
from .user_prompt import USER_PROMPT as WORD_USER_PROMPT


logger = logging.getLogger(__name__)


class DeepSeekTextAnalyzer:
    MAX_SELECTED_TEXT_LENGTH = 600
    MAX_CONTEXT_LENGTH = 2000
    # Whitespace-separated selections longer than this are not idioms or
    # collocations, so they go straight to sentence analysis without the
    # classification request. Tune if you see long proverbs misrouted.
    MAX_EXPRESSION_TOKENS = 12
    WORD_MAX_TOKENS = 1500
    MULTI_TOKEN_MAX_TOKENS = 1500
    SENTENCE_MAX_TOKENS = 2000

    def __init__(self):
        self.api_key = getattr(settings, "DEEPSEEK_API_KEY", None)
        self.base_url = getattr(
            settings,
            "DEEPSEEK_BASE_URL",
            "https://api.deepseek.com/chat/completions",
        )
        self.model = getattr(
            settings,
            "DEEPSEEK_WORD_ANALYSIS_MODEL",
            "deepseek-flash",
        )
        self.session = requests.Session()

    @staticmethod
    def _get_value(obj, field_name, default=None):
        if isinstance(obj, dict):
            return obj.get(field_name, default)
        return getattr(obj, field_name, default)

    @staticmethod
    def _clean_json_content(content: str) -> str:
        content = (content or "").strip()
        if content.startswith("```"):
            content = re.sub(
                r"^```(?:json)?\s*",
                "",
                content,
                count=1,
                flags=re.IGNORECASE,
            )
            content = re.sub(r"\s*```$", "", content, count=1)
        return content.strip()

    def _build_word_user_prompt(self, translation: Translation) -> str:
        return WORD_USER_PROMPT.format(
            user_data_json=self._build_user_data_json(translation),
        )

    def _build_user_data_json(self, translation: Translation) -> str:
        selected_text = self._get_value(translation, "original_text", "") or ""
        translated_text = self._get_value(translation, "translated_text", "") or ""
        source_language = self._get_value(translation, "source_language", "") or "unknown"
        target_language = self._get_value(translation, "target_language", "") or "unknown"
        context = (self._get_value(translation, "context", "") or "").strip()
        context = context[: self.MAX_CONTEXT_LENGTH]

        # JSON encoding keeps user-controlled values inside data fields rather
        # than interpolating them into the surrounding prompt instructions.
        user_data = {
            "selected_text": selected_text,
            "machine_translation_hint": translated_text,
            "source_language": source_language,
            "target_language": target_language,
            "surrounding_book_context": context or None,
        }
        return json.dumps(user_data, ensure_ascii=False)

    def _build_multi_token_user_prompt(
        self,
        translation: Translation,
        selection_form: str,
    ) -> str:
        return MULTI_TOKEN_USER_PROMPT.format(
            selection_form=selection_form,
            user_data_json=self._build_user_data_json(translation),
        )

    def _build_sentence_user_prompt(self, translation: Translation) -> str:
        return SENTENCE_USER_PROMPT.format(
            user_data_json=self._build_user_data_json(translation),
        )

    def _complete(
        self,
        translation: Translation,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        read_timeout: int = 30,
    ) -> str:
        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        user_id = self._get_value(translation, "user_id")
        translation_id = self._get_value(translation, "id")

        try:
            response = self.session.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=(5, read_timeout),
            )
            response.raise_for_status()
            raw_response = response.json()
            choice = raw_response["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            usage = raw_response.get("usage") or {}
        except requests.exceptions.Timeout:
            logger.error(
                "DeepSeek API timeout: user_id=%s, translation_id=%s",
                user_id,
                translation_id,
            )
            raise
        except requests.exceptions.RequestException:
            logger.exception(
                "DeepSeek HTTP error: user_id=%s, translation_id=%s",
                user_id,
                translation_id,
            )
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            logger.exception(
                "Unexpected DeepSeek response envelope: user_id=%s, translation_id=%s",
                user_id,
                translation_id,
            )
            raise ValueError("Unexpected DeepSeek response format")

        logger.info(
            "DeepSeek usage: user_id=%s, translation_id=%s, prompt_tokens=%s, completion_tokens=%s, finish_reason=%s",
            user_id,
            translation_id,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            finish_reason,
        )

        if finish_reason == "length":
            logger.warning(
                "DeepSeek response truncated: user_id=%s, translation_id=%s, max_tokens=%s",
                user_id,
                translation_id,
                max_tokens,
            )
            raise ValueError("DeepSeek response was truncated")

        if not isinstance(content, str) or not content.strip():
            raise ValueError("DeepSeek returned empty content")

        return self._clean_json_content(content)

    @staticmethod
    def _validate_word(content: str) -> dict:
        try:
            data = WORD_DATA_ADAPTER.validate_json(content)
        except (ValidationError, ValueError) as exc:
            raise ValueError("DeepSeek returned an invalid word-analysis structure") from exc

        return {
            "kind": "word",
            "data": data.model_dump(mode="json"),
        }

    @staticmethod
    def _validate_multi_token(content: str) -> dict:
        try:
            result = ANALYSIS_RESULT_ADAPTER.validate_json(content)
        except (ValidationError, ValueError) as exc:
            raise ValueError("DeepSeek returned an invalid text-analysis structure") from exc

        return result.model_dump(mode="json", by_alias=True)

    @staticmethod
    def _validate_sentence(content: str) -> dict:
        try:
            data = SENTENCE_DATA_ADAPTER.validate_json(content)
        except (ValidationError, ValueError) as exc:
            raise ValueError("DeepSeek returned an invalid sentence-analysis structure") from exc

        return {
            "kind": "sentence",
            "data": data.model_dump(mode="json"),
        }

    def _analyze_sentence(self, translation: Translation) -> dict:
        content = self._complete(
            translation=translation,
            system_prompt=SENTENCE_SYSTEM_PROMPT,
            user_prompt=self._build_sentence_user_prompt(translation),
            max_tokens=self.SENTENCE_MAX_TOKENS,
        )
        return self._validate_sentence(content)

    def analyze(self, translation: Translation) -> dict:
        selected_text = (self._get_value(translation, "original_text", "") or "").strip()
        if len(selected_text) > self.MAX_SELECTED_TEXT_LENGTH:
            raise InvalidSelectionError(
                f"Selected text exceeds the maximum of {self.MAX_SELECTED_TEXT_LENGTH} characters."
            )

        source_language = self._get_value(translation, "source_language", "") or ""
        selection_form = detect_selection_form(selected_text, source_language)

        if not self.api_key:
            raise ValueError("DeepSeek API key is not configured")

        if selection_form == "word":
            content = self._complete(
                translation=translation,
                system_prompt=WORD_SYSTEM_PROMPT + WORD_SECURITY_SUFFIX,
                user_prompt=self._build_word_user_prompt(translation),
                max_tokens=self.WORD_MAX_TOKENS,
            )
            result = self._validate_word(content)
        elif (
            selection_form == "multi_token"
            and count_tokens(selected_text) > self.MAX_EXPRESSION_TOKENS
        ):
            # Too long to be a lexical expression: skip classification.
            result = self._analyze_sentence(translation)
        else:
            content = self._complete(
                translation=translation,
                system_prompt=MULTI_TOKEN_SYSTEM_PROMPT,
                user_prompt=self._build_multi_token_user_prompt(
                    translation,
                    selection_form,
                ),
                max_tokens=self.MULTI_TOKEN_MAX_TOKENS,
                read_timeout=15,
            )
            result = self._validate_multi_token(content)

            # The model may return a word only for scripts where the backend
            # cannot infer token boundaries. This full word payload is reused;
            # no second DeepSeek request is necessary.
            if selection_form == "multi_token" and result["kind"] == "word":
                raise ValueError(
                    "DeepSeek classified a known multi-token selection as a word"
                )

            # Classification said "sentence": run the dedicated sentence analysis.
            if result["kind"] == "sentence":
                result = self._analyze_sentence(translation)

        logger.info(
            "DeepSeek text analysis completed: user_id=%s, translation_id=%s, selection_form=%s, kind=%s",
            self._get_value(translation, "user_id"),
            self._get_value(translation, "id"),
            selection_form,
            result["kind"],
        )
        return result