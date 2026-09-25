"""Award-race + stat-leader computation from this repo's own collected data.

Pure stdlib (csv + json, no pandas) so the daily collector workflow stays
light. Reads data/raw/{season}/games/*.json (one parsed box score per final
game) and data/raw/{season}/schedule.csv (final scores -> team records).

HONEST FRAMING: these are homegrown, transparently-weighted metrics (each
formula is a one-liner documented on its function and repeated verbatim in
the dashboard caption) -- NOT official NBA voting, awards, or any vendor's
proprietary rating. The point is that a reader can check the math.

Races:
    MVP      production impact x team-success multiplier
    DPOY     stocks (STL+BLK) per game x team defensive multiplier
    6th Man  bench scoring/playmaking (players who mostly did NOT start)
    MIP      year-over-year impact improvement (needs the prior season)

Plus per-game stat leaders (PTS/REB/AST/STL/BLK/3PM) behind a games-played
qualifier, the same way rate titles work, with shooting splits (FG/3P/FT
counts + FG%/3P%/eFG%/TS%) attached to every row.

Cross-season sections (built from ALL collected seasons at once):
    all-time  career totals over the merged pool: full-career ESPN lines for
              every history/award/window player history.py fetches (see its
              module docstring), counting-stat boards + efficiency boards
              behind attempt floors -- i.e. NBA history, not just the window;
              with no history input it degrades to window-only (legacy shape)
    GOAT      a transparent composite of career production (40%), official
              NBA honours (35%, ESPN's 20 award types -- GOAT_HONOURS_WEIGHTS
              prints every per-win weight) and peak season (25%); computed
              from the same merged pool, with impossible-zero totals or an
              untrusted peak dropped per player and the remaining weights
              rescaled (the row says so in data_gaps). Homegrown, explicitly
              NOT an official NBA ranking.
    profile   build_career's `history` input also carries per-season logs and
              honours that refresh_dashboard_fallbacks.py publishes as
              data/dashboard_players.json (Player Profile page).

Season convention follows espn_api: a season label's END year is the calendar
year it finishes in (2025-26 games live under data/raw/2025-26/).
"""

import csv
import glob
import json
import os
import re
from collections import Counter

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw")
SEASON_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

# Impact weights, applied identically in MVP and MIP so the two races are
# comparable: one point ~ one point scored, a rebound worth 3/4 of a bucket,
# an assist worth a full one (roughly the traditional points-added intuition).
IMPACT_RPG_WEIGHT = 0.75
IMPACT_APG_WEIGHT = 1.00

# A rotation player is a "bench" player for the 6th-Man race when he started
# at most this fraction of the games he played in.
SIXTH_MAN_MAX_START_PCT = 0.40

# Team-success / team-defense multipliers are bounded so a great player on a
# mediocre team is discounted (0.6x) but never erased, while a great player on
# the league's best team tops out at 1.0x.
MULTIPLIER_FLOOR = 0.60

# Season stat-leader boards: counting rates only. Efficiency boards live in
# ALLTIME_CATEGORIES -- a percentage crown needs an attempts floor, which only
# makes sense over a whole career, not a partial season.
STAT_CATEGORIES = ("pts", "reb", "ast", "stl", "blk", "fg3m")
EFFICIENCY_CATEGORIES = ("fgp", "fg3p", "ftp", "efg", "ts")
ALLTIME_CATEGORIES = STAT_CATEGORIES + EFFICIENCY_CATEGORIES
LEADERS_PER_CATEGORY = 25
RACE_SIZE = 10

# All-time boards: career totals over the collected window. Qualified at a
# half-season of career games; percentage boards additionally demand a per-game
# attempts floor so a 1-1 shooter can't top FG% (see ALLTIME_ATTEMPT_FLOORS).
ALLTIME_PER_CATEGORY = 25
ALLTIME_MIN_GP = 41
ALLTIME_ATTEMPT_FLOORS = {"fgp": ("fga", 5.0), "fg3p": ("fg3a", 2.0),
                          "ftp": ("fta", 1.0), "efg": ("fga", 5.0),
                          "ts": ("fga", 5.0)}

