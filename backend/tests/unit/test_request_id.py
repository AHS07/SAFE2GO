"""Request ID on responses, in logs, and in the 500 error body (I-12)."""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.error_handlers import register_error_handlers
from app.core.logging import JsonFormatter
from app.core.request_context import REQUEST_ID_HEADER, RequestIdMiddleware


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.formatter = JsonFormatter()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    async def ok() -> dict:
        logging.getLogger("safe2go.test").info("handled")
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("secret internal detail")

    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


async def test_each_response_carries_a_request_id() -> None:
    async with await _client(_app()) as client:
        first = await client.get("/ok")
        second = await client.get("/ok")
    assert first.headers[REQUEST_ID_HEADER]
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_log_lines_inside_a_request_carry_its_id() -> None:
    capture = _Capture()
    logger = logging.getLogger("safe2go.test")
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    try:
        async with await _client(_app()) as client:
            response = await client.get("/ok")
    finally:
        logger.removeHandler(capture)
    assert capture.lines[0]["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_unexpected_error_returns_the_logged_request_id() -> None:
    capture = _Capture()
    logger = logging.getLogger("safe2go.errors")
    logger.addHandler(capture)
    try:
        async with await _client(_app()) as client:
            response = await client.get("/boom")
    finally:
        logger.removeHandler(capture)
    body = response.json()["error"]
    assert response.status_code == 500
    assert body["code"] == "INTERNAL_ERROR"
    assert "secret" not in body["message"]
    assert body["details"]["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert capture.lines[0]["request_id"] == body["details"]["request_id"]
