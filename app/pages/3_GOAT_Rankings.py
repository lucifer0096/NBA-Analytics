"""NBA Analytics -- GOAT Rankings page (all NBA history).

The ladder is built from ESPN's ALL-NBA-HISTORY career lines and official
award honours (src/collector/history.py + awards.py, committed as
data/dashboard_awards.json) -- every season a player ever played, NOT just
this repo's 16-season collection window. Transparent homegrown composite:
the exact formula prints verbatim on screen, every honour weight is listed,
championships are a bounded component, and any component the data can't
support prints as `—` with its gap named instead of being silently estimated.
"""

import streamlit as st

import shared

import awards

st.set_page_config(page_title="GOAT Rankings", page_icon="🐐", layout="wide")

shared.inject_css()
st.title("GOAT Rankings")

with st.sidebar:
    st.markdown("### Data inventory")
    shared.sidebar_games(shared.load_games_by_season(),
                         shared.season_options())

_, goat, window, career_note = shared.load_awards_career()
goat_rows = goat.get("rows") or []
if not goat_rows:
    st.info("No qualified players yet: the GOAT ladder needs players "
            f"with ≥{goat.get('min_career_gp', awards.GOAT_MIN_CAREER_GP)} "
            "career games (`python src/collector/"
            "refresh_dashboard_fallbacks.py`).")
else:
    st.caption(f"Source: {career_note}")
    st.caption(
        "Homegrown composite: NOT an official NBA ranking, award, or "
        "any vendor's rating. "
        f"{goat.get('formula') or awards.GOAT_FORMULA}"
    )
    st.caption(
        "Built from ESPN's all-NBA-history career lines and official award "
        f"honours, covering every season, not just this repo's "
        f"{window.get('seasons', 16)}-season collection window "
        f"({window.get('first', '2010-11')} → {window.get('last', '—')}, "
        "which still supplies peak-season context). Qualified at "
        f"≥{goat.get('min_career_gp', awards.GOAT_MIN_CAREER_GP)} career GP; "
        "career boards elsewhere additionally require ≥41 GP."
    )
    shared.section("🐐 GOAT ladder · all-NBA-history · top "
                   f"{len(goat_rows)}")
    for row in goat_rows:
        st.markdown(shared.goat_row_html(row), unsafe_allow_html=True)
    st.caption(
        "Honour chips are ESPN's 20 official award types × wins, heaviest "
        "first, exactly the weights printed in the formula; 🏆×N feeds the "
        + f"{awards.GOAT_WEIGHTS['championships']:.0%} championships "
        "component (champion-season rows plus verified official-record "
        "counts), and `—` means the count can't be computed honestly. "
        "prod/honours/peak/titles are the 0-100 component scores behind "
        "the headline number; a dropped component prints as `—` with its "
        "gap named, and the score rescales over the weights that ARE "
        "available."
    )
