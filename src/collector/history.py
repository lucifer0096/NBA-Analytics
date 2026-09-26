"""All-NBA-history career data from ESPN's free APIs -- the network half of
the dashboard's All-Time Stats, GOAT Rankings and Player Profile panels.

`awards.py` stays pure-local: it reads the collected box scores and knows
nothing about careers outside the collected window (2010-11 -> 2025-26). A
GOAT ladder built from that window alone is not a GOAT ladder, so this module
fetches the two missing ingredients, both from ESPN's free APIs (same headers
and retry/backoff policy as espn_api):

1. **Full-career stat lines** -- ``athletes/{id}/statistics/0`` career totals,
   ``athletes/{id}`` display name/debut year, and the site-api per-season rows
   (``.../athletes/{id}/stats``) that feed peak, championships and the Player
   Profile career-progression graph. Career totals span a player's WHOLE
   NBA career, not the 16 collected seasons.
2. **Official NBA honours** -- ESPN's awards index: 20 award types (MVP,
   Finals MVP, DPOY, All-NBA / All-Defensive teams, ROY, 6MOY, MIP, All-Star
   MVP, Cup awards, ...) x every season (~850 season detail calls once), plus
   the champion team id per season read off the Finals-MVP winner's team ref
   (1970 on -- the 1969 Finals MVP wore a losing jersey). This is what the
   GOAT formula's honours component scores; race-race finishes no longer
   count there (the homegrown races stay on the Awards Ladder tab).

Everything is cached in ``data/processed/history_cache.json`` (kept fresh by
the daily collector via the .gitignore exception, so CI refetches only what
moved):

- award season details for seasons that ended two or more years ago are a
  closed set -> cached until the end of time; recent seasons re-read for 7d
  so newly announced awards land;
- athlete entries carry a fetch stamp: players whose last season row is at
  least a season old are treated as static (180d re-read, which still picks
  up a comeback), active players refresh after ATHLETE_TTL_DAYS (7d);
- the career-leaders index (5 categories x top 25) is re-read every build
  (one call).

Honest failure policy: per-athlete fetches that die fall back to the
collected-window line (``line_source: "window"``) or drop the player from the
pool when he has no window line either -- an honest absence retried next run.
``build()`` raises only when it can produce nothing at all (no leaders live
and none cached, or an empty awards index with no cache), which makes the
refresh script keep its previous payload.

Trust rules (per-season rows -> peak / seasons / championships), all applied
here so the dashboard only ever displays what the data supports:

- rows are trusted for a career only when ``min(row year) <= debutYear + 1``
  (ESPN's per-season history starts late for some pre-1977 careers -- Kareem
  has rows only from 1976, Wilt only 1969-73);
- untrusted rows -> ``seasons: None`` (blank) and peak falls back to the best
  collected season, else ``None`` (component dropped on the ladder);
- championships are counted only for trusted careers whose rows start in
  1970+ (champion index begins there) and cover every completed row season;
  careers outside that rule take the verified official-record count from
  ``OFFICIAL_CHAMPIONSHIPS`` instead of a blank. Championship counts feed
  only the GOAT ladder's bounded championships component
  (``awards.GOAT_WEIGHTS``), nowhere else. Mid-season trades can mislead
  (a row carries one team), which the dashboard captions.

ESPN quirks preserved and captioned, never "fixed": ABA/NBA totals merged
(Dr. J 30,026 PTS), the blocks leader category is mislabelled
``name='assists'``, and some pre-1977 careers have late-starting season
rows (untrusted -> peak/seasons blank). The one overlay: ESPN reports
career/season rebounds as 0 for eleven pre-1974 legends (they are absent
from its rebound leaders entirely), so build() applies the official-record
rebound (OFFICIAL_REB_CAREER / OFFICIAL_REB_SEASON) -- and only where
ESPN's own value is 0, so every real ESPN number survives. What stays an
era fact: steals/blocks/3PM zeros before they were tracked, which GOAT
production drops per player instead of scoring a zero he could never earn.
"""

