from datetime import datetime, timezone

from app.services import call_policy_service


def test_evaluate_call_policy_allows_within_window_and_attempts() -> None:
    # 05:00 UTC = 10:30 IST
    now = datetime(2026, 3, 7, 5, 0, tzinfo=timezone.utc)
    allowed, reason = call_policy_service.evaluate_call_policy(attempts_today=1, now_utc=now)

    assert allowed is True
    assert reason == "Call policy check passed"


def test_evaluate_call_policy_blocks_outside_window() -> None:
    # 01:00 UTC = 06:30 IST
    now = datetime(2026, 3, 7, 1, 0, tzinfo=timezone.utc)
    allowed, reason = call_policy_service.evaluate_call_policy(attempts_today=0, now_utc=now)

    assert allowed is False
    assert "outside permitted window" in reason


def test_evaluate_call_policy_blocks_attempt_cap() -> None:
    # 05:00 UTC = 10:30 IST
    now = datetime(2026, 3, 7, 5, 0, tzinfo=timezone.utc)
    allowed, reason = call_policy_service.evaluate_call_policy(
        attempts_today=2,
        now_utc=now,
        max_attempts_per_day=2,
    )

    assert allowed is False
    assert "max 2 attempts" in reason
