Native API / Conda / tmux deployment (method migration, not a numeric paper reproduction)

Upstream base: f754d0f597612d1b18d937d87cdd5955cfe3637a.
Keep the gitlinks, data checksums, framework patches and original Tinker backend.
Use Python 3.12.13, Node 22.23.2, OpenClaw 2026.3.7 and the pinned project dependencies.
The supplied installer uses two dedicated Conda environments because the original project and
MetaClaw pin different Tinker versions. Tinker is installed for compatibility/imports; the native
model server never imports it or makes Tinker requests. Only skills is supported by this backend.

1. Clone your fork recursively, check out a fixed commit and apply/verify evolve_ipi.thirdparty.
2. Keep code/dependencies under your own home, and authoritative task data under your personal
   /data directory. Verify every item in data/SHA256SUMS before running. Keep the original data.
3. Install a checksum-verified ARM64 Miniforge in TASK_ROOT/miniforge3, then invoke
   bash lab/install_conda.sh TASK_ROOT from a dedicated tmux session. Use a systemd user scope
   with CPUQuota=200%, MemoryMax=4G and TasksMax=256. Install pinned Node/OpenClaw in a separate
   private prefix; do not change system packages or the Conda base environment.
4. Run tests and the official offline check before making a paid call. Missing checks are not passes.
5. Configure the replacement API key interactively with python3 lab/configure_key.py on the host.
   It writes only ~/.config/evolve-ipi/lab_api_key, mode 0600. No key in shell arguments, environment
   files, source, outputs or tmux command text. Revoke the old key first.
6. Run lab/boundary_probe.py with dedicated --conda-root (the directory containing the two envs),
   --node-root, --work, --data, --main-python and --meta-python paths. This uses a synthetic UDS
   broker and verifies the real bwrap filesystem/network boundary without any paid call.
7. Start lab/broker.py OUTSIDE the sandbox, supplying --key-file, --socket and --ledger paths.
   It exposes only a Unix socket, fixed per-role models and a fixed laboratory upstream endpoint.
   The default cap is 60 total upstream calls across roles/restarts, concurrency one. It writes
   aggregate usage/status/timing only, never request text, response text or credentials. API failure
   latches this broker run invalid, including SDK retries; pending calls prevent silent restart.
8. Run lab/isolate.py with the same environment paths, a fresh --work directory, NAS --data and
   the broker --socket. After --, pass the absolute main-environment Python, -m evolve_ipi.cli
   and the desired command using --configs lab/configs. Use a resource-limited tmux session.

The sandbox gets a fresh HOME, runtime, outputs, proc/PID namespace, loopback-only network,
read-only source/dependencies/data and one broker Unix socket. inside.py translates local TCP
port 30014 to that socket so OpenClaw and MetaClaw retain their original client interfaces.
No host home, SSH key, real account, host network or Docker socket is mounted. Conda/tmux alone
are not a security boundary. Do not run an untrusted episode outside lab/isolate.py with real keys.
All writable state must be outside source/dependency/data mounts, with one directory per instance.

Differences requiring reporting:
- Native structured tools and API usage replace HF tokenizer/family templates/raw sampling tokens.
- Target max_tokens is capped at 2048; evolver retains its native max_completion_tokens field.
- Temperature is forced for the target, while the evolver's original omitted temperature is preserved.
- ARM64/native OS differs from the author's amd64 image; the hostname and timezone are pinned.
- Session-close probes use a normal completion allowance rather than an intentional one-token
  truncation. Every such call and evolver preflight counts toward usage and broker limits.
- Missing usage, truncation, malformed tools, API errors and interruptions invalidate a run.
- The target token cap is per process. The trusted broker call cap spans roles/restarts. A usage
  token threshold cannot guarantee a currency bound before an unknown-size request finishes.
- Prices are not assumed: monetary cost stays unknown until the laboratory pricing is verified.
- Tests using mocked models, including the real SkillManager lifecycle test, are functional checks;
  they do not establish real-model skill evolution or any authorization-risk result.

Keep broker ledgers with run records, including invalid runs. Do not recycle a ledger to conceal a
failure. Freeze Skill hashes before evaluation and preserve each instance's separate state.
Before extending to website trajectories, preregister attempt/execution/confirmation/invalid rules,
report coverage including no-visit cases, and split data by independent root agent instance.
