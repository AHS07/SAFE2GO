"""Manual retrieval (edge tier, prd.md F9.1).

Splits the original sample manuals into sections at level-two headings
and ranks sections against a question with TF-IDF cosine similarity.
Runs fully on the edge with no network access.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from app.config.thresholds import get_thresholds
from app.shared.content import ManualEntry, load_manual_index, read_text

log = logging.getLogger("safe2go.retrieval")

_SECTION_PREFIX = "## "
# Section titles are short and precise, so they count twice in the index.
_TITLE_WEIGHT = 2


@dataclass(frozen=True)
class ManualSection:
    manual_id: str
    manual_title: str
    machine_type: str | None
    section_title: str
    text: str


@dataclass(frozen=True)
class SearchHit:
    section: ManualSection
    score: float


def split_sections(manual: ManualEntry, markdown: str) -> list[ManualSection]:
    sections: list[ManualSection] = []
    title: str | None = None
    lines: list[str] = []

    def flush() -> None:
        body = " ".join(line.strip() for line in lines if line.strip())
        if title is not None and body:
            sections.append(ManualSection(
                manual_id=manual.manual_id,
                manual_title=manual.title,
                machine_type=manual.machine_type,
                section_title=title,
                text=body,
            ))

    for line in markdown.splitlines():
        if line.startswith(_SECTION_PREFIX):
            flush()
            title = line[len(_SECTION_PREFIX):].strip()
            lines = []
        elif title is not None:
            lines.append(line)
    flush()
    return sections


class ManualIndex:
    def __init__(self, sections: list[ManualSection]) -> None:
        if not sections:
            raise ValueError("Manual index needs at least one section.")
        self._sections = sections
        self._vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), sublinear_tf=True
        )
        documents = [
            " ".join([s.section_title] * _TITLE_WEIGHT + [s.text]) for s in sections
        ]
        self._matrix = self._vectorizer.fit_transform(documents)

    @property
    def section_count(self) -> int:
        return len(self._sections)

    def search(
        self,
        query: str,
        *,
        top_k: int,
        min_score: float,
        machine_type: str | None = None,
    ) -> list[SearchHit]:
        scores = linear_kernel(self._vectorizer.transform([query]), self._matrix).ravel()
        ranked = sorted(range(len(self._sections)), key=lambda i: scores[i], reverse=True)

        hits: list[SearchHit] = []
        for i in ranked:
            section = self._sections[i]
            if scores[i] < min_score:
                break
            if machine_type and section.machine_type not in (None, machine_type):
                continue
            hits.append(SearchHit(section=section, score=round(float(scores[i]), 4)))
            if len(hits) == top_k:
                break
        return hits


@lru_cache
def get_manual_index() -> ManualIndex:
    sections: list[ManualSection] = []
    for manual in load_manual_index():
        sections.extend(split_sections(manual, read_text(manual.path)))
    index = ManualIndex(sections)
    log.info("Manual index built", extra={"sections": index.section_count})
    return index


def search_manuals(query: str, machine_type: str | None = None) -> list[SearchHit]:
    config = get_thresholds().assistant
    return get_manual_index().search(
        query,
        top_k=config.search_top_k,
        min_score=config.search_min_score,
        machine_type=machine_type,
    )
