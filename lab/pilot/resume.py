"""One explicit infrastructure recovery, retaining the pinned study checkout and frozen unit.

This controller is deployed separately from --runner-repo. It does not change the study's
prompts, website, predictor, seed, unit count or model configuration. It admits new stages
only after three low-pressure memory checks, archives the known interrupted attempt and
permits just one replacement attempt of that SAME stage/unit. No successful history is rerun.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def memory_state():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":", 1)
        values[key] = int(rest.strip().split()[0])
    full = next(line for line in Path("/proc/pressure/memory").read_text().splitlines() if line.startswith("full "))
    pressure = {k: float(v) for k, v in (part.split("=") for part in full.split()[1:])}
    return {"available_gib": values["MemAvailable"] / 1024**2, "full_psi_avg10": pressure["avg10"],
            "swap_used_gib": (values["SwapTotal"] - values["SwapFree"]) / 1024**2, "at_unix": time.time()}


def admit(root, name):
    stable, began = 0, time.monotonic()
    while time.monotonic() - began < 600:
        state = memory_state()
        state.update(stage=name, admission="waiting")
        safe = state["available_gib"] >= 24 and state["full_psi_avg10"] < 1.0
        stable = stable + 1 if safe else 0
        state["consecutive_safe_checks"] = stable
        with (root / "resource-admission.jsonl").open("a") as handle:
            handle.write(json.dumps(state) + "\n")
        print(json.dumps(state), flush=True)
        if stable >= 3:
            return
        time.sleep(10)
    raise RuntimeError("Resource admission unavailable for ten minutes; no model call started")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--runner-repo", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--resume-stage", choices=("p06-website-interactive",), required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.runner_repo.resolve() / "lab"))
    from pilot.campaign import Campaign, ASSIGNMENTS, export_events
    from pilot.protocol import save_json, skill_manifest
    root = args.campaign.resolve()
    original = root / "stages" / args.resume_stage
    archived = original.with_name(original.name + "-interrupted-1")
    if archived.exists():
        raise RuntimeError("This one-off recovery was already consumed; inspect live handles, do not repeat")
    audit = json.loads((root / "interruption-audit.json").read_text())
    if not audit["all_recorded_processes_gone"]:
        raise RuntimeError("A positive terminal-process audit is required")
    pids = json.loads((original / "live-processes.json").read_text())
    if any(Path(f"/proc/{pid}").exists() for pid in pids.values() if pid):
        raise RuntimeError("An original process still exists; observation expiry does not justify restart")
    ledger = json.loads((original / "broker.json").read_text())
    if ledger["calls"] or ledger["pending"]:
        raise RuntimeError("This recovery was authorized only for the verified zero-call interruption")
    invalid = json.loads((original / "outcome.json").read_text())
    if invalid["valid"] or invalid["error_type"] != "InfrastructureInterruption":
        raise RuntimeError("Only the audited infrastructure interruption may be recovered")

    class AdmittedCampaign(Campaign):
        def stage(self, unit, phase, **kwargs):
            suffix = kwargs.get("variant") if phase == "website" else (str(kwargs.get("index")) if phase == "risk" else "")
            name = "-".join(x for x in (unit, phase, suffix) if x)
            if not (self.root / "stages" / name / "outcome.json").exists():
                admit(self.root, name)
            return super().stage(unit, phase, **kwargs)

    c = AdmittedCampaign(args.task_root.resolve(), root)
    admit(root, args.resume_stage)
    # Recheck immediately before the only archive/replacement. All paths stay inside this campaign.
    assert original.parent == root / "stages" and archived.parent == original.parent
    assert not any(Path(f"/proc/{pid}").exists() for pid in pids.values() if pid)
    original.rename(archived)
    save_json(root / "recovery-controller.json", {"controller_pid": os.getpid(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pinned_study_commit": c.protocol["source_commit"], "interrupted_stage": str(archived),
        "max_replacement_attempts": 1, "same_unit": "p06", "history_rerun": False,
        "resource_limits": {"cpu_quota": "200%", "memory_max": "4G", "tasks_max": 256}})
    c.failures.append(archived.name)
    # Complete the partial root with its existing, unchanged frozen library.
    unit, condition, replicate = next(row for row in ASSIGNMENTS if row[0] == "p06")
    directory = root / "instances" / unit
    if (directory / "unit.json").exists() or (root / "predictions" / unit).exists():
        raise RuntimeError("Partial root unexpectedly has a result or sealed prediction")
    seed = directory / "seed"
    manifest = json.loads((directory / "frozen-manifest.json").read_text())
    if skill_manifest(seed) != manifest:
        raise RuntimeError("Partial root's original frozen seed changed")
    training = json.loads((root / "stages/p06-adapt/outcome.json").read_text())
    observations, websites = [], []
    for variant in ("static", "interactive"):
        stage, outcome = c.stage(unit, "website", variant=variant, seed=seed)
        observations.append({"variant": variant, "events": export_events(stage / "website-events.jsonl")})
        websites.append(outcome)
    prediction = c.predict_blind(unit, observations)
    risks = [c.stage(unit, "risk", index=index, seed=seed)[1] for index in (0, 1)]
    assert skill_manifest(seed) == manifest
    save_json(directory / "unit.json", {"unit": unit, "condition": condition, "replicate": replicate,
        "training": training, "seed_manifest": manifest, "websites": websites, "prediction": prediction,
        "risks": risks, "infrastructure_replacement": archived.name})
    for unit, condition, replicate in ASSIGNMENTS[6:]:
        if len(c.failures) >= 2:
            raise RuntimeError("Second failed stage stops this recovery")
        c.unit(unit, condition, replicate)
    print(json.dumps({"status": "completed_nine_roots", "archived_invalid_stages": c.failures}), flush=True)


if __name__ == "__main__":
    main()
