"""Thin client for ESPN's free, public, unauthenticated NBA and WNBA APIs.

Both leagues' endpoints are the same paths with the league slug swapped
(nba / wnba) -- every function here takes `league` and validates it through
leagues.py; the NBA names stay the defaults so no existing call changes.

Endpoints used (all verified working, no API key, no auth):

- teams: every NBA team's basic info
    site.api.espn.com/.../sports/basketball/nba/teams
- schedule: one team's full-season schedule with ESPN event ids
    site.api.espn.com/.../teams/{team_id}/schedule?season={end_year}&seasontype=2
    (30 calls cover every game of a season -- there is no season-wide schedule
    endpoint; verified: .../schedule?season={end_year} 404s)
- summary: one game's full box score (both teams, per-player lines incl. DNP)
    site.api.espn.com/.../summary?event={event_id}
    verified back to 1995-96 -- the pre-2010 window is real, this project
    just deliberately focuses on 2010-11+ (see README)
- standings: full conference standings for one season
    site.web.api.espn.com/apis/v2/.../standings?season={end_year}
    NOTE: site.api's own /standings path only returns a link stub ("Full
    Standings") -- the site.web.api v2 path is the one with actual entries
- roster: one team-season roster with player positions (G/F/C)
    site.web.api.espn.com/apis/common/v3/.../teams/{team_id}/roster?season={end_year}
- scoreboard: every game on one calendar date (any historical date)
    site.api.espn.com/.../scoreboard?dates=YYYYMMDD

SEASON PARAM SEMANTICS (easy to get wrong): ESPN's `season` parameter is the
year a season ENDS, not the year it starts. Season 2010-11 -> season=2011,
2024-25 -> season=2025. Use season_param()/season_label() below instead of
doing the arithmetic inline anywhere -- the same off-by-one appears in every
endpoint above.

No third-party HTTP dependency on purpose (mirrors FPL-Analytics' fpl_api.py):
urllib from the standard library, so requirements.txt stays model/dashboard
only.
"""

import json
import time
import urllib.error
import urllib.request

import leagues

# League base URLs -- WNBA paths verified live Sep 2026 (teams, standings,
# schedules, summary box scores all answer 200 with the NBA shapes).
SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB_API_V2 = "https://site.web.api.espn.com/apis/v2/sports/basketball/nba"
WEB_API_COMMON = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"


def site_api(league: str = "nba") -> str:
    """site.api base for `league` (teams/schedule/summary/scoreboard)."""
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/{leagues.validate(league)}"


def web_api_v2(league: str = "nba") -> str:
    """site.web.api v2 base for `league` (standings)."""
    return f"https://site.web.api.espn.com/apis/v2/sports/basketball/{leagues.validate(league)}"


def web_api_common(league: str = "nba") -> str:
    """site.web.api common v3 base for `league` (rosters, athlete stats)."""
    return f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/{leagues.validate(league)}"

# ESPN's edge (Fastly WAF) answers DIFFERENTLY depending on the client AND
# the caller's network -- verified directly with the probe-espn.yml workflow
# (run it from the Actions tab after any future 403):
#
#   GitHub-hosted runner:  spoofed Chrome UA -> 403, default/curl UA -> 200
#                          site.web.api + sports.core.api -> 200 either way
#   Dev machine:           every form below -> 200
#
# So the primary client is the HONEST one (urllib's own User-Agent, no
# browser impersonation), and a 403 rotates through the alternatives below
# before giving up. UA-guessing is not a reliable strategy -- if this ever
# 403s again, re-run the probe and fix the sets HERE, in one place, not
# scattered across callers.
_HEADER_SETS = (
    # 1. Honest default: no User-Agent/Accept spoofing at all. This is the
    #    form the runner-side probe proved 200 from a datacenter IP.
    {},
    # 2. curl's default UA -- the other runner-proven 200, for edges that
    #    block unknown libraries specifically.
    {"User-Agent": "curl/8.5.0", "Accept": "application/json"},
    # 3. Full browser-looking set: works from ordinary networks (and is kept
    #    last, because it is the one datacenter IPs get challenged for).
    {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nba.com/",
    },
)

# ESPN rate-limits aggressive clients; a polite floor between retries of the
# SAME request (the snapshot's own inter-request delay lives in snapshot.py).
# Four attempts cover every header set once plus one wrap-around.
_MAX_RETRIES = 4


