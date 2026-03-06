from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.routers import rent


client = TestClient(app)


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lt(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class _FakeSupabase:
    def table(self, name):
        if name == "call_logs":
            return _FakeQuery([{"id": "c1", "tenant_id": "t1", "initiated_by": "agent:l1"}])
        if name == "tenancies":
            return _FakeQuery([{"units": {"properties": {"landlord_id": "l1"}}}])
        if name == "users":
            return _FakeQuery([{"name": "Owner"}])
        return _FakeQuery([])


class _ChatMemoryQuery:
    def __init__(self, db, table_name: str):
        self.db = db
        self.table_name = table_name
        self._filters: list[tuple[str, object]] = []
        self._limit: int | None = None
        self._order_field: str | None = None
        self._order_desc: bool = False
        self._mode: str = "select"
        self._payload: dict | None = None

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def eq(self, field: str, value):
        self._filters.append((field, value))
        return self

    def order(self, field: str, desc: bool = False):
        self._order_field = field
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def insert(self, payload: dict):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict):
        self._mode = "update"
        self._payload = payload
        return self

    def _rows(self) -> list[dict]:
        return self.db.conversations if self.table_name == "conversations" else self.db.chat_messages

    def _filtered(self) -> list[dict]:
        rows = list(self._rows())
        for field, value in self._filters:
            rows = [r for r in rows if r.get(field) == value]
        if self._order_field:
            rows = sorted(
                rows,
                key=lambda x: x.get(self._order_field) or "",
                reverse=self._order_desc,
            )
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def execute(self):
        if self._mode == "insert":
            row = dict(self._payload or {})
            if self.table_name == "conversations":
                self.db._conv_counter += 1
                row.setdefault("id", f"conv-{self.db._conv_counter}")
                row.setdefault("created_at", "2026-01-01T00:00:00Z")
                self.db.conversations.append(row)
            else:
                self.db._msg_counter += 1
                row.setdefault("id", f"msg-{self.db._msg_counter}")
                row.setdefault("created_at", "2026-01-01T00:00:00Z")
                self.db.chat_messages.append(row)
            return SimpleNamespace(data=[row], count=1)

        if self._mode == "update":
            rows = self._filtered()
            for row in rows:
                row.update(self._payload or {})
            return SimpleNamespace(data=rows, count=len(rows))

        rows = self._filtered()
        return SimpleNamespace(data=rows, count=len(rows))


class _ChatMemorySupabase:
    def __init__(self):
        self.conversations: list[dict] = []
        self.chat_messages: list[dict] = []
        self._conv_counter = 0
        self._msg_counter = 0

    def table(self, name: str):
        return _ChatMemoryQuery(self, name)


