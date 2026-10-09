"""Per-league configuration for the two ESPN leagues this project serves.

The collectors, dashboard and model were NBA-only by construction; the WNBA
means every NBA-shaped assumption (season labels, data paths, ESPN's
award-type ids, honours tables) is declared HERE once instead of being
re-derived at each call site.

ESPN's WNBA endpoints are the same shapes as the NBA's -- verified live
Sep 2026: teams, standings (East/West children, same entry fields), team
schedules, summary box scores with IDENTICAL stat labels
(MIN/PTS/FG/3PT/FT/REB/.../+/-), core.api athletes/career statistics, and
the awards index. What actually differs:

- Season labels: an NBA season spans two calendar years ('2010-11'; ESPN's
  season param = the year it ENDS). A WNBA season is one calendar year
  ('2026'; season param = that year, its schedule runs May->October).
  espn_api.season_param/season_label/current_season_label take `league`.
- Award types: ESPN serves 16 WNBA award types (ids 237-251 and 257; id 240
  is empty, like the NBA's 34/37) under their own exact names, so the GOAT
  honours weights/labels are per league (see WNBA_GOAT_HONOURS_* below).
  ESPN's full award names are the table keys -- verified live Sep 2026.
- Data paths: the NBA's committed files keep their historical names (no
  246MB rename); every WNBA file gets a '_wnba' infix ('data/raw_wnba',
  'dashboard_wnba_teams.json', 'races_wnba', 'schedules_wnba',
  'history_cache_wnba.json').

Usage: cfg(league) returns the config dict, validate(league) the name, and
the path helpers below are the only place file names are spelled out.
CLI-first collectors (snapshot/history/refresh) switch league with their
own set_league(); anything that serves BOTH leagues in one process (the
Streamlit app) passes `league` explicitly through every call.
"""

import os
import re

LEAGUES = ("nba", "wnba")
DEFAULT_LEAGUE = "nba"

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
DATA_DIR = os.path.join(REPO_ROOT, "data")

# ---------------------------------------------------------------------------
# Official honours -> points per win, per league. Keys are ESPN's EXACT
# award names (the awards API's own strings), so a rename upstream shows up
# as a dropped honour in tests rather than silent drift. All-Star game
# SELECTIONS aren't in ESPN's awards API at all for either league, so they
# can't be scored here. Championships are a separate bounded GOAT component
# (GOAT_WEIGHTS["championships"]), counted from champion-team rows --
# Finals-MVP team refs -- not part of these tables.
# ---------------------------------------------------------------------------

# Official NBA honours (verified live Sep 2026 against the 20 non-empty
# award types -- ids 34/37 are empty).
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
# runs with; both dicts are asserted against goat_formula in the tests).
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

# Official WNBA honours -> points per win, mirroring the NBA table's
# semantics. Keys are ESPN's EXACT award names (live-verified Sep 2026
# against the 15 non-empty award types -- id 240 is empty like the NBA's
# 34/37).
# All-Star game SELECTIONS are not in ESPN's awards API for either league;
# championships stay a separate bounded GOAT component (finals-MVP team
# refs cover every WNBA season from 1997 on).
WNBA_GOAT_HONOURS_WEIGHTS = {
    "MVP": 6.0,
    "Finals MVP": 5.0,
    "Defensive Player of the Year": 4.5,
    "All-WNBA 1st Team": 3.0,
    "All-Defensive 1st Team": 2.5,
    "All-WNBA 2nd Team": 2.0,
    "All-Defensive 2nd Team": 1.5,
    "Rookie of the Year": 1.5,
    "Sixth Player of the Year": 1.5,
    "Most Improved Player": 1.5,
    "All-Star MVP": 1.5,
    "Commissioner's Cup MVP": 1.0,
    "All-Rookie Team": 0.5,
    "WNBA Sportsmanship Award": 0.25,
    "Seasonlong Community Assist Award": 0.25,
}
# Short display names for the caption (the weights above are what the math
# runs with; both dicts are asserted against goat_formula("wnba") in tests).
WNBA_GOAT_HONOUR_LABELS = {
    "MVP": "MVP",
    "Finals MVP": "Finals MVP",
    "Defensive Player of the Year": "DPOY",
    "All-WNBA 1st Team": "All-WNBA 1st",
    "All-Defensive 1st Team": "All-Def 1st",
    "All-WNBA 2nd Team": "All-WNBA 2nd",
    "All-Defensive 2nd Team": "All-Def 2nd",
    "Rookie of the Year": "ROY",
    "Sixth Player of the Year": "6MOY",
    "Most Improved Player": "MIP",
    "All-Star MVP": "All-Star MVP",
    "Commissioner's Cup MVP": "Cup MVP",
    "All-Rookie Team": "All-Rookie",
    "WNBA Sportsmanship Award": "Sportsmanship",
    "Seasonlong Community Assist Award": "Comm. Assist",
}

