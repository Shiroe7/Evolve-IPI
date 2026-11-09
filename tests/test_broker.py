"""Mocked trusted-broker checks: role separation, persistent caps and credential confinement."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location("lab_broker", Path(__file__).resolve().parents[1] / "lab" / "broker.py")
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)
KEY = "fake-private-broker-key"
BODY = {"model": "attacker-selected", "messages": [{"role": "user", "content": "synthetic"}],
        "max_completion_tokens": 15}


def answer(content="done", finish="stop"):
    return {"id": "x", "model": "deepseek-flash", "created": 0,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


def test_independent_roles_preserve_evolver_parameters_and_redact_key(tmp_path):
    requests = []
    def upstream(request):
        assert request.headers["authorization"] == "Bearer " + KEY
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=answer(KEY))
    b = broker.Broker(KEY, tmp_path / "ledger.json", models={"target": "target-model", "evolver": "evolver-model"},
                      transport=httpx.MockTransport(upstream))
    async def run():
        for role in ("target", "evolver"):
            r = await b.complete(role, BODY)
            assert r.status_code == 200 and KEY.encode() not in r.body
    asyncio.run(run())
    assert [r["model"] for r in requests] == ["target-model", "evolver-model"]
    assert "temperature" not in requests[1] and requests[1]["max_completion_tokens"] == 15
    assert KEY not in b.ledger.read_text() and b.state["usage_total_tokens"] == 10


def test_budget_survives_restart_and_unknown_routes_do_not_call_api(tmp_path):
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json=answer()))
    path = tmp_path / "ledger.json"
    first = broker.Broker(KEY, path, max_calls=1, transport=transport)
    assert asyncio.run(first.complete("arbitrary-url", BODY)).status_code == 404
    assert asyncio.run(first.complete("target", BODY)).status_code == 200
    restarted = broker.Broker(KEY, path, max_calls=1, transport=transport)
    assert asyncio.run(restarted.complete("evolver", BODY)).status_code == 429
    assert len(calls) == 1


def test_upstream_failure_latches_across_roles_and_restarts(tmp_path):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": KEY}}))
    path = tmp_path / "ledger.json"
    b = broker.Broker(KEY, path, transport=transport)
    r = asyncio.run(b.complete("evolver", BODY))
    assert r.status_code == 401 and KEY.encode() not in r.body
    assert asyncio.run(b.complete("target", BODY)).status_code == 503
    restarted = broker.Broker(KEY, path, transport=transport)
    assert asyncio.run(restarted.complete("evolver", BODY)).status_code == 503
    assert b.state["calls"] == 1 and KEY not in path.read_text()


def test_pending_work_prevents_silent_restart(tmp_path):
    path = tmp_path / "ledger.json"
    b = broker.Broker(KEY, path)
    b.state["pending"] = True
    b.save()
    with pytest.raises(ValueError, match="interrupted"):
        broker.Broker(KEY, path)


def test_thinking_configuration_is_explicit_and_independent_of_agent_input(tmp_path):
    received = []
    transport = httpx.MockTransport(lambda r: received.append(json.loads(r.content)) or httpx.Response(200, json=answer()))
    b = broker.Broker(KEY, tmp_path / "ledger.json", transport=transport,
                      thinking_modes={"target": "disabled", "evolver": "enabled"})
    async def run():
        for role in ("target", "evolver"):
            assert (await b.complete(role, {**BODY, "thinking": {"type": "attacker-selected"}})).status_code == 200
    asyncio.run(run())
    assert [r["thinking"]["type"] for r in received] == ["disabled", "enabled"]
    assert b.state["config"]["thinking_modes"] == {"target": "disabled", "evolver": "enabled"}


def test_observed_token_budget_latches_and_blocks_both_roles(tmp_path):
    calls = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json=answer()))
    b = broker.Broker(KEY, tmp_path / "ledger.json", transport=transport, max_total_tokens=4)
    assert asyncio.run(b.complete("target", BODY)).status_code == 429
    assert b.state["usage_total_tokens"] == 5 and b.state["invalid"]
    assert asyncio.run(b.complete("evolver", BODY)).status_code == 503
    assert len(calls) == 1
