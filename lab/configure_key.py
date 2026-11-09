"""Run interactively on Spark1. The API key is never an argument, environment value or output."""
from pathlib import Path
import getpass
import os
import stat


def main():
    directory = Path.home() / ".config" / "evolve-ipi"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise SystemExit("Private directory ownership must be checked")
    directory.chmod(0o700)
    target = directory / "lab_api_key"
    if target.exists() or target.is_symlink():
        raise SystemExit("Key file already exists; handle rotation privately before rerunning")
    if not os.isatty(0):
        raise SystemExit("Use an interactive terminal")
    if input("Old API key revoked? Type REVOKED: ") != "REVOKED":
        raise SystemExit("Rotation confirmation required")
    key = getpass.getpass("New laboratory API key (hidden): ")
    if not key or any(c.isspace() for c in key):
        raise SystemExit("Invalid key format")
    if key != getpass.getpass("Repeat key (hidden): "):
        raise SystemExit("Entries differ")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(key)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    print("Private key file configured. Never mount this file in the agent container.")


if __name__ == "__main__":
    main()
