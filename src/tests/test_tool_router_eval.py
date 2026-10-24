import json
from pathlib import Path

from aletheore import tool_router as tr


def _eval_accuracy(name):
    data = json.loads((Path(__file__).parent / "data" / name).read_text())
    paths = {d["target"] for d in data if d["target"]}
    for d in data:
        paths.update(tr._PATH_TOKEN.findall(d["question"]))
    hits = high = high_hits = 0
    for d in data:
        decision = tr.route(d["question"], d["target"], sorted(paths))
        got = decision.intent if decision.confidence != "none" else "none"
        hits += got == d["intent"]
        if decision.confidence == "high":
            high += 1
            high_hits += got == d["intent"]
    return hits / len(data), high_hits / high


def test_router_floor_on_the_tuning_questions():
    # The keyword rules were fitted to this set, so this only guards against regressions.
    top1, precision = _eval_accuracy("router_eval_tuning.json")
    assert top1 >= 0.90 and precision >= 0.95


def test_router_floor_on_the_held_out_questions():
    # Written blind by a separate agent that never saw the rules. Measured 81% top-1 and 88% when confident; the
    # floor sits a little below so a real regression fails and noise does not.
    top1, precision = _eval_accuracy("router_eval_heldout.json")
    assert top1 >= 0.76 and precision >= 0.84
