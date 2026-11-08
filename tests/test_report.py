from redteam import report
from redteam.attacks import Attack
from redteam.runner import Result

PROFILES = ["none", "policy"]


def attack(id, kind="attack"):
    return Attack(id=id, title=id.title(), category="c", kind=kind, prompt="p",
                  oracle={"type": "response_contains", "needles": ["x"]}, hypothesis="h")


ATTACKS = [attack("harm"), attack("ctrl", "control"), attack("task", "benign")]


def res(attack_id, profile, outcome, interventions=(), error=None, repeat=0):
    return Result(attack_id, profile, repeat, outcome, error=error,
                  interventions=list(interventions), response="r")


def summarize(results, repeats=2):
    return report.summarize(results, ATTACKS, PROFILES, repeats)


# ---------------------------------------------------------------- counting
def test_cells_count_outcomes_over_valid_runs():
    s = summarize([res("harm", "none", True), res("harm", "none", True),
                   res("harm", "none", False)])
    cell = s["cells"]["harm"]["none"]
    assert (cell["outcomes"], cell["valid"]) == (2, 3)
    assert round(cell["rate"], 2) == 0.67


def test_errors_are_excluded_from_the_denominator_and_counted():
    s = summarize([res("harm", "none", True), res("harm", "none", None, error="boom")])
    cell = s["cells"]["harm"]["none"]
    assert (cell["outcomes"], cell["valid"], cell["errors"]) == (1, 1, 1)
    assert s["errors"] == 1 and s["totals"]["none"]["errors"] == 1


def test_a_cell_with_only_errors_has_no_rate_rather_than_zero():
    s = summarize([res("harm", "none", None, error="boom")])
    assert s["cells"]["harm"]["none"]["rate"] is None
    assert s["totals"]["none"]["by_kind"]["attack"]["rate"] is None


def test_totals_are_kept_per_kind():
    s = summarize([
        res("harm", "none", True), res("harm", "none", False),
        res("ctrl", "none", False),
        res("task", "none", True),
    ])
    kinds = s["totals"]["none"]["by_kind"]
    assert kinds["attack"]["rate"] == 0.5
    assert kinds["control"]["rate"] == 0.0
    assert kinds["benign"]["rate"] == 1.0


# ---------------------------------------------------------------- attribution
def test_a_prevented_attack_is_credited_to_the_layer_that_intervened():
    s = summarize([res("harm", "policy", False, ["gateway_policy"]),
                   res("harm", "policy", False, ["gateway_policy", "tool_result_screen"])])
    assert s["totals"]["policy"]["stopped_by"] == {"gateway_policy": 2, "tool_result_screen": 1}


def test_a_prevented_attack_with_no_intervention_is_credited_to_the_model():
    s = summarize([res("harm", "none", False)])
    assert s["totals"]["none"]["stopped_by"] == {report.MODEL_ONLY: 1}


def test_successful_attacks_and_benign_runs_are_not_credited_to_any_layer():
    s = summarize([res("harm", "none", True), res("task", "none", False, ["guardrail_input"])])
    assert s["totals"]["none"]["stopped_by"] == {}


# ---------------------------------------------------------------- rendering
def test_the_text_scoreboard_shows_every_attack_and_profile():
    s = summarize([res("harm", "none", True), res("harm", "policy", False, ["gateway_policy"])])
    text = report.render_text(s)
    assert "harm" in text and "ctrl" in text and "task" in text
    assert "none" in text and "policy" in text
    assert "1/1" in text and "0/1" in text
    assert "gateway_policy" in text


def test_the_text_scoreboard_warns_loudly_about_errors():
    s = summarize([res("harm", "none", None, error="boom")])
    assert "WARNING" in report.render_text(s)


def test_a_clean_run_has_no_warning():
    assert "WARNING" not in report.render_text(summarize([res("harm", "none", True)]))


def test_missing_cells_render_as_a_dash():
    text = report.render_text(summarize([res("harm", "none", True)]))
    assert "-" in text


# ---------------------------------------------------------------- persistence
def test_results_round_trip_through_a_file(tmp_path):
    results = [res("harm", "none", True)]
    s = summarize(results)
    path = tmp_path / "nested" / "run.json"
    report.save(path, s, results)
    assert report.load(path)["cells"]["harm"]["none"]["outcomes"] == 1


def test_samples_are_capped_per_cell():
    s = summarize([res("harm", "none", True, repeat=n) for n in range(10)])
    assert len(s["cells"]["harm"]["none"]["samples"]) == 3
