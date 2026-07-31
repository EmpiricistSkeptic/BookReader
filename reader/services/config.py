from dataclasses import dataclass

from .prompts.base import BASE_PROMPT
from .prompts.grammar import GRAMMAR_PROMPT
from .prompts.roleplay import ROLEPLAY_PROMPT
from .prompts.conversation import CONVERSATION_PROMPT
from .prompts.vocabulary import VOCABULARY_PROMPT
from .prompts.writing import WRITING_PROMPT

from reader.models import ConversationMode

@dataclass(frozen=True)
class AIMode:
    prompt: str
    temperature: float

AI_MODES = {

    ConversationMode.DEFAULT:

        AIMode(
            prompt="",
            temperature=0.3,
        ),

    ConversationMode.GRAMMAR:

        AIMode(
            prompt=GRAMMAR_PROMPT,
            temperature=0.15,
        ),

    ConversationMode.ROLEPLAY:

        AIMode(
            prompt=ROLEPLAY_PROMPT,
            temperature=0.8,
        ),

    ConversationMode.CONVERSATION:

        AIMode(
            prompt=CONVERSATION_PROMPT,
            temperature=0.55,
        ),

    ConversationMode.WRITING:

        AIMode(
            prompt=WRITING_PROMPT,
            temperature=0.25,
        ),

    ConversationMode.VOCABULARY:

        AIMode(
            prompt=VOCABULARY_PROMPT,
            temperature=0.35,
        ),
}
