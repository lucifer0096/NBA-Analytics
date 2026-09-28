"""NBA Analytics -- GOAT Rankings page (all league history, both leagues).

Two league tabs carry one ladder each: built from ESPN's ALL-HISTORY
career lines and official award honours (src/collector/history.py +
awards.py, committed as data/dashboard_awards.json per league) -- every
season a player ever played, NOT just this repo's collection window.
Transparent homegrown composite: the exact formula prints verbatim on
screen, every honour weight is listed, championships are a bounded
component, and any component the data can't support prints as `—` with
its gap named instead of being silently estimated.
"""

import streamlit as st

import shared

import leagues

import awards

st.set_page_config(page_title="GOAT Rankings", page_icon="🐐", layout="wide")

shared.inject_css()
st.title("GOAT Rankings")

with st.sidebar:
    st.markdown("### Data inventory")
    for league in ("nba", "wnba"):
        shared.sidebar_games(shared.load_games_by_season(league),
                             shared.season_options(league), league)


def _render_league(league: str) -> None:
    display = leagues.display(league)
    refresh_cmd = ("python src/collector/refresh_dashboard_fallbacks.py"
                   + ("" if league == "nba" else f" --league {league}"))

    _, goat, window, career_note = shared.load_awards_career(league)
    goat_rows = goat.get("rows") or []
    if not goat_rows:
        st.info("No qualified players yet: the GOAT ladder needs players "
                f"with ≥{goat.get('min_career_gp', awards.GOAT_MIN_CAREER_GP)} "
                f"career games (`{refresh_cmd}`).")
        return
    st.caption(f"Source: {career_note}")
    st.caption(
        f"Homegrown composite: NOT an official {display} ranking, award, or "
        "any vendor's rating. "
        f"{goat.get('formula') or awards.goat_formula(league)}"
    )
    st.caption(
        f"Built from ESPN's all-{display}-history career lines and official "
        "award honours, covering every season, not just this repo's "
        f"{window.get('seasons', 16)}-season collection window "
        f"({window.get('first', leagues.first_season(league))} → "
        f"{window.get('last', '—')}, which still supplies peak-season "
        "context and the window-only +/- inputs). Qualified at "
        f"≥{goat.get('min_career_gp', awards.GOAT_MIN_CAREER_GP)} career GP; "
        "career boards elsewhere additionally require ≥41 GP."
    )
    shared.section(f"🐐 GOAT ladder · all-{display}-history · top "
                   f"{len(goat_rows)}")
    for row in goat_rows:
        st.markdown(shared.goat_row_html(row, league), unsafe_allow_html=True)
    st.caption(
        f"Honour chips are ESPN's {len(awards.honours_weights(league))} "
        "official award types × wins, heaviest "
        "first, exactly the weights printed in the formula; 🏆×N feeds the "
        + f"{awards.GOAT_WEIGHTS['championships']:.0%} championships "
        "component (champion-season rows plus verified official-record "
        "counts), and `—` means the count can't be computed honestly. "
        "prod/honours/peak/titles are the 0-100 component scores behind "
        "the headline number; a dropped component prints as `—` with its "
        "gap named, and the score rescales over the weights that ARE "
        "available."
    )


league_tabs = shared.league_tabs()
with league_tabs["NBA"]:
    _render_league("nba")
with league_tabs["WNBA"]:
    _render_league("wnba")

# Corrupt-fallback warnings ride at the very END (see app.py).
shared.render_fallback_warnings()