# GOAT ladder: three components, each normalized 0-100 against the best
# qualified player in the merged pool, then mixed with these weights (they
# MUST match GOAT_FORMULA's text -- test_goat_* asserts the pair stays in
# sync). Production spans careers over ALL of NBA history when history.py's
# lines are merged in; honours are the OFFICIAL NBA awards ESPN carries.
GOAT_SIZE = 25
GOAT_MIN_CAREER_GP = 82
GOAT_WEIGHTS = {"production": 0.40, "honours": 0.35, "peak": 0.25}
GOAT_PRODUCTION_WEIGHTS = {"pts": 0.40, "reb": 0.15, "ast": 0.15,
                           "stl": 0.10, "blk": 0.10, "fg3m": 0.10}
GOAT_PROD_LABELS = {"pts": "PTS", "reb": "REB", "ast": "AST",
                    "stl": "STL", "blk": "BLK", "fg3m": "3PM"}
# Official NBA honours -> points per win (keys are ESPN's EXACT award names;
# verified live Sep 2026 against the 20 non-empty award types -- ids 34/37
# are empty. All-Star game SELECTIONS and team championships are not in
# ESPN's awards API at all, so they can't be scored here: championships are
# counted separately from champion-team rows and shown next to the ladder,
# never inside the score).
GOAT_HONOURS_WEIGHTS = {
    "MVP": 6.0,
    "Finals MVP": 5.0,
    "Defensive Player of the Year": 4.5,
    "All-NBA 1st Team": 3.0,
    "All-Defensive 1st Team": 2.5,
    "All-NBA 2nd Team": 2.0,
    "NBA Western Conference Finals MVP": 2.0,
    "NBA Eastern Conference Finals MVP": 2.0,
    "All-Defensive 2nd Team": 1.5,
    "Rookie of the Year": 1.5,
    "Sixth Man of the Year": 1.5,
    "Most Improved Player": 1.5,
    "All-Star MVP": 1.5,
    "All-NBA 3rd Team": 1.0,
    "Clutch Player of the Year": 1.0,
    "NBA Cup MVP": 1.0,
    "NBA Cup All-Tournament Team": 0.5,
    "All-Rookie 1st Team": 0.5,
    "All-Rookie 2nd Team": 0.25,
    "Twyman-Stokes Teammate of the Year Award": 0.25,
}
# Short display names for the caption (the weights above are what the math
# runs with; both dicts are asserted against GOAT_FORMULA in the tests).
GOAT_HONOUR_LABELS = {
    "MVP": "MVP",
    "Finals MVP": "Finals MVP",
    "Defensive Player of the Year": "DPOY",
    "All-NBA 1st Team": "All-NBA 1st",
    "All-Defensive 1st Team": "All-Def 1st",
    "All-NBA 2nd Team": "All-NBA 2nd",
    "NBA Western Conference Finals MVP": "Conf MVP (W)",
    "NBA Eastern Conference Finals MVP": "Conf MVP (E)",
    "All-Defensive 2nd Team": "All-Def 2nd",
    "Rookie of the Year": "ROY",
    "Sixth Man of the Year": "6MOY",
    "Most Improved Player": "MIP",
    "All-Star MVP": "All-Star MVP",
    "All-NBA 3rd Team": "All-NBA 3rd",
    "Clutch Player of the Year": "Clutch POY",
    "NBA Cup MVP": "Cup MVP",
    "NBA Cup All-Tournament Team": "Cup Tourney",
    "All-Rookie 1st Team": "All-Rookie 1st",
    "All-Rookie 2nd Team": "All-Rookie 2nd",
    "Twyman-Stokes Teammate of the Year Award": "Twyman-Stokes",
}

# Display metadata shared with the dashboard (shared.py imports these so the
# formula text shown in a caption is the SAME string that defines the math --
# no drift between what's computed and what's claimed on screen).
STAT_LABELS = {"pts": "Points", "reb": "Rebounds", "ast": "Assists",
               "stl": "Steals", "blk": "Blocks", "fg3m": "Three-pointers",
               "fgp": "Field-goal %", "fg3p": "Three-point %",
               "ftp": "Free-throw %", "efg": "Effective FG%",
               "ts": "True shooting%"}
