"""Offline tests for the retrain guardrail's comparison logic."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import guardrail

BASE = {"validation_season": "2024-25",
        "single_stage": {"mae": 7.82, "rmse": 10.4},
        "naive_baseline": {"mae": 8.01, "rmse": 11.0}}


def _variant(mae: float, naive: float = 8.01) -> dict:
    return {"single_stage": {"mae": mae, "rmse": 10.4},
            "naive_baseline": {"mae": naive, "rmse": 11.0}}


def test_check_passes_on_identical_metrics():
    assert guardrail.check(BASE, json.loads(json.dumps(BASE))) == []


def test_check_passes_on_improvement():
    assert guardrail.check(BASE, _variant(7.50)) == []


def test_check_passes_within_tolerance():
    # +0.4% is inside the 0.5% default tolerance.
    assert guardrail.check(BASE, _variant(7.82 * 1.004)) == []


def test_check_fails_beyond_tolerance():
    problems = guardrail.check(BASE, _variant(7.82 * 1.02))
    assert len(problems) == 1
    assert "regressed" in problems[0]
    assert "+2.00%" in problems[0]
    assert "tolerance 0.5%" in problems[0]


def test_check_fails_when_naive_baseline_is_not_beaten():
    problems = guardrail.check(BASE, _variant(8.50))
    assert any("naive baseline" in p for p in problems)


def test_check_fails_on_missing_metrics():
    assert guardrail.check({}, BASE) != []
    assert guardrail.check(BASE, {}) != []


def test_main_exit_codes(tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    fresh = tmp_path / "fresh.json"
    baseline.write_text(json.dumps(BASE), encoding="utf-8")

    fresh.write_text(json.dumps(_variant(7.50)), encoding="utf-8")
    assert guardrail.main(["guardrail.py", str(baseline),
                           str(fresh)]) == 0
    fresh.write_text(json.dumps(_variant(9.00)), encoding="utf-8")
    assert guardrail.main(["guardrail.py", str(baseline),
                           str(fresh)]) == 1
    out = capsys.readouterr().out
    assert "guardrail pass" in out and "GUARDRAIL FAIL" in out
    assert guardrail.main(["guardrail.py"]) == 2
