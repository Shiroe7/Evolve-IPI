"""Trusted controller: serial independent instances, bounded stages, blind prediction, then risk.

This program never reads the API key. Only broker.py does. Website logs and risk records are
kept outside each agent sandbox. The predictor is isolated with only an allowlisted feature file.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
REPO = LAB.parent
sys.path.insert(0, str(LAB))
from pilot.protocol import VERSION, LIMITS, BRIEF, QUESTIONS, HISTORY_ITEMS, RISK_ITEMS, save_json, skill_manifest

ASSIGNMENTS = [("p01", "autonomous", 1), ("p02", "confirmation", 1), ("p03", "baseline", 1),
               ("p04", "baseline", 2), ("p05", "autonomous", 2), ("p06", "confirmation", 2),
               ("p07", "confirmation", 3), ("p08", "baseline", 3), ("p09", "autonomous", 3)]
PUBLIC_PATHS = {"/brief", "/planner", "/question", "/"}


def wait_socket(path, process, seconds=30):
    deadline = time.monotonic() + seconds
    while not path.is_socket():
        if process.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError("A trusted service failed to create its socket")
        time.sleep(0.1)


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def export_events(path):
    if not path.exists():
        raise RuntimeError("Missing website log is not evidence of no visits")
    events = []
    for line in path.read_text().splitlines():
        raw = json.loads(line)
        event = {k: raw[k] for k in ("seq", "elapsed_s", "method", "status")}
        event["path"] = raw["path"] if raw["path"] in PUBLIC_PATHS else "/other"
        if raw.get("question") in QUESTIONS:
            event["question"] = raw["question"]
        events.append(event)
    return events


class Campaign:
    def __init__(self, task_root, root):
        self.task = task_root
        self.root = root
        self.python = task_root / "envs/main/bin/python"
        self.meta = task_root / "envs/metaclaw/bin/python"
        self.node = task_root / "node-v22.23.2-linux-arm64"
        self.data = Path("/data") / os.environ["USER"] / "evolve-ipi/20261109/data"
        self.env = {"PATH": str(self.node / "bin") + ":/usr/bin:/bin", "HOME": str(Path.home()),
                    "PYTHONPATH": str(REPO / "src") + ":" + str(LAB), "PYTHONUNBUFFERED": "1",
                    "LANG": "C.UTF-8"}
        self.protocol = {"version": VERSION, "instances": ASSIGNMENTS, "limits": LIMITS,
                         "history_items": HISTORY_ITEMS, "risk_items": RISK_ITEMS,
                         "history_conversations": 2, "feedback_turns_per_conversation": 1,
                         "website_brief": BRIEF, "website_questions": QUESTIONS,
                         "predictor_sha256": hashlib.sha256((LAB / "pilot/predict.py").read_bytes()).hexdigest(),
                         "source_commit": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
                         "thinking": {"target": "disabled", "evolver": "disabled"},
                         "attempts_per_stage": 1, "max_failed_stages_before_stop": 2,
                         "cost": None, "cost_status": "laboratory_relay_price_and_quota_unverified"}
        root.mkdir(parents=True, exist_ok=True)
        config = root / "protocol.json"
        if config.exists() and json.loads(config.read_text()) != json.loads(json.dumps(self.protocol)):
            raise RuntimeError("Campaign protocol differs; start a new campaign instead of mixing evidence")
        save_json(config, self.protocol)
        nas = self.data.parents[1] / "pilot-v1" / root.name
        save_json(nas / "protocol.json", self.protocol)
        self.failures = []

    def stage(self, unit, phase, *, condition=None, variant=None, index=None, seed=None):
        suffix = variant if phase == "website" else (str(index) if phase == "risk" else "")
        name = "-".join(x for x in (unit, phase, suffix) if x)
        directory = self.root / "stages" / name
        outcome_file = directory / "outcome.json"
        if outcome_file.exists():
            old = json.loads(outcome_file.read_text())
            if not old["valid"]:
                raise RuntimeError("A failed stage is never silently retried")
            return directory, old
        if directory.exists():
            # A resumed controller must inspect live process handles, not assume this directory means stopped.
            raise RuntimeError("Unfinished stage requires a live-process audit before any retry")
        directory.mkdir(parents=True)
        cap = LIMITS[phase]
        broker, website, child = None, None, None
        outcome = {"unit": unit, "phase": phase, "variant": variant, "risk_index": index,
                   "valid": False, "attempts": 1, "limits": cap, "started_unix": time.time()}
        handles = []
        try:
            log = (directory / "broker.log").open("w"); handles.append(log)
            broker_argv = [str(self.python), str(LAB / "broker.py"), "--socket", str(directory / "model.sock"),
                "--ledger", str(directory / "broker.json"), "--max-calls", str(cap["calls"]),
                "--max-total-tokens", str(cap["tokens"]), "--target-thinking", "disabled", "--evolver-thinking", "disabled"]
            if phase == "baseline":
                broker_argv += ["--offline"]
            else:
                broker_argv += ["--key-file", str(Path.home() / ".config/evolve-ipi/lab_api_key")]
            broker = subprocess.Popen(broker_argv, env=self.env, stdout=log, stderr=subprocess.STDOUT)
            wait_socket(directory / "model.sock", broker)
            isolate_args = []
            if phase == "website":
                site_log = (directory / "site-server.log").open("w"); handles.append(site_log)
                website = subprocess.Popen([str(self.python), "-m", "pilot.site", "--socket", str(directory / "site.sock"),
                    "--log", str(directory / "website-events.jsonl"), "--variant", variant], env=self.env,
                    stdout=site_log, stderr=subprocess.STDOUT)
                wait_socket(directory / "site.sock", website)
                isolate_args += ["--site-socket", str(directory / "site.sock")]
            if seed is not None:
                isolate_args += ["--seed", str(seed)]
            driver_args = ["--phase", phase]
            if condition:
                driver_args += ["--condition", condition]
            if index is not None:
                driver_args += ["--risk-index", str(index)]
            command = [str(self.python), str(LAB / "isolate.py"), "--conda-root", str(self.task / "envs"),
                "--node-root", str(self.node), "--work", str(directory / "sandbox"), "--data", str(self.data),
                "--socket", str(directory / "model.sock"), "--main-python", str(self.python), "--meta-python", str(self.meta),
                *isolate_args, "--", str(self.python), str(LAB / "pilot/driver.py"), *driver_args]
            agent_log = (directory / "agent.log").open("w"); handles.append(agent_log)
            child = subprocess.Popen(command, env=self.env, stdout=agent_log, stderr=subprocess.STDOUT)
            save_json(directory / "live-processes.json", {"controller": os.getpid(), "broker": broker.pid,
                      "website": website.pid if website else None, "agent_supervisor": child.pid})
            try:
                code = child.wait(timeout=cap["seconds"])
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=10)
                raise RuntimeError("Stage wall-clock limit exceeded")
            ledger = json.loads((directory / "broker.json").read_text())
            report_path = directory / "sandbox/outputs/phase.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
            outcome.update(exit_code=code, agent_report=report, broker_calls=ledger["calls"],
                reported_tokens=ledger["usage_total_tokens"], broker_invalid=ledger["invalid"], broker_pending=ledger["pending"])
            outcome["valid"] = code == 0 and report.get("valid") is True and not ledger["invalid"] and not ledger["pending"]
        except Exception as error:
            outcome.update(error_type=type(error).__name__, error=str(error)[:500])
        finally:
            stop(child); stop(website); stop(broker)
            for handle in handles:
                handle.close()
            outcome["finished_unix"] = time.time()
            # Preserve usage even if the driver was killed before writing its report.
            ledger = directory / "broker.json"
            if ledger.exists():
                budget = json.loads(ledger.read_text())
                outcome.update(broker_calls=budget["calls"], reported_tokens=budget["usage_total_tokens"],
                               broker_invalid=budget["invalid"], broker_pending=budget["pending"])
            save_json(outcome_file, outcome)
        print(json.dumps({k: outcome.get(k) for k in ("unit", "phase", "variant", "risk_index", "valid", "broker_calls", "reported_tokens")}), flush=True)
        if not outcome["valid"]:
            self.failures.append(name)
        return directory, outcome

    def predict_blind(self, unit, observations):
        directory = self.root / "predictions" / unit
        directory.mkdir(parents=True, exist_ok=True)
        features = {"unit": unit, "observations": observations}
        save_json(directory / "features.json", features)
        output = directory / "blind-output"
        output.mkdir(exist_ok=True)
        # Only a single feature file and the frozen pure predictor are mounted; no source repo,
        # registry, Skill seed, agent outputs or risk labels are visible to this process.
        argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "--ro-bind", "/usr", "/usr"]
        for p in ("/lib", "/lib64", "/bin", "/etc/ssl"):
            if Path(p).exists():
                argv += ["--ro-bind", p, p]
        argv += ["--ro-bind", str(self.task / "envs"), str(self.task / "envs"),
                 "--ro-bind", str(LAB / "pilot/predict.py"), "/predict.py",
                 "--ro-bind", str(directory / "features.json"), "/features.json", "--bind", str(output), "/output",
                 "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
                 str(self.python), "/predict.py", "/features.json", "/output/prediction.json"]
        subprocess.run(argv, check=True, timeout=30, env=self.env)
        prediction = json.loads((output / "prediction.json").read_text())
        if prediction["predictor_sha256"] != self.protocol["predictor_sha256"]:
            raise RuntimeError("Predictor changed after protocol freeze")
        save_json(directory / "sealed.json", {"prediction_sha256": hashlib.sha256((output / "prediction.json").read_bytes()).hexdigest(),
                    "sealed_before_risk_unix": time.time(), "input_mounts": ["features.json", "predict.py", "Conda dependencies"]})
        return prediction

    def unit(self, unit, condition, replicate):
        directory = self.root / "instances" / unit
        directory.mkdir(parents=True, exist_ok=True)
        unit_file = directory / "unit.json"
        if unit_file.exists():
            return json.loads(unit_file.read_text())
        phase = "baseline" if condition == "baseline" else "adapt"
        adaptation, training = self.stage(unit, phase, condition=condition if phase == "adapt" else None)
        if not training["valid"]:
            raise RuntimeError("The end-to-end chain requires a valid frozen adaptation state")
        seed = directory / "seed"
        if seed.exists():
            raise RuntimeError("A partial instance must be audited, not silently replaced")
        seed.mkdir()
        source = adaptation / "sandbox/outputs/library"
        for rel in skill_manifest(source):
            dest = seed / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, dest)
        manifest = skill_manifest(seed)
        save_json(directory / "frozen-manifest.json", manifest)
        observations, websites = [], []
        variants = ("interactive", "static") if replicate % 2 else ("static", "interactive")
        for variant in variants:
            stage, outcome = self.stage(unit, "website", variant=variant, seed=seed)
            observations.append({"variant": variant, "events": export_events(stage / "website-events.jsonl")})
            websites.append(outcome)
        prediction = self.predict_blind(unit, observations)
        risks = []
        for index in (0, 1):
            _, outcome = self.stage(unit, "risk", index=index, seed=seed)
            risks.append(outcome)
        if skill_manifest(seed) != manifest:
            raise RuntimeError("Frozen instance changed across observation/risk copies")
        record = {"unit": unit, "condition": condition, "replicate": replicate, "training": training,
                  "seed_manifest": manifest, "websites": websites, "prediction": prediction, "risks": risks}
        save_json(unit_file, record)
        return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--units", type=int, choices=range(1, 10), default=1)
    args = parser.parse_args()
    campaign = Campaign(args.task_root.resolve(), args.campaign.resolve())
    for unit, condition, replicate in ASSIGNMENTS[:args.units]:
        campaign.unit(unit, condition, replicate)
        if len(campaign.failures) >= 2:
            raise RuntimeError("Campaign stopped after two failed stages; no automatic retry")
    print(json.dumps({"status": "complete_for_requested_units", "units": args.units, "failed_stages": campaign.failures}), flush=True)


if __name__ == "__main__":
    main()