import concurrent.futures as cf
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import espn_api  # noqa: E402
import awards  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
CACHE_PATH = os.path.join(REPO_ROOT, "data", "processed", "history_cache.json")

BASE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
WEB_STATS_URL = ("https://site.web.api.espn.com/apis/common/v3/sports/"
                 "basketball/nba/athletes/{aid}/stats")

# The 20 award types ESPN answers with (ids 34/37 are empty). Verified live
# Sep 2026: names are the official ones and are the exact keys used in
# awards.GOAT_HONOURS_WEIGHTS.
AWARD_TYPE_IDS = (33, 35, 36, 39, 40, 43, 44, 45, 46, 47, 48, 49, 50, 53,
                  77, 217, 218, 297, 377, 378)
FINALS_MVP_ID = 43
CHAMPION_FIRST_YEAR = 1970  # 1969's Finals MVP (Jerry West) lost the Finals

# Official NBA championship counts (as a player) for careers the champion
# index can't vouch for: it starts at CHAMPION_FIRST_YEAR (1970) and ESPN's
# season rows start late for some legends (Kareem's begin in 1976, missing
# his 1971 Bucks title season), so the derived count returns None. These
# are the official record, verified against Wikipedia and Basketball-
# Reference player pages (Sep 2026), keyed by ESPN athlete id; used
# exactly like the derived counts (scored only in the GOAT ladder's
# bounded championships component). 0 means the record confirms no
# titles (so the row can show 0 instead of a blank).
OFFICIAL_CHAMPIONSHIPS = {
    3776: 1,   # Moses Malone (1983 76ers)
    4119: 1,   # Oscar Robertson (1971 Bucks)
    4120: 8,   # John Havlicek
    4121: 0,   # Lenny Wilkens
    4122: 1,   # Rick Barry (1975 Warriors)
    4123: 1,   # Paul Arizin (1956 Warriors)
    4124: 2,   # Dave DeBusschere (1970, 1973 Knicks)
    4125: 1,   # Billy Cunningham (1967 76ers)
    4126: 4,   # Bill Sharman
    4127: 2,   # Bill Walton (1977 Blazers, 1986 Celtics)
    4128: 1,   # Dolph Schayes (1954-55 Syracuse Nationals, 1955 Finals)
    4129: 2,   # Walt Frazier (1970, 1973 Knicks)
    4131: 0,   # George Gervin
    4132: 2,   # Dave Cowens (1974, 1976 Celtics)
    4133: 6,   # Bob Cousy
    4134: 1,   # Elvin Hayes (1978 Bullets)
    4135: 0,   # Elgin Baylor
    4136: 1,   # Earl Monroe (1973 Knicks)
    4137: 1,   # Jerry Lucas (1973 Knicks)
    4138: 10,  # Sam Jones
    4139: 0,   # Nate Thurmond
    4140: 1,   # Bob Pettit (1958 St. Louis Hawks)
    4141: 2,   # Willis Reed (1970, 1973 Knicks)
    4142: 2,   # Wilt Chamberlain (1967 76ers, 1972 Lakers)
    4143: 1,   # Hal Greer (1967 76ers)
    4145: 6,   # Kareem Abdul-Jabbar (1971 Bucks; 1980/82/85/87/88 Lakers)
    4146: 0,   # Dave Bing
    4147: 5,   # George Mikan (1949 BAA + 1950/52/53/54 NBA)
    4148: 1,   # Wes Unseld (1978 Bullets)
    4149: 1,   # Nate Archibald (1981 Celtics)
    4150: 0,   # Pete Maravich
    4151: 1,   # Jerry West (1972 Lakers)
    4152: 11,  # Bill Russell (Celtics 1957, 1959-66, 1968, 1969)
}

THREADS = 8

