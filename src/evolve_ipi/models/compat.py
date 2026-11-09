"""Native OpenAI chat-completions sampling. No Tinker, tokenizer or family template imports.

This process belongs on the trusted side of the sandbox when it holds real credentials.
In the agent container TARGET_API_KEY must be a dummy credential for a separate broker.
No retries: failures remain in evidence even if a caller retries the request.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from evolve_ipi.config import CompatSpec

logger = logging.getLogger(__name__)
INTERNAL = {"session_id", "session_done", "turn_type", "memory_scope", "user_id", "workspace_id"}


class InvalidResponse(ValueError):
    pass


def validate_response(result: Any, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Reject truncation, malformed/undeclared calls, ambiguous choices and unsupported finishes."""
    if not isinstance(result, dict) or result.get("error"):
        raise InvalidResponse("error_envelope")
    choices = result.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise InvalidResponse("choices")
    choice = choices[0]
    finish = choice.get("finish_reason")
    if finish not in ("stop", "tool_calls"):
        raise InvalidResponse("truncated" if finish == "length" else "finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise InvalidResponse("message")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise InvalidResponse("content")
    # Native fields are required; do not silently repair family markup as MetaClaw otherwise would.
    if any(marker in (content or "") for marker in ("<tool_call>", "<|tool_call", "<|content_invoke_tool")):
        raise InvalidResponse("text_tool_markup")
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or bool(calls) != (finish == "tool_calls"):
        raise InvalidResponse("tool_finish_mismatch")
    declared = {t.get("function", {}).get("name") for t in tools if isinstance(t, dict)}
    seen = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise InvalidResponse("tool_type")
        ident, function = call.get("id"), call.get("function")
        if not isinstance(ident, str) or not ident or ident in seen or not isinstance(function, dict):
            raise InvalidResponse("tool_identity")
        seen.add(ident)
        if function.get("name") not in declared or not isinstance(function.get("arguments"), str):
            raise InvalidResponse("tool_function")
        try:
            args = json.loads(function["arguments"])
        except (ValueError, TypeError):
            raise InvalidResponse("tool_arguments") from None
        if not isinstance(args, dict):
            raise InvalidResponse("tool_arguments")
    usage = result.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            raise InvalidResponse("usage")
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidResponse("usage")
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            raise InvalidResponse("usage_totals")
    return result


def audit_evidence(path: Path) -> list[str]:
    """A provider-specific, mandatory validity check for adaptation and evaluation."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["provider"] != "openai_compat" or data["schema"] != 1:
            return ["unknown compatible evidence schema"]
        started, completed = data["requests_forwarded"], data["requests_completed"]
        failures = data["failures"]
        reasons = ["compatible provider failures: " + ", ".join(sorted({f["kind"] for f in failures}))] if failures else []
        if not isinstance(started, int) or started <= 0 or completed != started:
            reasons.append("compatible requests missing, pending or interrupted")
        if data["usage_missing"]:
            reasons.append("compatible usage missing; token budget cannot be audited")
        return reasons
    except (OSError, ValueError, KeyError, TypeError):
        return ["compatible evidence unreadable or incomplete"]


class CompatBackend:
    def __init__(self, spec: CompatSpec, key: str, temperature: float, evidence: Path, raw: Path,
                 *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.spec, self.key, self.temperature = spec, key, temperature
        self.evidence, self.raw, self.transport = evidence, raw, transport
        self.lock = asyncio.Lock()
        self.state: dict[str, Any] = {
            "schema": 1, "provider": "openai_compat", "base_model": spec.model,
            "config": spec.model_dump(mode="json"), "expected_temperature": temperature,
            "requests_forwarded": 0, "requests_completed": 0, "failures": [],
            "usage_missing": 0, "usage_total_tokens": 0, "requests": [],
            "cost": None, "cost_status": "price_not_verified",
        }
        for file in (evidence, raw):
            if file.exists() and file.stat().st_size:
                raise ValueError("refusing to reuse provider evidence or raw output")
            file.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text("", encoding="utf-8")
        self._save()

    def _safe(self, value: Any) -> Any:
        # Never forward a server's key echo to the agent or persist it in a response/log.
        if isinstance(value, str):
            return value.replace(self.key, "[REDACTED]")
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        if isinstance(value, dict):
            return {self._safe(k): self._safe(v) for k, v in value.items()}
        return value

    def _save(self) -> None:
        tmp = self.evidence.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._safe(self.state), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.evidence)

    def _fail(self, kind: str, status: int, number: int | None, elapsed: float) -> JSONResponse:
        self.state["failures"].append({"kind": kind, "status": status, "request": number, "elapsed_s": elapsed})
        self._save()
        logger.error("COMPAT_INVALID kind=%s status=%s request=%s", kind, status, number)
        return JSONResponse({"error": {"type": "compat_invalid", "code": kind, "message": "Invalid provider run"}},
                            status_code=status)

    async def complete(self, body: dict[str, Any], session_id: str) -> dict[str, Any] | JSONResponse:
        async with self.lock:
            began = time.monotonic()
            if self.state["failures"] or self.state["usage_missing"]:
                return self._fail("run_already_invalid", 503, None, 0)
            if (self.state["requests_forwarded"] >= self.spec.max_requests
                    or self.state["usage_total_tokens"] >= self.spec.max_total_tokens):
                return self._fail("budget_exhausted", 429, None, 0)
            if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
                return self._fail("invalid_request", 400, None, 0)
            send = {k: copy.deepcopy(v) for k, v in body.items() if k not in INTERNAL}
            send.pop("stream_options", None)
            send.update(model=self.spec.model, stream=False, temperature=self.temperature)
            if send.get("n", 1) != 1:
                return self._fail("multiple_choices_unsupported", 400, None, 0)
            limits = [send.pop(k) for k in ("max_tokens", "max_completion_tokens") if k in send]
            if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in limits):
                return self._fail("invalid_token_limit", 400, None, 0)
            # Use one native field only; the laboratory endpoint must be probed for max_tokens support.
            send["max_tokens"] = min([self.spec.max_output_tokens, *limits])
            self.state["requests_forwarded"] += 1
            number = self.state["requests_forwarded"]
            self._save()  # a killed in-flight request leaves a detectable pending request
            try:
                async with httpx.AsyncClient(timeout=self.spec.timeout_s, trust_env=False,
                                             follow_redirects=False, transport=self.transport) as client:
                    response = await client.post(self.spec.base_url + "/chat/completions", json=send,
                                                 headers={"Authorization": "Bearer " + self.key})
                if not 200 <= response.status_code < 300:
                    return self._fail("http_" + str(response.status_code),
                                      response.status_code if 400 <= response.status_code <= 599 else 502,
                                      number, time.monotonic() - began)
                result = validate_response(response.json(), send.get("tools") or [])
            except asyncio.CancelledError:
                self._fail("interrupted", 499, number, time.monotonic() - began)
                raise
            except httpx.TimeoutException:
                return self._fail("timeout", 504, number, time.monotonic() - began)
            except httpx.HTTPError:
                return self._fail("transport_error", 502, number, time.monotonic() - began)
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                kind = str(error) if isinstance(error, InvalidResponse) else "response_json"
                return self._fail(kind, 502, number, time.monotonic() - began)
            result = self._safe(result)
            result["session_id"] = session_id
            usage = result.get("usage")
            self.state["requests_completed"] += 1
            if usage is None:
                self.state["usage_missing"] += 1
            else:
                self.state["usage_total_tokens"] += usage["total_tokens"]
            elapsed = time.monotonic() - began
            choice = result["choices"][0]
            self.state["requests"].append({"request": number, "elapsed_s": elapsed,
                                           "finish_reason": choice["finish_reason"], "usage": usage,
                                           "parameters": {k: send[k] for k in ("temperature", "max_tokens", "reasoning_effort", "store", "stream") if k in send}})
            line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "request": number,
                    "messages": len(send["messages"]), "n_tokens": usage["completion_tokens"] if usage else None,
                    "raw": json.dumps(choice["message"], ensure_ascii=False), "usage": usage}
            with self.raw.open("a", encoding="utf-8") as out:
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._save()
            if usage is None:
                return self._fail("usage_missing", 502, number, elapsed)
            if self.state["usage_total_tokens"] > self.spec.max_total_tokens:
                return self._fail("token_budget_exceeded", 429, number, elapsed)
            return result


async def chunks(result: dict[str, Any]):
    choice = result["choices"][0]
    base = {k: result.get(k) for k in ("id", "created", "model", "session_id")}
    base["object"] = "chat.completion.chunk"
    message = choice["message"]
    delta = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(message["tool_calls"])]
    for item in ({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                 {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
                  "usage": result.get("usage")}):
        yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


def build_app(backend: CompatBackend, ready_line: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if ready_line:
            print(ready_line, flush=True)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "provider": "openai_compat"}

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return backend._fail("invalid_json", 400, None, 0)
        session = request.headers.get("x-session-id") or (body.get("session_id", "unknown") if isinstance(body, dict) else "unknown")
        result = await backend.complete(body, session)
        if isinstance(result, JSONResponse):
            return result
        if body.get("stream"):
            return StreamingResponse(chunks(result), media_type="text/event-stream")
        return JSONResponse(result)

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("family", "base-model", "served-model-name", "record-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--temperature", type=float, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--raw-out", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout-s", type=float, required=True)
    for name in ("max-output-tokens", "max-requests", "max-total-tokens"):
        parser.add_argument("--" + name, type=int, required=True)
    args = parser.parse_args()
    key = os.environ.get("TARGET_API_KEY")
    if not key:
        parser.error("TARGET_API_KEY is required")
    if args.family != "openai_compat":
        parser.error("native compatibility provider requires family=openai_compat")
    spec = CompatSpec(base_url=args.base_url, model=args.base_model, timeout_s=args.timeout_s,
                      max_output_tokens=args.max_output_tokens, max_requests=args.max_requests,
                      max_total_tokens=args.max_total_tokens)
    backend = CompatBackend(spec, key, args.temperature, args.evidence_out, args.raw_out)
    # The launcher additionally waits for the port to listen.
    app = build_app(backend, f"EVAL PROXY READY on {args.host}:{args.port} serving base:{args.base_model} (native OpenAI)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    main()
