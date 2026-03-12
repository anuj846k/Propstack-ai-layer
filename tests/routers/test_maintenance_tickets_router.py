from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routers import maintenance_tickets


client = TestClient(app)


class _FakeQuery:
    def __init__(self, db, table_name: str):
        self.db = db
        self.table_name = table_name
        self._eq_filters: list[tuple[str, object]] = []
        self._in_filters: list[tuple[str, set[object]]] = []

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key: str, value: object):
        self._eq_filters.append((key, value))
        return self

    def in_(self, key: str, values: list[object]):
        self._in_filters.append((key, set(values)))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        rows = list(self.db.rows_by_table.get(self.table_name, []))

        def _matches(row: dict) -> bool:
            for k, v in self._eq_filters:
                if row.get(k) != v:
                    return False
            for k, s in self._in_filters:
                if row.get(k) not in s:
                    return False
            return True

        return SimpleNamespace(data=[r for r in rows if _matches(r)])


class _FakeSupabase:
    def __init__(self, rows_by_table: dict[str, list[dict]]):
        self.rows_by_table = rows_by_table

    def table(self, name: str):
        return _FakeQuery(self, name)


def _auth_headers(*, landlord_id: str) -> dict[str, str]:
    return {
        "x-internal-secret": settings.internal_api_secret,
        "x-landlord-id": landlord_id,
    }


def test_list_tickets_includes_image_proxy_urls(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_api_secret", "test-secret")
    fake_sb = _FakeSupabase(
        rows_by_table={
            "properties": [{"id": "p1", "landlord_id": "l1", "name": "P", "address": "A"}],
            "units": [{"id": "u1", "property_id": "p1", "unit_number": "101"}],
            "maintenance_tickets": [
                {
                    "id": "t1",
                    "unit_id": "u1",
                    "tenant_id": "tenant-1",
                    "assigned_vendor_id": None,
                    "title": "Leak",
                    "issue_category": "plumbing",
                    "issue_description": "Pipe leak",
                    "priority": "high",
                    "status": "open",
                    "ai_severity_score": 90,
                    "ai_summary": "Bad leak",
                    "scheduled_at": None,
                    "resolved_at": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "image_url": "https://api.twilio.com/media/1",
                }
            ],
            "users": [{"id": "tenant-1", "name": "T", "phone": "+1"}],
            "ticket_images": [
                {
                    "id": "img-1",
                    "ticket_id": "t1",
                    "image_url": "https://api.twilio.com/media/2",
                    "uploaded_at": "2026-01-01T00:00:01Z",
                }
            ],
            "vendor_dispatch_logs": [{"ticket_id": "t1", "status": "called", "created_at": "x"}],
        }
    )
    monkeypatch.setattr(maintenance_tickets, "get_supabase", lambda: fake_sb)

    res = client.get("/api/v1/maintenance/tickets", headers=_auth_headers(landlord_id="l1"))
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == "t1"
    assert data[0]["image_proxy_url"] == "/api/v1/maintenance/tickets/t1/image"
    assert data[0]["images"][0]["image_proxy_url"] == "/api/v1/maintenance/tickets/t1/images/img-1"


def test_list_tickets_requires_headers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_api_secret", "test-secret")
    res = client.get("/api/v1/maintenance/tickets")
    # Missing required headers yields FastAPI validation error (422) or auth error (401).
    assert res.status_code in (400, 401, 422)

