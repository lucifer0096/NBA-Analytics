# Deployment

## 1. Push to GitHub

This repo has no remote yet. From the repo root:

```bash
# Create an empty repo named NBA-Analytics on github.com/new (no README —
# the local history already has one), then:
git remote add origin https://github.com/<your-user>/NBA-Analytics.git
git push -u origin main
```

If `git push` asks for credentials: GitHub no longer accepts account
passwords over HTTPS — use a Personal Access Token
(<https://github.com/settings/tokens>, `repo` scope) as the password, or an
SSH remote (`git@github.com:<your-user>/NBA-Analytics.git`).

## 2. Streamlit Community Cloud

1. <https://share.streamlit.io> → **New app** → select the repo.
2. **Main file path**: `app/app.py`
3. Deploy. Cloud installs `requirements.txt` (the exact tested pins; Python
   3.14, matching CI) and picks up `.streamlit/config.toml` for the theme —
   nothing else to configure.

### What a deployed instance can and cannot do

| Works | Needs a data machine |
|---|---|
| Standings (live ESPN → committed fallback) | `data/raw/` history (gitignored, regenerates via `snapshot.py --backfill`) |
| Schedule & Scores (committed `dashboard_schedule.json` + live scoreboard) | Retraining (`features.py` + `train.py`) |
| Season Leaders (committed `dashboard_leaderboards.json`) | — |
| Awards Ladder + Court View (committed `dashboard_awards.json`, every collected season) | — |
| All-Time Stats + GOAT Rankings (career sections of the same file) | — |

The daily workflow (`.github/workflows/collector.yml`) keeps every committed
fallback fresh — schedule, standings, positions, leaderboards, award races,
and (once `models/proj_model.txt` exists) a rolling 21-day projection
window — so the deployed app stays current without access to raw data.
