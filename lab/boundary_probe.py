"""Exercise the actual bwrap/UDS boundary with a synthetic broker, never a paid API or real key."""
import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from broker import Broker, app_for
from isolate import argv_for


def main():
    parser = argparse.ArgumentParser()
    for name in ("conda-root", "node-root", "work", "data", "main-python", "meta-python"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    if work.exists():
        parser.error("Use a fresh boundary-probe directory")
    work.mkdir(parents=True, mode=0o700)
    private = work / "private-sentinel"
    private.write_text("synthetic-host-only-sentinel")
    uds = work / "mock.sock"
    def response(request):
        return httpx.Response(200, json={"id": "probe", "created": 0, "model": "deepseek-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "synthetic-only"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}})
    b = Broker("synthetic-probe-key", work / "ledger.json", transport=httpx.MockTransport(response))
    server = uvicorn.Server(uvicorn.Config(app_for(b), uds=str(uds), access_log=False, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            raise RuntimeError("Synthetic UDS server did not start")
        time.sleep(0.05)
    code = r'''
import json, os, pathlib, socket
import httpx
private, repo, host_pid = __import__('sys').argv[1:]
assert not pathlib.Path(private).exists(), 'Host sentinel exposed'
assert not pathlib.Path('/home/ljw/.ssh').exists(), 'SSH directory exposed'
assert not pathlib.Path('/home/ljw/.config/evolve-ipi').exists(), 'Private key directory exposed'
assert not pathlib.Path('/proc', host_pid, 'environ').exists(), 'Host process environment exposed'
assert 'TINKER_API_KEY' not in os.environ
assert os.environ['TARGET_API_KEY'] == 'dummy'
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('External network exposed')
try:
    pathlib.Path(repo, 'probe-write-must-fail').write_text('must fail')
except OSError:
    pass
else:
    raise AssertionError('Source mount is writable')
pathlib.Path('/home/agent/allowed-probe').write_text('synthetic')
r = httpx.post('http://127.0.0.1:30014/target/v1/chat/completions', trust_env=False, timeout=10,
               json={'model':'local', 'messages':[{'role':'user','content':'synthetic'}]})
assert r.status_code == 200, r.status_code
assert r.json()['choices'][0]['message']['content'] == 'synthetic-only'
print(json.dumps({'filesystem_isolation':True,'external_network_blocked':True,'broker_bridge':True,
                  'source_readonly':True,'real_api_calls':0}))
'''
    repo = Path(__file__).resolve().parents[1]
    command = [str(args.main_python), "-c", code, str(private), str(repo), str(os.getpid())]
    try:
        result = subprocess.run(argv_for(repo, args.conda_root.resolve(), args.node_root.resolve(),
            work / "sandbox", args.data.resolve(), uds, args.main_python, args.meta_python, command),
            capture_output=True, text=True, timeout=60)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
    (work / "stdout.txt").write_text(result.stdout)
    (work / "stderr.txt").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError("Boundary probe failed: " + result.stderr[-800:])
    if b.state["calls"] != 1 or b.state["invalid"]:
        raise RuntimeError("Unexpected synthetic broker usage")
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
