"""Pure parsing functions: ESPN payload dict -> flat rows.

Every function here is a deterministic transformation of an already-fetched
payload (no network), so the offline test suite can pin them against the real
recorded fixtures in fixtures/ -- including a game from 1995-96, deliberately
kept as proof the parser survives the oldest data the API returns.

Canonical row schemas (these are the contract load_historical.py reads):

game row (parse_schedule):
    game_id, date, season, home_id, home_abbrev, home_score,
    away_id, away_abbrev, away_score, status, neutral

player-game row (parse_summary):
    game_id, date, team_id, team_abbrev, opponent_id, opponent_abbrev,
    is_home, player_id, player_name, starter, did_not_play,
    min, pts, reb, oreb, dreb, ast, stl, blk, to, pf,
    fgm, fga, fg3m, fg3a, ftm, fta, plus_minus

team row (parse_teams):
    team_id, abbrev, display_name, location, name, slug

standing row (parse_standings):
    team_id, team, conference, wins, losses, win_percent,
    playoff_seed, games_behind, streak, avg_points_for, avg_points_against

roster row (parse_roster):
    player_id, player_name, position, team_id
"""

from datetime import datetime

# Box-score label (ESPN's own `labels` array) -> canonical column, plus the
# "made-attempted" labels that need splitting into two columns.
_STAT_COLUMNS = {
    "MIN": "min",
    "PTS": "pts",
    "REB": "reb",
    "OREB": "oreb",
    "DREB": "dreb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "TO": "to",
    "PF": "pf",
    "+/-": "plus_minus",
}
_SPLIT_COLUMNS = {"FG": ("fgm", "fga"), "3PT": ("fg3m", "fg3a"), "FT": ("ftm", "fta")}

# Standings stat names we keep (ESPN exposes many more; these are what the
# dashboard and the model's team-strength features actually use).
_STANDING_STATS = {
    "wins": ("wins", int),
    "losses": ("losses", int),
    "winPercent": ("win_percent", str),
    "playoffSeed": ("playoff_seed", int),
    "gamesBehind": ("games_behind", str),
    "streak": ("streak", str),
    "avgPointsFor": ("avg_points_for", float),
    "avgPointsAgainst": ("avg_points_against", float),
}


def season_label_from_date(date_str: str) -> str:
    """Derive the NBA season label from a game date.

    Games Oct-Dec belong to the season starting that year; Jan-Jun (regular
    season end + playoffs) to the one that started the previous year.
    Deliberately derived from the game's own date rather than trusting a
    season field, so a mislabelled API field can't silently shift a whole
    block of rows into the wrong training season."""
    d = datetime.fromisoformat(str(date_str).replace("Z", "+00:00")).date()
    start = d.year if d.month >= 8 else d.year - 1
    # (Aug-Sep only ever appears as preseason -- still part of the season that
    # starts in October of that year; the NBA's regular-season calendar
    # guarantees no real game lands in Jul.)
    if d.month == 7:
        start = d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


def _to_int(value, default=None):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value, default=None):
    try:
        return float(str(value).replace("%", "").replace(".", "0.", 1)
                     if str(value).startswith(".") else value)
    except (TypeError, ValueError):
        return default


def _score_value(score) -> float | None:
    """ESPN puts final scores in two different shapes: schedule payloads use
    {'value': 105.0, 'displayValue': '105'} while a summary's header carries a
    bare '105' string. Accept both -- verified on the fixtures."""
    if isinstance(score, dict):
        score = score.get("value")
    return _to_float(score)


def _split_ma(value) -> tuple:
    """'6-13' -> (6, 13); a missing/unparseable value -> (None, None)."""
    if not isinstance(value, str) or "-" not in value:
        return (None, None)
    made, attempted = value.split("-", 1)
    return (_to_int(made), _to_int(attempted))


def _walk_standings_entries(node, conference: str, out: list) -> None:
    """Recursively find standings `entries` blocks (they sit under conference
    -> division children, with the nesting varying by season structure)."""
    if isinstance(node, dict):
        name = node.get("name", "")
        next_conf = name if name.endswith("Conference") else conference
        if isinstance(node.get("entries"), list):
            for entry in node["entries"]:
                row = _parse_standing_entry(entry, next_conf)
                if row is not None:
                    out.append(row)
        for value in node.values():
            _walk_standings_entries(value, next_conf, out)
    elif isinstance(node, list):
        for value in node:
            _walk_standings_entries(value, conference, out)