_CONFIG = {
    "nba": {
        "display": "NBA",
        # ESPN's award-type ids the GOAT honours run over (34/37 empty).
        "award_type_ids": (33, 35, 36, 39, 40, 43, 44, 45, 46, 47, 48, 49,
                           50, 53, 77, 217, 218, 297, 377, 378),
        "finals_mvp_id": 43,
        # Champion index starts 1970 (1969's Finals MVP -- Jerry West --
        # lost the Finals); earlier titles come from OFFICIAL_CHAMPIONSHIPS.
        "champion_first_year": 1970,
        "season_dir_re": re.compile(r"^\d{4}-\d{2}$"),
        "first_season": "2010-11",   # the project's stated collection window
        "min_unique_teams": 30,      # a healthy current-franchise list
        "honours_weights": GOAT_HONOURS_WEIGHTS,
        "honours_labels": GOAT_HONOUR_LABELS,
        # The four homegrown races' OFFICIAL counterparts (ESPN's exact
        # award names, the honours table's own strings): the Awards
        # Ladder's official-vs-algorithm verdicts match on these.
        "race_award_names": {"mvp": "MVP",
                             "dpoy": "Defensive Player of the Year",
                             "sixth_man": "Sixth Man of the Year",
                             "mip": "Most Improved Player"},
        "official_overrides": True,  # NBA legends' verified record patches
        # No verified champion overlay: ESPN's Finals-MVP team refs cover
        # 1970 on (earlier titles come from OFFICIAL_CHAMPIONSHIPS).
        "verified_champions": {},
    },
    "wnba": {
        "display": "WNBA",
        # ids 237-251 + 257 (240 is empty). Finals MVP (257) doubles as the
        # champion index for every season since 1997 -- with one ESPN gap
        # (1997-2002 winners carry no team ref, verified live Sep 2026)
        # filled by verified_champions below, so no career-spanning override
        # table exists for the WNBA.
        "award_type_ids": (237, 238, 239, 241, 242, 243, 244, 245, 246, 247,
                           248, 249, 250, 251, 257),
        "finals_mvp_id": 257,
        "champion_first_year": 1997,  # the league's first season
        "season_dir_re": re.compile(r"^\d{4}$"),
        "first_season": "2010",       # same window policy as the NBA side
        "min_unique_teams": 10,       # 15 franchises in 2026; a floor, not
                                      # an exact count, so expansion never
                                      # breaks the guard
        "honours_weights": WNBA_GOAT_HONOURS_WEIGHTS,
        "honours_labels": WNBA_GOAT_HONOUR_LABELS,
        # Same four races' official counterparts as the NBA table (exact
        # ESPN names); the sixth-man award is the WNBA's "Sixth Player".
        "race_award_names": {"mvp": "MVP",
                             "dpoy": "Defensive Player of the Year",
                             "sixth_man": "Sixth Player of the Year",
                             "mip": "Most Improved Player"},
        "official_overrides": False,
        # {season: ESPN team id} for the six seasons whose Finals-MVP
        # detail omits the winner's team ref (verified live Sep 2026).
        # League record: Houston Comets 1997-2000 (ESPN team id 4),
        # Los Angeles Sparks 2001-2002 (id 6 -- confirmed by ESPN's own
        # 2002 team ref). Applied with setdefault in history.honours():
        # ESPN's refs always win where they exist.
        "verified_champions": {1997: 4, 1998: 4, 1999: 4, 2000: 4,
                               2001: 6, 2002: 6},
    },
}


