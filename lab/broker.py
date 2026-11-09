"""Trusted UDS-only model broker. Never run this inside the agent's filesystem/PID namespace.

Only aggregate usage/timing/status is persisted. The upstream URL and per-role models are fixed
by trusted configuration, never by an agent request. Counts survive restarts; pending work
requires review. The request cap includes errors and SDK retries.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evolve_ipi.models.compat import InvalidResponse, validate_response


class Broker:
    def __init__(self, key: str, ledger: Path, *, base_url="https://api.cslcqu.cn/v1",
                 models=None, max_calls=60, max_output_tokens=3000, timeout_s=90, transport=None):
        self.key, self.ledger = key, ledger
        self.base_url = base_url
        self.models = models or {"target": "deepseek-flash", "evolver": "deepseek-flash"}
        self.max_calls, self.max_output_tokens = max_calls, max_output_tokens
        self.timeout_s, self.transport = timeout_s, transport
        self.lock = asyncio.Lock()
        self.config = {"base_url": base_url, "models": self.models, "max_calls": max_calls,
                       "max_output_tokens": max_output_tokens, "timeout_s": timeout_s}
        if ledger.exists():
            self.state = json.loads(ledger.read_text())
            if self.state.get("config") != self.config:
                raise ValueError("Broker configuration differs from its persistent ledger")
            if self.state.get("pending"):
                raise ValueError("Broker has interrupted work; review the ledger before continuing")
        else:
            self.state = {"schema": 1, "config": self.config, "calls": 0, "pending": False,
                          "invalid": False,
                          "records": [], "usage_total_tokens": 0, "usage_missing": 0,
                          "cost": None, "cost_status": "price_not_verified"}
            self.save()

    def save(self):
        self.ledger.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.ledger.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)
        temporary.replace(self.ledger)

    def redact(self, value):
        if isinstance(value, str):
            return value.replace(self.key, "[REDACTED]")
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, dict):
            return {self.redact(k): self.redact(v) for k, v in value.items()}
        return value

    async def complete(self, role, body):
        async with self.lock:
            if role not in self.models:
                return self.error("unknown_role", 404)
            if self.state.get("invalid"):
                return self.error("run_already_invalid", 503)
            if self.state["calls"] >= self.max_calls or self.state["usage_missing"]:
                return self.error("budget_or_usage_gate", 429)
            if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
                return self.error("invalid_request", 400)
            if body.get("stream") or body.get("n", 1) != 1:
                return self.error("nonstream_single_choice_required", 400)
            send = {k: v for k, v in body.items() if k not in {"session_id", "session_done", "turn_type"}}
            send["model"] = self.models[role]
            send["stream"] = False
            limits = [k for k in ("max_tokens", "max_completion_tokens") if k in send]
            if len(limits) > 1:
                return self.error("ambiguous_token_limit", 400)
            token_key = limits[0] if limits else ("max_tokens" if role == "target" else "max_completion_tokens")
            value = send.get(token_key, self.max_output_tokens)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return self.error("invalid_token_limit", 400)
            send[token_key] = min(value, self.max_output_tokens)
            self.state["calls"] += 1
            number = self.state["calls"]
            self.state["pending"] = True
            self.save()
            began = time.monotonic()
            status, kind, usage, result = 502, "interrupted", None, None
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False,
                                             follow_redirects=False, transport=self.transport) as client:
                    response = await client.post(self.base_url + "/chat/completions", json=send,
                                                 headers={"Authorization": "Bearer " + self.key})
                if response.status_code != 200:
                    status = response.status_code if 400 <= response.status_code <= 599 else 502
                    kind = "http_" + str(response.status_code)
                else:
                    result = validate_response(response.json(), send.get("tools") or [])
                    usage = result.get("usage")
                    status, kind = (200, "ok") if usage is not None else (502, "usage_missing")
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                status, kind = 504, "timeout"
            except httpx.HTTPError:
                kind = "transport_error"
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                kind = str(error) if isinstance(error, InvalidResponse) else "response_json"
            finally:
                self.state["pending"] = False
                if kind != "ok":
                    self.state["invalid"] = True
                if usage:
                    self.state["usage_total_tokens"] += usage["total_tokens"]
                elif kind == "usage_missing":
                    self.state["usage_missing"] += 1
                self.state["records"].append({"request": number, "role": role, "status": status,
                                               "kind": kind, "usage": usage,
                                               "elapsed_s": time.monotonic() - began})
                self.save()
            return JSONResponse(self.redact(result)) if kind == "ok" else self.error(kind, status)

    @staticmethod
    def error(kind, status):
        # Never include upstream error bodies or exception messages; these can echo credentials.
        return JSONResponse({"error": {"type": "broker_error", "code": kind,
                                        "message": "Model broker request did not complete"}}, status_code=status)


def app_for(broker):
    app = FastAPI()

    @app.post("/{role}/v1/chat/completions")
    async def complete(role: str, request: Request):
        try:
            body = await request.json()
        except ValueError:
            return broker.error("invalid_json", 400)
        return await broker.complete(role, body)

    return app


def read_key(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("The private key must be a regular owner-only 0600 file")
    key = path.read_text().strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Invalid private key file")
    return key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--offline", action="store_true", help="Reject all model work without reading a key or accessing a network")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=60)
    args = parser.parse_args()
    if args.max_calls < 1 or args.socket.exists():
        parser.error("Use a positive cap and a fresh, dedicated socket path")
    os.umask(0o077)
    args.socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not args.offline and args.key_file is None:
        parser.error("--key-file is required unless --offline")
    transport = httpx.MockTransport(lambda _: httpx.Response(503, json={"error": "offline"})) if args.offline else None
    broker = Broker("offline-dummy" if args.offline else read_key(args.key_file), args.ledger,
                    max_calls=args.max_calls, transport=transport)
    uvicorn.run(app_for(broker), uds=str(args.socket), access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
