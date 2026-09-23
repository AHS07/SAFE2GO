"""Phase 9: service suggestions, DeepSeek explanations with fallback, shift summary math."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import assistant as assistant_routes
from app.cloud.assistant.deepseek_client import DeepSeekClient
from app.cloud.assistant.explain import SourceSection, build_user_message, clean_answer
from app.config.thresholds import MaintenanceThresholds
from app.core.connectivity import set_cloud_reachable
from app.core.errors import LLMUnavailableError
from app.core.security import create_access_token
from app.main import app
from app.shared.maintenance import ServiceStatus, service_suggestion
from app.shared.shift_report import actual_minutes, counts, report_time

CFG = MaintenanceThresholds(due_soon_fraction=0.9, overdue_fraction=1.0)
T0 = datetime(2030, 1, 7, 7, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Service suggestions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours_run", "status"),
    [(0, ServiceStatus.OK), (449.9, ServiceStatus.OK), (450, ServiceStatus.DUE_SOON),
     (499.9, ServiceStatus.DUE_SOON), (500, ServiceStatus.OVERDUE), (620, ServiceStatus.OVERDUE)],
)
def test_service_status_follows_share_of_interval(hours_run: float, status: ServiceStatus) -> None:
    result = service_suggestion(1000.0 + hours_run, 1000.0, 500.0, CFG)
    assert result.status == status
    assert result.hours_since_service == round(hours_run, 1)
    assert (result.message is None) == (status == ServiceStatus.OK)


def test_overdue_message_states_the_overrun() -> None:
    result = service_suggestion(1120.0, 600.0, 500.0, CFG)
    assert result.hours_remaining == -20.0
    assert "overdue by 20 engine hours" in result.message


def test_meter_below_last_service_counts_as_zero() -> None:
    assert service_suggestion(90.0, 100.0, 500.0, CFG).hours_since_service == 0.0


# ---------------------------------------------------------------------------
# DeepSeek client
# ---------------------------------------------------------------------------


def _answer(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def _client(handler, retries: int = 1) -> DeepSeekClient:  # noqa: ANN001 - transport handler
    return DeepSeekClient("key", "https://example.test", "deepseek-chat", 10.0, retries, transport=httpx.MockTransport(handler))


async def test_client_returns_the_answer_and_sends_only_the_given_messages() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer key"
        return _answer("  Check the oil.  ")

    assert await _client(handler).complete("system", "user", 100, 0.2) == "Check the oil."
    assert [m["content"] for m in seen[0]["messages"]] == ["system", "user"]


async def test_client_retries_once_after_a_timeout() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return _answer("ok")

    assert await _client(handler).complete("s", "u", 100, 0.2) == "ok"
    assert len(calls) == 2


async def test_client_gives_up_after_the_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(LLMUnavailableError):
        await _client(handler).complete("s", "u", 100, 0.2)
    assert len(calls) == 2


async def test_client_does_not_retry_a_rejected_key() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(LLMUnavailableError):
        await _client(handler).complete("s", "u", 100, 0.2)
    assert len(calls) == 1


async def test_client_rejects_an_unreadable_answer() -> None:
    with pytest.raises(LLMUnavailableError):
        await _client(lambda r: httpx.Response(200, json={"choices": []})).complete("s", "u", 100, 0.2)


def test_prompt_holds_only_the_question_and_manual_text() -> None:
    message = build_user_message("How do I start?", [SourceSection("Manual A", "Starting", "Fasten the seatbelt.")])
    assert message == "Manual sections:\n\nSection 1: Manual A, Starting\nFasten the seatbelt.\n\nQuestion: How do I start?"


def test_emojis_are_removed_from_answers() -> None:
    assert clean_answer("Fasten the seatbelt \U0001F44D✅ first.") == "Fasten the seatbelt first."


# ---------------------------------------------------------------------------
# Explain route: sections always returned, explanation only when possible
# ---------------------------------------------------------------------------


class _FakeClient:
    model = "deepseek-chat"

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.calls = 0

    async def complete(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        self.calls += 1
        if self.answer is None:
            raise LLMUnavailableError("down")
        return self.answer


async def _explain(monkeypatch, client, question: str = "How do I start the engine?") -> dict:  # noqa: ANN001
    monkeypatch.setattr(assistant_routes, "get_deepseek_client", lambda: client)
    token = create_access_token(user_id=str(uuid.uuid4()), role="operator", operator_id="op-1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(
            "/api/assistant/explain", json={"question": question}, headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    return response.json()


async def test_explain_returns_explanation_and_sources(monkeypatch) -> None:
    body = await _explain(monkeypatch, _FakeClient("1. Fasten the seatbelt."))
    assert body["explanation"] == "1. Fasten the seatbelt."
    assert body["results"] and body["notice"] is None


async def test_explain_falls_back_to_sections_when_the_service_fails(monkeypatch) -> None:
    body = await _explain(monkeypatch, _FakeClient(None))
    assert body["explanation"] is None and body["results"]
    assert body["notice"] == assistant_routes.NOTICE_FAILED


async def test_explain_skips_the_service_while_the_cloud_is_down(monkeypatch) -> None:
    client = _FakeClient("unused")
    set_cloud_reachable(False)
    try:
        body = await _explain(monkeypatch, client)
    finally:
        set_cloud_reachable(True)
    assert body["notice"] == assistant_routes.NOTICE_OFFLINE and client.calls == 0
    assert body["results"]


async def test_explain_without_a_key_shows_sections(monkeypatch) -> None:
    body = await _explain(monkeypatch, None)
    assert body["notice"] == assistant_routes.NOTICE_DISABLED and body["results"]


async def test_explain_with_no_matching_section_does_not_call_the_service(monkeypatch) -> None:
    client = _FakeClient("unused")
    body = await _explain(monkeypatch, client, question="zzqx vbnm")
    assert body["results"] == [] and client.calls == 0
    assert body["notice"] == assistant_routes.NOTICE_NO_SECTIONS


# ---------------------------------------------------------------------------
# Shift summary math
# ---------------------------------------------------------------------------


def test_actual_minutes_excludes_paused_and_blocked_time() -> None:
    task = SimpleNamespace(status="done", actual_start=T0, actual_end=T0 + timedelta(minutes=90),
                           paused_minutes=10.0, blocked_minutes=5.0)
    assert actual_minutes(task) == 75.0
    assert actual_minutes(SimpleNamespace(status="in_progress", actual_start=T0, actual_end=None,
                                          paused_minutes=0.0, blocked_minutes=0.0)) is None


def test_working_time_is_engine_time_not_idle() -> None:
    time = report_time({"engine_hours": 2.0, "idle_minutes": 30.0, "idle_ratio": 0.25, "cycle_count": 40, "fuel_used": 31.26})
    assert time.working_minutes == 90.0 and time.fuel_used == 31.3
    assert report_time(None) is None


def test_counts_are_sorted_by_frequency() -> None:
    assert [(c.type, c.count) for c in counts(["tilt", "proximity", "proximity"])] == [("proximity", 2), ("tilt", 1)]