STAT_ABBR = {"pts": "PPG", "reb": "RPG", "ast": "APG", "stl": "SPG",
             "blk": "BPG", "fg3m": "3PM", "fgp": "FG%", "fg3p": "3P%",
             "ftp": "FT%", "efg": "eFG%", "ts": "TS%"}
RACE_LABELS = {"mvp": "MVP race", "dpoy": "DPOY race",
               "sixth_man": "6th Man race", "mip": "MIP race"}
RACE_EMOJI = {"mvp": "🏆", "dpoy": "🛡️", "sixth_man": "🪑", "mip": "📈"}
RACE_FORMULAS = {
    "mvp": ("Impact per game (PTS + 0.75·REB + 1.0·AST) scaled by the "
            "team's win percentage, 0.6× floor to 1.0× at a perfect record."),
    "dpoy": ("Stocks per game (STL + BLK) scaled by the team's rank in "
             "points allowed, 0.6× floor to 1.0× for the best defense."),
    "sixth_man": ("Bench production per game (PTS + 0.5·AST + 0.25·REB), "
                  "players who started at most 40% of their games."),
    "mip": ("Year-over-year change in per-game impact vs the previous "
            "season; both seasons must clear the games-played qualifier."),
}

# The GOAT formula is assembled from the very constants the math runs with,
# so the caption can never drift from the computation.
GOAT_FORMULA = (
    "GOAT score = "
    f"{GOAT_WEIGHTS['production']:.0%} career production ("
    + ", ".join(f"{GOAT_PROD_LABELS[s]} {w:.0%}"
                for s, w in GOAT_PRODUCTION_WEIGHTS.items())
    + " of the component, each career total vs the window's best) + "
    f"{GOAT_WEIGHTS['awards']:.0%} award-race resume (top-"
    f"{RACE_SIZE} finishes are worth {RACE_SIZE + 1}-rank points, race "
    + ", ".join(f"{RACE_LABELS[k].split()[0]} ×{w:.1f}"
                for k, w in GOAT_AWARD_WEIGHTS.items())
    + f") + {GOAT_WEIGHTS['peak']:.0%} peak (best season's per-game impact), "
    f"each component 0-100 vs the best player in the window; requires "
    f"≥{GOAT_MIN_CAREER_GP} career games in the collected seasons."
)


def previous_season(season: str) -> str:
    """'2025-26' -> '2024-25' (MIP needs the year before)."""
    start = int(season[:4])
    prev_start = start - 1
    return f"{prev_start}-{(prev_start + 1) % 100:02d}"


def min_games_for(max_team_games: int) -> int:
    """Rate-title qualifier: half the games any team played (so the bar grows
    naturally as the season progresses), floored at 8 so early-season runs
    still produce a race instead of an empty tab."""
    return max(8, int(0.5 * max_team_games))


def aggregate_players(season: str, raw_dir: str = None) -> list:
    """Per-player season aggregates from every stored box score.

    Games where the player was flagged did_not_play don't count toward games
    or starts (same semantics as refresh_leaderboards' `games`). A traded
    player's team is the one he appeared for in the most games; ties go to
    whichever comes first in sorted game-id order (deterministic).
    """
    raw = raw_dir or RAW_DIR
    files = sorted(glob.glob(os.path.join(raw, season, "games", "*.json")))
    if not files:
        return []

    players: dict = {}
    for path in files:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        for row in payload.get("players") or []:
            if row.get("did_not_play"):
                continue
            entry = players.get(row["player_id"])
            if entry is None:
                entry = players[row["player_id"]] = {
                    "player_id": row["player_id"],
                    "player_name": row.get("player_name"),
                    "team_abbrev": None,
                    "_team_counts": Counter(),
                    "gp": 0, "starts": 0, "minutes": 0,
                }
                for stat in CAREER_SUM_STATS:
                    entry[stat] = 0
            entry["gp"] += 1
            if row.get("starter"):
                entry["starts"] += 1
            entry["minutes"] += row.get("min") or 0
            for stat in CAREER_SUM_STATS:
                entry[stat] += row.get(stat) or 0
            if row.get("team_abbrev"):
                entry["_team_counts"][row["team_abbrev"]] += 1

    out = []
    for entry in players.values():
        counts = entry.pop("_team_counts")
        entry["team_abbrev"] = counts.most_common(1)[0][0] if counts else None
        out.append(entry)
    return out