def _parse_standing_entry(entry: dict, conference: str) -> dict | None:
    team = entry.get("team") or {}
    if "id" not in team:
        return None
    row = {
        "team_id": int(team["id"]),
        "team": team.get("displayName") or team.get("name"),
        "conference": conference,
    }
    for stat in entry.get("stats", []):
        mapped = _STANDING_STATS.get(stat.get("name"))
        if mapped is None:
            continue
        column, caster = mapped
        raw = stat.get("displayValue", stat.get("value"))
        try:
            # winPercent arrives as '.780' (leading dot) -- float() rejects
            # that form, so normalize before casting to float-ish types.
            row[column] = caster(raw) if caster in (str, int) else _to_float(raw, raw)
        except (TypeError, ValueError):
            row[column] = None
    return row


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------

def parse_teams(payload: dict) -> list:
    """teams payload -> one row per team."""
    rows = []
    sports = payload.get("sports") or [{}]
    leagues = (sports[0] or {}).get("leagues") or [{}]
    for wrapper in (leagues[0] or {}).get("teams") or []:
        team = wrapper.get("team") or {}
        if "id" not in team:
            continue
        rows.append({
            "team_id": int(team["id"]),
            "abbrev": team.get("abbreviation"),
            "display_name": team.get("displayName"),
            "location": team.get("location"),
            "name": team.get("name"),
            "slug": team.get("slug"),
        })
    return rows


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def parse_schedule(payload: dict, season: str | None = None) -> list:
    """One team's schedule payload -> game rows (dedup on game_id happens in
    snapshot.py, since every game appears in both teams' schedules).

    `season` (label like '2010-11') overrides the date-derived season label;
    normally the two agree -- the override exists so a partial fetch can't
    misfile a game whose date field is missing."""
    rows = []
    for event in payload.get("events") or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors") or []
        by_side = {c.get("homeAway"): c for c in competitors}
        if "home" not in by_side or "away" not in by_side:
            continue
        home, away = by_side["home"], by_side["away"]
        date_str = event.get("date") or comp.get("date")
        rows.append({
            "game_id": int(event["id"]),
            "date": date_str,
            "season": season or season_label_from_date(date_str),
            "home_id": int(home["team"]["id"]),
            "home_abbrev": home["team"].get("abbreviation"),
            "home_score": _score_value(home.get("score")),
            "away_id": int(away["team"]["id"]),
            "away_abbrev": away["team"].get("abbreviation"),
            "away_score": _score_value(away.get("score")),
            "status": ((comp.get("status") or {}).get("type") or {}).get("name"),
            "neutral": bool(comp.get("neutralSite")),
        })
    return rows


# ---------------------------------------------------------------------------
# summary (box score)
# ---------------------------------------------------------------------------