# Official rebound totals (career) for the eleven pre-1974 legends whose
# ESPN career line carries a broken reb 0 (they're absent from ESPN's
# rebound leaders entirely). Career numbers = exact sums of the official
# season totals, read from Basketball-Reference career rows (via the
# Wayback Machine) and cross-checked on StatMuse (Sep 2026). Applied at
# build time ONLY where ESPN reports 0, so every real ESPN value
# survives untouched.
OFFICIAL_REB_CAREER = {
    4123: 6129,    # Paul Arizin
    4126: 2793,    # Bill Sharman
    4128: 11256,   # Dolph Schayes
    4133: 4786,    # Bob Cousy
    4135: 11463,   # Elgin Baylor
    4138: 4305,    # Sam Jones
    4140: 12849,   # Bob Pettit
    4142: 23924,   # Wilt Chamberlain
    4143: 5665,    # Hal Greer
    4147: 4167,    # George Mikan
    4152: 21620,   # Bill Russell
}

# The same careers' season rows carry 0.0 rebounds too (peaks and the
# profile progression chart would read wrong), so build() overlays the
# official per-game rebound (rebounds / games, 1dp = ESPN's row
# convention) from Basketball-Reference season tables, keyed by season
# END year; each value was cross-checked identical on StatMuse. Only
# seasons the record actually has are listed; an unlisted season keeps
# ESPN's row -- deliberately, so Mikan 1947-48/1948-49/1949-50 and
# Schayes 1949-50 keep their 0.0: the league recorded no rebounds then
# (an era fact, not a gap to estimate away).
OFFICIAL_REB_SEASON = {
    4123: {1951: 9.8, 1952: 11.3, 1955: 9.4, 1956: 7.5, 1957: 7.9,
           1958: 7.4, 1959: 9.1, 1960: 8.6, 1961: 8.6, 1962: 6.8},
    4126: {1951: 3.5, 1952: 3.5, 1953: 4.1, 1954: 3.5, 1955: 4.4,
           1956: 3.6, 1957: 4.3, 1958: 4.7, 1959: 4.1, 1960: 3.7,
           1961: 3.7},
    4128: {1951: 16.4, 1952: 12.3, 1953: 13.0, 1954: 12.1, 1955: 12.3,
           1956: 12.4, 1957: 14.0, 1958: 14.2, 1959: 13.4, 1960: 12.8,
           1961: 12.2, 1962: 7.8, 1963: 5.7, 1964: 4.6},
    4133: {1951: 6.9, 1952: 6.4, 1953: 6.3, 1954: 5.5, 1955: 6.0,
           1956: 6.8, 1957: 4.8, 1958: 5.0, 1959: 5.5, 1960: 4.7,
           1961: 4.4, 1962: 3.5, 1963: 2.5, 1970: 0.7},
    4135: {1959: 15.0, 1960: 16.4, 1961: 19.8, 1962: 18.6, 1963: 14.3,
           1964: 12.0, 1965: 12.8, 1966: 9.6, 1967: 12.8, 1968: 12.2,
           1969: 10.6, 1970: 10.4, 1971: 5.5, 1972: 6.3},
    4138: {1958: 2.9, 1959: 6.0, 1960: 5.1, 1961: 5.4, 1962: 5.9,
           1963: 5.2, 1964: 4.6, 1965: 5.1, 1966: 5.2, 1967: 4.7,
           1968: 4.9, 1969: 3.8},
    4140: {1955: 13.8, 1956: 16.2, 1957: 14.6, 1958: 17.4, 1959: 16.4,
           1960: 17.0, 1961: 20.3, 1962: 18.7, 1963: 15.1, 1964: 15.3,
           1965: 12.4},
    4142: {1960: 27.0, 1961: 27.2, 1962: 25.7, 1963: 24.3, 1964: 22.3,
           1965: 22.9, 1966: 24.6, 1967: 24.2, 1968: 23.8, 1969: 21.1,
           1970: 18.4, 1971: 18.2, 1972: 19.2, 1973: 18.6},
    4143: {1959: 2.9, 1960: 4.3, 1961: 5.8, 1962: 7.4, 1963: 5.7,
           1964: 6.1, 1965: 5.1, 1966: 5.9, 1967: 5.3, 1968: 5.4,
           1969: 5.3, 1970: 4.7, 1971: 4.5, 1972: 3.3, 1973: 2.8},
    4147: {1951: 14.1, 1952: 13.5, 1953: 14.4, 1954: 14.3, 1956: 8.3},
    4152: {1957: 19.6, 1958: 22.7, 1959: 23.0, 1960: 24.0, 1961: 23.9,
           1962: 23.6, 1963: 23.6, 1964: 24.7, 1965: 24.1, 1966: 22.8,
           1967: 21.0, 1968: 18.6, 1969: 19.3},
}
ATHLETE_TTL_DAYS = 7        # active players' career totals move with seasons
STATIC_TTL_DAYS = 180       # retired careers barely move; still catches comebacks

