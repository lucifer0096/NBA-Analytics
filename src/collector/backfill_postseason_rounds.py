"""One-off maintenance: annotate committed postseason schedules with the
round each game belongs to ('East 1st Round', 'NBA Play-In',
'EASTERN CONFERENCE 1st ROUND', 'NBA Finals').

The collector's team-schedule payloads carry ESPN's per-game round note
(parsing.round_label off comp.notes[0].headline) -- but only when newly
fetched: the committed data/postseason*/ trees predate the column. This
script backfills them, league by league:

    python src/collector/backfill_postseason_rounds.py --league nba
    python src/collector/refresh_dashboard_fallbacks.py --league nba

Step 1 writes the round into the raw postseason.csv files (gitignored
local state); step 2 rebuilds the committed JSONs from those CSVs through
the normal refresh path. Idempotent: seasons whose rows all carry a round
are skipped entirely (zero ESPN calls), a game ESPN gives no note keeps
no round -- the app groups those honestly under 'Round not recorded'
rather than guessing.

Two fetch sources, matching the collector's own split: the team-schedule
endpoint (seasontype=3) covers every playoff series, and -- because
play-in games answer under NEITHER seasontype (snapshot.py's gap probe
found the same) -- whatever rows are still round-less are looked up on
the scoreboard by their game day.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_api  # noqa: E402
import leagues  # noqa: E402
import parsing  # noqa: E402

GAP_BETWEEN_CALLS = 0.2  # polite pacing: ~350 calls for a full NBA pass


def _day_candidates(iso_date) -> list:
    """YYYYMMDD scoreboard buckets worth trying for a game stamped `iso_date`
    (UTC): ESPN buckets events by US date, which trails UTC -- so the UTC
    day itself and the day before it. Unparseable -> no candidates (the
    row stays un-annotated, honestly)."""
    try:
        day = datetime.fromisoformat(
            str(iso_date).replace("Z", "+00:00")).astimezone(
                timezone.utc).date()
    except (TypeError, ValueError):
        return []
    return [d.strftime("%Y%m%d") for d in (day - timedelta(days=1), day)]


def _round_of_event(event) -> str | None:
    """The round label of one scoreboard/team-schedule event, or None when
    ESPN serves it no note (the shared payload shape both endpoints
    carry)."""
    comps = event.get("competitions") or []
    if not comps:
        return None
    notes = comps[0].get("notes") or [{}]
    return parsing.round_label((notes[0] or {}).get("headline"))


def fill_rounds(rows: list, mapping: dict) -> int:
    """Write mapping[game_id] into every row still missing a round (rows
    already annotated are never overwritten -- a committed label wins
    over a re-fetch). Returns how many rows gained a round."""
    filled = 0
    for row in rows:
        if str(row.get("round") or "").strip():
            continue
        label = mapping.get(str(row.get("game_id")))
        if label:
            row["round"] = label
            filled += 1
    return filled


def annotate_season(season: str, league: str,
                    sleep: float = GAP_BETWEEN_CALLS) -> tuple | None:
    """(games, filled, unknown, calls) for one season's raw
    postseason.csv -- fetching every playoff team's schedule, then the
    scoreboard for whatever the schedules don't cover (play-in). None when
    the season has no raw file or no games."""
    csv_path = os.path.join(leagues.raw_dir(league), season,
                             "postseason.csv")
    if not os.path.isfile(csv_path):
        return None
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not rows:
        return None
    # Local normalization first: a pass made with the earlier hyphen-only
    # split kept en-dash tails ('West Semifinals – Game 1') -- re-derive
    # the round from the committed label itself: no network, no guessing.
    normalized = 0
    for row in rows:
        current = str(row.get("round") or "").strip()
        label = parsing.round_label(current) if current else None
        if current and label and label != current:
            row["round"] = label
            normalized += 1
    if all(str(r.get("round") or "").strip() for r in rows):
        if normalized:
            _write_csv(csv_path, fieldnames, rows)
        return (len(rows), normalized, 0, 0)  # annotated: zero calls

    team_ids = sorted(
        {int(r["home_id"]) for r in rows if str(r.get("home_id") or "").strip()}
        | {int(r["away_id"]) for r in rows if str(r.get("away_id") or "").strip()})
    mapping: dict = {}
    calls = 0
    for team_id in team_ids:
        calls += 1
        try:
            payload = espn_api.get_schedule(
                espn_api.season_param(season, league), team_id, league, 3)
        except Exception as exc:  # noqa: BLE001 -- one team's miss must not
            print(f"    WARNING [{season}]: team {team_id} failed "
                  f"({type(exc).__name__}: {exc}) -- retry the pass later")
            time.sleep(sleep)
            continue
        for event in payload.get("events") or []:
            label = _round_of_event(event)
            if label and str(event.get("id")) not in mapping:
                mapping[str(event.get("id"))] = label
        time.sleep(sleep)

    filled = fill_rounds(rows, mapping)
    # Play-in games: on neither seasontype -- scoreboard by game day.
    still = [r for r in rows if not str(r.get("round") or "").strip()]
    days = sorted({d for r in still for d in _day_candidates(r.get("date"))})
    for day in days:
        calls += 1
        try:
            payload = espn_api.get_scoreboard(day, league)
        except Exception as exc:  # noqa: BLE001
            print(f"    WARNING [{season}]: scoreboard {day} failed "
                  f"({type(exc).__name__}: {exc}) -- retried next pass")
            time.sleep(sleep)
            continue
        for event in payload.get("events") or []:
            label = _round_of_event(event)
            if label:
                mapping[str(event.get("id"))] = label
        time.sleep(sleep)
    filled += fill_rounds(rows, mapping)

    if filled or normalized:
        _write_csv(csv_path, fieldnames, rows)
    unknown = sum(1 for r in rows
                  if not str(r.get("round") or "").strip())
    return (len(rows), filled + normalized, unknown, calls)


def _write_csv(csv_path: str, fieldnames: list, rows: list) -> None:
    """Rewrite the season's raw CSV with its rows (round column joined
    the header the first time one was written)."""
    if "round" not in fieldnames:
        fieldnames.append("round")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="nba", choices=("nba", "wnba"))
    parser.add_argument("--sleep", type=float, default=GAP_BETWEEN_CALLS,
                        help="seconds between ESPN calls")
    args = parser.parse_args(argv)
    league = leagues.validate(args.league)
    raw_dir = leagues.raw_dir(league)
    seasons = sorted(
        name for name in os.listdir(raw_dir)
        if os.path.isfile(os.path.join(raw_dir, name, "postseason.csv")))
    if not seasons:
        print(f"[{league}] no postseason.csv anywhere under {raw_dir}")
        return
    totals = [0, 0, 0, 0]
    for season in seasons:
        result = annotate_season(season, league, sleep=args.sleep)
        if result is None:
            continue
        games, filled, unknown, calls = result
        totals = [a + b for a, b in zip(totals, result)]
        note = ("already annotated" if not calls
                else f"{filled} filled, {unknown} unknown, {calls} calls")
        print(f"  [{league}] {season}: {games} games -- {note}")
    print(f"[{league}] total: {totals[0]} games, {totals[1]} filled, "
          f"{totals[2]} unknown, {totals[3]} ESPN calls")
    print("next: python src/collector/refresh_dashboard_fallbacks.py "
          f"--league {league}")


if __name__ == "__main__":
    main()
