"""Sync dead-letter window setting."""
from __future__ import annotations

from app.config.settings import Settings

STRONG = "s" * 32


def test_dead_letter_window_default_and_override() -> None:
    assert Settings(jwt_secret=STRONG, credential_signing_key=STRONG).sync_dead_letter_after_seconds == 3600.0
    custom = Settings(jwt_secret=STRONG, credential_signing_key=STRONG, sync_dead_letter_after_seconds=60)
    assert custom.sync_dead_letter_after_seconds == 60.0