def team_records(season: str, raw_dir: str = None) -> dict:
    """Team W-L and points allowed per game from the season's final games.

    Returns {abbrev: {"wins", "losses", "win_pct", "opp_pg", "games"}} and
    also lets callers derive max_team_games for the qualifier. Non-final or
    scoreless schedule rows (the normal pre-tip-off state) are skipped.
    """
    raw = raw_dir or RAW_DIR
    path = os.path.join(raw, season, "schedule.csv")
    stats: dict = {}
    if not os.path.exists(path):
        return stats

    def side(abbrev):
        return stats.setdefault(abbrev, {
            "wins": 0, "losses": 0, "points_for": 0, "points_allowed": 0,
            "games": 0,
        })

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "STATUS_FINAL":
                continue
            try:
                # schedule.csv scores can arrive as '125' or '125.0'
                # (column dtype rounding when the file was written).
                home_score = int(float(row["home_score"]))
                away_score = int(float(row["away_score"]))
            except (KeyError, TypeError, ValueError):
                continue
            home, away = side(row["home_abbrev"]), side(row["away_abbrev"])
            for team, scored, allowed in ((home, home_score, away_score),
                                           (away, away_score, home_score)):
                team["games"] += 1
                team["points_for"] += scored
                team["points_allowed"] += allowed
            if home_score > away_score:
                home["wins"] += 1
                away["losses"] += 1
            else:
                away["wins"] += 1
                home["losses"] += 1

    return {
        abbrev: {
            "wins": t["wins"],
            "losses": t["losses"],
            "win_pct": round(t["wins"] / t["games"], 3) if t["games"] else 0.0,
            "opp_pg": round(t["points_allowed"] / t["games"], 1) if t["games"] else None,
            "games": t["games"],
        }
        for abbrev, t in stats.items()
    }


def max_games_played(records: dict) -> int:
    return max((r["games"] for r in records.values()), default=0)


CAREER_SUM_STATS = ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "to",
                    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta")


def _per_game(entry: dict, stat: str) -> float:
    return round((entry.get(stat) or 0) / entry["gp"], 1) if entry["gp"] else 0.0


def _pct(made, attempts):
    """Shooting percentage on a 0-100 scale, 1dp; None when there was no
    attempt at all (never an invented 0.0% or 100.0%)."""
    if not attempts:
        return None
    return round(100.0 * (made or 0) / attempts, 1)


def efficiency(entry: dict) -> dict:
    """FG% / 3P% / FT% / eFG% / TS% from season- or career-total box-score
    counts: eFG% = (FGM + 0.5·3PM) / FGA, TS% = PTS / (2·(FGA + 0.44·FTA))."""
    fga = entry.get("fga") or 0
    return {
        "fgp": _pct(entry.get("fgm"), fga),
        "fg3p": _pct(entry.get("fg3m"), entry.get("fg3a")),
        "ftp": _pct(entry.get("ftm"), entry.get("fta")),
        "efg": _pct((entry.get("fgm") or 0)
                    + 0.5 * (entry.get("fg3m") or 0), fga),
        "ts": _pct(entry.get("pts"), 2 * (fga + 0.44 * (entry.get("fta") or 0))),
    }


def _impact(entry: dict) -> float:
    return (entry["pts"]
            + IMPACT_RPG_WEIGHT * entry["reb"]
            + IMPACT_APG_WEIGHT * entry["ast"]) / entry["gp"] if entry["gp"] else 0.0


def mvp_rows(players: list, records: dict, min_gp: int) -> list:
    """MVP ladder: (pts + 0.75*reb + 1.0*ast)/gp x (0.6 + 0.4 * team win%).

    A player's team win percentage scales his production between 0.6x (winless)
    and 1.0x (.500+ ... scaling tops out at 1.0 for a perfect team). Neutral
    0.5 win% is used when the player's team can't be matched to a record
    (traded to a team with no final games yet, abbreviation quirks)."""
    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        record = records.get(p["team_abbrev"] or "")
        win_pct = record["win_pct"] if record else 0.5
        score = _impact(p) * (MULTIPLIER_FLOOR + (1 - MULTIPLIER_FLOOR) * win_pct)
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "wins": record["wins"] if record else None,
            "losses": record["losses"] if record else None,
            "win_pct": round(win_pct, 3),
            "fgp": _pct(p.get("fgm"), p.get("fga")),
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["ppg"], r["player_name"] or ""))
    return _ranked(rows)


