"""Deterministic tests for award-race math and aggregation semantics --
qualifiers, multipliers, boundary conditions, and the payload envelope.
No network, no pandas: pure stdlib, fixture games under tmp_path.
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import awards  # noqa: E402


def _player(pid, name="Player", team="BOS", gp=10, starts=0, **stats):
    """A minimal aggregate_players()-shaped entry for the race functions."""
    entry = {
        "player_id": pid, "player_name": name, "team_abbrev": team,
        "gp": gp, "starts": starts, "minutes": gp * 30,
        "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "to": 0,
    }
    entry.update(stats)
    return entry


def _box(pid, name, team, starter=False, dnp=False, minutes=30, pts=0,
         reb=0, ast=0, stl=0, blk=0, to=0, **extra):
    """A stored box-score player row (the shape snapshot.py writes); `extra`
    carries shooting splits (fgm/fga/fg3m/...) when a test needs them."""
    row = {
        "player_id": pid, "player_name": name, "team_abbrev": team,
        "starter": starter, "did_not_play": dnp, "min": minutes,
        "pts": pts, "reb": reb, "ast": ast, "stl": stl, "blk": blk, "to": to,
    }
    row.update(extra)
    return row


def _write_games(tmp_path, season, games):
    """games: {game_id: [player rows]} -> data/raw-shaped JSON files."""
    gdir = tmp_path / season / "games"
    gdir.mkdir(parents=True, exist_ok=True)
    for gid, rows in games.items():
        (gdir / f"{gid}.json").write_text(
            json.dumps({"players": rows}), encoding="utf-8"
        )


def _write_schedule(tmp_path, season, rows):
    """rows: [(status, home, away, home_score, away_score), ...]."""
    sdir = tmp_path / season
    sdir.mkdir(parents=True, exist_ok=True)
    fields = ["game_id", "date", "status", "home_abbrev", "away_abbrev",
              "home_score", "away_score"]
    with open(sdir / "schedule.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, (status, home, away, hs, as_) in enumerate(rows, start=1):
            writer.writerow({
                "game_id": 1000 + i, "date": "2026-01-01", "status": status,
                "home_abbrev": home, "away_abbrev": away,
                "home_score": hs, "away_score": as_,
            })


def test_previous_season_century_wrap():
    assert awards.previous_season("2025-26") == "2024-25"
    # The two-digit suffix must roll 00 -> the previous century, not 25-26.
    assert awards.previous_season("2000-01") == "1999-00"


def test_min_games_floor():
    # Floored at 8 so early-season runs still produce a race.
    assert awards.min_games_for(0) == 8
    assert awards.min_games_for(10) == 8
    # After that it's half the games any team played.
    assert awards.min_games_for(82) == 41
    assert awards.min_games_for(100) == 50


def test_team_records_parses_float_scores_and_skips_non_final(tmp_path):
    _write_schedule(tmp_path, "2012-13", [
        ("STATUS_FINAL", "BOS", "NYK", "125.0", "110"),   # float-string score
        ("STATUS_SCHEDULED", "BOS", "NYK", "", ""),       # not final: skip
        ("STATUS_FINAL", "BOS", "NYK", "", "110"),        # scoreless: skip
    ])
    records = awards.team_records("2012-13", raw_dir=str(tmp_path))
    assert records["BOS"]["games"] == 1
    assert records["BOS"]["wins"] == 1
    assert records["BOS"]["win_pct"] == 1.0
    assert records["BOS"]["opp_pg"] == 110.0
    assert records["NYK"]["losses"] == 1
    assert awards.max_games_played(records) == 1


def test_mvp_multiplier_orders_equal_production():
    # Identical lines; only the team record differs -> win% multiplier decides.
    records = {
        "BOS": {"wins": 8, "losses": 2, "win_pct": 0.8, "opp_pg": 105.0,
                "games": 10},
        "DET": {"wins": 2, "losses": 8, "win_pct": 0.2, "opp_pg": 118.0,
                "games": 10},
    }
    winner = _player(1, "Alpha", "BOS", pts=200, reb=80, ast=60)
    loser = _player(2, "Beta", "DET", pts=200, reb=80, ast=60)
    # Below the qualifier: same production, must not appear at all.
    short = _player(3, "Gamma", "BOS", gp=9, pts=200, reb=80, ast=60)

    rows = awards.mvp_rows([loser, short, winner], records, min_gp=10)
    assert [r["player_name"] for r in rows] == ["Alpha", "Beta"]
    assert rows[0]["rank"] == 1 and rows[1]["rank"] == 2
    # impact = (200 + 0.75*80 + 60)/10 = 32; multipliers 0.92 vs 0.68.
    assert rows[0]["score"] == 29.44
    assert rows[1]["score"] == 21.76


def test_sixth_man_start_filter_boundary():
    # Exactly 40% starts is IN (<= the cap); 50% is out.
    bench = _player(1, "Sixth", "MIA", gp=10, starts=4, pts=200, ast=40,
                    reb=40)
    starterish = _player(2, "Starter", "MIA", gp=10, starts=5, pts=300)
    rows = awards.sixth_man_rows([bench, starterish], {}, min_gp=8)
    assert [r["player_name"] for r in rows] == ["Sixth"]
    assert rows[0]["start_pct"] == 0.4
    # score = (200 + 0.5*40 + 0.25*40)/10 = 230/10 = 23.0
    assert rows[0]["score"] == 23.0


def test_mip_needs_prev_season():
    p = _player(1, "Riser", "BOS", pts=300)
    # No collected prior-season games -> honest empty, never an invented winner.
    assert awards.mip_rows([p], [], min_gp=8, min_gp_prev=8) == []


def test_leader_rows_qualification_and_rank_cap():
    # 12 qualifiers + 1 short-season player; RACE_SIZE caps at 10.
    players = [_player(i, f"P{i}", "BOS", gp=10, pts=10 * i, minutes=300)
               for i in range(1, 13)]
    players.append(_player(99, "Short", "BOS", gp=7, pts=5000))

    out = awards.leader_rows(players, min_gp=10)
    assert set(out) == set(awards.STAT_CATEGORIES)
    pts_rows = out["pts"]
    assert len(pts_rows) == awards.RACE_SIZE
    assert [r["rank"] for r in pts_rows] == list(range(1, awards.RACE_SIZE + 1))
    assert 99 not in [r["player_id"] for r in pts_rows]
    # Highest per-game rate first: P12 has pts 120 / 10 gp = 12.0.
    assert pts_rows[0]["player_id"] == 12
    assert pts_rows[0]["per_game"] == 12.0
    # The tooltip's full stat line needs minutes per game too.
    assert pts_rows[0]["mpg"] == 30.0
    assert pts_rows[0]["total"] == 120


def test_build_payload_empty_without_games(tmp_path):
    # A season with no collected box scores -> {} (caller keeps old file).
    assert awards.build_payload("2099-00", raw_dir=str(tmp_path)) == {}


def test_efficiency_splits_and_none_guards():
    # 10-20 FG, 4-10 3P, 6-7 FT, 30 pts:
    # eFG = (10 + 0.5*4)/20 = 60%, TS = 30 / (2*(20 + 0.44*7)) = 65%.
    eff = awards.efficiency({"pts": 30, "fgm": 10, "fga": 20, "fg3m": 4,
                             "fg3a": 10, "ftm": 6, "fta": 7})
    assert eff == {"fgp": 50.0, "fg3p": 40.0, "ftp": 85.7, "efg": 60.0,
                   "ts": 65.0}
    # No attempts at all -> None everywhere, never an invented 0.0/100.0.
    assert set(awards.efficiency({}).values()) == {None}


def test_aggregate_players_sums_shooting_splits(tmp_path):
    _write_games(tmp_path, "2018-19", {
        1: [_box(1, "Shooter", "BOS", minutes=36, pts=30, fgm=11, fga=20,
                 fg3m=4, fg3a=10, ftm=4, fta=5, oreb=1, dreb=4)],
        2: [_box(1, "Shooter", "BOS", minutes=34, pts=20, fgm=8, fga=18,
                 fg3m=2, fg3a=8, ftm=2, fta=2, oreb=0, dreb=5)],
    })
    players = {p["player_id"]: p for p in
               awards.aggregate_players("2018-19", raw_dir=str(tmp_path))}
    p = players[1]
    assert p["gp"] == 2 and p["minutes"] == 70
    assert (p["fgm"], p["fga"]) == (19, 38)
    assert (p["fg3m"], p["fg3a"]) == (6, 18)
    assert (p["ftm"], p["fta"]) == (6, 7)
    assert (p["oreb"], p["dreb"]) == (1, 9)


def test_leader_rows_carry_shooting_splits_and_3pm_board():
    players = [_player(1, "Sniper", "BOS", gp=10, pts=100, fgm=40, fga=80,
                       fg3m=20, fg3a=50, minutes=300),
               _player(2, "Driver", "CLE", gp=10, pts=120, fgm=50, fga=90,
                       fg3m=0, fg3a=0, minutes=300)]
    out = awards.leader_rows(players, min_gp=8)
    # 3PM joins the season boards; totals rank, 0-for-0 shows no fake 3P%.
    assert "fg3m" in out
    assert out["fg3m"][0]["player_name"] == "Sniper"
    assert out["fg3m"][0]["per_game"] == 2.0
    row = out["pts"][0]
    assert (row["fgm"], row["fga"]) == (50, 90)
    assert row["fgp"] is not None
    sniper = next(r for r in out["fg3m"] if r["player_name"] == "Sniper")
    assert sniper["fg3p"] == 40.0


def test_alltime_players_merges_seasons_and_tracks_peak(tmp_path):
    _write_games(tmp_path, "2016-17", {
        1: [_box(1, "Ace", "BOS", minutes=30, pts=20, fgm=8, fga=16)],
        2: [_box(9, "Ghost", "BOS", dnp=True)],
    })
    _write_games(tmp_path, "2017-18", {
        1: [_box(1, "Ace", "CLE", minutes=32, pts=30, fgm=11, fga=20)],
    })
    merged = {p["player_id"]: p
              for p in awards.alltime_players(raw_dir=str(tmp_path))}
    ace = merged[1]
    assert ace["seasons"] == 2 and ace["gp"] == 2
    assert ace["pts"] == 50
    assert (ace["fgm"], ace["fga"]) == (19, 36)
    assert ace["team_abbrev"] == "CLE"        # latest season wins
    assert ace["peak_impact"] == 30.0         # best season's impact/pg
    assert 9 not in merged                    # DNP-only rows never aggregate


def test_alltime_rows_boards_and_qualifiers():
    players = [
        _player(1, "Volume", "BOS", gp=82, pts=2000, fgm=700, fga=1500,
                fg3m=100, fg3a=300, ftm=500, fta=600),
        _player(2, "Efficient", "CLE", gp=82, pts=1600, fgm=640, fga=1100,
                fg3m=150, fg3a=350, ftm=170, fta=200),
        _player(3, "Tiny", "NYK", gp=40, pts=800),        # < 41 career GP
        _player(4, "OneGame", "MIA", gp=41, pts=200, fgm=5, fga=6),
    ]
    boards = awards.alltime_rows(players)
    assert set(boards) == set(awards.ALLTIME_CATEGORIES)
    # Counting boards rank by career TOTAL behind the GP floor (OneGame
    # clears 41 GP, so his tiny total legitimately lands third).
    pts_names = [r["player_name"] for r in boards["pts"]]
    assert pts_names == ["Volume", "Efficient", "OneGame"]
    assert boards["pts"][0]["pts"] == 2000
    # FG%: Efficient (58.2%) over Volume (46.7%); OneGame fails the
    # >=5 FGA/g floor; Tiny already failed the career GP floor.
    fgp_names = [r["player_name"] for r in boards["fgp"]]
    assert fgp_names == ["Efficient", "Volume"]
    # Every row carries the full parameter set for the dashboard table.
    row = boards["pts"][0]
    for field in ("oreb", "dreb", "to", "ftm", "fta", "minutes", "efg", "ts"):
        assert field in row


def test_goat_rows_formula_titles_and_career_gate():
    players = [
        _player(1, "King", "CLE", gp=164, pts=4000, reb=1600, ast=1600,
                stl=300, blk=100, fg3m=300, peak_impact=35.0),
        _player(2, "Role", "BOS", gp=82, pts=1000, reb=400, ast=400,
                stl=80, blk=20, fg3m=60, peak_impact=15.0),
        _player(3, "Short", "MIA", gp=81, pts=3000),      # < 82 career GP
    ]
    # Official ESPN award names -> wins (history.py's shape).
    honours = {
        1: {"MVP": 4, "Finals MVP": 3, "All-NBA 1st Team": 8},
        2: {"All-Defensive 2nd Team": 3, "All-Rookie 1st Team": 1},
    }
    rows = awards.goat_rows(players, honours)
    assert [r["player_name"] for r in rows] == ["King", "Role"]
    king, role = rows
    # Honour points: King = 4×6 + 3×5 + 8×3 = 63.0,
    # Role = 3×1.5 + 1×0.5 = 5.0; unweighted names contribute nothing.
    assert king["honour_points"] == 63.0
    assert king["honours_total"] == 15
    assert role["honour_points"] == 5.0
    assert king["honours"] == honours[1]
    # The headline score IS the captioned weighted mix (0-100 components).
    for r in rows:
        expected = (awards.GOAT_WEIGHTS["production"] * r["production"]
                    + awards.GOAT_WEIGHTS["honours"] * r["honours_score"]
                    + awards.GOAT_WEIGHTS["peak"] * r["peak_score"])
        assert abs(r["score"] - round(expected, 1)) <= 0.15
    # Production is normalized against the pool's best: King leads every
    # counted category -> 100.0.
    assert king["production"] == 100.0
    # Below the career gate: never appears, however good the stats.
    assert all(r["player_name"] != "Short" for r in rows)
    # The formula string shown on screen is the committed one.
    assert awards.GOAT_FORMULA.count("+") >= 2
    assert f"{awards.GOAT_MIN_CAREER_GP}" in awards.GOAT_FORMULA


def test_goat_formula_prints_every_weight_verbatim():
    """GOAT_FORMULA must contain each component weight and each of the 20
    honour-point weights, so an on-screen reader can audit the whole math
    without opening the source."""
    formula = awards.GOAT_FORMULA
    for w in awards.GOAT_WEIGHTS.values():
        assert f"{w:.0%}" in formula
    for stat, w in awards.GOAT_PRODUCTION_WEIGHTS.items():
        assert f"{awards.GOAT_PROD_LABELS[stat]} {w:.0%}" in formula
    for name, w in awards.GOAT_HONOURS_WEIGHTS.items():
        assert f"{awards.GOAT_HONOUR_LABELS[name]} ×{w:g}" in formula
    assert len(awards.GOAT_HONOURS_WEIGHTS) == 20
    assert "rescaled" in formula
    assert f"≥{awards.GOAT_MIN_CAREER_GP} career games" in formula


def test_goat_rows_drop_untrusted_components_and_rescale():
    """A player with impossible-zero REB (Wilt's ESPN line) has that stat
    dropped and the remaining production weights rescaled; missing peak or
    honours input drops that component and rescales GOAT_WEIGHTS."""
    players = [
        _player(1, "King", "CLE", gp=164, pts=4000, reb=1600, ast=1600,
                stl=300, blk=100, fg3m=300, peak_impact=35.0),
        _player(4, "Broken", "LAL", gp=1045, pts=31419, reb=0, ast=4643,
                peak_impact=30.0),
    ]
    honours = {1: {"MVP": 4}, 4: {"MVP": 2}}
    rows = {r["player_name"]: r for r in awards.goat_rows(players, honours)}
    broken = rows["Broken"]
    assert "reb" in broken["data_gaps"]
    # Production rescaled over PTS/AST/STL/BLK/3PM only -- the gap is
    # disclosed, not silently counted as zero.
    assert broken["production"] > 0
    # No honours input at all -> the component is dropped for everyone.
    rows2 = awards.goat_rows(players, None)
    for r in rows2:
        assert "honours" in r["data_gaps"]
        assert r["honours_score"] is None
        expected = (awards.GOAT_WEIGHTS["production"] * r["production"]
                    + awards.GOAT_WEIGHTS["peak"] * r["peak_score"]) / (
            awards.GOAT_WEIGHTS["production"] + awards.GOAT_WEIGHTS["peak"])
        assert abs(r["score"] - round(expected, 1)) <= 0.15
    # No peak (untrusted rows, no collected season) -> peak dropped.
    players[0]["peak_impact"] = 0
    players[1]["peak_impact"] = 0
    rows3 = awards.goat_rows(players, honours)
    for r in rows3:
        assert "peak" in r["data_gaps"]
        assert r["peak_score"] is None
        expected = (awards.GOAT_WEIGHTS["production"] * r["production"]
                    + awards.GOAT_WEIGHTS["honours"] * r["honours_score"]) / (
            awards.GOAT_WEIGHTS["production"] + awards.GOAT_WEIGHTS["honours"])
        assert abs(r["score"] - round(expected, 1)) <= 0.15


def test_build_career_window_and_honest_empties(tmp_path):
    for season, scorer in (("2016-17", 20), ("2017-18", 30)):
        _write_games(tmp_path, season, {
            1: [_box(1, "Ace", "BOS", minutes=30, pts=scorer,
                     fgm=8, fga=16)],
        })
    season_payloads = [awards.build_payload(s, raw_dir=str(tmp_path))
                       for s in ("2017-18", "2016-17")]
    career = awards.build_career(season_payloads, raw_dir=str(tmp_path))
    assert career["window"] == {"first": "2016-17", "last": "2017-18",
                                "seasons": 2, "players": 1}
    assert set(career["alltime"]["leaders"]) == set(awards.ALLTIME_CATEGORIES)
    assert career["goat"]["formula"] == awards.GOAT_FORMULA
    # 2 career games: below the 41-GP boards and the 82-GP ladder ->
    # honest empties, never an invented king.
    assert all(rows == []
               for rows in career["alltime"]["leaders"].values())
    assert career["goat"]["rows"] == []
    # No season payloads at all -> {} (caller keeps the previous file).
    assert awards.build_career([], raw_dir=str(tmp_path)) == {}


def test_build_payload_envelope(tmp_path):
    """End-to-end over a synthetic 8-game season: DNP skip, traded majority,
    a 4-4 traded tie resolved to the first sorted game, starter counts, the
    qualifier floor, and an honest empty MIP (no prior season collected)."""
    games = {}
    for i in range(1, 9):
        rows = [
            _box(1, "Star", "BOS", starter=(i <= 4), minutes=30, pts=30,
                 reb=8, ast=6, stl=1, blk=1),
            _box(2, "Bench", "BOS", minutes=25, pts=20, reb=5, ast=4,
                 stl=1, blk=0),
            # Majority: AAA x3 then BBB x5 -> BBB.
            _box(4, "Traded", "AAA" if i <= 3 else "BBB", minutes=20, pts=10,
                 reb=4, ast=3, stl=1, blk=1),
            # 4-4 tie -> first sorted game (1.json) says AAA.
            _box(5, "Tie", "AAA" if i % 2 == 1 else "BBB", minutes=20, pts=12,
                 reb=4, ast=3, stl=2, blk=0),
            # DNP rows never count: only game 8 gives him gp=1.
            _box(3, "DNP Guy", "NYK", dnp=(i <= 7),
                 minutes=10, pts=5, reb=1, ast=1),
        ]
        games[i] = rows
    _write_games(tmp_path, "2012-13", games)
    _write_schedule(tmp_path, "2012-13", [
        ("STATUS_FINAL", "BOS", "NYK", "110.0", "100.0") for _ in range(8)
    ])

    players = {p["player_id"]: p
               for p in awards.aggregate_players("2012-13", raw_dir=str(tmp_path))}
    assert players[1]["gp"] == 8 and players[1]["starts"] == 4
    assert players[3]["gp"] == 1                      # 7 DNPs skipped
    assert players[4]["team_abbrev"] == "BBB"         # 5 > 3 majority
    assert players[5]["team_abbrev"] == "AAA"         # 4-4 tie -> first game

    payload = awards.build_payload("2012-13", raw_dir=str(tmp_path))
    assert payload["season"] == "2012-13"
    assert payload["prev_season"] is None             # 2011-12 not collected
    assert payload["min_games"] == 8                  # max(8, 0.5*8)
    assert payload["races"]["mip"] == []              # needs the prior season

    mvp = payload["races"]["mvp"]
    assert [r["player_name"] for r in mvp] == ["Star", "Bench", "Tie",
                                               "Traded"]
    assert mvp[0]["score"] == 42.0                    # (240+48+48)/8 * 1.0
    assert mvp[0]["wins"] == 8                        # BOS swept the fixture
    # DNP guy cleared only 1 game: below the qualifier, in no race.
    assert all(r["player_id"] != 3 for rows in payload["races"].values()
               for r in rows)

    sixth = payload["races"]["sixth_man"]
    # Star started 50% of his games -> out; the three never-started are in.
    assert [r["player_name"] for r in sixth] == ["Bench", "Tie", "Traded"]

    assert len(payload["races"]["dpoy"]) == 4

    pts = payload["leaders"]["pts"]
    assert pts[0]["player_name"] == "Star"
    assert pts[0]["per_game"] == 30.0
    assert pts[0]["mpg"] == 30.0
    assert len(pts) == 4                             # DNP guy excluded