def test_chat_stream_persists_messages(monkeypatch) -> None:
    fake_sb = _ChatMemorySupabase()
    monkeypatch.setattr(rent, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(rent.settings, "internal_api_secret", "secret")

    async def _fake_stream_agent(**_kwargs):
        yield "Hello "
        yield "there"

    monkeypatch.setattr(rent, "_stream_agent", _fake_stream_agent)

    response = client.post(
        "/api/v1/chat",
        headers={"x-internal-secret": "secret", "x-landlord-id": "l1"},
        json={"user_id": "landlord-1", "message": "Hi Sara"},
    )

    assert response.status_code == 200
    assert response.text == "Hello there"
    assert len(fake_sb.conversations) == 1
    assert len(fake_sb.chat_messages) == 2
    assert fake_sb.chat_messages[0]["sender_type"] == "landlord"
    assert fake_sb.chat_messages[0]["sender_id"] == "l1"
    assert fake_sb.chat_messages[0]["message_text"] == "Hi Sara"
    assert fake_sb.chat_messages[1]["sender_type"] == "ai"
    assert fake_sb.chat_messages[1]["sender_id"] == "l1"
    assert fake_sb.chat_messages[1]["message_text"] == "Hello there"
    assert fake_sb.chat_messages[0]["metadata"]["session_id"]


def test_sweep_requires_internal_token(monkeypatch) -> None:
    monkeypatch.setattr(rent.settings, "internal_scheduler_token", "secret-token")

    response = client.post(
        "/api/v1/rent/sweep",
        json={"mode": "kickoff", "month": "2026-03", "dry_run": True},
    )

    assert response.status_code == 401


def test_initiate_call_returns_twilio_metadata(monkeypatch) -> None:
    monkeypatch.setattr(rent, "get_supabase", lambda: object())
    monkeypatch.setattr(rent, "_find_landlord_name", lambda _landlord_id: "Owner")
    monkeypatch.setattr(
        rent.call_policy_service,
        "validate_tenant_landlord_ownership",
        lambda sb, landlord_id, tenant_id: True,
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "get_policy_limits",
        lambda: {"start_hour_ist": 9, "end_hour_ist": 20, "max_attempts_per_day": 2},
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "count_call_attempts_today",
        lambda sb, tenant_id, landlord_id: 0,
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "evaluate_call_policy",
        lambda **kwargs: (True, "ok"),
    )

    async def _tenants(_landlord_id: str):
        return {
            "status": "success",
            "tenants": [
                {
                    "tenant_id": "t1",
                    "tenant_name": "Tenant One",
                    "tenant_phone": "+911234567890",
                    "preferred_language": "english",
                    "rent_amount": 12000,
                    "days_overdue": 3,
                    "property_name": "Apt",
                    "unit_number": "101",
                    "is_overdue": True,
                }
            ],
        }

    async def _payment_history(_tenant_id: str):
        return {"total_payments": 1}

    async def _call_history(_tenant_id: str):
        return {"total_past_calls": 2}

    async def _initiate_call(**_kwargs):
        return {
            "status": "queued",
            "message": "Twilio call queued",
            "data": {
                "call_id": "c1",
                "provider_status": "queued",
                "provider_call_sid": "CA1234567890",
            },
            "error_message": None,
        }

    monkeypatch.setattr(rent, "get_tenants_with_rent_status", _tenants)
    monkeypatch.setattr(rent, "get_tenant_payment_history", _payment_history)
    monkeypatch.setattr(rent, "get_tenant_collection_history", _call_history)
    monkeypatch.setattr(rent, "initiate_rent_collection_call", _initiate_call)

    response = client.post(
        "/api/v1/initiate-call",
        json={"landlord_id": "l1", "tenant_id": "t1", "tenant_name": "Tenant One"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["call_id"] == "c1"
    assert payload["provider_status"] == "queued"
    assert payload["provider_call_sid"] == "CA1234567890"


def test_initiate_call_trial_blocked(monkeypatch) -> None:
    monkeypatch.setattr(rent, "get_supabase", lambda: object())
    monkeypatch.setattr(rent, "_find_landlord_name", lambda _landlord_id: "Owner")
    monkeypatch.setattr(
        rent.call_policy_service,
        "validate_tenant_landlord_ownership",
        lambda sb, landlord_id, tenant_id: True,
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "get_policy_limits",
        lambda: {"start_hour_ist": 9, "end_hour_ist": 20, "max_attempts_per_day": 2},
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "count_call_attempts_today",
        lambda sb, tenant_id, landlord_id: 0,
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "evaluate_call_policy",
        lambda **kwargs: (True, "ok"),
    )

    async def _tenants(_landlord_id: str):
        return {
            "status": "success",
            "tenants": [
                {
                    "tenant_id": "t1",
                    "tenant_name": "Tenant One",
                    "tenant_phone": "+911234567890",
                    "preferred_language": "english",
                    "rent_amount": 12000,
                    "days_overdue": 3,
                    "property_name": "Apt",
                    "unit_number": "101",
                    "is_overdue": True,
                }
            ],
        }

    async def _payment_history(_tenant_id: str):
        return {"total_payments": 1}

    async def _call_history(_tenant_id: str):
        return {"total_past_calls": 2}

    async def _initiate_call(**_kwargs):
        return {
            "status": "failed",
            "message": "Twilio trial can call only verified numbers.",
            "data": {
                "call_id": "c1",
                "provider_status": "trial_blocked",
            },
            "error_message": "trial_blocked",
        }

    monkeypatch.setattr(rent, "get_tenants_with_rent_status", _tenants)
    monkeypatch.setattr(rent, "get_tenant_payment_history", _payment_history)
    monkeypatch.setattr(rent, "get_tenant_collection_history", _call_history)
    monkeypatch.setattr(rent, "initiate_rent_collection_call", _initiate_call)

    response = client.post(
        "/api/v1/initiate-call",
        json={"landlord_id": "l1", "tenant_id": "t1", "tenant_name": "Tenant One"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["provider_status"] == "trial_blocked"
    assert "verified numbers" in payload["message"]


def test_sweep_processes_candidate(monkeypatch) -> None:
    monkeypatch.setattr(rent.settings, "internal_scheduler_token", "secret-token")
    monkeypatch.setattr(rent, "get_supabase", lambda: object())
    monkeypatch.setattr(
        rent.rent_cycle_service,
        "list_overdue_candidates",
        lambda sb, month: [
            {
                "tenant_id": "t1",
                "tenancy_id": "tn1",
                "landlord_id": "l1",
                "tenant_name": "Tenant One",
                "tenant_phone": "+911234567890",
                "preferred_language": "english",
                "amount_outstanding": 12000,
                "days_overdue": 3,
                "property_name": "Apt",
                "unit_number": "101",
                "period_month": month,
                "landlord_name": "Owner",
            }
        ],
    )
    monkeypatch.setattr(
        rent.rent_cycle_service,
        "mark_candidate_cycle_overdue",
        lambda sb, candidate, month: {"status": "success", "data": {}},
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "get_policy_limits",
        lambda: {"start_hour_ist": 9, "end_hour_ist": 20, "max_attempts_per_day": 2},
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "count_call_attempts_today",
        lambda sb, tenant_id, landlord_id: 0,
    )
    monkeypatch.setattr(
        rent.call_policy_service,
        "evaluate_call_policy",
        lambda **kwargs: (True, "ok"),
    )

    async def _payment_history(_tenant_id: str):
        return {"total_payments": 1}

    async def _call_history(_tenant_id: str):
        return {"total_past_calls": 2}

    async def _initiate_call(**_kwargs):
        return {
            "status": "queued",
            "message": "queued",
            "data": {"call_id": "c1", "provider_status": "queued"},
            "error_message": None,
        }

    async def _notify(**_kwargs):
        return {
            "status": "success",
            "data": {"notification_id": "n1"},
            "message": "ok",
            "error_message": None,
        }

    monkeypatch.setattr(rent, "get_tenant_payment_history", _payment_history)
    monkeypatch.setattr(rent, "get_tenant_collection_history", _call_history)
    monkeypatch.setattr(rent, "initiate_rent_collection_call", _initiate_call)
    monkeypatch.setattr(rent, "create_notification", _notify)

    response = client.post(
        "/api/v1/rent/sweep",
        headers={"X-Internal-Token": "secret-token"},
        json={"mode": "kickoff", "month": "2026-03", "dry_run": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 1
    assert payload["called"] == 1


def test_callback_endpoint_success(monkeypatch) -> None:
    monkeypatch.setattr(rent.settings, "callback_shared_secret", "")
    monkeypatch.setattr(rent, "get_supabase", lambda: _FakeSupabase())

    async def _save_result(**_kwargs):
        return {
            "status": "success",
            "message": "saved",
            "data": {"call_record": {"id": "c1"}},
            "error_message": None,
        }

    async def _notify(**_kwargs):
        return {
            "status": "success",
            "message": "created",
            "data": {"notification_id": "n1"},
            "error_message": None,
        }

    monkeypatch.setattr(rent, "save_call_result", _save_result)
    monkeypatch.setattr(rent, "create_notification", _notify)

    response = client.post(
        "/api/v1/calls/callback",
        json={
            "call_id": "c1",
            "outcome": "promised_payment",
            "transcript": "Will pay tomorrow",
            "duration_seconds": 60,
            "provider_metadata": {"simulated": True},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["notification_id"] == "n1"


def test_twilio_status_callback_updates_call_log_and_notifies(monkeypatch) -> None:
    monkeypatch.setattr(rent, "get_supabase", lambda: _FakeSupabase())
    monkeypatch.setattr(
        rent.twilio_voice,
        "validate_signature",
        lambda url, params, signature: True,
    )

    called = {}

    async def _save_result(**kwargs):
        called["save_call_result"] = kwargs
        return {
            "status": "success",
            "message": "saved",
            "data": {"call_record": {"id": kwargs["call_id"]}},
            "error_message": None,
        }

    async def _notify(**kwargs):
        called["notify"] = kwargs
        return {
            "status": "success",
            "message": "created",
            "data": {"notification_id": "n1"},
            "error_message": None,
        }

    monkeypatch.setattr(rent, "save_call_result", _save_result)
    monkeypatch.setattr(rent, "create_notification", _notify)

    response = client.post(
        "/api/v1/calls/twilio/status?call_id=c1",
        headers={"X-Twilio-Signature": "valid"},
        data={"CallSid": "CA123", "CallStatus": "completed", "CallDuration": "42"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["call_id"] == "c1"
    assert payload["provider_call_sid"] == "CA123"
    assert payload["provider_status"] == "completed"
    assert payload["outcome"] == "completed"
    assert payload["is_terminal"] is True
    assert payload["notification_id"] == "n1"
    assert called["save_call_result"]["call_id"] == "c1"
    assert called["save_call_result"]["outcome"] == "completed"
    assert called["save_call_result"]["duration_seconds"] == 42


def test_twilio_status_callback_rejects_bad_signature(monkeypatch) -> None:
    monkeypatch.setattr(rent, "get_supabase", lambda: _FakeSupabase())
    monkeypatch.setattr(
        rent.twilio_voice,
        "validate_signature",
        lambda url, params, signature: False,
    )

    response = client.post(
        "/api/v1/calls/twilio/status?call_id=c1",
        headers={"X-Twilio-Signature": "invalid"},
        data={"CallSid": "CA123", "CallStatus": "ringing"},
    )

    assert response.status_code == 401


def test_twilio_twiml_endpoint_returns_xml(monkeypatch) -> None:
    monkeypatch.setattr(
        rent.twilio_voice,
        "build_twiml_bootstrap_response",
        lambda call_id: "<Response><Connect><Stream url='wss://example/ws' /></Connect></Response>",
    )

    response = client.post(
        "/api/v1/calls/twilio/twiml/c1",
    )

    assert response.status_code == 200
    assert "<Connect>" in response.text
    assert "application/xml" in response.headers["content-type"]


def test_live_session_start_endpoint(monkeypatch) -> None:
    rent.live_session_service.shutdown()

    async def _save_result(**_kwargs):
        return {
            "status": "success",
            "message": "saved",
            "data": {"call_record": {"id": "c1"}},
            "error_message": None,
        }

    monkeypatch.setattr(rent, "_find_call_log", lambda call_id: {"id": call_id, "tenant_id": "t1", "initiated_by": "agent:l1"})
    monkeypatch.setattr(rent, "save_call_result", _save_result)

    response = client.post(
        "/api/v1/calls/live/session/start",
        json={"call_id": "c1", "source": "api"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["call_id"] == "c1"
    assert payload["live_session_id"]


def test_live_session_end_endpoint(monkeypatch) -> None:
    rent.live_session_service.shutdown()
    record = rent.live_session_service.start_session(call_id="c1", source="api")

    async def _save_result(**_kwargs):
        return {
            "status": "success",
            "message": "saved",
            "data": {"call_record": {"id": "c1"}},
            "error_message": None,
        }

    async def _notify(**_kwargs):
        return {
            "status": "success",
            "message": "created",
            "data": {"notification_id": "n1"},
            "error_message": None,
        }

    monkeypatch.setattr(
        rent,
        "_find_call_log",
        lambda call_id: {"id": call_id, "tenant_id": "t1", "initiated_by": "agent:l1"},
    )
    monkeypatch.setattr(rent, "_resolve_landlord_id_for_call_row", lambda _row: "l1")
    monkeypatch.setattr(rent, "save_call_result", _save_result)
    monkeypatch.setattr(rent, "create_notification", _notify)

    response = client.post(
        "/api/v1/calls/live/session/end",
        json={"live_session_id": record["session_id"], "outcome": "completed"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["call_id"] == "c1"
    assert payload["live_state"] == "ended"