def dpoy_rows(players: list, records: dict, min_gp: int) -> list:
    """DPOY ladder: (stl + blk)/gp x (0.6 + 0.4 * team defense percentile).

    The team factor is each team's rank in points allowed per game mapped to
    (n - rank0) / n: best defense -> 1.0, worst -> ~0.03. Unknown teams get
    the neutral 0.5. Stock totals are the per-game headline; the team factor
    prevents a shot-happy roamer on a sieve defense from leading outright."""
    ranked_teams = sorted(
        (a for a, r in records.items() if r["opp_pg"] is not None),
        key=lambda a: records[a]["opp_pg"],
    )
    n_teams = len(ranked_teams)
    defense_pct = {
        a: (n_teams - i) / n_teams for i, a in enumerate(ranked_teams)
    }

    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        stocks = (p["stl"] + p["blk"]) / p["gp"] if p["gp"] else 0.0
        team_pct = defense_pct.get(p["team_abbrev"] or "", 0.5)
        score = stocks * (MULTIPLIER_FLOOR + (1 - MULTIPLIER_FLOOR) * team_pct)
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "spg": round(p["stl"] / p["gp"], 1) if p["gp"] else 0.0,
            "bpg": round(p["blk"] / p["gp"], 1) if p["gp"] else 0.0,
            "stocks_pg": round(stocks, 1),
            "opp_pg": (records.get(p["team_abbrev"] or "") or {}).get("opp_pg"),
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["stocks_pg"], r["player_name"] or ""))
    return _ranked(rows)


def sixth_man_rows(players: list, records: dict, min_gp: int) -> list:
    """6th-Man ladder: ppg + 0.5*apg + 0.25*rpg, bench players only.

    Eligible when the player started at most 40% of the games he played in --
    the classic "comes off the bench but carries the second unit" profile.
    Pure production, no team multiplier: bench minutes don't inherit a
    starter's team context, and the roster's bench quality IS the point."""
    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        start_pct = p["starts"] / p["gp"] if p["gp"] else 1.0
        if start_pct > SIXTH_MAN_MAX_START_PCT:
            continue
        score = (p["pts"]
                 + 0.5 * p["ast"]
                 + 0.25 * p["reb"]) / p["gp"] if p["gp"] else 0.0
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "starts": p["starts"],
            "start_pct": round(start_pct, 3),
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "score": round(score, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["ppg"], r["player_name"] or ""))
    return _ranked(rows)


def mip_rows(players: list, prev_players: list, min_gp: int,
             min_gp_prev: int) -> list:
    """MIP ladder: this season's impact/pg MINUS last season's impact/pg.

    Uses the same impact formula as MVP, so the delta reads as "+N points of
    production per game vs last year". Both seasons must clear their own
    games-played qualifier (a 12-game flash in either year is noise). Empty
    list when the prior season has no collected games -- a normal state, the
    dashboard says so instead of inventing a winner."""
    if not prev_players:
        return []
    prev_by_id = {p["player_id"]: p for p in prev_players if p["gp"] >= min_gp_prev}
    rows = []
    for p in players:
        if p["gp"] < min_gp:
            continue
        prev = prev_by_id.get(p["player_id"])
        if prev is None:
            continue
        delta = _impact(p) - _impact(prev)
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "gp": p["gp"],
            "gp_prev": prev["gp"],
            "ppg": _per_game(p, "pts"),
            "rpg": _per_game(p, "reb"),
            "apg": _per_game(p, "ast"),
            "ppg_prev": _per_game(prev, "pts"),
            "rpg_prev": _per_game(prev, "reb"),
            "apg_prev": _per_game(prev, "ast"),
            "delta_ppg": round(_per_game(p, "pts") - _per_game(prev, "pts"), 1),
            "score": round(delta, 2),
        })
    rows.sort(key=lambda r: (-r["score"], -r["delta_ppg"], r["player_name"] or ""))
    return _ranked(rows)


