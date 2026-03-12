from datetime import date

from app.tools import rent_intel_tools


def test_calculate_vacancy_cost_for_landlord_empty(monkeypatch) -> None:
  """When a landlord has no properties or units, vacancy summary should be zeroed."""

  class _FakeTable:
    def __init__(self, name: str):
      self.name = name

    def select(self, *_args, **_kwargs):
      return self

    def eq(self, *_args, **_kwargs):
      return self

    def in_(self, *_args, **_kwargs):
      return self

    def order(self, *_args, **_kwargs):
      return self

    def execute(self):
      # Return empty data for all tables
      return type("Res", (), {"data": []})()

  class _FakeSupabase:
    def table(self, name: str):
      return _FakeTable(name)

  monkeypatch.setattr(rent_intel_tools, "get_supabase", lambda: _FakeSupabase())

  today = date(2026, 3, 12)
  result = rent_intel_tools._calculate_vacancy_cost_for_landlord(
    landlord_id="landlord-1",
    as_of=today,
  )

  assert result["status"] == "success"
  summary = result["summary"]
  assert summary["total_vacant_units"] == 0
  assert summary["total_days_vacant"] == 0
  assert summary["total_vacancy_cost"] == 0.0
  assert result["units"] == []

