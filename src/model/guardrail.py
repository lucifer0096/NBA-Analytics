"""Retrain guardrail: compare a freshly trained metrics.json against the
committed baseline and refuse a validation-MAE regression.

Wired into .github/workflows/retrain.yml between train.py and the commit
step: a monthly retrain that makes validation WORSE (or stops beating its
naive baseline) fails the run, so the committed model/metrics are only
overwritten when the fresh numbers pass. The comparison logic lives here
rather than inline in the workflow so it is unit-testable offline.

Usage:
    python src/model/guardrail.py BASELINE_METRICS FRESH_METRICS [TOLERANCE]
exit 0 = pass, 1 = regression, 2 = bad usage.
"""

import json
import os
import sys

DEFAULT_TOLERANCE = 0.005  # 0.5% relative validation-MAE headroom


def check(old: dict, new: dict, tolerance: float = DEFAULT_TOLERANCE) -> list:
    """Human-readable failures; an empty list means the retrain passes.

    Rules: single_stage.mae must exist on both sides, may not regress beyond
    `tolerance` relative to the committed baseline, and the single-stage
    model must still beat its naive baseline -- a model that can't beat the
    player's own rolling-5 average is not worth committing at all."""
    problems = []
    old_mae = ((old or {}).get("single_stage") or {}).get("mae")
    new_mae = ((new or {}).get("single_stage") or {}).get("mae")
    if old_mae is None or new_mae is None:
        missing = ("committed baseline" if old_mae is None else "fresh metrics")
        problems.append(f"single_stage.mae missing from {missing}")
        return problems
    if new_mae > old_mae * (1.0 + tolerance):
        problems.append(
            f"single-stage validation MAE regressed {old_mae:.4f} -> "
            f"{new_mae:.4f} ({(new_mae / old_mae - 1) * 100:+.2f}%, "
            f"tolerance {tolerance * 100:.1f}%)")
    new_naive = ((new or {}).get("naive_baseline") or {}).get("mae")
    if new_naive is not None and new_mae >= new_naive:
        problems.append(
            f"single-stage MAE {new_mae:.4f} no longer beats the naive "
            f"baseline {new_naive:.4f}")
    return problems


def _append_summary(text: str) -> None:
    """Echo the verdict into the workflow's step summary when running in CI
    (no-op locally)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"### Model retrain guardrail\n\n{text}\n")


def main(argv: list) -> int:
    if len(argv) not in (3, 4):
        print("usage: guardrail.py BASELINE_METRICS FRESH_METRICS "
              "[TOLERANCE]", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        old = json.load(f)
    with open(argv[2], encoding="utf-8") as f:
        new = json.load(f)
    tolerance = float(argv[3]) if len(argv) == 4 else DEFAULT_TOLERANCE
    problems = check(old, new, tolerance)
    if problems:
        for problem in problems:
            print(f"GUARDRAIL FAIL: {problem}")
        _append_summary("**FAIL**\n\n" + "\n".join(f"- {p}"
                                                   for p in problems))
        return 1
    old_mae = old["single_stage"]["mae"]
    new_mae = new["single_stage"]["mae"]
    print(f"guardrail pass: single-stage validation MAE "
          f"{old_mae:.4f} -> {new_mae:.4f} ({new_mae - old_mae:+.4f})")
    _append_summary(f"**PASS** -- single-stage validation MAE "
                    f"{old_mae:.4f} -> {new_mae:.4f} "
                    f"({new_mae - old_mae:+.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