def leader_rows(players: list, min_gp: int) -> dict:
    """Per-game stat leaders behind the same games-played qualifier.

    Returns {category: [top-N rows]} -- per-game rate (the fair cross-team,
    cross-pace comparison) with the season total alongside for context, plus
    the shooting splits and efficiencies behind FG%/3P%/eFG%/TS% so the
    dashboard can show 3PM/3PA/FGM/FGA without a second data source."""
    out = {}
    for stat in STAT_CATEGORIES:
        rows = []
        for p in players:
            if p["gp"] < min_gp:
                continue
            row = {
                "player_id": p["player_id"],
                "player_name": p["player_name"],
                "team_abbrev": p["team_abbrev"],
                "gp": p["gp"],
                "total": p.get(stat) or 0,
                "per_game": _per_game(p, stat),
                # Full per-game line so the dashboard's hover tooltip can show a
                # player's whole stat line, not just this category.
                "mpg": _per_game(p, "minutes"),
                "ppg": _per_game(p, "pts"),
                "rpg": _per_game(p, "reb"),
                "apg": _per_game(p, "ast"),
                "spg": _per_game(p, "stl"),
                "bpg": _per_game(p, "blk"),
                # Shooting splits (counts + rates) for the tooltip/meta lines.
                "fgm": p.get("fgm") or 0,
                "fga": p.get("fga") or 0,
                "fg3m": p.get("fg3m") or 0,
                "fg3a": p.get("fg3a") or 0,
            }
            row.update(efficiency(p))
            rows.append(row)
        rows.sort(key=lambda r: (-r["per_game"], -r["total"], r["player_name"] or ""))
        out[stat] = _ranked(rows[:LEADERS_PER_CATEGORY])
    return out


def _ranked(rows: list, size: int = RACE_SIZE) -> list:
    for i, row in enumerate(rows[:size], start=1):
        row["rank"] = i
    return rows[:size]


def build_payload(season: str, raw_dir: str = None) -> dict:
    """Everything data/dashboard_awards.json carries, or {} when the season
    has no collected games (caller keeps the previous file untouched)."""
    raw = raw_dir or RAW_DIR
    players = aggregate_players(season, raw_dir=raw)
    if not players:
        return {}

    records = team_records(season, raw_dir=raw)
    min_gp = min_games_for(max_games_played(records))

    prev_label = previous_season(season)
    prev_players = aggregate_players(prev_label, raw_dir=raw) if SEASON_DIR_RE.fullmatch(prev_label) else []
    prev_records = team_records(prev_label, raw_dir=raw) if prev_players else {}
    min_gp_prev = min_games_for(max_games_played(prev_records))

    return {
        "season": season,
        "prev_season": prev_label if prev_players else None,
        "min_games": min_gp,
        "races": {
            "mvp": mvp_rows(players, records, min_gp),
            "dpoy": dpoy_rows(players, records, min_gp),
            "sixth_man": sixth_man_rows(players, records, min_gp),
            "mip": mip_rows(players, prev_players, min_gp, min_gp_prev),
        },
        "leaders": leader_rows(players, min_gp),
    }


def collected_seasons(raw_dir: str = None) -> list:
    """Season labels with at least one collected box score, oldest first
    (career boards iterate ascending so a player's latest team wins)."""
    raw = raw_dir or RAW_DIR
    if not os.path.isdir(raw):
        return []
    return sorted(
        name for name in os.listdir(raw)
        if SEASON_DIR_RE.fullmatch(name)
        and glob.glob(os.path.join(raw, name, "games", "*.json"))
    )


