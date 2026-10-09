from __future__ import annotations

from app.core.logging import redact


def test_redact_hides_secret_fields_recursively() -> None:
    assert redact({"api_key": "do-not-log", "nested": {"token": "hidden"}}) == {
        "api_key": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }


def test_task_execution_proofs_are_redacted():
    assert redact({"task_grant": {"lease_token": "private"}, "lease_hash": "private"}) == {
        "task_grant": "[REDACTED]",
        "lease_hash": "[REDACTED]",
    }