def validate(league: str) -> str:
    """The league name, or ValueError listing the valid ones."""
    if league not in LEAGUES:
        raise ValueError(f"unknown league {league!r}; expected one of {LEAGUES}")
    return league


def cfg(league: str = DEFAULT_LEAGUE) -> dict:
    """Config dict for `league` (validates the name first)."""
    return _CONFIG[validate(league)]


def display(league: str = DEFAULT_LEAGUE) -> str:
    """'NBA' / 'WNBA' -- the word prose and captions should use."""
    return cfg(league)["display"]


def season_dir_re(league: str = DEFAULT_LEAGUE):
    """Regex matching a collected season directory: '2010-11' (NBA) or the
    single-year '2010' (WNBA)."""
    return cfg(league)["season_dir_re"]


def first_season(league: str = DEFAULT_LEAGUE) -> str:
    """First season of the project's collection window for `league`."""
    return cfg(league)["first_season"]


# ---------------------------------------------------------------------------
# Data paths -- the only place per-league file names are spelled out.
# The NBA's names are the pre-WNBA historical ones (unchanged, so the
# 246MB of committed raw data never moves); WNBA files carry '_wnba'.
# ---------------------------------------------------------------------------

def raw_dir(league: str = DEFAULT_LEAGUE) -> str:
    return os.path.join(DATA_DIR, "raw" if league == "nba" else "raw_wnba")


def races_dir(league: str = DEFAULT_LEAGUE) -> str:
    return os.path.join(DATA_DIR, "races" if league == "nba" else "races_wnba")


def schedules_dir(league: str = DEFAULT_LEAGUE) -> str:
    return (os.path.join(DATA_DIR, "schedules")
            if league == "nba" else os.path.join(DATA_DIR, "schedules_wnba"))


def postseason_dir(league: str = DEFAULT_LEAGUE) -> str:
    """Committed postseason schedule files (one JSON per season): a tree
    deliberately SEPARATE from schedules/, so standings, leaders, GOAT and
    the model's training frame -- all readers of schedules/ -- stay
    regular-season-only while the Schedule tab can show playoff scores
    under their own label. The folder rule matches races/schedules: the
    NBA's historical name, '_wnba' for the other league."""
    return (os.path.join(DATA_DIR, "postseason")
            if league == "nba" else os.path.join(DATA_DIR, "postseason_wnba"))


def named_path(directory: str, name: str,
               league: str = DEFAULT_LEAGUE) -> str:
    """`name` inside `directory` with THE league naming rule: the NBA keeps
    the historical name; other leagues get '_{league}' right after a
    'dashboard_' prefix ('dashboard_teams.json' ->
    'dashboard_wnba_teams.json'), or before the extension when there is no
    such prefix. One rule for every writer and reader (collector refresh,
    app loaders) so a path can never disagree with its counterpart."""
    slug = validate(league)
    if slug != "nba":
        if name.startswith("dashboard_"):
            name = "dashboard_" + slug + "_" + name[len("dashboard_"):]
        else:
            stem, ext = os.path.splitext(name)
            name = f"{stem}_{slug}{ext}"
    return os.path.join(directory, name)


def dashboard_path(stem: str, league: str = DEFAULT_LEAGUE) -> str:
    """dashboard_path('dashboard_teams.json') -> data/dashboard_teams.json
    (NBA, the historical name) or data/dashboard_wnba_teams.json."""
    return named_path(DATA_DIR, stem, league)


def history_cache_path(league: str = DEFAULT_LEAGUE) -> str:
    return os.path.join(
        DATA_DIR, "processed",
        "history_cache.json" if league == "nba" else "history_cache_wnba.json")