def alltime_players(raw_dir: str = None) -> list:
    """Career aggregates merged across every collected season.

    Same row semantics as aggregate_players (DNP rows skipped), plus `seasons`
    (how many collected seasons he appeared in) and `peak_impact` (his best
    single-season per-game impact, feeding the GOAT ladder's peak component).
    Seasons are merged oldest-first, so team_abbrev is the most recent team."""
    merged: dict = {}
    for season in collected_seasons(raw_dir):
        for p in aggregate_players(season, raw_dir=raw_dir):
            entry = merged.get(p["player_id"])
            if entry is None:
                entry = merged[p["player_id"]] = {
                    "player_id": p["player_id"],
                    "player_name": p["player_name"],
                    "team_abbrev": None,
                    "seasons": 0, "gp": 0, "starts": 0, "minutes": 0,
                }
                for stat in CAREER_SUM_STATS:
                    entry[stat] = 0
            entry["seasons"] += 1
            entry["gp"] += p["gp"]
            entry["starts"] += p.get("starts") or 0
            entry["minutes"] += p.get("minutes") or 0
            for stat in CAREER_SUM_STATS:
                entry[stat] += p.get(stat) or 0
            entry["team_abbrev"] = p["team_abbrev"] or entry["team_abbrev"]
            entry["peak_impact"] = max(entry.get("peak_impact") or 0.0,
                                       _impact(p))
    return list(merged.values())


def _career_row(p: dict) -> dict:
    """One all-time board row: every counting total, every shooting split,
    every efficiency, with per-game context for the dashboard."""
    row = {
        "player_id": p["player_id"],
        "player_name": p["player_name"],
        "team_abbrev": p["team_abbrev"],
        "seasons": p.get("seasons", 0),
        "gp": p["gp"],
        "minutes": p.get("minutes") or 0,
        "ppg": _per_game(p, "pts"),
        "rpg": _per_game(p, "reb"),
        "apg": _per_game(p, "ast"),
    }
    for stat in CAREER_SUM_STATS:
        row[stat] = p.get(stat) or 0
    row.update(efficiency(p))
    return row


def _meets_attempt_floor(p: dict, stat: str) -> bool:
    """Efficiency-board qualifier: enough attempts per game that the
    percentage means something (e.g. ≥5 FGA/g for FG%)."""
    field, floor = ALLTIME_ATTEMPT_FLOORS[stat]
    if not p["gp"]:
        return False
    return (p.get(field) or 0) / p["gp"] >= floor


def alltime_rows(players: list) -> dict:
    """{category: [top-N career rows]} over the collected window.

    Counting boards rank by career TOTAL (the classic all-time view) with the
    per-game rate as tie-breaker; efficiency boards rank by percentage behind
    ALLTIME_MIN_GP + the per-game attempts floors. Every row carries the full
    stat line, so the dashboard's table columns don't depend on which board
    is selected."""
    qualified = [p for p in players if p["gp"] >= ALLTIME_MIN_GP]
    out = {}
    for stat in STAT_CATEGORIES:
        rows = [_career_row(p) for p in qualified]
        rows.sort(key=lambda r: (-r[stat], -r["ppg"], r["player_name"] or ""))
        out[stat] = _ranked(rows[:ALLTIME_PER_CATEGORY], ALLTIME_PER_CATEGORY)
    for stat in EFFICIENCY_CATEGORIES:
        # The raw player dict has no derived pct keys -- they only exist on
        # the built career row, so attempt-floor first, then drop None pcts
        # (no attempts left after qualification is still possible in theory).
        rows = []
        for p in qualified:
            if not _meets_attempt_floor(p, stat):
                continue
            row = _career_row(p)
            if row.get(stat) is not None:
                rows.append(row)
        rows.sort(key=lambda r: (-r[stat], -(r.get(
            ALLTIME_ATTEMPT_FLOORS[stat][0]) or 0), r["player_name"] or ""))
        out[stat] = _ranked(rows[:ALLTIME_PER_CATEGORY], ALLTIME_PER_CATEGORY)
    return out


