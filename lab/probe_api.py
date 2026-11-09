"""Small, synthetic, sequential capability probe through the trusted UDS broker.

No tool is executed: tool results are literal synthetic strings. This observes ordinary/tool/
multi-turn/evolver behavior and usage. It does not deliberately exhaust a provider's rate limit.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx


async def run(args):
    tools = [{"type": "function", "function": {"name": name, "description": "Synthetic offline test only",
             "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
             for name in ("synthetic_read", "synthetic_write")]
    records = []
    transport = httpx.AsyncHTTPTransport(uds=str(args.socket))
    async with httpx.AsyncClient(transport=transport, base_url="http://broker", timeout=100) as client:
        async def call(case, role, body):
            began = time.monotonic()
            response = await client.post(f"/{role}/v1/chat/completions", json=body)
            record = {"case": case, "status": response.status_code, "elapsed_s": time.monotonic() - began}
            if response.status_code != 200:
                record["error"] = response.json()
                records.append(record)
                raise RuntimeError("Capability probe failed: " + case)
            result = response.json()
            choice = result["choices"][0]
            record.update(usage=result.get("usage"), finish_reason=choice.get("finish_reason"),
                          tool_count=len(choice["message"].get("tool_calls") or []))
            records.append(record)
            return choice["message"]

        try:
            await call("plain_chat", "target", {"model": "local", "messages": [{"role": "user", "content": "Reply exactly: synthetic-ok"}],
                                                "max_tokens": 64, "temperature": 0.6,
                                                "reasoning_effort": "low", "store": False})
            messages = [{"role": "user", "content": "Call synthetic_read for name=report. All data and tools are fictional."}]
            for name in ("synthetic_read", "synthetic_write"):
                message = await call(name, "target", {"model": "local", "messages": messages, "tools": tools,
                    "tool_choice": {"type": "function", "function": {"name": name}}, "max_tokens": 256, "temperature": 0.6})
                if not message.get("tool_calls") or any(c["function"]["name"] != name for c in message["tool_calls"]):
                    raise RuntimeError("Requested tool choice was not honored")
                messages.append(message)
                for tool in message["tool_calls"]:
                    messages.append({"role": "tool", "tool_call_id": tool["id"], "content": "Synthetic result: success; no external action occurred."})
                messages.append({"role": "user", "content": "Now call synthetic_write for name=copy." if name == "synthetic_read"
                                 else "Summarize the fictional results. Do not call any more tools."})
            await call("multi_turn_final", "target", {"model": "local", "messages": messages, "max_tokens": 256, "temperature": 0.6})
            await call("evolver_parameter_shape", "evolver", {"model": "local", "messages": [{"role": "user", "content": "Reply exactly: synthetic-evolver-ok"}],
                                                                "max_completion_tokens": 64})
            status = "passed"
        except Exception:
            status = "failed"
            raise
        finally:
            report = {"status": status, "cases": records, "upstream_calls_max": 5,
                      "rate_limit": "not deliberately triggered", "timeout": "mock-tested; not induced here",
                      "cost": None, "cost_status": "price_not_verified", "real_tool_actions": 0}
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"status": status, "cases": len(records), "report": str(args.report)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a fresh report path")
    asyncio.run(run(args))
