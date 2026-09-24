"""Shared data loading for every dashboard page: LIVE-first from ESPN's free
APIs, falling back to the committed data/dashboard_*.json copies exactly when
the API is unreachable (Streamlit Cloud, offline dev, ESPN outage) -- the same
60s-cache + fallback contract as FPL-Analytics' shared.py.

Why the fallbacks exist at all: data/raw/ is gitignored (it's ~20k regenerable
files) and Streamlit Cloud can't run the collector, so refresh_dashboard_
fallbacks.py commits the small stable copies the tabs actually read. Every
fallback carries its own _generated_utc, which this module surfaces via
data_age_note() so the UI can say "live" vs "as of <time>" honestly instead
of implying freshness it doesn't have.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"

sys.path.insert(0, str(REPO_ROOT / "src" / "collector"))
sys.path.insert(0, str(REPO_ROOT / "src" / "model"))

import espn_api  # noqa: E402

# ---------------------------------------------------------------------------
# Theme + presentation helpers (the "UI pass")
#
# All CSS-only and built on Streamlit's CSS variables (primary/background/
# text colors from .streamlit/config.toml), so it stays correct if the theme
# changes, and every transition/animation is disabled under
# prefers-reduced-motion (same discipline as FPL-Analytics' UI pass).
# ---------------------------------------------------------------------------

CSS = """
<style>
:root {
  --na-gold: #F5C542;
  --na-accent: var(--primary-color, #FF4B2B);
  --na-card: color-mix(in srgb, var(--secondary-background-color) 70%, transparent);
  --na-border: color-mix(in srgb, var(--text-color) 14%, transparent);
}

/* Gradient title: accent -> gold, theme-token based */
[data-testid="stTitle"] h1 {
  background: linear-gradient(90deg, var(--na-accent), var(--na-gold));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: -0.01em;
}

/* Hero strip under the title: season pill + source note live inside it */
.na-hero {
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  padding: 12px 16px; margin: 4px 0 14px;
  border: 1px solid var(--na-border); border-radius: 14px;
  background: linear-gradient(120deg,
    color-mix(in srgb, var(--na-accent) 14%, transparent), transparent 55%),
    var(--na-card);
}
.na-pill {
  display: inline-block; padding: 3px 12px; border-radius: 999px;
  font-size: 0.78rem; font-weight: 700; letter-spacing: 0.06em;
  background: color-mix(in srgb, var(--na-accent) 22%, transparent);
  color: var(--text-color);
  border: 1px solid color-mix(in srgb, var(--na-accent) 45%, transparent);
}
.na-pill--gold {
  background: color-mix(in srgb, var(--na-gold) 20%, transparent);
  border-color: color-mix(in srgb, var(--na-gold) 55%, transparent);
}
.na-sub { opacity: 0.75; font-size: 0.9rem; }

/* Uppercase section labels */
.na-section {
  margin: 18px 0 6px; font-size: 0.8rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em; opacity: 0.65;
}

/* Metric cards: glass tiles instead of bare numbers */
div[data-testid="stMetric"] {
  background: var(--na-card); border: 1px solid var(--na-border);
  border-radius: 14px; padding: 14px 16px;
}

/* Tabs: accent underline on the active tab */
[data-baseweb="tab-list"] { gap: 6px; }
[data-baseweb="tab"] { padding-left: 14px; padding-right: 14px; }
[data-baseweb="tab"][aria-selected="true"] {
  background: transparent;
  border-bottom: 2px solid var(--na-accent);
  color: var(--text-color);
}

/* Lineup slot chips */
.na-slot {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 0.72rem; font-weight: 800; letter-spacing: 0.08em;
  border: 1px solid var(--na-border); background: var(--na-card);
}
.na-slot--G { color: #7EA8FF; border-color: #3B6FE0; }
.na-slot--F { color: #7FE0A8; border-color: #2E9E63; }
.na-slot--C { color: #FFB37F; border-color: #E07A2E; }
.na-slot--UTIL { color: var(--na-gold); border-color: color-mix(in srgb, var(--na-gold) 60%, transparent); }

/* Optimizer result cards */
.na-player-card {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  border: 1px solid var(--na-border); background: var(--na-card);
  border-radius: 14px; padding: 10px 16px; margin-bottom: 8px;
}
.na-player-card .na-name { font-weight: 700; }
.na-player-card .na-meta { opacity: 0.7; font-size: 0.82rem; }
.na-player-card .na-pts {
  font-weight: 800; font-size: 1.05rem; color: var(--na-gold); white-space: nowrap;
}
.na-total {
  font-size: 1.9rem; font-weight: 800;
  color: var(--na-gold); line-height: 1.1;
}

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def hero(season_label: str, source_note: str, extra: str = "") -> None:
    """Accent strip below the title: season pill, gold data-age pill, note."""
    extra_html = f'<span class="na-pill na-pill--gold">{extra}</span>' if extra else ""
    st.markdown(
        f'<div class="na-hero">'
        f'<span class="na-pill">🏀 {season_label}</span>'
        f"{extra_html}"
        f'<span class="na-sub">{source_note}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )


def section(title: str) -> None:
    st.markdown(f'<div class="na-section">{title}</div>', unsafe_allow_html=True)


def slot_chip(slot: str) -> str:
    """HTML span for a lineup slot chip (G/F/C/UTIL colored distinctly)."""
    return f'<span class="na-slot na-slot--{slot}">{slot}</span>'


def player_card_html(slot: str, name: str, meta: str, points: float) -> str:
    return (
        f'<div class="na-player-card">'
        f'<div>{slot_chip(slot)}&nbsp;&nbsp;'
        f'<span class="na-name">{name}</span>'
        f'<div class="na-meta">{meta}</div></div>'
        f'<div class="na-pts">{points:.1f}</div>'
        f"</div>"
    )


def _read_fallback(name: str) -> dict:
    path = DATA_DIR / name
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def data_age_note(payload: dict, live: bool) -> str:
    """Honest freshness label for the UI: 'Live (60s cache)' when the API
    answered, otherwise the fallback file's own generation timestamp."""
    if live:
        return "Live (60s cache)"
    generated = payload.get("_generated_utc")
    if generated:
        return f"Offline fallback — data as of {generated}"
    return "Offline fallback"


@st.cache_data(ttl=60, show_spinner=False)
def load_teams() -> tuple:
    """(teams DataFrame, note). Live teams endpoint, else committed copy."""
    try:
        import parsing

        raw = espn_api.get_teams()
        rows = parsing.parse_teams(raw)
        if rows:
            return (pd.DataFrame(rows), data_age_note({}, True))
    except Exception:
        pass
    payload = _read_fallback("dashboard_teams.json")
    return (pd.DataFrame(payload.get("teams", [])), data_age_note(payload, False))


@st.cache_data(ttl=60, show_spinner=False)
def load_standings(season: str) -> tuple:
    """(standings DataFrame, note) for one season label like '2025-26'."""
    try:
        import parsing

        raw = espn_api.get_standings(espn_api.season_param(season))
        rows = parsing.parse_standings(raw)
        if rows:
            return (pd.DataFrame(rows), data_age_note({}, True))
    except Exception:
        pass
    payload = _read_fallback("dashboard_standings.json")
    rows = payload.get("standings", [])
    note = data_age_note(payload, False)
    if rows and payload.get("season") != season:
        # The committed fallback carries ONE season (the newest with data --
        # e.g. 2025-26 while 2026-27 hasn't tipped off). Show it rather than
        # an empty tab, but SAY which season it is, so the header can't
        # misrepresent 2025-26 numbers as the requested season's.
        note = (f"{note} — showing {payload.get('season')} standings "
                f"({season} has none yet)")
    return (pd.DataFrame(rows), note)


@st.cache_data(ttl=300, show_spinner=False)
def load_schedule(season: str) -> tuple:
    """(games DataFrame, note). Local-first: the schedule is collector output
    refreshed daily (30 live calls would be too slow for a page render), with
    live TODAY games layered on top by load_scoreboard()."""
    payload = _read_fallback("dashboard_schedule.json")
    games = payload.get("games", [])
    if games and payload.get("season") == season:
        return (pd.DataFrame(games), data_age_note(payload, False))
    return (pd.DataFrame(), data_age_note(payload, False))


@st.cache_data(ttl=60, show_spinner=False)
def load_scoreboard(dates: str) -> tuple:
    """(scoreboard events DataFrame, note) for a YYYYMMDD date -- genuinely
    live ESPN call, used for 'games today'; empty (not an error) when there
    are none, which is the normal offseason state."""
    try:
        payload = espn_api.get_scoreboard(dates)
        events = payload.get("events") or []
        rows = []
        for event in events:
            comps = event.get("competitions") or []
            if not comps:
                continue
            comp = comps[0]
            sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            rows.append({
                "game_id": event.get("id"),
                "date": event.get("date"),
                "status": ((comp.get("status") or {}).get("type") or {}).get("name"),
                "home": sides.get("home", {}).get("team", {}).get("abbreviation"),
                "away": sides.get("away", {}).get("team", {}).get("abbreviation"),
                "home_score": (sides.get("home", {}).get("score") or {}).get("displayValue"),
                "away_score": (sides.get("away", {}).get("score") or {}).get("displayValue"),
            })
        return (pd.DataFrame(rows), data_age_note({}, True))
    except Exception:
        return (pd.DataFrame(), "Offline fallback")


@st.cache_data(ttl=300, show_spinner=False)
def load_positions() -> pd.DataFrame:
    """Current player -> position map (for the optimizer pool)."""
    payload = _read_fallback("dashboard_positions.json")
    return pd.DataFrame(payload.get("players", []))


@st.cache_data(ttl=300, show_spinner=False)
def load_projections() -> tuple:
    """(projections DataFrame, note) -- the committed fallback the refresh
    script wrote (computed WHERE the raw data exists), since Streamlit Cloud
    can't rebuild 20k game files to project itself."""
    payload = _read_fallback("dashboard_projections.json")
    projections = payload.get("projections", [])
    df = pd.DataFrame(projections)
    if df.empty:
        return (df, data_age_note(payload, False))

    # Display-friendly enrichment: projections carry raw ids (Streamlit Cloud
    # can't rebuild names from 20k game files), so join the committed
    # position map for player names and the team list for abbreviations --
    # otherwise the dashboard shows bare numeric ids where a human wants
    # names.
    positions = pd.DataFrame(
        _read_fallback("dashboard_positions.json").get("players", [])
    )
    if not positions.empty and "player_name" in positions.columns:
        df = df.merge(
            positions[["player_id", "player_name"]].drop_duplicates("player_id"),
            on="player_id", how="left",
        )
    else:
        df["player_name"] = df["player_id"]

    teams = pd.DataFrame(_read_fallback("dashboard_teams.json").get("teams", []))
    if not teams.empty:
        abbrev = teams.set_index("team_id")["abbrev"].to_dict()
        df["team_abbrev"] = df["team_id"].map(abbrev)
        df["opponent_abbrev"] = df["opponent_id"].map(abbrev)
    else:
        df["team_abbrev"] = df["team_id"].astype(str)
        df["opponent_abbrev"] = df["opponent_id"].astype(str)

    return (df, data_age_note(payload, False))


@st.cache_data(ttl=3600, show_spinner=False)
def load_metrics() -> dict:
    path = REPO_ROOT / "models" / "metrics.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(ttl=3600, show_spinner=False)
def load_leaderboards() -> tuple:
    """(leaders DataFrame, note) -- last completed season's per-player totals."""
    path = PROCESSED_DIR / "dashboard_leaderboards.json"
    if not path.exists():
        return (pd.DataFrame(), "")
    # The file is a JSON object envelope (season/leaders/source/_generated_utc).
    with open(path, encoding="utf-8") as f:
        envelope = json.load(f)
    return (pd.DataFrame(envelope.get("leaders", [])), data_age_note(envelope, False))


def current_season() -> str:
    return espn_api.current_season_label()


def latest_season_with_data() -> str:
    """Newest season label that has a committed schedule (or collected raw);
    used as the default season selector value in the offseason, when the
    'current' season's schedule may not exist yet."""
    current = current_season()
    if (DATA_DIR / "dashboard_schedule.json").exists():
        payload = _read_fallback("dashboard_schedule.json")
        if payload.get("games"):
            return payload.get("season", current)
    return current


def season_options() -> list:
    """Selectable seasons for the sidebar: every collected/committed season,
    newest first, plus the current label always available."""
    labels = set()
    for name in ("dashboard_schedule.json", "dashboard_standings.json"):
        payload = _read_fallback(name)
        if payload.get("season"):
            labels.add(payload["season"])
    raw_dir = DATA_DIR / "raw"
    if raw_dir.exists():
        for entry in raw_dir.iterdir():
            if entry.is_dir() and "-" in entry.name and entry.name[:4].isdigit():
                labels.add(entry.name)
    labels.add(current_season())
    return sorted(labels, reverse=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
