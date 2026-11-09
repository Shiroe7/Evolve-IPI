"""Launch a Conda-based Evolve-IPI run through bwrap, with no host network or home access.

Invoke under a resource-limited systemd user scope, normally from a dedicated tmux session.
Only the broker's UDS is shared with the trusted side. All supplied mounts must be dedicated
to this experiment. Never supply a real credential in --setenv or command arguments.
"""
import argparse
import os
import subprocess
from pathlib import Path


def argv_for(repo, conda_root, node_root, work, data, socket, main_python, meta_python, command):
    for item in (repo, conda_root, node_root, data):
        if not item.is_dir():
            raise ValueError(f"Required experiment directory is missing: {item}")
    if not socket.is_socket():
        raise ValueError("A dedicated broker socket is required")
    if any(work.is_relative_to(item) or item.is_relative_to(work) for item in (repo, conda_root, node_root, data)):
        raise ValueError("Writable run state must be separate from code, dependencies and data")
    for name in ("home", "runtime", "outputs", "etc"):
        (work / name).mkdir(parents=True, exist_ok=True)
    (work / "etc" / "passwd").write_text(f"agent:x:{os.getuid()}:{os.getgid()}:Agent:/home/agent:/bin/bash\n")
    (work / "etc" / "group").write_text(f"agent:x:{os.getgid()}:\n")
    (work / "etc" / "hosts").write_text("127.0.0.1 localhost evolve-ipi\n")
    args = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--hostname", "evolve-ipi",
            "--clearenv", "--ro-bind", "/usr", "/usr"]
    for item in ("/lib", "/lib64", "/bin", "/sbin", "/etc/ssl", "/etc/ld.so.cache"):
        if Path(item).exists():
            args += ["--ro-bind", item, item]
    for item in (repo, conda_root, node_root):
        args += ["--ro-bind", str(item), str(item)]
    args += ["--ro-bind", str(data), str(repo / "data")]
    for name in ("passwd", "group", "hosts"):
        args += ["--ro-bind", str(work / "etc" / name), "/etc/" + name]
    args += ["--bind", str(work / "home"), "/home/agent",
             "--bind", str(work / "runtime"), "/var/lib/evolve-ipi",
             "--bind", str(work / "outputs"), "/outputs",
             "--ro-bind", str(socket), "/run/eipi-broker.sock",
             "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--chdir", str(repo)]
    env = {"HOME": "/home/agent", "PATH": str(node_root / "bin") + ":/usr/bin:/bin",
           "SHELL": "/bin/bash", "LANG": "C.UTF-8", "TZ": "America/New_York",
           "PYTHONPATH": str(repo / "src"), "PYTHONUNBUFFERED": "1",
           "EVOLVE_IPI_RUNTIME": "/var/lib/evolve-ipi", "EVOLVE_IPI_OUTPUTS": "/outputs",
           "EVOLVE_IPI_METACLAW_PYTHON": str(meta_python),
           "TARGET_API_KEY": "dummy", "EVOLVER_API_KEY": "dummy",
           "EVOLVER_BASE_URL": "http://127.0.0.1:30014/evolver/v1", "EVOLVER_MODEL": "deepseek-flash"}
    for name, value in env.items():
        args += ["--setenv", name, value]
    return args + [str(main_python), str(repo / "lab" / "inside.py"), "--", *command]


def main():
    parser = argparse.ArgumentParser()
    for name in ("conda-root", "node-root", "work", "data", "socket", "main-python", "meta-python"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command.pop(0)
    if not args.command:
        parser.error("A command is required")
    repo = Path(__file__).resolve().parents[1]
    argv = argv_for(repo, args.conda_root.resolve(), args.node_root.resolve(), args.work.resolve(),
                    args.data.resolve(), args.socket.resolve(), args.main_python.resolve(),
                    args.meta_python.resolve(), args.command)
    raise SystemExit(subprocess.run(argv, check=False).returncode)


if __name__ == "__main__":
    main()
