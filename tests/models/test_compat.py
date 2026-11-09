"""Offline protocol and failure accounting checks. Every upstream call uses MockTransport."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from evolve_ipi.config import CompatSpec, ModelSpec, load_configs
from evolve_ipi.models.compat import CompatBackend, audit_evidence, build_app

TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}},
         {"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}]
BODY = {"model": "local-alias", "messages": [{"role": "user", "content": "Synthetic task"}], "tools": TOOLS}
KEY = "test-fake-key-001"


def reply(content="Done", *, call=None, finish=None, usage=True):
    message = {"role": "assistant", "content": content}
    if call is not None:
        message["tool_calls"] = [{"id": "call-1", "type": "function",
                                  "function": {"name": call, "arguments": '{"path":"synthetic.txt"}'}}]
    result = {"id": "chat-test", "created": 1, "model": "deepseek-flash",
              "choices": [{"index": 0, "message": message,
                           "finish_reason": finish or ("tool_calls" if call else "stop")}]}
    if usage:
        result["usage"] = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    return result


def backend(tmp_path, handler, **overrides):
    spec = CompatSpec(**{"base_url": "https://offline.invalid/v1", "model": "deepseek-flash",
                         "timeout_s": 2, "max_output_tokens": 128, "max_requests": 8,
                         "max_total_tokens": 500, **overrides})
    return CompatBackend(spec, KEY, 0.6, tmp_path / "evidence.json", tmp_path / "raw.jsonl",
                         transport=httpx.MockTransport(handler))


def test_normal_single_and_multiturn_tools_preserve_history(tmp_path):
    received = []
    scripted = [reply(None, call="read"), reply(None, call="write"), reply()]

    def upstream(request):
        assert request.url == "https://offline.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + KEY
        received.append(json.loads(request.content))
        return httpx.Response(200, json=scripted[len(received) - 1])

    b = backend(tmp_path, upstream)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(b)), base_url="http://test") as client:
            body = copy.deepcopy(BODY)
            for i in range(3):
                response = await client.post("/v1/chat/completions", json={**body, "max_completion_tokens": 64},
                                             headers={"x-session-id": "session-A"})
                assert response.status_code == 200
                result = response.json()
                assert result["session_id"] == "session-A"
                message = result["choices"][0]["message"]
                body["messages"].append(message)
                if i < 2:
                    body["messages"].append({"role": "tool", "tool_call_id": "call-1", "content": "synthetic result"})
            assert result["choices"][0]["finish_reason"] == "stop"

    asyncio.run(scenario())
    assert [len(x["messages"]) for x in received] == [1, 3, 5]
    assert received[2]["messages"][2]["tool_call_id"] == "call-1"
    assert all(x["model"] == "deepseek-flash" and x["temperature"] == 0.6 and x["max_tokens"] == 64 for x in received)
    assert all("max_completion_tokens" not in x and x["stream"] is False for x in received)
    assert audit_evidence(b.evidence) == []
    assert b.state["usage_total_tokens"] == 36
    assert len(b.raw.read_text().splitlines()) == 3
    assert KEY not in b.evidence.read_text() + b.raw.read_text()


def test_native_sse_has_indexed_tools_terminal_and_usage(tmp_path):
    b = backend(tmp_path, lambda _: httpx.Response(200, json=reply(None, call="read")))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_app(b)), base_url="http://test") as client:
            r = await client.post("/v1/chat/completions", json={**BODY, "stream": True, "session_id": "s"})
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            pieces = r.text.split("\n\n")
            first, final = [json.loads(x.removeprefix("data: ")) for x in pieces[:2]]
            assert first["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
            assert final["choices"][0]["finish_reason"] == "tool_calls"
            assert final["usage"]["total_tokens"] == 12 and "data: [DONE]" in r.text

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503, 307])
def test_upstream_errors_are_invalid_and_do_not_leak_key_or_retry(tmp_path, status):
    count = 0

    def upstream(_):
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": {"message": KEY}})

    b = backend(tmp_path, upstream)
    async def scenario():
        first = await b.complete(BODY, "s")
        second = await b.complete(BODY, "s")
        assert first.status_code >= 400 and second.status_code == 503
        assert KEY.encode() not in first.body
    asyncio.run(scenario())
    assert count == 1 and audit_evidence(b.evidence)
    assert b.state["failures"][0]["kind"] == "http_" + str(status)
    assert KEY not in b.evidence.read_text()


@pytest.mark.parametrize("alter,kind", [
    (lambda r: r["choices"][0].update(finish_reason="length"), "truncated"),
    (lambda r: r["choices"][0].update(finish_reason=None), "finish_reason"),
    (lambda r: r["choices"][0]["message"].update(content="<tool_call>{}</tool_call>"), "text_tool_markup"),
    (lambda r: r["choices"][0]["message"]["tool_calls"][0]["function"].update(arguments="{"), "tool_arguments"),
    (lambda r: r["choices"][0]["message"]["tool_calls"][0]["function"].update(name="undeclared"), "tool_function"),
    (lambda r: r["usage"].update(total_tokens=0), "usage_totals"),
])
def test_truncation_and_malformed_native_tools_void_the_run(tmp_path, alter, kind):
    result = reply(None, call="read")
    alter(result)
    b = backend(tmp_path, lambda _: httpx.Response(200, json=result))
    r = asyncio.run(b.complete(BODY, "s"))
    assert r.status_code == 502 and b.state["failures"][0]["kind"] == kind
    assert audit_evidence(b.evidence)


def test_timeout_transport_error_and_missing_usage_are_distinct(tmp_path):
    for kind, error in [("timeout", httpx.ReadTimeout("do not log " + KEY)),
                        ("transport_error", httpx.ConnectError(KEY))]:
        def upstream(_, error=error):
            raise error
        b = backend(tmp_path / kind, upstream)
        assert asyncio.run(b.complete(BODY, "s")).status_code >= 500
        assert b.state["failures"][0]["kind"] == kind and audit_evidence(b.evidence)
        assert KEY not in b.evidence.read_text()
    b = backend(tmp_path / "usage", lambda _: httpx.Response(200, json=reply(usage=False)))
    assert asyncio.run(b.complete(BODY, "s")).status_code == 502
    assert b.state["usage_missing"] == 1 and audit_evidence(b.evidence)


def test_interruption_is_persisted_and_propagates(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        async def upstream(_):
            entered.set()
            await asyncio.Event().wait()
        b = backend(tmp_path, upstream)
        task = asyncio.create_task(b.complete(BODY, "s"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert b.state["failures"][0]["kind"] == "interrupted"
        assert audit_evidence(b.evidence)
    asyncio.run(scenario())


def test_request_cap_and_token_usage_limit_block_further_paid_work(tmp_path):
    b = backend(tmp_path, lambda _: httpx.Response(200, json=reply()), max_requests=1)
    async def scenario():
        assert isinstance(await b.complete(BODY, "s"), dict)
        assert (await b.complete(BODY, "s")).status_code == 429
    asyncio.run(scenario())
    assert b.state["requests_forwarded"] == 1 and audit_evidence(b.evidence)


def test_evidence_detects_kill_before_request_returns(tmp_path):
    b = backend(tmp_path, lambda _: httpx.Response(200, json=reply()))
    b.state["requests_forwarded"] = 1
    b._save()
    assert "pending" in " ".join(audit_evidence(b.evidence))


def test_native_module_does_not_import_tinker_or_tokenizers():
    code = "import sys; import evolve_ipi.models.compat; assert not {'tinker','torch','transformers','api_server'} & sys.modules.keys()"
    subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), check=True, capture_output=True)


def test_backend_configuration_refuses_rl_and_credentials_in_url(configs_dir):
    original = load_configs(configs_dir).models["qwen3.8-27b"]
    assert original.provider == "tinker"
    spec = CompatSpec(base_url="http://broker:8080/target/v1", model="deepseek-flash", timeout_s=30,
                      max_output_tokens=2048, max_requests=30, max_total_tokens=200000)
    data = dict(name="lab", family="openai_compat", provider="openai_compat", compat=spec,
                skill_served_name="lab-target-no-tokenizer", skill_train_retries=0, mechanisms=("skills",))
    assert ModelSpec(**data).sampling_model == "deepseek-flash"
    with pytest.raises(ValueError, match="skills only"):
        ModelSpec(**{**data, "mechanisms": ("rl", "skills")})
    with pytest.raises(ValueError, match="without credentials"):
        CompatSpec(**{**spec.model_dump(), "base_url": "https://secret@example.com/v1"})


def test_provider_secret_routing_and_environment_isolation(configs_dir, tmp_path, monkeypatch):
    from evolve_ipi.children import model_server_env
    from evolve_ipi.config import Combo, Secrets
    from evolve_ipi.paths import RuntimePaths
    from evolve_ipi.runner import _secret_fields
    cfgs = load_configs(configs_dir)
    spec = CompatSpec(base_url="http://broker:8080/target/v1", model="deepseek-flash", timeout_s=30,
                      max_output_tokens=128, max_requests=10, max_total_tokens=1000)
    model = ModelSpec(name="lab", family="openai_compat", provider="openai_compat", compat=spec,
                      skill_served_name="lab-target-no-tokenizer", skill_train_retries=0, mechanisms=("skills",))
    cfgs.models["lab"] = model
    combo = Combo(model="lab", task="file-create", mechanism="skills")
    assert _secret_fields([combo], False, cfgs) == ("target_api_key",)
    assert set(_secret_fields([combo], True, cfgs)) == {"target_api_key", "evolver_api_key", "evolver_model", "evolver_base_url"}
    monkeypatch.setenv("TINKER_API_KEY", "must-not-pass")
    env = model_server_env(RuntimePaths(root=tmp_path / "runtime"),
                           Secrets(target_api_key="dummy", tinker_api_key="must-not-pass"), model)
    assert env["TARGET_API_KEY"] == "dummy" and "TINKER_API_KEY" not in env and "HF_TOKEN" not in env


@pytest.mark.skipif(sys.platform == "win32", reason="The process launcher uses Linux process groups/proc")
def test_native_model_process_serves_without_tinker_key(tmp_path, free_port):
    from evolve_ipi.models.launch import server_files, start_model_server
    captured = []
    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            data = json.dumps(reply()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    spec = CompatSpec(base_url=f"http://127.0.0.1:{upstream.server_port}/v1", model="deepseek-flash",
                      timeout_s=5, max_output_tokens=128, max_requests=4, max_total_tokens=1000)
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": os.environ["PYTHONPATH"],
           "HOME": str(tmp_path), "TARGET_API_KEY": KEY}
    server = None
    try:
        server = start_model_server("openai_compat", "deepseek-flash", "local-alias", port,
                                    tmp_path / "server", None, env, temperature=0.6, compat=spec)
        result = httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=BODY,
                            timeout=10, trust_env=False)
        assert result.status_code == 200 and result.json()["choices"][0]["message"]["content"] == "Done"
    finally:
        if server:
            server.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
    assert len(captured) == 1 and captured[0]["model"] == "deepseek-flash"
    assert audit_evidence(server_files(tmp_path / "server").evidence) == []
