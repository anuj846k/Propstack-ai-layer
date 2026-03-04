from app.agents.rent_collection import agent


class _FakeTool:
    def __init__(self, name: str):
        self.name = name


class _FakeContext:
    def __init__(self):
        self.state = {}


def test_before_tool_blocks_missing_ids() -> None:
    result = agent._before_tool_guardrail(
        _FakeTool("initiate_rent_collection_call"),
        args={},
        context=_FakeContext(),
    )

    assert result is not None
    assert result["status"] == "blocked"


def test_before_tool_allows_when_policy_passes(monkeypatch) -> None:
    monkeypatch.setattr(agent, "get_supabase", lambda: object())
    monkeypatch.setattr(
        agent.call_policy_service,
        "validate_tenant_landlord_ownership",
        lambda sb, landlord_id, tenant_id: True,
    )
    monkeypatch.setattr(
        agent.call_policy_service,
        "count_call_attempts_today",
        lambda sb, tenant_id, landlord_id, now_utc=None: 0,
    )
    monkeypatch.setattr(
        agent.call_policy_service,
        "get_policy_limits",
        lambda: {"start_hour_ist": 9, "end_hour_ist": 20, "max_attempts_per_day": 2},
    )
    monkeypatch.setattr(
        agent.call_policy_service,
        "evaluate_call_policy",
        lambda **kwargs: (True, "ok"),
    )

    result = agent._before_tool_guardrail(
        _FakeTool("initiate_rent_collection_call"),
        args={"landlord_id": "l1", "tenant_id": "t1"},
        context=_FakeContext(),
    )

    assert result is None


def test_after_tool_normalizes_plain_dict() -> None:
    context = _FakeContext()
    normalized = agent._after_tool_normalizer(
        _FakeTool("sample_tool"),
        args={},
        context=context,
        tool_response={"foo": "bar"},
    )

    assert normalized["status"] == "success"
    assert normalized["data"] == {"foo": "bar"}
    assert context.state["temp:last_tool_status"] == "success"
