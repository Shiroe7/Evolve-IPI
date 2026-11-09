"""Real pinned SkillEvolver/SkillManager file lifecycle with a synthetic, offline LLM client.

Import these two dependency-free modules directly; this does not claim to test the full MetaClaw proxy.
"""
import asyncio
import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from evolve_ipi.adapt.library import lib_sha


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_synthetic_update_persists_reloads_and_isolates_instances(repo_root, tmp_path):
    source = repo_root / "third_party" / "MetaClaw" / "metaclaw"
    evolver_module = module("offline_skill_evolver", source / "skill_evolver.py")
    manager_module = module("offline_skill_manager", source / "skill_manager.py")
    first, second, frozen = (tmp_path / name for name in ("agent-A", "agent-B", "frozen"))
    first.mkdir()
    second.mkdir()
    manager = manager_module.SkillManager(skills_dir=str(first), retrieval_mode="template")
    empty = lib_sha(first)

    class SyntheticLLM:
        def chat_complete(self, prompt):
            assert "Synthetic authorized request" in prompt and "Synthetic feedback" in prompt
            return json.dumps([{"name": "synthetic-confirm", "description": "Confirm scope before a simulated write",
                                "category": "general", "content": "Ask for authorization before a new simulated action."}])

    evolver = evolver_module.SkillEvolver(llm_client=SyntheticLLM(), history_path=str(first / "evolution_history.jsonl"))
    sample = SimpleNamespace(conversation_text="user: Synthetic authorized request\nuser: Synthetic feedback",
                             response_text="I completed the authorized synthetic action.", reward=0)
    skills = asyncio.run(evolver.evolve([sample], manager.skills))
    assert manager.add_skills(skills) == 1
    assert lib_sha(first) != empty and lib_sha(second) == empty
    counts = manager_module.SkillManager(skills_dir=str(first), retrieval_mode="template").get_skill_count()
    assert sum(counts.values()) == 1
    shutil.copytree(first, frozen)
    before = lib_sha(frozen)
    manager.add_skills([{"name": "synthetic-additional", "description": "Synthetic second update",
                        "content": "A second synthetic skill.", "category": "general"}])
    assert lib_sha(frozen) == before and lib_sha(first) != before and lib_sha(second) == empty
    assert len((frozen / "evolution_history.jsonl").read_text().splitlines()) == 1
