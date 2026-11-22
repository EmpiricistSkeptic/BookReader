from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
)


class OutputModel(BaseModel):
    # Ignore harmless extra keys from the model, but validate all fields that
    # the application actually relies on.
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _clean_string_list(value):
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return value


def _clean_grammar(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if isinstance(item, str):
                item = item.strip() or None
            cleaned[key] = item
        return cleaned
    return value


def _keep_first(limit: int):
    # The model sometimes returns more items than requested. Keep the first
    # `limit`; fewer than the required minimum still fails schema validation.
    def validator(value):
        if isinstance(value, list):
            return value[:limit]
        return value

    return BeforeValidator(validator)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]
StringList = Annotated[list[str], BeforeValidator(_clean_string_list)]


class ExampleOut(OutputModel):
    source: NonEmptyStr
    translation: NonEmptyStr


Examples = Annotated[
    list[ExampleOut],
    _keep_first(3),
    Field(min_length=3, max_length=3),
]


# ------------------------------------------------------------------ word

class WordData(OutputModel):
    lemma: NonEmptyStr
    ipa: NonEmptyStr
    part_of_speech: NonEmptyStr
    grammar: Annotated[
        dict[str, str | int | None],
        BeforeValidator(_clean_grammar),
    ]
    explanation: NonEmptyStr
    synonyms: StringList = Field(default_factory=list)
    examples: Examples
    etymology: OptionalText = None


# ------------------------------------------------------------------ expression

class ExpressionFull(OutputModel):
    expression_type: Literal[
        "phrasal_verb",
        "idiom",
        "collocation",
        "fixed_expression",
        "other",
    ]
    meaning: NonEmptyStr
    literal_translation: OptionalText = None
    style: OptionalText = None
    synonyms: StringList = Field(default_factory=list)
    examples: Examples
    notes: OptionalText = None


class FreeCombination(OutputModel):
    expression_type: Literal["free_combination"]
    status: Literal["no_special_expression"] = "no_special_expression"


ExpressionData = Annotated[
    Union[ExpressionFull, FreeCombination],
    Field(discriminator="expression_type"),
]


# ------------------------------------------------------------------ sentence

class BreakdownItem(OutputModel):
    # Verbatim fragment of the analyzed sentence, in reading order.
    text: NonEmptyStr
    # Role of the fragment (subject, main verb, relative clause...) in the TARGET LANGUAGE.
    role: NonEmptyStr
    note: OptionalText = None


class KeyExpression(OutputModel):
    text: NonEmptyStr
    meaning: NonEmptyStr
    expression_type: OptionalText = None


class GrammarPoint(OutputModel):
    title: NonEmptyStr
    explanation: NonEmptyStr


class SentenceData(OutputModel):
    structure: NonEmptyStr
    breakdown: Annotated[
        list[BreakdownItem],
        _keep_first(12),
        Field(min_length=1),
    ]
    key_expressions: Annotated[
        list[KeyExpression],
        _keep_first(6),
    ] = Field(default_factory=list)
    grammar_points: Annotated[
        list[GrammarPoint],
        _keep_first(5),
    ] = Field(default_factory=list)
    translation_notes: OptionalText = None


# ------------------------------------------------------------------ results

class WordResult(OutputModel):
    kind: Literal["word"]
    data: WordData


class ExpressionResult(OutputModel):
    kind: Literal["expression"]
    data: ExpressionData


class SentenceMarker(OutputModel):
    """Classification-only answer: the fragment is a sentence.

    The actual sentence analysis is produced by a separate request.
    """

    kind: Literal["sentence"]


# Answer of the multi-token classification request.
AnalysisResult = Annotated[
    Union[WordResult, ExpressionResult, SentenceMarker],
    Field(discriminator="kind"),
]

WORD_DATA_ADAPTER = TypeAdapter(WordData)
SENTENCE_DATA_ADAPTER = TypeAdapter(SentenceData)
ANALYSIS_RESULT_ADAPTER = TypeAdapter(AnalysisResult)