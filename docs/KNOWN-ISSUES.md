# Known Issues & Investigation Log

A dated log of every data-source decision and API quirk found while building
this project — each entry has the evidence, the decision, and the permanent
guard left behind in code. Newest last.

## 2026-09-24 — data-source selection (pre-build research)

| Candidate | Finding | Decision |
|---|---|---|
| `stats.nba.com` | Official, 1996-97+, but **unreachable from this environment** — curl exit 000, webfetch timeout, and even an `r.jina.ai` text proxy timed out. Historically blocks cloud/datacenter IPs outright. | Not used. Any future integration would be code-with-mocked-tests only. |
| `cdn.nba.com` (NBA stats CDN) | 403 even with browser headers. | Not used. |
| `balldontlie.io` | 401 on `/v1/games` — free tier now requires a registered API key. | Not used (no-key constraint). |
| basketball-reference.com | 1946+ data, but scraping-hostile (robots + bot walls). | Not used. |
| **ESPN `site.api.espn.com` family** | 200 on teams/schedule/summary/scoreboard, no auth, box scores verified back to **1995-96**. | **Chosen** — sole source. |

Guard: the fixture set in `src/collector/fixtures/` pins the verified
endpoints' real response shapes, so an upstream change fails a test instead
of silently breaking collection.

## 2026-09-24 — ESPN's `season` parameter means the END year

`teams/4/schedule?season=1996` returns the **1995-96** season (events from
Nov 1995 → Apr 1996). Same for standings (`season=2025` → 2024-25).
Off-by-one waiting to happen in six different endpoints.

**Guard**: only `espn_api.season_param()` / `season_label()` do this
arithmetic; tests `test_season_param_is_the_end_year` and
`test_season_label_round_trips` pin it.

## 2026-09-24 — no season-wide schedule endpoint

`.../nba/schedule?season=2011` → **404**. The only full-season route is the
per-team schedule (30 calls/season), every game appearing in both teams'
schedules.

**Guard**: `snapshot.parse_schedule` output is deduped on `game_id`;
`test_parse_schedule_1995_96_fixture` pins that a team schedule carries both
its home and away games.

## 2026-09-24 — ESPN's WAF 403s simplistic User-Agents

Direct A/B test against the same URL:

| Request headers | Result |
|---|---|
| bare `User-Agent: Mozilla/5.0` | **403** |
| custom `Mozilla/5.0 (project-name…)` UA | **403** |
| Python urllib's default `Python-urllib/3.x` | 200 |
| full browser-like set (UA + Accept + Referer) | 200 |

**Guard**: `espn_api._HEADERS` is the single place the working header set is
encoded, with the A/B evidence in its comment — if 403s ever return, that's
the one file to fix.

## 2026-09-24 — standings: two wrong paths

1. `site.api.../nba/standings?season=2025` returns **only** a
   `{"fullViewLink": …}` stub — no entries, HTTP 200 (looks healthy, isn't).
   The working path is `site.web.api.espn.com/apis/v2/.../standings`.
2. The working path for a **not-yet-started season** (asked for 2026-27 in
   Sep) returns a valid 30-row table of **all zeros**.

**Guard**: `espn_api.get_standings()` uses the v2 path;
`refresh_dashboard_fallbacks._has_played_standings()` skips zero-record
tables so the committed fallback is 2025-26 (real records), not an empty
shell of 2026-27.

## 2026-09-24 — roster endpoint ignores its `season` parameter

`common/v3/.../teams/1/roster?season=2011`, `?season=2026` and
`?season=2027` all return the **identical current 18-man Hawks roster** —
including players drafted years after 2011 (verified: Nickeil
Alexander-Walker on a "2010-11" roster). There is no historical-roster route.

**Guard**: positions live in ONE global file `data/raw/player_positions.json`
(current-state only, refreshed on a 7-day mtime cadence) instead of a
per-season `rosters.json` that would have stored today's roster 16 times
under 16 false labels. Retired players get `position="UNK"`; position is
deliberately **not** a model feature (its missingness would encode row era)
and only feeds the optimizer's slot constraints, which act on today's
players anyway.

## 2026-09-24 — athlete gamelog endpoint is unusable

`site.web.api.espn.com/.../athletes/{id}/gamelog?season=2003` → 200, but
the payload contains only `filters` — no game rows.

**Guard**: never used; per-game data comes exclusively from
`summary?event={id}` (verified complete, incl. DNP flags, back to 1995-96).

## 2026-09-24 — score shape differs between endpoints

Schedule payloads carry `score: {"value": 105.0, "displayValue": "105"}`;
a summary header carries the bare string `"105"`. One parser crashed with
`AttributeError: 'str' object has no attribute 'get'` until both shapes were
handled.

**Guard**: `parsing._score_value()` accepts both (dict or scalar), pinned by
fixture tests over a real 1995-96 schedule **and** summary.

## 2026-09-24 — box scores carry no position; group-level roster position is null

Game lines have `position: null` (verified on the 1995-96 fixture), and
`positionGroups[].position` is null too — only each **athlete** carries a
position dict.

**Guard**: `parsing.parse_roster()` reads position per athlete with a
group-level fallback; fixture test asserts `positions ⊆ {G, F, C}`.

## 2026-09-24 — bad `pyarrow==23.1.0` pin (build issue)

The first `requirements.txt` pinned `pyarrow==23.1.0`, which **does not
exist** on PyPI (…23.0.0, 23.0.1, 24.0.0, 25.0.1…) — pip aborted the whole
install set, leaving a half-built venv.

**Guard**: pin corrected to `pyarrow==25.0.1`; `requirements.in` remains the
re-resolve source of truth.
