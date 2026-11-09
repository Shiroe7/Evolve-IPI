#!/bin/bash
# Start under a dedicated tmux session and a resource-limited systemd user scope.
# Usage: bash lab/run_spark1.sh TASK_ROOT RUN_ID check|probe|eval|adapt ...
set -euo pipefail
task_root=$(realpath -- "${1:?Task root required}")
run_id=${2:?Fresh run ID required}
shift 2
case "$task_root/" in "$HOME/"*) ;; *) echo 'Task root must be under your own home'; exit 2;; esac
case "$run_id" in ''|*[!A-Za-z0-9_-]*) echo 'Invalid run ID'; exit 2;; esac
source_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
main_python="$task_root/envs/main/bin/python"
meta_python="$task_root/envs/metaclaw/bin/python"
node_root="$task_root/node-v22.23.2-linux-arm64"
data_root="/data/$(id -un)/evolve-ipi/20261109/data"
run_root="$task_root/runs/$run_id"
umask 077
mkdir -p "$task_root/runs"
mkdir "$run_root" # Never reuse or silently discard previous results.
export PYTHONPATH="$source_root/src"
"$main_python" "$source_root/lab/boundary_probe.py" --conda-root "$task_root/envs" \
    --node-root "$node_root" --work "$run_root/boundary" --data "$data_root" \
    --main-python "$main_python" --meta-python "$meta_python" > "$run_root/boundary.log" 2>&1
broker_args=(--socket "$run_root/model.sock" --ledger "$run_root/broker.json" --max-calls 60
             --target-thinking disabled --evolver-thinking disabled)
if test "${1:-}" = check; then
    broker_args+=(--offline)
else
    broker_args+=(--key-file "$HOME/.config/evolve-ipi/lab_api_key")
fi
"$main_python" "$source_root/lab/broker.py" "${broker_args[@]}" > "$run_root/broker.log" 2>&1 &
broker_pid=$!
trap 'kill "$broker_pid" 2>/dev/null || true; wait "$broker_pid" 2>/dev/null || true' EXIT
for ((n=0;n<100;n++)); do
    test ! -S "$run_root/model.sock" || break
    kill -0 "$broker_pid" 2>/dev/null || { echo 'Broker startup failed'; exit 1; }
    sleep 0.1
done
test -S "$run_root/model.sock"
set +e
if test "${1:-}" = probe; then
    "$main_python" "$source_root/lab/probe_api.py" --socket "$run_root/model.sock" --report "$run_root/probe.json" \
        > "$run_root/command.log" 2>&1
    result=$?
else
    if test "${1:-}" = smoke; then
        command_args=("$main_python" "$source_root/lab/smoke_cycle.py" --configs "$source_root/lab/configs")
    else
        command_args=("$main_python" -m evolve_ipi.cli "$@" --configs "$source_root/lab/configs")
    fi
    "$main_python" "$source_root/lab/isolate.py" --conda-root "$task_root/envs" --node-root "$node_root" \
        --work "$run_root/sandbox" --data "$data_root" --socket "$run_root/model.sock" \
        --main-python "$main_python" --meta-python "$meta_python" -- \
        "${command_args[@]}" \
        > "$run_root/command.log" 2>&1
    result=$?
fi
set -e
"$main_python" - "$run_root" "$result" "${1:-}" <<'PY'
import json, pathlib, sys
root, status, command = pathlib.Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
ledger = json.loads((root / 'broker.json').read_text())
valid = status == 0 and not ledger['invalid'] and not ledger['pending']
if command == 'check' and ledger['calls']:
    valid = False
record = {'exit_code': status, 'valid': valid, 'broker_calls': ledger['calls'],
          'usage_total_tokens': ledger['usage_total_tokens'], 'cost': None,
          'cost_status': 'no_paid_calls' if command == 'check' else 'price_not_verified'}
(root / 'outcome.json').write_text(json.dumps(record, indent=2))
print(json.dumps(record))
if not valid:
    sys.exit(1)
PY
exit "$result"
