"""NBA Analytics -- Model & History page.

- **Model Performance** -- validation metrics straight from models/metrics.json
  (single-stage vs two-stage vs the naive rolling-5 baseline), with an MAE
  comparison chart
- **Season Leaders**    -- per-player totals from the committed leaderboards
  fallback (fantasy points under the default scoring + full shooting splits:
  FG/3P made-attempt counts, FG%, eFG%, TS%), podium-styled
- **Court View**         -- the selected stat's leaders on a CSS-only
  hardwood court: 2 G / 2 F / 1 C filled in rank order by each player's
  real position, everyone else on a bench strip, hover a card for the full
  per-game line incl. shooting splits. Follows the sidebar's season
  selector (every collected season, 2010-11 -> present).

This page deliberately shows the naive baseline alongside the model: if the
model can't beat "last 5 games' average" on a screen, the number on screen
isn't worth a manager's trust.
"""

import pandas as pd
import streamlit as st

import shared

import awards

st.set_page_config(page_title="Model & History", page_icon="📈", layout="wide")

shared.inject_css()
st.title("Model & History")

with st.sidebar:
    st.markdown("### Settings")
    options = shared.season_options()
    season = st.selectbox(
        "Season", options,
        index=shared.default_season_index(options) if options else None,
        help="Leads with the newest season that has collected games; every "
             "year from 2010-11 through the upcoming season is selectable. "
             "Drives Court View.",
    )
    st.caption(f"Today (UTC): {shared.now_utc():%Y-%m-%d}")
    st.markdown("---")
    shared.sidebar_games(shared.load_games_by_season(), options)

tabs = st.tabs(["Model Performance", "Season Leaders", "Court View"])

# ---------------------------------------------------------------------------
# Model Performance
# ---------------------------------------------------------------------------

with tabs[0]:
    metrics = shared.load_metrics()
    if not metrics:
        st.info("No models/metrics.json yet — train the model first: "
                "`python src/model/features.py && python src/model/train.py`.")
    else:
        single = metrics.get("single_stage", {})
        two = metrics.get("two_stage", {})
        naive = metrics.get("naive_baseline", {})

        shared.hero(
            metrics.get("validation_season", "?"),
            f"train rows: {metrics.get('n_train_rows', 0):,}",
            extra=f"holdout untouched: {metrics.get('final_holdout_season')}",
        )

        comparison = pd.DataFrame([
            {"Model": "Single-stage (used at inference)",
             "MAE": single.get("mae"), "RMSE": single.get("rmse")},
            {"Model": "Two-stage (P(plays) × E[pts|plays])",
             "MAE": two.get("mae"), "RMSE": two.get("rmse")},
            {"Model": "Naive: own rolling-5 average",
             "MAE": naive.get("mae"), "RMSE": naive.get("rmse")},
        ])

        shared.section("Validation comparison")
        st.bar_chart(comparison.set_index("Model")["MAE"])
        st.dataframe(
            comparison.style.format({"MAE": "{:.3f}", "RMSE": "{:.3f}"}),
            width="stretch", hide_index=True,
        )

        if single and naive and single.get("mae", 1) < naive.get("mae", 0):
            delta = naive["mae"] - single["mae"]
            st.success(
                f"Model beats the naive baseline by {delta:.3f} MAE on "
                f"{metrics.get('validation_season')}."
            )
        elif single and naive:
            st.warning("Model does NOT beat the naive baseline on this "
                       "validation season — treat projections accordingly.")

        played = metrics.get("single_stage_played_only", {})
        dnp = metrics.get("single_stage_dnp_only", {})
        if played and dnp:
            shared.section("Error budget split")
            col1, col2 = st.columns(2)
            col1.metric("MAE · players who played",
                        f"{played.get('mae', float('nan')):.3f}")
            col2.metric("MAE · did-not-play rows",
                        f"{dnp.get('mae', float('nan')):.3f}")
            st.caption(
                "The FPL project found its error budget concentrated in "
                "non-playing rows; this split shows where this model's sits."
            )

        auc = two.get("play_classifier_auc")
        if auc:
            st.caption(f"Play classifier ROC-AUC: {auc:.3f}")

# ---------------------------------------------------------------------------
# Season Leaders
# ---------------------------------------------------------------------------

