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

import html
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

# Award display constants (STAT_LABELS/RACE_FORMULAS/...) come from the module
# that computes them, so the UI can never show a label/formula that disagrees
# with the math actually run.
import awards  # noqa: E402

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

/* Award-race + stat-leader rows (Awards Ladder) */
.na-race-row {
  display: flex; align-items: center; gap: 12px; padding: 8px 12px;
  border: 1px solid var(--na-border); background: var(--na-card);
  border-radius: 12px; margin-bottom: 6px;
}
.na-race-row .na-rank {
  width: 34px; flex: none; text-align: center;
  font-weight: 800; opacity: 0.75;
}
.na-race-row .na-rbody { flex: 1; min-width: 0; }
.na-race-row .na-rname {
  font-weight: 700; display: flex; align-items: center; gap: 6px;
}
.na-race-row .na-rmeta {
  opacity: 0.7; font-size: 0.8rem; margin-top: 2px;
}
.na-race-row .na-rscore {
  margin-left: auto; font-weight: 800; font-size: 1.05rem;
  color: var(--na-gold); white-space: nowrap;
}

/* Real photos: headshot <img> over a team-logo CSS background. The fallback
   shows through when the CDN 404s -- no JS (Streamlit sanitizes onerror
   away), no broken-image icon (no alt attribute), no cropped heads
   (object-fit: contain). */
.na-shot {
  position: relative; display: inline-block; overflow: hidden;
  vertical-align: middle; flex: none;
  background-size: 78%; background-repeat: no-repeat;
  background-position: center; background-color: rgba(255, 255, 255, 0.06);
}
.na-shot img {
  display: block; width: 100%; height: 100%; object-fit: contain;
}
.na-logo {
  display: inline-block; vertical-align: middle; flex: none;
  background-size: contain; background-repeat: no-repeat;
  background-position: center;
}

/* Court View: CSS-only hardwood, slot rows, hover stat tooltips */
.na-court {
  position: relative; border-radius: 12px; padding: 16px 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
}
.na-court-head {
  position: relative; z-index: 1; text-align: center; color: #fff;
  font-size: 12px; font-weight: 700; letter-spacing: 0.06em;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35); margin-bottom: 6px;
}
.na-court-row {
  position: relative; z-index: 1; display: flex; justify-content: center;
  gap: 14px; margin: 14px 0; flex-wrap: wrap;
}
.na-court-card {
  position: relative; width: 104px; padding: 8px 6px; text-align: center;
  background: rgba(18, 14, 10, 0.86);
  border: 1px solid rgba(255, 255, 255, 0.25); border-radius: 10px;
}
.na-court-card .na-cname {
  font-weight: 700; font-size: 0.78rem; line-height: 1.15; margin-top: 4px;
}
.na-court-card .na-cteam {
  display: flex; align-items: center; justify-content: center; gap: 4px;
  font-size: 0.7rem; opacity: 0.8; margin-top: 3px;
}
.na-court-card .na-cstat {
  font-weight: 800; color: var(--na-gold);
  font-size: 0.92rem; margin-top: 3px;
}
.na-bench-label {
  text-align: center; font-size: 11px; font-weight: 700;
  letter-spacing: 0.08em; opacity: 0.7; margin-top: 12px;
}
.na-bench {
  display: flex; justify-content: center; gap: 14px; flex-wrap: wrap;
  margin-top: 8px; padding-top: 10px;
  border-top: 1px dashed var(--na-border);
}
.na-tip {
  display: none; position: absolute; bottom: calc(100% + 8px); left: 50%;
  transform: translateX(-50%); z-index: 30;
  background: rgba(15, 15, 15, 0.97); color: #f0f0f0;
  border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 8px;
  padding: 8px 10px; font-size: 10.5px; line-height: 1.5;
  text-align: left; white-space: normal; max-width: 250px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.4);
}
.na-tip::after {
  content: ""; position: absolute; top: 100%; left: 50%;
  transform: translateX(-50%); border: 6px solid transparent;
  border-top-color: rgba(15, 15, 15, 0.97);
}
.na-court-card:hover .na-tip { display: block; }

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


def team_logo_url(abbrev: str) -> str:
    """ESPN CDN team logo (verified 200 for upper/lowercase abbrevs). Empty
    when the abbrev is missing so callers render nothing instead of a URL
    that is guaranteed to fail."""
    if not abbrev:
        return ""
    return f"https://a.espncdn.com/i/teamlogos/nba/500/{abbrev}.png"


