"""Explain retrieved manual sections in plain language.

Only the operator's question and the retrieved manual text are sent to the
model: never telemetry, never operator data. The answer is limited to the
given sections, and any emoji the model adds is removed (rules.md section 4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.cloud.assistant.deepseek_client import DeepSeekClient
from app.config.thresholds import get_thresholds

_SECTION_CHAR_LIMIT = 2000

SYSTEM_PROMPT = (
    "You help a construction machine operator understand sections of a machine manual. "
    "Answer the question using only the manual sections provided. "
    "Use plain, calm language and short sentences. Give steps as a numbered list when the question asks how to do something. "
    "If the sections do not answer the question, say that the manual sections shown do not cover it. "
    "Do not invent specifications, values, or procedures. Do not use emojis. Keep the answer under 150 words."
)

# Pictographs, dingbats, flags, and variation selectors.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U0000FE0F\U0000200D]"
)


@dataclass(frozen=True)
class SourceSection:
    manual_title: str
    section_title: str
    text: str


def build_user_message(question: str, sections: list[SourceSection]) -> str:
    parts = [
        f"Section {i}: {s.manual_title}, {s.section_title}\n{s.text[:_SECTION_CHAR_LIMIT]}"
        for i, s in enumerate(sections, start=1)
    ]
    return "Manual sections:\n\n" + "\n\n".join(parts) + f"\n\nQuestion: {question}"


def clean_answer(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text)).strip()


async def explain_sections(client: DeepSeekClient, question: str, sections: list[SourceSection]) -> str:
    cfg = get_thresholds().assistant
    answer = await client.complete(
        SYSTEM_PROMPT,
        build_user_message(question, sections),
        max_tokens=cfg.explain_max_tokens,
        temperature=cfg.explain_temperature,
    )
    return clean_answer(answer)