# ESPN career-split stat name -> our career-line key (awards.CAREER_SUM_STATS
# plus gp/starts, which _career_row / _per_game / _impact read).
_STAT_KEY = {
    "gamesPlayed": "gp",
    "gamesStarted": "starts",
    "minutes": "minutes",
    "points": "pts",
    "rebounds": "reb",
    "offensiveRebounds": "oreb",
    "defensiveRebounds": "dreb",
    "assists": "ast",
    "steals": "stl",
    "blocks": "blk",
    "turnovers": "to",
    "fieldGoalsMade": "fgm",
    "fieldGoalsAttempted": "fga",
    "threePointFieldGoalsMade": "fg3m",
    "threePointFieldGoalsAttempted": "fg3a",
    "freeThrowsMade": "ftm",
    "freeThrowsAttempted": "fta",
}
_LINE_KEYS = ("gp", "starts", "minutes") + tuple(awards.CAREER_SUM_STATS)

# Per-season log columns (compact arrays inside the cache/profile file):
# year, team id, team abbrev, gp, min, pts, reb, ast, stl, blk, fg%, 3P%.
_ROW_FIELDS = ("year", "team_id", "team", "gp", "min", "pts", "reb", "ast",
               "stl", "blk", "fg_pct", "fg3_pct")

_ATHLETE_RE = re.compile(r"/athletes/(\d+)")
_SEASON_RE = re.compile(r"/seasons/(\d+)/awards/(\d+)")