def headshot_url(player_id) -> str:
    """ESPN CDN player headshot (200 for real ids, 404 for fabricated ones --
    which is exactly why headshot_html wraps it in the CSS fallback below)."""
    if player_id is None or player_id == "":
        return ""
    return f"https://a.espncdn.com/i/headshots/nba/players/full/{player_id}.png"


def team_logo_html(abbrev: str, size: int = 18) -> str:
    """Team logo as a CSS background-image span, deliberately NOT an <img>:
    a 404 then just paints nothing -- no broken-image icon, no alt-text
    oddities. Single logical line (see race_row_html's docstring)."""
    url = team_logo_url(abbrev)
    if not url:
        return ""
    return (f'<span class="na-logo" style="width: {size}px; height: {size}px; '
            f'background-image: url({url});"></span>')


def headshot_html(player_id, team_abbrev: str, size: int = 44) -> str:
    """Player headshot with FPL-Analytics' proven CSS-only fallback: the
    <img> sits over the team logo drawn as the wrapper's background-image,
    object-fit: contain, and deliberately carries NO alt attribute.

    Why this shape: Streamlit sanitizes st.markdown(unsafe_allow_html=True),
    so an onerror JS handler never fires reliably (broken-image icons showed
    through in FPL-Analytics), and `alt` would print alt text over the
    fallback. A failed <img> has no visible content of its own, so the
    team-logo background shows through instead -- no JS anywhere, and the
    fallback is the player's real team, not a generic gray box."""
    src = headshot_url(player_id)
    bg = f"background-image: url({team_logo_url(team_abbrev)}); " if team_abbrev else ""
    if not src:
        return (f'<span class="na-shot" style="width: {size}px; '
                f'height: {size}px; {bg}"></span>')
    return (f'<span class="na-shot" style="width: {size}px; height: {size}px; {bg}">'
            f'<img src="{src}" '
            f'style="width: 100%; height: 100%; object-fit: contain;" '
            f'loading="lazy"></span>')


def race_meta(race: str, row: dict) -> str:
    """Race-specific stat line under a player's name (single logical line).

    Each branch shows only fields the race's row actually carries, so a
    missing record (traded player, unmatched abbrev) drops the fragment
    instead of printing None."""
    team = str(row.get("team_abbrev") or "—")
    gp = row.get("gp", 0)
    if race == "mvp":
        rec = (f" · {row['wins']}-{row['losses']} ({row['win_pct']:.3f})"
               if row.get("wins") is not None else "")
        fg = (f" · {row['fgp']}% FG" if row.get("fgp") is not None else "")
        return (f"{team} · {gp} GP · {row.get('ppg', 0)} PPG · "
                f"{row.get('rpg', 0)} RPG · {row.get('apg', 0)} APG{rec}{fg}")
    if race == "dpoy":
        opp = (f" · {row['opp_pg']} opp pts/g"
               if row.get("opp_pg") is not None else "")
        return (f"{team} · {gp} GP · {row.get('stocks_pg', 0)} stocks "
                f"({row.get('spg', 0)} STL · {row.get('bpg', 0)} BLK){opp}")
    if race == "sixth_man":
        start_pct = 100 * float(row.get("start_pct") or 0)
        return (f"{team} · {gp} GP · {row.get('starts', 0)} starts "
                f"({start_pct:.0f}%) · {row.get('ppg', 0)} PPG · "
                f"{row.get('apg', 0)} APG")
    return (f"{team} · {gp} GP · {row.get('ppg_prev', 0)} → "
            f"{row.get('ppg', 0)} PPG ({row.get('delta_ppg', 0):+.1f}) · "
            f"{row.get('gp_prev', 0)} GP last season")


def race_row_html(row: dict, race: str) -> str:
    """One award-race ladder row: medal/rank, real headshot (team-logo CSS
    fallback behind it), name + team logo, the race's stat line, the score.

    IMPORTANT: single logical line, no leading indentation anywhere in the
    string -- this goes through st.markdown(unsafe_allow_html=True), which
    parses Markdown BEFORE HTML, and Markdown renders a newline followed by
    4+ spaces as a visible code block (the exact trap FPL-Analytics' cards
    hit). The adjacent f-string literals below concatenate into ONE line at
    runtime; the Python source's indentation is not part of the string."""
    rank = int(row.get("rank") or 0)
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, str(rank))
    name = html.escape(str(row.get("player_name") or "Unknown"))
    team = str(row.get("team_abbrev") or "")
    score = float(row.get("score") or 0)
    score_text = f"{score:+.1f}" if race == "mip" else f"{score:.1f}"
    return (f'<div class="na-race-row">'
            f'<div class="na-rank">{medal}</div>'
            f'{headshot_html(row.get("player_id"), team, 44)}'
            f'<div class="na-rbody">'
            f'<div class="na-rname">{name}{team_logo_html(team, 16)}</div>'
            f'<div class="na-rmeta">{race_meta(race, row)}</div>'
            f'</div>'
            f'<div class="na-rscore">{score_text}</div>'
            f'</div>')


