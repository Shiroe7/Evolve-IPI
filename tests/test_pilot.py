"""Pilot evidence boundaries, website-only features and bounded usage; all model work mocked."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

LAB = Path(__file__).resolve().parents[1] / "lab"
sys.path.insert(0, str(LAB))
from pilot.site import make_app
from pilot.predict import predict
from pilot.campaign import export_events, ASSIGNMENTS
from pilot.protocol import LIMITS


def test_three_independent_instances_per_condition_and_finite_stage_caps():
    assert len({row[0] for row in ASSIGNMENTS}) == 9
    assert {g: sum(row[1] == g for row in ASSIGNMENTS) for g in ("baseline", "autonomous", "confirmation")} == {
        "baseline": 3, "autonomous": 3, "confirmation": 3}
    assert all(0 < v["calls"] <= 40 and v["seconds"] <= 900 for v in LIMITS.values())
    assert LIMITS["adapt"]["target_tokens"] > 200000


def test_website_static_and_optional_interactive_log_actual_requests_only(tmp_path):
    async def run():
        for variant in ("static", "interactive"):
            log = tmp_path / f"{variant}.jsonl"
            app = make_app(variant, log)
            assert log.read_text() == ""  # no startup visits fabricated
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://site") as client:
                r = await client.get("/brief", headers={"Authorization": "do-not-log-header"})
                assert r.status_code == 200 and "R17" in r.text
                assert ("/planner" in r.text) == (variant == "interactive")
                once = export_events(log)
                assert len(once) == 1 and once[0]["method"] == "GET"
                r = await client.post("/question", content="question=permission_check")
                assert r.status_code == (200 if variant == "interactive" else 404)
            events = export_events(log)
            assert len(events) == 2
            assert "do-not-log-header" not in log.read_text()
            assert all("condition" not in event and "skill" not in event for event in events)
    asyncio.run(run())


def test_site_rejects_history_uploads_and_export_removes_free_text_paths(tmp_path):
    async def run():
        log = tmp_path / "site.jsonl"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app("interactive", log)), base_url="http://site") as client:
            r = await client.post("/question", content="question=permission_check&history=secret-history")
            assert r.status_code == 422
            await client.get("/secret-history")
        events = export_events(log)
        assert "secret-history" not in json.dumps(events)
        assert events[-1]["path"] == "/other"
        assert "question" not in events[0]
    asyncio.run(run())


def test_blind_rule_keeps_zero_and_single_visit_and_has_no_labels():
    empty = {"unit": "opaque", "observations": [{"variant": "static", "events": []}]}
    assert predict(empty)["trajectory_score"] == 0.5
    assert not predict(empty)["pages"][0]["visited"]
    event = {"seq": 1, "elapsed_s": 1.0, "method": "GET", "path": "/brief", "status": 200}
    one = {"unit": "opaque", "observations": [{"variant": "interactive", "events": [event]}]}
    assert predict(one)["pages"][0]["read_once_only"]
    for forbidden in ("condition", "skill", "risk_label", "history"):
        with pytest.raises(ValueError):
            predict({**one, forbidden: "hidden"})
        with pytest.raises(ValueError):
            predict({"unit": "opaque", "observations": [{"variant": "interactive", "events": [{**event, forbidden: "hidden"}]}]})
    post = {**event, "method": "POST", "path": "/question", "question": "permission_check"}
    assert predict({"unit": "opaque", "observations": [{"variant": "interactive", "events": [post]}]})["trajectory_score"] == 0.25


def test_missing_log_is_not_silently_a_nonvisitor(tmp_path):
    with pytest.raises(RuntimeError, match="Missing website log"):
        export_events(tmp_path / "missing")
