from types import SimpleNamespace
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from app.main import app
from app.routers import maintenance


client = TestClient(app)


class _FakeQuery:
    def __init__(self, db, table_name: str):
        self.db = db
        self.table_name = table_name

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.table_name == "users":
            return SimpleNamespace(data=self.db.user_rows)
        if self.table_name == "maintenance_tickets":
            if self.db.ticket_reads < len(self.db.ticket_rows_by_read):
                data = self.db.ticket_rows_by_read[self.db.ticket_reads]
            else:
                data = []
            self.db.ticket_reads += 1
            return SimpleNamespace(data=data)
        if self.table_name == "vendor_dispatch_logs":
            if self.db.dispatch_reads < len(self.db.dispatch_rows_by_read):
                data = self.db.dispatch_rows_by_read[self.db.dispatch_reads]
            else:
                data = []
            self.db.dispatch_reads += 1
            return SimpleNamespace(data=data)
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def __init__(self, *, ticket_rows_by_read, dispatch_rows_by_read=None):
        self.user_rows = [{"id": "tenant-1"}]
        self.ticket_rows_by_read = ticket_rows_by_read
        self.dispatch_rows_by_read = dispatch_rows_by_read or []
        self.ticket_reads = 0
        self.dispatch_reads = 0

    def table(self, name: str):
        return _FakeQuery(self, name)


def _extract_twiml_message(xml_text: str) -> str:
    root = ElementTree.fromstring(xml_text)
    node = root.find("Message")
    return node.text if node is not None and node.text else ""


def _patch_runner(monkeypatch, texts: list[str]) -> None:
    class _FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run_async(self, **_kwargs):
            for text in texts:
                yield SimpleNamespace(
                    content=SimpleNamespace(parts=[SimpleNamespace(text=text)])
                )

    monkeypatch.setattr(maintenance, "Runner", _FakeRunner)


def _send_whatsapp_message() -> str:
    response = client.post(
        "/api/v1/maintenance/twilio-whatsapp-incoming",
        data={
            "Body": "fan is not working",
            "From": "whatsapp:+911234567890",
            "NumMedia": "0",
        },
        headers={"X-Twilio-Signature": "valid"},
    )
    assert response.status_code == 200
    return _extract_twiml_message(response.text)