def _shooting_fragments(row: dict) -> str:
    """' · 712-1580 FG (45.1%) · 268-702 3P (38.2%)' fragments for a leader
    row's meta line, empty when the box scores carry no attempts (older
    fixtures / DNP-heavy rows), so nothing prints as '0-0'."""
    bits = []
    if row.get("fga"):
        bits.append(f" · {row.get('fgm', 0)}-{row['fga']} FG "
                    f"({row.get('fgp')}%)")
    if row.get("fg3a"):
        bits.append(f" · {row.get('fg3m', 0)}-{row['fg3a']} 3P "
                    f"({row.get('fg3p')}%)")
    return "".join(bits)


def leader_row_html(row: dict, stat: str) -> str:
    """One stat-leader row: rank, headshot, name + team logo, games + season
    total for context + the shooting splits, headline per-game rate on the
    right. Same single-logical-line rule as race_row_html."""
    rank = int(row.get("rank") or 0)
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, str(rank))
    name = html.escape(str(row.get("player_name") or "Unknown"))
    team = str(row.get("team_abbrev") or "")
    total = int(row.get("total") or 0)
    label = awards.STAT_LABELS.get(stat, stat)
    abbr = awards.STAT_ABBR.get(stat, stat.upper())
    meta = (f"{team} · {row.get('gp', 0)} GP · {total:,} {label.lower()} "
            f"(season total){_shooting_fragments(row)}")
    return (f'<div class="na-race-row">'
            f'<div class="na-rank">{medal}</div>'
            f'{headshot_html(row.get("player_id"), team, 44)}'
            f'<div class="na-rbody">'
            f'<div class="na-rname">{name}{team_logo_html(team, 16)}</div>'
            f'<div class="na-rmeta">{meta}</div>'
            f'</div>'
            f'<div class="na-rscore">{row.get("per_game", 0)} {abbr}</div>'
            f'</div>')


def court_card_html(row: dict, bucket: str, stat: str) -> str:
    """One Court View player card: headshot over team-logo fallback, name,
    team logo + position chip, headline per-game rate, and a CSS-only hover
    tooltip (.na-tip) with the player's full per-game line, shooting splits
    and season total.

    Single logical line -- see race_row_html's docstring."""
    name = html.escape(str(row.get("player_name") or "Unknown"))
    team = str(row.get("team_abbrev") or "")
    chip = slot_chip(bucket) if bucket else ""
    total = int(row.get("total") or 0)
    total_label = awards.STAT_LABELS.get(stat, stat).lower()
    tip = (f"{row.get('gp', 0)} GP · {row.get('mpg', 0)} MIN · "
           f"{row.get('ppg', 0)} PTS · {row.get('rpg', 0)} REB · "
           f"{row.get('apg', 0)} AST · {row.get('spg', 0)} STL · "
           f"{row.get('bpg', 0)} BLK{_shooting_fragments(row)} · "
           f"{total:,} {total_label} total")
    abbr = awards.STAT_ABBR.get(stat, stat.upper())
    return (f'<div class="na-court-card">'
            f'<div class="na-tip">{tip}</div>'
            f'{headshot_html(row.get("player_id"), team, 56)}'
            f'<div class="na-cname">{name}</div>'
            f'<div class="na-cteam">{team_logo_html(team, 14)}{team}{chip}</div>'
            f'<div class="na-cstat">{row.get("per_game", 0)} {abbr}</div>'
            f'</div>')


def _court_bucket(raw_pos: str) -> str:
    """ESPN's coarse roster position -> lineup bucket: PG/SG -> G, SF/PF -> F,
    C -> C. Anything else (UNK, missing) -> '' so the player sits on the
    bench strip rather than being forced into a slot he doesn't play."""
    pos = (raw_pos or "").strip().upper()
    if pos in ("PG", "SG", "G"):
        return "G"
    if pos in ("SF", "PF", "F"):
        return "F"
    if pos == "C":
        return "C"
    return ""