def parse_summary(payload: dict) -> tuple:
    """One game summary payload -> (game_meta dict, player-game rows list).

    The game's header carries the competitors' home/away assignment, final
    scores and status; boxscore.players carries one block per team with that
    team's player lines. Every scheduled player appears, including
    did-not-play rows (stats empty, did_not_play=True) -- those are real
    fantasy outcomes (a 0) and the model's availability signal, not junk to
    drop."""
    header = payload.get("header") or {}
    comps = header.get("competitions") or []
    if not comps:
        return ({}, [])
    comp = comps[0]
    competitors = comp.get("competitors") or []
    by_side = {c.get("homeAway"): c for c in competitors}
    if "home" not in by_side or "away" not in by_side:
        return ({}, [])

    game_date = comp.get("date") or header.get("gameDate")
    meta = {
        "game_id": int(comp["id"]),
        "date": game_date,
        "season": season_label_from_date(game_date),
        "status": ((comp.get("status") or {}).get("type") or {}).get("name"),
        "home_id": int(by_side["home"]["team"]["id"]),
        "home_abbrev": by_side["home"]["team"].get("abbreviation"),
        "home_score": _score_value(by_side["home"].get("score")),
        "away_id": int(by_side["away"]["team"]["id"]),
        "away_abbrev": by_side["away"]["team"].get("abbreviation"),
        "away_score": _score_value(by_side["away"].get("score")),
    }

    opponent_of = {
        meta["home_id"]: (meta["away_id"], meta["away_abbrev"]),
        meta["away_id"]: (meta["home_id"], meta["home_abbrev"]),
    }

    rows = []
    for block in (payload.get("boxscore") or {}).get("players") or []:
        team = block.get("team") or {}
        team_id = _to_int(team.get("id"))
        if team_id is None or team_id not in opponent_of:
            continue  # a block for a team not in this header -- not our game
        stats_groups = block.get("statistics") or []
        if not stats_groups:
            continue
        labels = stats_groups[0].get("labels") or []
        opponent_id, opponent_abbrev = opponent_of[team_id]

        for athlete in stats_groups[0].get("athletes") or []:
            info = athlete.get("athlete") or {}
            if "id" not in info:
                continue
            values = athlete.get("stats") or []
            by_label = dict(zip(labels, values))

            row = {
                "game_id": meta["game_id"],
                "date": game_date,
                "season": meta["season"],
                "team_id": team_id,
                "team_abbrev": team.get("abbreviation"),
                "opponent_id": opponent_id,
                "opponent_abbrev": opponent_abbrev,
                "is_home": team_id == meta["home_id"],
                "player_id": int(info["id"]),
                "player_name": info.get("displayName") or info.get("shortName"),
                "starter": bool(athlete.get("starter")),
                "did_not_play": bool(athlete.get("didNotPlay")),
            }
            for label, column in _STAT_COLUMNS.items():
                row[column] = _to_int(by_label.get(label))
            for label, (made_col, attempted_col) in _SPLIT_COLUMNS.items():
                made, attempted = _split_ma(by_label.get(label))
                row[made_col] = made
                row[attempted_col] = attempted

            if row["did_not_play"] or not values:
                # Honest zeros vs NaN: a DNP's stat line is a real 0 for
                # fantasy purposes (they scored 0), not missing data. min
                # stays 0 so played = (min > 0) is well-defined everywhere.
                for column in ("min", "pts", "reb", "oreb", "dreb", "ast", "stl",
                               "blk", "to", "pf", "fgm", "fga", "fg3m", "fg3a",
                               "ftm", "fta"):
                    row[column] = row.get(column) or 0
                row["min"] = 0
            rows.append(row)

    return (meta, rows)


# ---------------------------------------------------------------------------
# standings
# ---------------------------------------------------------------------------

def parse_standings(payload: dict) -> list:
    """standings payload -> one row per team, conference attached."""
    rows: list = []
    for child in payload.get("children") or []:
        _walk_standings_entries(child, child.get("name", ""), rows)
    # Defensive dedupe: the recursive walk can reach the same entries block
    # through more than one path when ESPN nests children oddly.
    seen: dict = {}
    for row in rows:
        seen.setdefault(row["team_id"], row)
    return list(seen.values())


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------

def parse_roster(payload: dict, team_id: int | None = None) -> list:
    """roster payload -> (player_id, name, position, team_id) rows.

    Position is read off each ATHLETE (ESPN's group-level `position` is null
    on real payloads -- verified on the fixture -- while every athlete carries
    a full position dict with an abbreviation). Coarse G/F/C, good enough for
    the lineup optimizer's slot constraints, deliberately not pretending ESPN
    gives us PG-vs-SG splits it doesn't."""
    team_id = _to_int((payload.get("team") or {}).get("id"), team_id)
    rows = []
    for group in payload.get("positionGroups") or []:
        group_position = group.get("position")
        if isinstance(group_position, dict):
            group_position = group_position.get("abbreviation") or group_position.get("name")
        for athlete in group.get("athletes") or []:
            if "id" not in athlete:
                continue
            position = athlete.get("position")
            if isinstance(position, dict):
                position = position.get("abbreviation") or position.get("name")
            rows.append({
                "player_id": int(athlete["id"]),
                "player_name": athlete.get("displayName") or athlete.get("fullName"),
                "position": position or group_position,
                "team_id": team_id,
            })
    return rows
