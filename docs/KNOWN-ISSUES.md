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

## 2026-09-28 — ESPN career statistics carry no plus-minus

The athlete statistics endpoint (`site.web.api.espn.com/apis/common/v3/
sports/basketball/nba/athletes/{id}/stats`, the source for `history.py`'s
career line and `seasons_log`) answers three categories — averages,
totals, miscellaneous (DD2, TD3, SC-EFF, …): **108 stat descriptors on
Jokic, zero containing plus/minus** (verified live Sep 2026). ESPN box
scores carry a per-game +/- on each line, but no career or per-season
aggregate exists anywhere to read.

**Guard**: `awards.CAREER_SUM_STATS` lists `plus_minus` while
`history._LINE_KEYS` excludes it, so an ESPN line can never fabricate a
0 that would clobber the real window total in `build_career`'s
history-over-window merge. The value comes only from this repo's
collected box scores (window-only, 2010-11 → present): uncovered careers
keep `None`, the All-Time +/- board skips them, and the GOAT production
blend drops the stat (named in `data_gaps`, remaining weights rescaled)
instead of scoring a zero.

## 2026-09-28 — ESPN's core API has no WNBA leaders endpoint

The NBA's `{core}/nba/leaders` feeds `history.leader_ids`, the "career
leaders" component of the player pool. The WNBA equivalent
(`{core}/wnba/leaders` and the site-slug variant) answers **404** --
verified live Sep 2026 while every sibling endpoint (athletes, awards,
seasons) responded normally.

**Guard**: `history.leader_ids` returns an empty set for the WNBA by
design (stated in its docstring), so the pool builds from the other two
components instead -- official-award winners plus the collected players
≥41 GP (441 players total for the WNBA), a set the missing leaders list
would not have widened. `build()`'s meta reports `leaders: 0` honestly
instead of failing or pretending a fetch happened.

## 2026-09-28 — WNBA athlete payloads carry no debutYear

NBA athlete payloads carry `debutYear`, which the trust check uses to
answer "do per-season rows cover the career from its start?". WNBA
athletes have no such field at all -- verified across the athlete pool
Sep 2026. `draft.year` and `experience.years` exist but are null for
early-era players, and draft year would be the wrong fallback anyway:
a drafted player's zero-play rookie season would make her rows look
incomplete and false-fail trust forever.

**Guard**: `_fetch_athlete`'s WNBA branch derives `debut` from the first
row year of the athlete's own season rows -- sound because ESPN's WNBA
season data begins at the league's 1997 inception. `_entry_age_ok`
returns False for a WNBA cache bundle with no `debut`, triggering a
one-time refetch wave that upgrades pre-fallback bundles, and `_trusted`
discloses that the WNBA check reduces to "rows exist". Pinned by
`test_history.py` (debut fallback vs the NBA's `debutYear`, the refetch
trigger, the disclosure).

## 2026-09-28 — WNBA Finals-MVP detail omits team refs for 1997-2002

The champion index maps season → champion team id by reading the
Finals-MVP winner's team reference (the Finals MVP wears the
championship jersey). For 1997-2000 and 2001-2002 ESPN's WNBA
Finals-MVP detail returns **no team ref at all** (verified live
Sep 2026), so those six seasons -- the Houston Comets' four-peat and
the LA Sparks' back-to-back, the entire 1997-2002 run -- would silently
vanish from every championship count.

**Guard**: `leagues.verified_champions` pins
`{1997-2000: team id 4 (Houston Comets), 2001-2002: id 6 (LA Sparks)}`
for the WNBA (the NBA table is empty -- its refs are complete);
`history.honours()` applies the overlay with `setdefault`, so ESPN's
refs always win where they exist and the pin only fills the six gaps.
Championship counts beyond the overlay stay honest `None`s rather than
borrowing a number. Pinned by `test_leagues.py`.

## 2026-09-28 — actions/checkout pins the dispatch-time SHA, so queued data runs push against a moved ref

`actions/checkout@v4` defaults to `${{ github.sha }}`, and for both
`schedule` and `workflow_dispatch` events that SHA is resolved when the
run is **created**. The data workflows (collector, refresh-history,
retrain) share the `data-refresh` concurrency group, so a run that
queues behind another one -- each of which finishes by pushing to
`main` -- starts on a stale commit: it refreshes, commits, and its final
`git push` is rejected (`! [rejected] main -> main (fetch first)`,
exit 1) with the refreshed work stranded. Hit live on Sep 2026:
history queued behind the collector (run 36415413941, opening issue
#2), and it explains the earlier scheduled-run failures that looked
flaky.

**Guard**: all three pushing workflows now check out `ref: main` (the
branch tip at job start, which the group lock guarantees to be stable
until this run's own push). Any other bot job that pushes at the end of
its run needs the same `ref:` -- `${{ github.sha }}` is only safe when
the run neither queues nor races a human push.
