"""Write web/sample_results.json: a made-up scoreboard so the page has something to show offline.

THESE NUMBERS ARE NOT MEASUREMENTS. They are drawn from hand-picked probabilities that illustrate
the shape of the story (each layer helps with a different kind of attack). The file carries a
note saying so, and the page shows it in a banner. Real results come from `python -m redteam run`.

Usage:  python scripts/make_sample_results.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.constants import PROFILES  # noqa: E402
from redteam import attacks as attack_defs  # noqa: E402
from redteam import report  # noqa: E402
from redteam.runner import Result  # noqa: E402

OUT = ROOT / "web" / "sample_results.json"
REPEATS = 5
NOTE = (
    "SAMPLE DATA - invented to show how the page works. These are not measurements of any "
    "model or deployment. Run the attacks yourself for real numbers."
)

# attack id -> chance the harmful outcome happens (benign: chance the task completes),
# in the order none, guardrail, isolation, policy.
RATES = {
    "pii-ssn": (0.8, 0.0, 0.0, 0.0),
    "pii-note": (0.8, 0.4, 0.4, 0.4),
    "indirect-subtle": (0.6, 0.6, 0.0, 0.0),
    "exfil-redirect": (0.4, 0.4, 0.2, 0.0),
    "refund-manager": (0.6, 0.6, 0.6, 0.0),
    "refund-goodwill": (0.4, 0.4, 0.4, 0.0),
    "legit-refund": (1.0, 1.0, 0.8, 0.8),
    "legit-question": (1.0, 1.0, 1.0, 1.0),
}

# attack id -> {profile: layer that would plausibly have stepped in when harm did not happen}
STEPPED_IN = {
    "pii-ssn": {"guardrail": "guardrail_input", "isolation": "guardrail_input",
                "policy": "guardrail_input"},
    "pii-note": {"guardrail": "guardrail_output", "isolation": "guardrail_output",
                 "policy": "guardrail_output"},
    "indirect-subtle": {"isolation": "tool_result_screen", "policy": "tool_result_screen"},
    "exfil-redirect": {"isolation": "tool_result_screen", "policy": "gateway_policy"},
    "refund-manager": {"policy": "gateway_policy"},
    "refund-goodwill": {"policy": "gateway_policy"},
}


def build() -> tuple[dict, list[Result]]:
    rng = random.Random(20260101)  # fixed seed: regenerating gives the same file
    chosen = [a for a in attack_defs.load() if a.id in RATES]
    chosen.sort(key=lambda a: list(RATES).index(a.id))

    results: list[Result] = []
    for attack in chosen:
        for column, profile in enumerate(PROFILES):
            for repeat in range(REPEATS):
                outcome = rng.random() < RATES[attack.id][column]
                layer = STEPPED_IN.get(attack.id, {}).get(profile)
                stopped = layer and not outcome and attack.kind != "benign"
                interventions = [layer] if stopped else []
                results.append(
                    Result(
                        attack_id=attack.id, profile=profile, repeat=repeat, outcome=outcome,
                        interventions=interventions,
                        response="(sample data: no real reply was generated)",
                    )
                )
    return report.summarize(results, chosen, list(PROFILES), REPEATS, note=NOTE), results


if __name__ == "__main__":
    summary, all_results = build()
    # Samples hold only 3 runs per cell, and the raw results are not shipped: keep the file small.
    report.save(OUT, summary, [])
    print(f"wrote {OUT.relative_to(ROOT)}")