def goat_row_html(row: dict) -> str:
    """One GOAT-ladder row: rank/medal, real headshot (team-logo CSS
    fallback), career line, race titles won in this repo's races, the three
    component scores, and the headline GOAT score on the right.

    Single logical line -- see race_row_html's docstring."""
    rank = int(row.get("rank") or 0)
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, str(rank))
    name = html.escape(str(row.get("player_name") or "Unknown"))
    team = str(row.get("team_abbrev") or "")
    titles = row.get("titles") or {}
    title_bits = [f"{awards.RACE_EMOJI.get(race, '')}×{count}"
                  for race, count in titles.items() if count]
    titles_text = " · ".join(title_bits) if title_bits else "no race titles"
    meta = (f"{team} · {row.get('seasons', 0)} seasons · "
            f"{row.get('gp', 0)} GP · {row.get('pts', 0):,} PTS · "
            f"{row.get('ppg', 0)} PPG · {titles_text} · "
            f"prod {row.get('production', 0)} · awards "
            f"{row.get('awards_score', 0)} · peak {row.get('peak_score', 0)}")
    return (f'<div class="na-race-row">'
            f'<div class="na-rank">{medal}</div>'
            f'{headshot_html(row.get("player_id"), team, 44)}'
            f'<div class="na-rbody">'
            f'<div class="na-rname">{name}{team_logo_html(team, 16)}</div>'
            f'<div class="na-rmeta">{meta}</div>'
            f'</div>'
            f'<div class="na-rscore">{row.get("score", 0)}</div>'
            f'</div>')