def _get_json(url: str, timeout: int = 20) -> dict:
    """GET url and parse JSON, retrying 429/403/5xx with exponential backoff
    while ROTATING through _HEADER_SETS (attempt N uses set N % 3), so a WAF
    that rejects one client form is offered the next instead of the same
    rejected header four times.

    4xx other than 429/403 are NOT retried (a 404 is a bug, not a hiccup);
    403 is retried with rotation because the WAF challenges by client form
    (see _HEADER_SETS note), then surfaced to the caller."""
    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        headers = _HEADER_SETS[attempt % len(_HEADER_SETS)]
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 403) or 500 <= e.code < 600:
                last_error = e
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"GET {url} failed after {_MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Season label <-> ESPN season param (the ENDS-in year -- see module docstring)
# ---------------------------------------------------------------------------

def season_param(season_label: str, league: str = "nba") -> int:
    """'2010-11' -> 2011 (ESPN's NBA season param is the year the season
    ends). The WNBA's label IS the param: '2026' -> 2026, because a WNBA
    season is one calendar year (May -> October)."""
    if league == "wnba":
        return int(season_label)
    start = int(str(season_label)[:4])
    return start + 1


def season_label(season_param_value: int, league: str = "nba") -> str:
    """2011 -> '2010-11' (NBA); 2026 -> '2026' (WNBA, one calendar year)."""
    if league == "wnba":
        return str(int(season_param_value))
    start = int(season_param_value) - 1
    return f"{start}-{str(int(season_param_value))[2:]}"


def current_season_label(today=None, league: str = "nba") -> str:
    """The season the league is currently in (or entering, in the offseason).

    NBA: seasons run Oct -> Jun, so from Jul onward the *next* season is the
    one that matters (its schedule/roster exist by then or soon will), Jan-Jun
    the one still being played.

    WNBA: seasons run May -> October (finals in September/October), so Nov-Dec
    are the offseason and the next calendar year is the one that matters (its
    schedule lands in the spring) -- Jan-Oct the calendar year itself, whose
    Jan-Apr months have not tipped off yet (the dashboard's honest empty
    states cover that gap)."""
    import datetime

    today = today or datetime.date.today()
    if league == "wnba":
        return str(today.year if today.month < 11 else today.year + 1)
    if today.month >= 7:
        return f"{today.year}-{str(today.year + 1)[2:]}"
    return f"{today.year - 1}-{str(today.year)[2:]}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def get_teams(league: str = "nba") -> dict:
    """Every team's basic info for `league` (current franchise list, not
    per-season)."""
    return _get_json(f"{site_api(league)}/teams")


def get_schedule(season: int, team_id: int, league: str = "nba",
                 season_type: int = 2) -> dict:
    """One team's schedule for the season ENDING in `season` (NBA) / the
    season `season` (WNBA) -- see season_param. `season_type` picks the
    phase ESPN serves: 2 = regular season (the frame every computed view
    reads), 3 = postseason (playoffs; play-in games answer under NEITHER
    type -- snapshot.py's gap probe collects those from the scoreboard)."""
    return _get_json(
        f"{site_api(league)}/teams/{team_id}/schedule"
        f"?season={season}&seasontype={season_type}"
    )


def get_event_summary(event_id: int | str, league: str = "nba") -> dict:
    """One game's full detail payload -- header (teams/date/status) + boxscore
    (per-player stat lines for both teams, did-not-play flags included)."""
    return _get_json(f"{site_api(league)}/summary?event={event_id}")


def get_standings(season: int, league: str = "nba") -> dict:
    """Full conference standings for the season ENDING in `season` (NBA) /
    the season `season` (WNBA; same East/West tree shape, verified live).
    The site.api /standings path only returns a link stub -- this is the
    site.web.api v2 path that actually carries entries (see module docstring)."""
    return _get_json(f"{web_api_v2(league)}/standings?season={season}")


def get_roster(team_id: int, season: int, league: str = "nba") -> dict:
    """One team's roster for the season ENDING in `season` (NBA) / the
    season `season` (WNBA), grouped by position -- the project's only source
    of player positions (G/F/C), since per-game box-score lines carry no
    position field (verified on the 1995-96 fixture: athlete entry has
    position: null)."""
    return _get_json(f"{web_api_common(league)}/teams/{team_id}/roster?season={season}")


def get_scoreboard(dates: str, league: str = "nba") -> dict:
    """Every game on calendar date `dates` (YYYYMMDD) for `league` -- works
    for historical dates too, which makes it a cheap schedule cross-check."""
    return _get_json(f"{site_api(league)}/scoreboard?dates={dates}")