def _get(url: str) -> dict:
    """One ESPN GET with the shared headers/retry policy; $refs arrive as
    http:// and must be rewritten to https:// (site-agnostic redirects are
    not guaranteed)."""
    return espn_api._get_json(url.replace("http://", "https://"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("listings", {})
            data.setdefault("awards", {})
            data.setdefault("athletes", {})
            data.setdefault("leaders", {})
            return data
    except (OSError, ValueError):
        pass
    return {"listings": {}, "awards": {}, "athletes": {}, "leaders": {}}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)


def _stamp(entry: dict) -> datetime | None:
    raw = entry.get("fetched")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _fresh(entry: dict, ttl_days: int, now: datetime) -> bool:
    fetched = _stamp(entry)
    return bool(fetched) and (now - fetched).total_seconds() < ttl_days * 86400


def _current_end_year() -> int:
    """ESPN's season param for the season the league is in (2026-27 -> 2027)."""
    return espn_api.season_param(espn_api.current_season_label())


# ---------------------------------------------------------------------------
# Official honours + champions
# ---------------------------------------------------------------------------

def _award_season_refs(tid: int, cache: dict, live: bool) -> list:
    """Season refs for one award type, re-read live (one call) and falling
    back to the cached listing when ESPN is unreachable or `live` is off
    (tests run fully from a fixture cache)."""
    listing = None
    if live:
        try:
            listing = _get(f"{BASE}/awards/{tid}?limit=500")
        except Exception:
            listing = None
    refs = [item.get("$ref") for item in ((listing or {}).get("items") or [])
            if item.get("$ref")]
    if refs:
        cache["listings"][str(tid)] = refs
        return refs
    return list(cache["listings"].get(str(tid)) or [])


def _award_detail(ref: str, cache: dict, now: datetime, live: bool) -> dict | None:
    """{name, athletes, champion} for one award-season, cached until the end
    of time for settled seasons and re-read for 7d while recent (announcements
    and the in-progress season land late)."""
    match = _SEASON_RE.search(ref)
    if not match:
        return None
    year, tid = int(match.group(1)), match.group(2)
    entry = cache["awards"].get(f"{tid}-{year}")
    settled = year <= _current_end_year() - 2
    if entry and entry.get("name") and (
            settled or _fresh(entry, ATHLETE_TTL_DAYS, now)):
        return entry
    if not live:
        return entry
    try:
        detail = _get(ref)
    except Exception:
        return entry  # keep the cached copy on failure
    name = detail.get("name")
    if not name:
        return entry
    athletes, champion = [], None
    for winner in detail.get("winners") or []:
        athlete_ref = (winner.get("athlete") or {}).get("$ref") or ""
        athlete_match = _ATHLETE_RE.search(athlete_ref)
        if not athlete_match:
            continue
        athletes.append(int(athlete_match.group(1)))
        if tid == str(FINALS_MVP_ID) and year >= CHAMPION_FIRST_YEAR:
            # The Finals MVP wears the championship jersey (1970 on; 1969's
            # Jerry West is the famous exception and is excluded above).
            team_ref = (winner.get("team") or {}).get("$ref") or ""
            team_match = re.search(r"/teams/(\d+)", team_ref)
            if team_match:
                champion = int(team_match.group(1))
    entry = {"name": name, "athletes": athletes, "champion": champion,
             "fetched": _iso(now)}
    cache["awards"][f"{tid}-{year}"] = entry
    return entry


def honours(cache: dict, live: bool = True, now: datetime = None) -> tuple:
    """({player_id: {official award name: career wins}}, {year: champion team
    id}, meta) over all 20 award types x every season ESPN carries."""
    now = now or _now()
    by_player: dict = {}
    champions: dict = {}
    seasons = 0
    name_by_tid: dict = {}
    for tid in AWARD_TYPE_IDS:
        for ref in _award_season_refs(tid, cache, live):
            match = _SEASON_RE.search(ref)
            if not match:
                continue
            detail = _award_detail(ref, cache, now, live)
            if not detail:
                continue
            seasons += 1
            name_by_tid[tid] = detail["name"]
            for pid in detail["athletes"]:
                counts = by_player.setdefault(pid, {})
                counts[detail["name"]] = counts.get(detail["name"], 0) + 1
            if detail.get("champion") is not None:
                champions[int(match.group(1))] = detail["champion"]
    if not seasons or not by_player:
        raise RuntimeError("ESPN awards index unavailable and no cached copy")
    meta = {
        "award_types": len(name_by_tid),
        "award_seasons": seasons,
        "honoured_players": len(by_player),
        "honour_wins": sum(sum(c.values()) for c in by_player.values()),
        "champion_years": len(champions),
    }
    return (by_player, champions, meta)


def leader_ids(cache: dict, live: bool = True) -> set:
    """Career top-25 in PTS/REB/AST/STL/BLK (the blocks category is
    mislabelled name='assists' -- we take every category's athletes)."""
    ids: set = set()
    if live:
        try:
            payload = _get(f"{BASE}/leaders")
            for category in payload.get("categories") or []:
                for leader in category.get("leaders") or []:
                    ref = (leader.get("athlete") or {}).get("$ref") or ""
                    match = _ATHLETE_RE.search(ref)
                    if match:
                        ids.add(int(match.group(1)))
        except Exception:
            ids = set()
    if ids:
        cache["leaders"] = {"fetched": _iso(_now()),
                            "ids": sorted(ids)}
        return ids
    cached = [int(i) for i in (cache.get("leaders") or {}).get("ids") or []]
    if not cached:
        raise RuntimeError("ESPN career leaders unavailable and no cached copy")
    return set(cached)


# ---------------------------------------------------------------------------
# Athlete bundles: career line + identity + per-season rows
# ---------------------------------------------------------------------------

def _parse_line(payload: dict) -> dict | None:
    """Career totals from the statistics/0 split (three categories merged --
    'points' lives under offensive, 'rebounds' under general)."""
    merged: dict = {}
    for category in ((payload.get("splits") or {}).get("categories")) or []:
        for stat in category.get("stats") or []:
            key = _STAT_KEY.get(stat.get("name"))
            if key and stat.get("value") is not None:
                merged[key] = stat["value"]
    if not merged.get("gp"):
        return None
    line = {key: int(round(float(merged.get(key) or 0)))
            for key in _LINE_KEYS}
    return line


def _num(value) -> float:
    try:
        return float(str(value).split("-")[0] or 0)  # "7.9-18.9" -> 7.9
    except (TypeError, ValueError):
        return 0.0


def _parse_rows(payload: dict) -> tuple:
    """(rows, last team abbrev) from the site-api averages category. Rows are
    per END year (2004 == 2003-04); teamSlug maps through the payload's own
    teams table to an abbreviation."""
    abbrevs = {}
    for slug, team in (payload.get("teams") or {}).items():
        abbrev = (team or {}).get("abbreviation")
        if abbrev:
            abbrevs[slug] = abbrev
    averages = next((c for c in payload.get("categories") or []
                     if c.get("name") == "averages"), None)
    labels = [str(x).upper() for x in (averages or {}).get("labels") or []]
    index = {label: i for i, label in enumerate(labels)}

    def cell(row: dict, label: str, default=0.0):
        i = index.get(label)
        stats = row.get("stats") or []
        if i is None or i >= len(stats):
            return default
        return _num(stats[i])

    rows, last_team = [], None
    for row in ((averages or {}).get("statistics") or []):
        season = row.get("season") or {}
        year = int(season.get("year") or 0)
        if not year:
            continue
        slug = row.get("teamSlug")
        team = abbrevs.get(slug)
        if team:
            last_team = team
        try:
            team_id = int(row.get("teamId") or 0)
        except (TypeError, ValueError):
            team_id = 0
        rows.append([
            year, team_id, team or "",
            int(cell(row, "GP", 0)), round(cell(row, "MIN"), 1),
            round(cell(row, "PTS"), 1), round(cell(row, "REB"), 1),
            round(cell(row, "AST"), 1), round(cell(row, "STL"), 1),
            round(cell(row, "BLK"), 1), round(cell(row, "FG%"), 1),
            round(cell(row, "3P%"), 1),
        ])
    rows.sort(key=lambda r: r[0])
    return (rows, last_team)


def _fetch_athlete(pid: int, now: datetime) -> dict | None:
    """One athlete's identity, career line and per-season rows. Every leg
    fails independently; only an athlete we can say nothing about returns
    None (he is skipped with an honest absence)."""
    info, line, rows, team = {}, None, [], None
    try:
        info = _get(f"{BASE}/athletes/{pid}") or {}
    except Exception:
        pass
    try:
        line = _parse_line(_get(f"{BASE}/athletes/{pid}/statistics/0"))
    except Exception:
        pass
    try:
        rows, team = _parse_rows(_get(WEB_STATS_URL.format(aid=pid)))
    except Exception:
        pass
    if not info.get("displayName") and line is None and not rows:
        return None
    entry = {
        "fetched": _iso(now),
        "name": info.get("displayName"),
        "debut": int(info["debutYear"]) if info.get("debutYear") else None,
        "line": line,
        "rows": rows,
        "team": team,
        # An athlete with no per-season rows (or no line/identity) is a
        # suspect fetch -- refetch next build instead of freezing it.
        "complete": bool(info.get("displayName")) and line is not None
                    and bool(rows),
    }
    return entry


def _entry_age_ok(entry: dict, now: datetime) -> bool:
    """Active players refresh weekly; players whose last season row predates
    the previous season are treated as static (6-month re-read still catches
    a comeback). Partial entries always refetch."""
    if not entry.get("complete"):
        return False
    ttl = ATHLETE_TTL_DAYS
    years = [row[0] for row in entry.get("rows") or []]
    if years and max(years) < _current_end_year() - 1:
        ttl = STATIC_TTL_DAYS
    return _fresh(entry, ttl, now)


def fetch_bundles(pool: set, cache: dict, live: bool = True) -> None:
    """Fill cache["athletes"] for every pool member that is missing or stale
    (threaded; failures leave the stale/absent entry in place so the next run
    retries). With live=False the cache is used as-is (offline builds/tests)."""
    if not live:
        return
    now = _now()
    todo = [pid for pid in sorted(pool)
            if not _entry_age_ok(cache["athletes"].get(str(pid)) or {}, now)]
    if not todo:
        return
    with cf.ThreadPoolExecutor(max_workers=THREADS) as executor:
        fetched = list(executor.map(
            lambda pid: (pid, _fetch_athlete(pid, now)), todo))
    for pid, entry in fetched:
        if entry is not None:
            cache["athletes"][str(pid)] = entry


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _season_log(rows: list) -> list:
    """[{season, team, gp, min, pts, reb, ast, stl, blk, fg_pct, fg3_pct}] for
    the Player Profile progression graph (per-game values, ESPN's own
    averages)."""
    log = []
    for row in rows:
        values = dict(zip(_ROW_FIELDS, row))
        log.append({
            "season": espn_api.season_label(values["year"]),
            "team": values["team"],
            "gp": values["gp"], "min": values["min"],
            "pts": values["pts"], "reb": values["reb"],
            "ast": values["ast"], "stl": values["stl"],
            "blk": values["blk"], "fg_pct": values["fg_pct"],
            "fg3_pct": values["fg3_pct"],
        })
    return log


def _trusted(entry: dict) -> bool:
    """Per-season rows cover the career from its start (ESPN starts some
    pre-1977 careers late -- Kareem's rows begin in 1976, debut 1969)."""
    rows = entry.get("rows") or []
    debut = entry.get("debut")
    if not rows or debut is None:
        return False
    return min(row[0] for row in rows) <= int(debut) + 1


def _official_reb_rows(pid: int, rows: list) -> list:
    """Season rows with official per-game rebounds overlaid where ESPN's
    row carries a broken 0.0 (OFFICIAL_REB_SEASON). Tuples stay tuples;
    rows ESPN reported a real value for are untouched."""
    overlay = OFFICIAL_REB_SEASON.get(pid)
    if not overlay or not rows:
        return rows
    index = _ROW_FIELDS.index("reb")
    out = []
    for row in rows:
        official = overlay.get(row[0])
        if official is not None and not row[index]:
            row = tuple(row[:index]) + (official,) + tuple(row[index + 1:])
        out.append(row)
    return out


def _peak_from_rows(rows: list) -> float | None:
    if not rows:
        return None
    best = 0.0
    for row in rows:
        values = dict(zip(_ROW_FIELDS, row))
        if not values["gp"]:
            continue
        best = max(best, values["pts"] + 0.75 * values["reb"] + values["ast"])
    return round(best, 1) or None


def _championships(entry: dict, champions: dict,
                   player_id: int = None) -> int | None:
    """Titles counted by matching a trusted career's season rows against the
    champion team per year (Finals-MVP team refs, 1970 on). Careers the
    index can't cover -- pre-1970 titles, late-starting rows (Kareem),
    careers the trust rule rejects -- take their verified official-record
    count from OFFICIAL_CHAMPIONSHIPS instead of returning None.
    Display-only: returns None rather than a wrong number when nothing
    applies, since mid-season trades can make a row carry the wrong team."""
    if player_id in OFFICIAL_CHAMPIONSHIPS:
        return OFFICIAL_CHAMPIONSHIPS[player_id]
    rows = entry.get("rows") or []
    if not rows or not champions or not _trusted(entry):
        return None
    years = [row[0] for row in rows]
    if min(years) < CHAMPION_FIRST_YEAR:
        return None  # titles before the champion index exists
    completed = [y for y in years if y < _current_end_year()]
    if not completed or any(y not in champions for y in completed):
        return None
    titles = 0
    for row in rows:
        values = dict(zip(_ROW_FIELDS, row))
        year = values["year"]
        if year in champions and values["team_id"] == champions[year]:
            titles += 1
    return titles


def _line_and_source(entry: dict, window_entry: dict) -> tuple:
    """(career line, source): the ESPN full-career line supersedes the
    collected-window line; a failed fetch falls back to the window line, and
    a window-only player keeps his own."""
    if entry and entry.get("line"):
        return (entry["line"], "espn")
    if window_entry:
        line = {key: window_entry.get(key) or 0 for key in _LINE_KEYS}
        return (line, "window")
    return (entry.get("line"), "window" if entry else None)


def build(window_players: list, cache: dict = None, live: bool = True) -> dict:
    """Full-history entries for the pool = career leaders (all-history top-25
    in 5 categories) U official-award winners U collected-window players with
    >=ALLTIME_MIN_GP. Returns:

    {"players": [career entries shaped like awards.alltime_players() rows,
                 plus championships/seasons_log/line_source/debut],
     "honours": {pid: {official award name: wins}},
     "champions": {end year: championship team id},
     "meta": counts for the caption, "stamp": ISO UTC}

    live=False builds purely from the cached fixtures (tests); the default
    refreshes stale entries first. Raises only when nothing at all can be
    fetched (refresh keeps its previous payload then)."""
    cache = cache if cache is not None else _load_cache()
    now = _now()
    leaders = leader_ids(cache, live)
    honours_map, champions, award_meta = honours(cache, live, now)
    _save_cache(cache)  # award phase is expensive; keep it across crashes

    window_index = {int(p["player_id"]): p for p in window_players or []}
    window_pool = {pid for pid, p in window_index.items()
                   if (p.get("gp") or 0) >= awards.ALLTIME_MIN_GP}
    pool = set(leaders) | set(honours_map) | window_pool

    fetch_bundles(pool, cache, live)
    _save_cache(cache)  # partial progress survives an interrupted build

    players = []
    espn_lines = window_fallbacks = official_reb_lines = 0
    for pid in sorted(pool):
        bundle = cache["athletes"].get(str(pid)) or {}
        window_entry = window_index.get(pid)
        name = bundle.get("name") or (window_entry or {}).get("player_name")
        line, source = _line_and_source(bundle, window_entry)
        if line and pid in OFFICIAL_REB_CAREER and not (line.get("reb") or 0):
            line = {**line, "reb": OFFICIAL_REB_CAREER[pid]}
            official_reb_lines += 1
        if not name or not line or not line.get("gp"):
            continue
        if source == "espn":
            espn_lines += 1
        else:
            window_fallbacks += 1
        trusted = _trusted(bundle)
        rows = _official_reb_rows(pid, bundle.get("rows") or [])
        if trusted:
            seasons = len(rows)
            peak = _peak_from_rows(rows)
        elif rows:
            seasons, peak = None, None
        else:
            seasons = (window_entry or {}).get("seasons")
            peak = None
        if peak is None and window_entry:
            peak = window_entry.get("peak_impact")  # best collected season
        entry = dict(line)
        entry.update({
            "player_id": pid,
            "player_name": name,
            "team_abbrev": (window_entry or {}).get("team_abbrev")
                           or bundle.get("team"),
            "seasons": seasons,
            "peak_impact": peak,
            "championships": _championships(bundle, champions, pid),
            "line_source": source,
            "debut": bundle.get("debut"),
        })
        if rows:
            entry["seasons_log"] = _season_log(rows)
        players.append(entry)

    if not players:
        raise RuntimeError("no athlete entries could be assembled")

    stamp = _iso(now)
    meta = {
        "pool": len(pool),
        "leaders": len(leaders),
        "window_41": len(window_pool),
        "espn_lines": espn_lines,
        "window_fallback": window_fallbacks,
        "official_champions": sum(1 for p in players
                                  if p["player_id"] in OFFICIAL_CHAMPIONSHIPS),
        "official_reb_lines": official_reb_lines,
        "stamp": stamp,
        **award_meta,
    }
    return {"players": players, "honours": honours_map,
            "champions": champions, "meta": meta, "stamp": stamp}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="build/refresh the all-NBA-history cache")
    parser.add_argument(
        "--no-window", action="store_true",
        help="skip the (slow) box-score scan: leaders + honours pool only")
    args = parser.parse_args()
    window = [] if args.no_window else awards.alltime_players()
    result = build(window)
    print(json.dumps(result["meta"], indent=1))
