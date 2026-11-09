"""Real bwrap/HTTP bridge and blind-predictor check with a synthetic model; zero paid calls."""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from broker import Broker, app_for
from isolate import argv_for
from pilot.site import make_app
from pilot.campaign import Campaign, export_events
from pilot.protocol import save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.exists():
        raise RuntimeError("Use a fresh preflight directory")
    root.mkdir(parents=True)
    c = Campaign(args.task_root.resolve(), root / "blind-check")
    model_socket, site_socket = root / "mock.sock", root / "site.sock"
    seed = root / "seed"
    seed.mkdir()
    def response(_):
        return httpx.Response(200, json={"id": "offline", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "synthetic-only"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    broker = Broker("synthetic-only-key", root / "mock-ledger.json", transport=httpx.MockTransport(response))
    servers = [uvicorn.Server(uvicorn.Config(app_for(broker), uds=str(model_socket), access_log=False, log_level="warning")),
               uvicorn.Server(uvicorn.Config(make_app("interactive", root / "site-events.jsonl"), uds=str(site_socket), access_log=False, log_level="warning"))]
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    try:
        deadline = time.monotonic() + 20
        while not all(server.started for server in servers):
            if time.monotonic() > deadline:
                raise RuntimeError("Preflight service startup timed out")
            time.sleep(0.05)
        code = r'''
import httpx,pathlib,sys,socket,json
assert not pathlib.Path('/home/ljw/.config/evolve-ipi').exists()
assert not pathlib.Path('/home/ljw/.ssh').exists()
assert not pathlib.Path('/proc',sys.argv[1],'environ').exists()
try: pathlib.Path('/seed/write-must-fail').write_text('no')
except OSError: pass
else: raise AssertionError('Seed writable')
try: socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError: pass
else: raise AssertionError('External network visible')
with httpx.Client(trust_env=False,timeout=10) as client:
 assert 'R17' in client.get('http://127.0.0.1:31080/brief').text
 assert client.post('http://127.0.0.1:31080/question',content='question=permission_check').status_code==200
 r=client.post('http://127.0.0.1:30014/target/v1/chat/completions',json={'messages':[{'role':'user','content':'synthetic'}]})
 assert r.json()['choices'][0]['message']['content']=='synthetic-only'
print('bridge-and-seed-ok')
'''
        command = [str(c.python), "-c", code, str(os.getpid())]
        result = subprocess.run(argv_for(LAB.parent, c.task / "envs", c.node, root / "sandbox", c.data,
            model_socket, c.python, c.meta, command, site_socket=site_socket, seed=seed),
            env=c.env, capture_output=True, text=True, timeout=60)
        save_json(root / "sandbox-result.json", {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError("Preflight sandbox failed: " + result.stderr[-800:])
        features = [{"variant": "interactive", "events": export_events(root / "site-events.jsonl")}]
        prediction = c.predict_blind("preflight-not-an-experimental-unit", features)
        assert prediction["trajectory_score"] == 0.25
        save_json(root / "result.json", {"valid": True, "real_api_calls": 0, "read_only_seed": True,
            "website_bridge": True, "model_bridge": True, "blind_predictor": True})
        print(json.dumps({"preflight": "passed", "real_api_calls": 0}))
    finally:
        for server in servers:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
