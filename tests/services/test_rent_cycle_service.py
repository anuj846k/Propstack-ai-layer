from datetime import date

import pytest

from app.services import rent_cycle_service


def test_build_rent_timeline_march_2026_defaults() -> None:
    timeline = rent_cycle_service.build_rent_timeline("2026-03", due_day=1, grace_period_days=5)

    assert timeline.due_date == date(2026, 3, 1)
    assert timeline.grace_date == date(2026, 3, 6)
    assert timeline.overdue_start_date == date(2026, 3, 7)


def test_derive_cycle_status_transitions() -> None:
    assert rent_cycle_service.derive_cycle_status(18000, 0) == "unpaid"
    assert rent_cycle_service.derive_cycle_status(18000, 9000) == "partially_paid"
    assert rent_cycle_service.derive_cycle_status(18000, 18000) == "paid"


def test_build_rent_timeline_invalid_month() -> None:
    with pytest.raises(ValueError):
        rent_cycle_service.build_rent_timeline("2026-13")