with tabs[1]:
    leaders, note = shared.load_leaderboards()
    if leaders.empty:
        st.info("No committed leaderboard data yet — run "
                "`python src/collector/refresh_dashboard_fallbacks.py` "
                "where collector data exists.")
    else:
        st.caption(f"Source: {note}")
        rank_labels = {"fantasy_points": "Fantasy pts", "pts": "Points",
                       "reb": "Rebounds", "ast": "Assists"}
        if "fg3m" in leaders.columns:
            rank_labels["fg3m"] = "3PM"
        metric = st.radio("Rank by", list(rank_labels), horizontal=True,
                          format_func=rank_labels.__getitem__)
        top = leaders.sort_values(metric, ascending=False).head(25).copy()
        top = top.reset_index(drop=True)
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        top["rank"] = [medals.get(i, str(i + 1)) for i in top.index]
        top["fantasy_points"] = top["fantasy_points"].round(1)
        shared.section("Top 25")
        columns = {
            "rank": "", "player_name": "Player", "team_abbrev": "Team",
            "games": "GP", "minutes": "MIN", "pts": "PTS", "reb": "REB",
            "ast": "AST", "fantasy_points": "FPTS",
        }
        keep = ["rank", "player_name", "team_abbrev", "games", "minutes",
                "pts", "reb", "ast", "fantasy_points"]
        if "fgm" in top.columns:
            top["FG"] = (top["fgm"].astype(int).astype(str) + "-"
                         + top["fga"].astype(int).astype(str))
            top["3P"] = (top["fg3m"].astype(int).astype(str) + "-"
                         + top["fg3a"].astype(int).astype(str))
            for col in ("fgp", "fg3p", "efg", "ts"):
                top[col] = pd.to_numeric(top[col], errors="coerce")
            keep += ["FG", "fgp", "3P", "fg3p", "efg", "ts"]
            columns.update({"FG": "FG", "fgp": "FG%", "3P": "3P",
                            "fg3p": "3P%", "efg": "eFG%", "ts": "TS%"})
        st.dataframe(
            top[keep].rename(columns=columns),
            width="stretch", hide_index=True,
        )
        st.caption("Fantasy points = default configurable weights "
                   "(src/model/scoring.py) applied to season totals; "
                   "FG/3P = made-attempt, eFG% = (FGM + 0.5·3PM) / FGA, "
                   "TS% = PTS / (2·(FGA + 0.44·FTA)) — computed from the "
                   "collected box scores.")

# ---------------------------------------------------------------------------
# Court View: the stat race's leaders on a real court formation
# ---------------------------------------------------------------------------

with tabs[2]:
    awards_payload, awards_note = shared.load_awards(season)
    st.caption(f"Source: {awards_note}")
    _shown = awards_payload.get("season")
    if _shown and _shown != season:
        st.warning(
            f"{season} has no collected games yet — Court View shows "
            f"{_shown}, the newest season with collected data."
        )
    if not awards_payload.get("leaders"):
        st.info("No committed leader data yet — run "
                "`python src/collector/refresh_dashboard_fallbacks.py` "
                "where collected box scores exist.")
    else:
        leaders_by_stat = awards_payload.get("leaders") or {}
        stat_keys = [k for k in awards.STAT_CATEGORIES
                     if k in leaders_by_stat] or list(leaders_by_stat)
        stat = st.radio(
            "Category", stat_keys, horizontal=True, key="court_stat",
            format_func=lambda k: (f"{awards.STAT_LABELS.get(k, k)} "
                                   f"({awards.STAT_ABBR.get(k, k)})"),
        )
        rows = leaders_by_stat.get(stat) or []
        if not rows:
            st.caption(
                f"No qualified {awards.STAT_LABELS.get(stat, stat).lower()} "
                f"leaders yet — needs ≥{awards_payload.get('min_games', '?')} "
                "GP."
            )
        else:
            shared.render_court(
                rows,
                shared.load_positions(),
                stat,
                int(awards_payload.get("min_games") or 8),
                str(awards_payload.get("season", "—")),
            )
        st.caption(
            "Formation 2 G · 2 F · 1 C mirrors a fantasy-style starting "
            "lineup slot structure (backend optimizer untouched in "
            "src/model/optimizer.py); positions are ESPN's coarse G/F/C "
            "roster buckets, so PG/SG land in G and SF/PF in F."
        )
