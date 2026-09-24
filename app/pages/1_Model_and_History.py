"""NBA Analytics -- Model & History page.

- **Model Performance** -- validation metrics straight from models/metrics.json
  (single-stage vs two-stage vs the naive rolling-5 baseline), with an MAE
  comparison chart
- **Season Leaders**    -- per-player totals from the committed leaderboards
  fallback (fantasy points under the default scoring), podium-styled
- **Lineup Optimizer**  -- PuLP best-lineup over a projected player pool,
  rendered as slot-colored cards with a big optimal-total readout

This page deliberately shows the naive baseline alongside the model: if the
model can't beat "last 5 games' average" on a screen, the number on screen
isn't worth a manager's trust.
"""

import pandas as pd
import streamlit as st

import shared

st.set_page_config(page_title="Model & History", page_icon="📈", layout="wide")

shared.inject_css()
st.title("Model & History")

tabs = st.tabs(["Model Performance", "Season Leaders", "Lineup Optimizer"])

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
        metric = st.radio("Rank by", list(rank_labels), horizontal=True,
                          format_func=rank_labels.__getitem__)
        top = leaders.sort_values(metric, ascending=False).head(25).copy()
        top = top.reset_index(drop=True)
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        top["rank"] = [medals.get(i, str(i + 1)) for i in top.index]
        top["fantasy_points"] = top["fantasy_points"].round(1)
        shared.section("Top 25")
        st.dataframe(
            top[["rank", "player_name", "team_abbrev", "games", "minutes",
                 "pts", "reb", "ast", "fantasy_points"]].rename(columns={
                "rank": "", "player_name": "Player", "team_abbrev": "Team",
                "games": "GP", "minutes": "MIN", "pts": "PTS", "reb": "REB",
                "ast": "AST", "fantasy_points": "FPTS",
            }),
            width="stretch", hide_index=True,
        )
        st.caption("Fantasy points = default configurable weights "
                   "(src/model/scoring.py) applied to season totals.")

# ---------------------------------------------------------------------------
# Lineup Optimizer
# ---------------------------------------------------------------------------

with tabs[2]:
    projections, note = shared.load_projections()
    if projections.empty:
        st.info("No committed projections to optimize over yet — see the "
                "Projections tab on the Home page.")
    else:
        st.caption(f"Projection source: {note}")
        st.markdown(
            "Best 7-man lineup (2 G · 2 F · 1 C · 2 UTIL) maximizing "
            "**projected fantasy points** — a PuLP MILP, so the answer is "
            "provably optimal for the pool and slots chosen."
        )

        projections = projections.copy()
        projections["day"] = projections["date"].astype(str).str[:10]
        game_days = sorted(projections["day"].unique())
        day = st.selectbox("Game day", game_days, key="opt_day")

        teams = shared.load_teams()
        day_pool = projections[projections["day"] == day]
        teams_today = sorted(set(day_pool["team_id"]))
        team_labels = {}
        if not teams.empty:
            name_by_id = teams.set_index("team_id")["display_name"].to_dict()
            team_labels = {t: name_by_id.get(t, str(t)) for t in teams_today}

        col_pool, col_slots = st.columns([2, 1])
        with col_pool:
            chosen_teams = st.multiselect(
                "Restrict pool to teams", teams_today,
                default=teams_today,
                format_func=lambda t: team_labels.get(t, str(t)),
            )
            pool_size = st.slider("Pool size (top-N by projection)", 10, 60, 30)

        pool = day_pool[day_pool["position"].notna()].copy()
        pool = pool[pool["team_id"].isin(chosen_teams)]
        pool = pool.sort_values("projected_points", ascending=False).head(pool_size)
        pool = pool.drop_duplicates(subset="player_id").reset_index(drop=True)

        if pool.empty:
            st.warning("No pool for this day/team selection.")
        else:
            import sys  # local import: pulp import cost only when reached
            from pathlib import Path

            sys.path.insert(0, str(Path(shared.REPO_ROOT) / "src" / "model"))
            import optimizer

            with col_slots:
                try:
                    lineup = optimizer.best_lineup(pool)
                    total = optimizer.total_points(lineup)
                    st.markdown(
                        '<div class="na-total">%.1f'
                        '<span style="font-size:.85rem;opacity:.7">'
                        '&nbsp;proj FPTS</span></div>' % total,
                        unsafe_allow_html=True,
                    )
                    slots = lineup["slot"].value_counts().to_dict()
                    chips = " ".join(shared.slot_chip(s) for s in slots)
                    st.markdown(
                        f'<div style="margin-top:6px">{chips}'
                        f'<span class="na-sub">&nbsp; filled</span></div>',
                        unsafe_allow_html=True,
                    )
                except optimizer.InfeasiblePool as e:
                    st.error(f"Infeasible pool: {e}")
                    lineup = None

            with st.expander("Player pool (candidates)"):
                show_pool = pool.copy()
                show_pool["projected_points"] = (
                    show_pool["projected_points"].round(1)
                )
                st.dataframe(
                    show_pool[["player_name", "team_abbrev",
                               "opponent_abbrev", "position",
                               "projected_points"]].rename(columns={
                        "player_name": "Player", "team_abbrev": "Team",
                        "opponent_abbrev": "Opp", "position": "Pos",
                        "projected_points": "Proj FPTS",
                    }),
                    width="stretch", hide_index=True,
                )

            if lineup is not None:
                shared.section("Best lineup")
                for _, row in lineup.iterrows():
                    meta = (f"{row.get('team_abbrev', row['team_id'])} vs "
                            f"{row.get('opponent_abbrev', row['opponent_id'])}"
                            f" · {row['position']}")
                    st.markdown(
                        shared.player_card_html(
                            row["slot"],
                            str(row.get("player_name", row["player_id"])),
                            meta,
                            float(row["projected_points"]),
                        ),
                        unsafe_allow_html=True,
                    )
                st.caption(
                    "Positions are ESPN's coarse G/F/C (its roster endpoint "
                    "has no PG/SG split); PG/SG/SF/PF inputs are bucketed "
                    "into G/F by src/model/optimizer.py."
                )
