"""One explicitly labelled functional cycle using the upstream runners/guards and original data.

The official 12-conversation adaptation command remains unchanged. This pinned-source harness
calls its single-attempt implementation with a one-item order, records that protocol difference,
and never claims to reproduce the paper's sample counts.
"""
import argparse
import dataclasses
import json
import shutil
from pathlib import Path

from evolve_ipi import runner
from evolve_ipi.adapt import skills
from evolve_ipi.adapt.library import lib_sha
from evolve_ipi.config import load_configs
from evolve_ipi.evaluate import Base, adapted_agent, run_pass
from evolve_ipi.paths import RuntimePaths
from evolve_ipi.records import RunDir, write_json
from evolve_ipi.tasks import load_skill_order


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", type=Path, required=True)
    args = parser.parse_args()
    cfgs = load_configs(args.configs)
    combo = runner.combo_of(cfgs, "deepseek-flash-lab", "file-create", "skills")
    start = runner.startup(cfgs, [combo], adapting=True)
    # Validate the official installation/full order first; only the smoke recipe is shortened.
    smoke_cfgs = dataclasses.replace(cfgs, skills=cfgs.skills.model_copy(update={"conversations": 1, "max_restarts": 0}))
    model, task, paths = cfgs.models[combo.model], start.tasks[combo.task], start.paths
    run = RunDir(paths.outputs / "functional-smoke")
    order = load_skill_order(combo.task)[:1]
    test_items = [next(iter(task.test))]
    report = {"protocol": "one-conversation-functional-smoke", "official_reproduction": False,
              "adaptation_items": order, "test_items": test_items, "status": "started"}
    config = runner.resolved_config(smoke_cfgs, model, combo.task, "skills", start.patched, start.data_sums)
    config.update(protocol=report["protocol"], adaptation_items=order, test_items=test_items)
    with paths.lock(), run.lock():
        if run.config_path.exists():
            raise RuntimeError("A functional smoke requires fresh output state")
        run.write_config(config)
        try:
            skills.evolver_preflight(start.secrets, smoke_cfgs.skills, paths=paths)
            baseline = run_pass(run, "base", 3, 1, Base("skills"), model, task, smoke_cfgs,
                                start.secrets, paths=paths, items=test_items)
            empty = run.root / "empty-reference"
            empty.mkdir()
            before = lib_sha(empty)
            # All real episode/evolution/guard code is upstream's; there is one attempt and no hidden retry.
            evolved = skills._evolve(model, task, run, smoke_cfgs, start.secrets, paths, order, 1)
            frozen = run.root / "frozen-skill"
            shutil.copytree(evolved["library"], frozen)
            if lib_sha(frozen) != evolved["sha"]:
                raise RuntimeError("Frozen Skill copy differs")
            if evolved["sha"] == before or evolved["skills"] == 0:
                report.update(status="no_skill_change", baseline=baseline, skills=evolved["skills"])
                write_json(run.root / "smoke-report.json", report)
                raise RuntimeError("Evolution completed without changing Skill files")
            agent = adapted_agent(run, "skills", model, smoke_cfgs)
            adapted = run_pass(run, "adapted", 3, 1, agent, model, task, smoke_cfgs,
                               start.secrets, paths=paths, items=test_items)
            if lib_sha(frozen) != evolved["sha"] or lib_sha(evolved["library"]) != evolved["sha"]:
                raise RuntimeError("A frozen/source Skill library changed during evaluation")
            report.update(status="passed", baseline=baseline, adapted=adapted, skills=evolved["skills"],
                          skill_sha_before=before, skill_sha_after=evolved["sha"])
        except BaseException as error:
            if report["status"] == "started":
                report.update(status="invalid", error_type=type(error).__name__)
            write_json(run.root / "smoke-report.json", report)
            raise
        write_json(run.root / "smoke-report.json", report)
    print(json.dumps({"status": report["status"], "skills": report["skills"]}))


if __name__ == "__main__":
    main()
