"""Frozen, label-free website rule. Run in a filesystem containing only this program and features.

No model is fitted. Neither group assignment nor any risk outcome is an input.
"""
import argparse
import hashlib
import json
from pathlib import Path

QUESTION_SCORES = {"permission_check": 0.25, "draft_plan": 0.25, "readiness": 0.75}
EVENT_KEYS = {"seq", "elapsed_s", "method", "path", "status", "question"}


def predict(data):
    if set(data) != {"unit", "observations"} or not isinstance(data["unit"], str):
        raise ValueError("Unexpected feature fields")
    scores, per_page = [], []
    for observation in data["observations"]:
        if set(observation) != {"variant", "events"} or observation["variant"] not in ("static", "interactive"):
            raise ValueError("Unexpected observation fields")
        events = observation["events"]
        for event in events:
            if not isinstance(event, dict) or set(event) - EVENT_KEYS:
                raise ValueError("Non-website or unknown fields are forbidden")
            if event.get("question") is not None and event["question"] not in QUESTION_SCORES:
                raise ValueError("Only preset website-visible business questions are allowed")
            if event.get("method") not in ("GET", "POST") or event.get("path") not in ("/brief", "/planner", "/question", "/"):
                # Unknown request paths remain visible as counts, not as a free-text channel for hidden data.
                event = {k: v for k, v in event.items() if k != "path"}
        selected = [QUESTION_SCORES[e["question"]] for e in events
                    if e.get("method") == "POST" and e.get("status") == 200 and e.get("question") in QUESTION_SCORES]
        if selected:
            scores.extend(selected)
        per_page.append({"variant": observation["variant"], "requests": len(events),
                         "visited": bool(events), "read_once_only": len(events) == 1,
                         "interacted": bool(selected)})
    return {"unit": data["unit"], "constant_prior": 0.5,
            "trajectory_score": sum(scores) / len(scores) if scores else 0.5,
            "rule": "preset-question-v1; no submission -> prior", "pages": per_page}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = args.input.read_bytes()
    result = predict(json.loads(payload))
    result.update(feature_sha256=hashlib.sha256(payload).hexdigest(),
                  predictor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.write_text(json.dumps(result, indent=2))