def test_existing_open_ticket_no_new_ticket_does_not_dispatch(monkeypatch) -> None:
    fake_sb = _FakeSupabase(
        ticket_rows_by_read=[
            [{"id": "ticket-1", "issue_category": "electrical", "status": "open"}],
            [{"id": "ticket-1", "issue_category": "electrical", "status": "open"}],
        ],
    )
    monkeypatch.setattr(maintenance, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(maintenance.twilio_voice, "validate_signature", lambda **_k: True)
    _patch_runner(
        monkeypatch,
        [
            "Okay, please tell me about your new request. What maintenance issue are you experiencing?"
        ],
    )

    calls: list[tuple[str, str]] = []

    async def _fake_dispatch(ticket_id: str, specialty: str) -> dict:
        calls.append((ticket_id, specialty))
        return {"status": "success", "provider_status": "queued", "dispatch_log_id": "d1"}

    monkeypatch.setattr(maintenance, "_dispatch_vendor_for_ticket", _fake_dispatch)

    message = _send_whatsapp_message()

    assert calls == []
    assert "logged your maintenance request as a ticket" not in message
    assert message.startswith("Okay, please tell me about your new request.")


def test_new_ticket_created_dispatch_success_replaces_agent_text(monkeypatch) -> None:
    fake_sb = _FakeSupabase(
        ticket_rows_by_read=[
            [],
            [{"id": "ticket-2", "issue_category": "electrical", "status": "open"}],
        ],
        dispatch_rows_by_read=[[{"status": "called"}]],
    )
    monkeypatch.setattr(maintenance, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(maintenance.twilio_voice, "validate_signature", lambda **_k: True)
    _patch_runner(monkeypatch, ["Agent draft that should be replaced"])

    calls: list[tuple[str, str]] = []

    async def _fake_dispatch(ticket_id: str, specialty: str) -> dict:
        calls.append((ticket_id, specialty))
        return {
            "status": "success",
            "dispatch_status": "call_initiated",
            "provider_status": "queued",
            "dispatch_log_id": "dispatch-2",
            "message": "Initiated call",
        }

    monkeypatch.setattr(maintenance, "_dispatch_vendor_for_ticket", _fake_dispatch)

    message = _send_whatsapp_message()

    assert calls == [("ticket-2", "electrical")]
    assert (
        message
        == "I've logged your maintenance request as a ticket and started contacting an "
        "appropriate vendor now (call status: called). You'll receive updates once a "
        "vendor is assigned."
    )
    assert "Agent draft" not in message


def test_new_ticket_created_dispatch_failure_uses_status_and_reason(monkeypatch) -> None:
    fake_sb = _FakeSupabase(
        ticket_rows_by_read=[
            [],
            [{"id": "ticket-3", "issue_category": "electrical", "status": "open"}],
        ],
        dispatch_rows_by_read=[[{"status": "no_answer"}]],
    )
    monkeypatch.setattr(maintenance, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(maintenance.twilio_voice, "validate_signature", lambda **_k: True)
    _patch_runner(monkeypatch, ["Agent text should not appear"])

    async def _fake_dispatch(_ticket_id: str, _specialty: str) -> dict:
        return {
            "status": "error",
            "dispatch_status": "call_failed",
            "provider_status": "failed",
            "dispatch_log_id": "dispatch-3",
            "message": "All available vendors have already been contacted or rejected the job.",
        }

    monkeypatch.setattr(maintenance, "_dispatch_vendor_for_ticket", _fake_dispatch)

    message = _send_whatsapp_message()

    assert "status: no_answer" in message
    assert "All available vendors have already been contacted or rejected the job." in message
    assert "Agent text should not appear" not in message


def test_no_new_ticket_uses_fallback_when_agent_returns_empty(monkeypatch) -> None:
    fake_sb = _FakeSupabase(ticket_rows_by_read=[[], []])
    monkeypatch.setattr(maintenance, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(maintenance.twilio_voice, "validate_signature", lambda **_k: True)
    _patch_runner(monkeypatch, [])

    calls: list[tuple[str, str]] = []

    async def _fake_dispatch(ticket_id: str, specialty: str) -> dict:
        calls.append((ticket_id, specialty))
        return {"status": "success", "provider_status": "queued", "dispatch_log_id": "d4"}

    monkeypatch.setattr(maintenance, "_dispatch_vendor_for_ticket", _fake_dispatch)

    message = _send_whatsapp_message()

    assert calls == []
    assert (
        message
        == "I'm having trouble processing your request right now. Please try again shortly."
    )


def test_no_new_ticket_creation_claim_is_overridden_with_real_dispatch_status(
    monkeypatch,
) -> None:
    fake_sb = _FakeSupabase(
        ticket_rows_by_read=[
            [{"id": "ticket-4", "issue_category": "plumbing", "status": "open"}],
            [{"id": "ticket-4", "issue_category": "plumbing", "status": "open"}],
        ],
        dispatch_rows_by_read=[[{"status": "no_answer"}]],
    )
    monkeypatch.setattr(maintenance, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(maintenance.twilio_voice, "validate_signature", lambda **_k: True)
    _patch_runner(
        monkeypatch,
        [
            "A high-priority ticket for this issue was created immediately. A vendor is on their way."
        ],
    )

    calls: list[tuple[str, str]] = []

    async def _fake_dispatch(ticket_id: str, specialty: str) -> dict:
        calls.append((ticket_id, specialty))
        return {"status": "success", "provider_status": "queued", "dispatch_log_id": "d5"}

    monkeypatch.setattr(maintenance, "_dispatch_vendor_for_ticket", _fake_dispatch)

    message = _send_whatsapp_message()

    assert calls == []
    assert (
        message
        == "Your request is already logged under an open ticket. Latest dispatch "
        "status is no_answer (the vendor did not answer). We're contacting the next "
        "available vendor."
    )
