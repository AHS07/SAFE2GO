"""Documentation assistant routes.

Search: TF-IDF retrieval over the sample manuals, on the edge, always available.
Explain: the same retrieval, plus a plain-language explanation from DeepSeek
when the cloud is reachable. The retrieved sections are always returned;
when no explanation can be produced the response says why (PRD F9.3).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.cloud.assistant.deepseek_client import get_deepseek_client
from app.cloud.assistant.explain import SourceSection, explain_sections
from app.core.connectivity import is_cloud_reachable
from app.core.deps import require_any_role
from app.core.errors import LLMUnavailableError, ValidationError
from app.edge.assistant.retrieval import search_manuals
from app.schemas.training import ManualSearchHit, ManualSearchResponse
from app.shared.enums import MachineType

log = logging.getLogger("safe2go.assistant_routes")
router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_MAX_QUERY_LENGTH = 300

NOTICE_NO_SECTIONS = "No manual section matches this question, so there is nothing to explain."
NOTICE_DISABLED = "Explanations are not set up on this system. The manual sections are shown below."
NOTICE_OFFLINE = "The cloud connection is down, so explanations are not available. The manual sections are shown below."
NOTICE_FAILED = "The explanation service did not answer. The manual sections are shown below."


def _clean_query(q: str) -> str:
    query = q.strip()
    if not query:
        raise ValidationError("Enter a question or keywords to search the manuals.")
    if len(query) > _MAX_QUERY_LENGTH:
        raise ValidationError(
            f"Search text is too long. Use at most {_MAX_QUERY_LENGTH} characters.",
            details={"max_length": _MAX_QUERY_LENGTH},
        )
    return query


def _hits(query: str, machine_type: MachineType | None) -> list[ManualSearchHit]:
    return [
        ManualSearchHit(
            manual_id=h.section.manual_id,
            manual_title=h.section.manual_title,
            machine_type=h.section.machine_type,
            section_title=h.section.section_title,
            text=h.section.text,
            score=h.score,
        )
        for h in search_manuals(query, machine_type.value if machine_type else None)
    ]


@router.get("/search", response_model=ManualSearchResponse)
async def search(
    q: str = Query(..., description="Question or keywords"),
    machine_type: MachineType | None = Query(None),
    _user: dict = Depends(require_any_role),
) -> ManualSearchResponse:
    query = _clean_query(q)
    return ManualSearchResponse(query=query, results=_hits(query, machine_type))


class ExplainRequest(BaseModel):
    question: str = Field(min_length=1, max_length=_MAX_QUERY_LENGTH)
    machine_type: MachineType | None = None


class ExplainResponse(ManualSearchResponse):
    explanation: str | None
    notice: str | None
    model: str | None


@router.post("/explain", response_model=ExplainResponse)
async def explain(
    body: ExplainRequest,
    _user: dict = Depends(require_any_role),
) -> ExplainResponse:
    query = _clean_query(body.question)
    hits = _hits(query, body.machine_type)

    def answer(explanation: str | None = None, notice: str | None = None, model: str | None = None) -> ExplainResponse:
        return ExplainResponse(query=query, results=hits, explanation=explanation, notice=notice, model=model)

    if not hits:
        return answer(notice=NOTICE_NO_SECTIONS)
    client = get_deepseek_client()
    if client is None:
        return answer(notice=NOTICE_DISABLED)
    if not is_cloud_reachable():
        return answer(notice=NOTICE_OFFLINE)

    sections = [SourceSection(h.manual_title, h.section_title, h.text) for h in hits]
    try:
        text = await explain_sections(client, query, sections)
    except LLMUnavailableError as exc:
        log.warning("Explanation unavailable, returning retrieval only", extra={"reason": exc.message})
        return answer(notice=NOTICE_FAILED)
    return answer(explanation=text, model=client.model)