def goat_rows(players: list, season_payloads: list) -> list:
    """The GOAT ladder: career production + award-race resume + peak season.

    Production mixes career totals against the window's best (weights in
    GOAT_PRODUCTION_WEIGHTS); the resume converts every top-{RACE_SIZE}
    finish in every collected season's races into (RACE_SIZE+1-rank) points
    weighted by race prestige (GOAT_AWARD_WEIGHTS), counting rank-1 finishes
    as titles; peak is his best season's per-game impact. Each component is
    normalized 0-100 against the best player in the window, then mixed with
    GOAT_WEIGHTS -- the exact text shown on screen is GOAT_FORMULA.

    Qualified at ≥GOAT_MIN_CAREER_GP career games so a ten-game hot streak
    can't be crowned the greatest of the era."""
    award_points: Counter = Counter()
    titles: dict = {}
    for payload in season_payloads:
        for race, race_rows in (payload.get("races") or {}).items():
            weight = GOAT_AWARD_WEIGHTS.get(race)
            if weight is None:
                continue
            for row in race_rows:
                pid = row.get("player_id")
                rank_ = row.get("rank")
                if pid is None or not rank_:
                    continue
                award_points[pid] += (RACE_SIZE + 1 - int(rank_)) * weight
                if int(rank_) == 1:
                    titles.setdefault(pid, Counter())[race] += 1

    qualified = [p for p in players if p["gp"] >= GOAT_MIN_CAREER_GP]
    if not qualified:
        return []
    max_totals = {
        stat: max((p.get(stat) or 0 for p in qualified), default=0)
        for stat in GOAT_PRODUCTION_WEIGHTS
    }
    max_awards = max(award_points.values(), default=0) or 1
    max_peak = max((p.get("peak_impact") or 0 for p in qualified), default=0) or 1

    rows = []
    for p in qualified:
        production = 100.0 * sum(
            w * ((p.get(stat) or 0) / max_totals[stat])
            for stat, w in GOAT_PRODUCTION_WEIGHTS.items()
            if max_totals[stat]
        )
        awards_score = 100.0 * award_points[p["player_id"]] / max_awards
        peak_score = 100.0 * (p.get("peak_impact") or 0) / max_peak
        score = (GOAT_WEIGHTS["production"] * production
                 + GOAT_WEIGHTS["awards"] * awards_score
                 + GOAT_WEIGHTS["peak"] * peak_score)
        player_titles = titles.get(p["player_id"]) or {}
        rows.append({
            "player_id": p["player_id"],
            "player_name": p["player_name"],
            "team_abbrev": p["team_abbrev"],
            "seasons": p.get("seasons", 0),
            "gp": p["gp"],
            "pts": p.get("pts") or 0,
            "reb": p.get("reb") or 0,
            "ast": p.get("ast") or 0,
            "ppg": _per_game(p, "pts"),
            "award_points": round(award_points[p["player_id"]], 1),
            "titles": {race: int(player_titles.get(race, 0))
                       for race in GOAT_AWARD_WEIGHTS},
            "titles_total": int(sum(player_titles.values())),
            "production": round(production, 1),
            "awards_score": round(awards_score, 1),
            "peak_score": round(peak_score, 1),
            "peak_impact": round(p.get("peak_impact") or 0.0, 1),
            "score": round(score, 1),
        })
    rows.sort(key=lambda r: (-r["score"], -r["production"],
                             r["player_name"] or ""))
    return _ranked(rows, GOAT_SIZE)


def build_career(season_payloads: list, raw_dir: str = None) -> dict:
    """The cross-season half of data/dashboard_awards.json: window metadata,
    the all-time boards, and the GOAT ladder. `season_payloads` are the
    build_payload() results for every collected season (their races feed the
    GOAT resume). Empty dict when no season has collected games."""
    players = alltime_players(raw_dir=raw_dir)
    labels = sorted(p["season"] for p in season_payloads if p.get("season"))
    if not labels or not players:
        return {}
    return {
        "window": {"first": labels[0], "last": labels[-1],
                   "seasons": len(labels), "players": len(players)},
        "alltime": {"leaders": alltime_rows(players)},
        "goat": {"rows": goat_rows(players, season_payloads),
                 "min_career_gp": GOAT_MIN_CAREER_GP,
                 "formula": GOAT_FORMULA},
    }


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target is None:
        candidates = sorted(
            (name for name in os.listdir(RAW_DIR)
             if SEASON_DIR_RE.fullmatch(name)
             and glob.glob(os.path.join(RAW_DIR, name, "games", "*.json"))),
            reverse=True,
        )
        if not candidates:
            raise SystemExit("no collected seasons under data/raw/")
        target = candidates[0]
    payload = build_payload(target)
    print(json.dumps(payload, indent=1)[:4000])