def render_court(leaders: list, positions, stat: str, min_games: int,
                 season: str) -> None:
    """Draw a stat race's top players on a real hardwood court: rank order
    fills a 2 G / 2 F / 1 C formation from the committed roster map (C row
    first -- the basket is at the top), and everyone else -- formation full,
    or position unmapped -- lands on the bench strip below the court, never
    forced into a slot. Hovering a card shows the full per-game line (CSS
    only). Missing/empty leaders render nothing (the caller says why).

    Every HTML string here is a single logical line -- see race_row_html's
    docstring on why (Markdown-then-HTML via st.markdown)."""
    if not leaders:
        return

    pos_by_id = {}
    if positions is not None and not getattr(positions, "empty", True):
        if "position" in positions.columns:
            pos_by_id = dict(zip(positions["player_id"], positions["position"]))

    capacity = {"G": 2, "F": 2, "C": 1}
    slots = {"G": [], "F": [], "C": []}
    bench = []
    for row in leaders:
        bucket = _court_bucket(str(pos_by_id.get(row.get("player_id"), "") or ""))
        if bucket and len(slots[bucket]) < capacity[bucket]:
            slots[bucket].append(row)
        else:
            bench.append((row, bucket))

    rows_html = ""
    for bucket in ("C", "F", "G"):
        if not slots[bucket]:
            continue
        cards = "".join(court_card_html(r, bucket, stat) for r in slots[bucket])
        rows_html += f'<div class="na-court-row">{cards}</div>'

    # Court markings, entirely CSS gradients layered under the wood grain
    # (no extra DOM, so the court stays one logical line -- see above): a
    # center circle, a half-court line, the boundary, and both painted keys.
    markings = (
        "radial-gradient(circle at 50% 50%, transparent 40px, "
        "rgba(255,255,255,0.45) 41px, rgba(255,255,255,0.45) 43px, transparent 44px), "
        "linear-gradient(rgba(255,255,255,0.45), rgba(255,255,255,0.45)) 0 50% / 100% 2px no-repeat, "
        "linear-gradient(rgba(255,255,255,0.4), rgba(255,255,255,0.4)) 0 0 / 100% 3px no-repeat, "
        "linear-gradient(rgba(255,255,255,0.4), rgba(255,255,255,0.4)) 0 100% / 100% 3px no-repeat, "
        "linear-gradient(rgba(255,255,255,0.4), rgba(255,255,255,0.4)) 0 0 / 3px 100% no-repeat, "
        "linear-gradient(rgba(255,255,255,0.4), rgba(255,255,255,0.4)) 100% 0 / 3px 100% no-repeat, "
        "linear-gradient(rgba(255,255,255,0.3), rgba(255,255,255,0.3)) 33% 0 / 34% 72px no-repeat, "
        "linear-gradient(rgba(255,255,255,0.3), rgba(255,255,255,0.3)) 33% 100% / 34% 72px no-repeat"
    )
    label = awards.STAT_LABELS.get(stat, stat).upper()
    court = (f'<div class="na-court" style="background: {markings}, '
             f'repeating-linear-gradient(90deg, #b57a3c 0 46px, #ab7136 46px 92px);">'
             f'<div class="na-court-head">🏀 {season} · {label} LEADERS · '
             f'2 G · 2 F · 1 C</div>{rows_html}</div>')
    st.markdown(court, unsafe_allow_html=True)

    if bench:
        bench_cards = "".join(court_card_html(r, b, stat) for r, b in bench)
        st.markdown(
            f'<div class="na-bench-label">🪑 BENCH · {len(bench)} not slotted '
            f'(formation full or position unmapped)</div>'
            f'<div class="na-bench">{bench_cards}</div>',
            unsafe_allow_html=True,
        )
    st.caption(
        f"Top {len(leaders)} in {awards.STAT_LABELS.get(stat, stat).lower()} "
        f"per game, qualified at ≥{min_games} GP ({season}); positions from "
        f"the committed roster map (coarse G/F/C). Hover a card for the "
        f"player's full per-game line."
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


def standings_have_results(df: pd.DataFrame) -> bool:
    """Whether a standings table has at least one completed game.

    ESPN returns 30 structurally valid all-zero rows for a season before
    tip-off. Treating those as a live 0-0 table is misleading, so both the
    live loader and the committed fallback use this same guard.
    """
    if df.empty or "wins" not in df.columns or "losses" not in df.columns:
        return False
    wins = pd.to_numeric(df["wins"], errors="coerce").fillna(0)
    losses = pd.to_numeric(df["losses"], errors="coerce").fillna(0)
    return bool((wins + losses).sum() > 0)


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
        if rows and standings_have_results(pd.DataFrame(rows)):
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
    elif rows and not standings_have_results(pd.DataFrame(rows)):
        return (pd.DataFrame(), f"{season} standings not started yet")
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
    """Current player -> position map (optimizer pool + Court View formation)."""
    payload = _read_fallback("dashboard_positions.json")
    return pd.DataFrame(payload.get("players", []))


@st.cache_data(ttl=300, show_spinner=False)
def load_awards(season: str = None) -> tuple:
    """(season payload, note) -- award races + stat leaders computed LOCALLY
    from the collected box scores (src/collector/awards.py) and committed as
    data/dashboard_awards.json. There is deliberately no live API for these,
    so the note states the computation stamp rather than implying live.

    The committed file carries EVERY collected season under "seasons"
    (2010-11 -> present); `season` picks one, falling back to the newest
    season at or before the request (sidebar can select 2026-27 before any
    games exist) and SAYING so, mirroring load_standings' honesty rule.
    The legacy single-season envelope (no "seasons" key) still loads."""
    payload = _read_fallback("dashboard_awards.json")
    seasons = payload.get("seasons")
    if not seasons:
        if not payload.get("races"):
            return (payload, "No committed award data yet — computed where "
                              "raw box scores exist "
                              "(refresh_dashboard_fallbacks.py)")
        data, actual = payload, payload.get("season")
    else:
        requested = season or ""
        if requested in seasons:
            actual = requested
        elif not requested:
            actual = max(seasons)
        else:
            # Nearest season at or before the request (2026-27 before tip-off
            # -> 2025-26); before the window entirely -> the earliest season.
            at_or_before = [s for s in seasons if s <= requested]
            actual = max(at_or_before) if at_or_before else min(seasons)
        data = seasons[actual] or {}
        if not data.get("races"):
            return (data, "No committed award data yet — computed where "
                           "raw box scores exist "
                           "(refresh_dashboard_fallbacks.py)")
    stamp = payload.get("_generated_utc")
    note = (f"Computed from collected box scores — as of {stamp}" if stamp
            else "Computed from collected box scores")
    if season and actual != season:
        note = (f"{note} — showing {actual} "
                f"({season} has no collected games yet)")
    return (data, note)


@st.cache_data(ttl=300, show_spinner=False)
def load_awards_career() -> tuple:
    """(alltime dict, goat dict, window dict, note) -- the cross-season
    sections of the committed awards file: career totals across every
    collected season (2010-11 -> present), the GOAT ladder built from them,
    and the window descriptor. Same honest stamp-only freshness as
    load_awards (computed locally, never live)."""
    payload = _read_fallback("dashboard_awards.json")
    stamp = payload.get("_generated_utc")
    note = (f"Computed from collected box scores — as of {stamp}" if stamp
            else "Computed from collected box scores")
    return (payload.get("alltime") or {}, payload.get("goat") or {},
            payload.get("window") or {}, note)


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
    note = data_age_note(envelope, False)
    if envelope.get("season"):
        note = f"{note} — season {envelope['season']}"
    return (pd.DataFrame(envelope.get("leaders", [])), note)


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
