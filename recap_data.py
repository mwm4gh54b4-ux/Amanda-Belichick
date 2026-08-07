#!/usr/bin/env python3
"""
Stage one of the weekly recap: compute the facts, write nothing else.

Emits recaps/<season>-wk<NN>.json containing every number the recap is allowed
to state. Stage two renders that JSON. Stage two may not open the raw data.

That separation is the whole design. A weekly recap invents dozens of new
claims every Tuesday, none of which the historical ledger covers. If the writer
can only reach values that this script computed and verify_recap.py re-derived,
an unverified number cannot reach the league by accident. It has to be smuggled
in deliberately, which is a different and much rarer kind of mistake.

Every fact carries a "basis" string naming what it was derived from, so a
disputed number can be traced without rerunning anything.

Usage:
    python3 recap_data.py --data sleeper_history --season 2025 --week 3
    python3 recap_data.py --data sleeper_history --season 2025 --week 3 \
        --snapshots snapshots --out recaps
"""

import argparse
import collections
import datetime
import json
import os

import lineup_efficiency as LE

# Denk's champion rule for winning 2023, first run 2024. Fourteen weeks.
# "computable" records whether the winner can be settled from the Sleeper
# league pull alone. The false ones need NFL box score stats or projections,
# which the matchup feed does not carry, or a mechanism the league has not
# built. They are reported as open rather than guessed at.
THEMES = {
    1:  ("Hot Start", "Highest team score", True),
    2:  ("Crash & Burn", "Lowest team score", True),
    3:  ("Tinker Stinker", "Highest scoring bench player, non-QB", True),
    4:  ("Defense Wins Championships", "Highest scoring defence", True),
    5:  ("Pickem Week", "Most correct winners picked", False),
    6:  ("I'm Open", "Starting WR with the most receiving yards", "stats"),
    7:  ("Biggest Loser", "Largest margin of defeat", True),
    8:  ("MVP", "Highest scoring starting player, non-QB", True),
    9:  ("Gronk Week", "Starting TE closest to 69 receiving yards", "stats"),
    10: ("Biggest Blowout", "Largest margin of victory", True),
    11: ("Bulls-eye", "Team closest to its projected total", False),
    12: ("Laces Out", "Highest scoring kicker", True),
    13: ("Run Forrest Run", "Starting RB with the most rushing yards", "stats"),
    14: ("Bad Beat", "Highest scoring losing team", True),
}

UNCOMPUTABLE_REASON = {
    5:  "Picks are not roster-based and there is no collection mechanism. "
        "Flagged [OPEN] in the constitution.",
    6:  "Needs receiving yards. Run pull_stats.py to fetch them.",
    9:  "Needs receiving yards. Run pull_stats.py to fetch them.",
    11: "Sleeper publishes projections, but only in standard, half-PPR and full "
        "PPR. This league runs half-PPR plus yardage bonuses added in 2025, so "
        "no published projection matches its scoring. An advisory ranking is "
        "included below; it is not rule-grade and should not settle $35.",
    13: "Needs rushing yards. Run pull_stats.py to fetch them.",
}

# Which raw stat decides each yardage theme, and how.
YARDAGE_THEMES = {
    6:  ("WR", "rec_yd", "most"),
    9:  ("TE", "rec_yd", "closest-69"),
    13: ("RB", "rush_yd", "most"),
}

# Weeks 2 and 7 pay out for losing, so they carry anti-tanking guards: no empty
# roster spots, no starting an inactive or IR player. Empty spots are visible in
# the starters array. Inactive status is not in the league pull at all - it is
# in the Sunday pre-lock snapshot, and only if one was captured that week.
GUARDED_WEEKS = {2, 7}

# Weekly prize, constitution section 4: $35 x 14 weeks = $490 of the $1,000 pot.
PAYOUT_USD = 35


def load(data, season, kind):
    p = os.path.join(data, f"{season}_{kind}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def owner_map(data, season):
    users = {u["user_id"]: u.get("display_name") for u in (load(data, season, "users") or [])}
    return {r["roster_id"]: users.get(r["owner_id"])
            for r in (load(data, season, "rosters") or [])}


def load_snapshot(snapdir, season, week):
    """Sunday pre-lock capture, if one exists for this week.

    Its absence is a fact worth recording rather than a silent skip: without it
    a start/sit call can only be graded on the outcome, which is hindsight, and
    the recap should say so out loud instead of pretending otherwise.
    """
    if not snapdir or not os.path.isdir(snapdir):
        return None
    for fn in sorted(os.listdir(snapdir)):
        if fn.startswith(f"{season}-wk{week:02d}") and fn.endswith(".json"):
            try:
                return json.load(open(os.path.join(snapdir, fn)))
            except (ValueError, OSError):
                return None
    return None


def week_entries(data, season, week):
    mt = load(data, season, "matchups") or {}
    return [e for e in (mt.get(str(week)) or [])
            if e.get("points") is not None]


def resolve_theme(data, season, week, entries, rid, pos, names, stats=None,
                  proj=None):
    """Settle the week's payout theme, or say precisely why it cannot be."""
    if week not in THEMES:
        return {"week": week, "computable": False,
                "reason": "No theme defined. Themes run weeks 1 to 14 only."}
    name, rule, computable = THEMES[week]
    # "stats" means settleable only once the box score file has been pulled.
    if computable == "stats":
        computable = bool(stats)
    out = {"week": week, "name": name, "rule": rule, "computable": computable,
           # The prize is a constitution fact, not a Sleeper one, so it is
           # carried here with its source named. Without it in the pack a writer
           # cannot state the stake without tripping the number guard, and the
           # right answer to that is to verify the figure rather than to let
           # unverified ones through.
           "payout_usd": PAYOUT_USD,
           "payout_basis": "constitution section 4, $35 per week for weeks 1-14"}
    if not computable:
        out["reason"] = UNCOMPUTABLE_REASON[week]
        out["basis"] = "constitution section 4a"
        if week == 11 and proj:
            out["advisory"] = bullseye_advisory(entries, rid, proj)
        return out

    teams = [(round(e["points"], 2), rid.get(e["roster_id"])) for e in entries]

    def player_pool(starters_only, exclude_pos=(), only_pos=None):
        pool = []
        for e in entries:
            st = set(e.get("starters") or [])
            for pid, pts in (e.get("players_points") or {}).items():
                on_bench = pid not in st
                if starters_only and on_bench:
                    continue
                if starters_only is False and not on_bench:
                    continue
                p = pos.get(str(pid))
                if p in exclude_pos:
                    continue
                if only_pos and p != only_pos:
                    continue
                pool.append((round(pts, 2), names.get(str(pid), f"player#{pid}"),
                             p, rid.get(e["roster_id"])))
        pool.sort(key=lambda x: (-x[0], x[1]))
        return pool

    if week in YARDAGE_THEMES and stats:
        want_pos, stat_key, mode = YARDAGE_THEMES[week]
        cands = []
        for e in entries:
            for pid in (e.get("starters") or []):
                if not pid or pid == "0":
                    continue
                if pos.get(str(pid)) != want_pos:
                    continue
                yds = (stats.get(str(pid)) or {}).get(stat_key)
                if yds is None:
                    continue
                cands.append((float(yds), names.get(str(pid), f"player#{pid}"),
                              rid.get(e["roster_id"])))
        if not cands:
            out["computable"] = False
            out["reason"] = (f"No starting {want_pos} recorded {stat_key} this week, "
                             "which is unlikely enough to mean the stats file is "
                             "incomplete rather than that nobody played.")
            return out
        if mode == "most":
            cands.sort(key=lambda x: (-x[0], x[1]))
            top = cands[0]
            out.update(winner=top[2], value=top[0], unit="yards", player=top[1],
                       runners_up=[{"owner": o, "player": n, "value": v}
                                   for v, n, o in cands[1:3]],
                       basis=f"starting {want_pos} {stat_key}, Sleeper stats feed")
        else:
            # Gronk Week: closest to 69 either side. A tie in distance is
            # possible and is reported rather than broken by an invented rule.
            cands.sort(key=lambda x: (abs(x[0] - 69), x[1]))
            top = cands[0]
            tied = [c for c in cands if abs(c[0] - 69) == abs(top[0] - 69)]
            out.update(winner=top[2], value=top[0], unit="receiving yards",
                       player=top[1], distance_from_69=round(abs(top[0] - 69), 2),
                       basis=f"starting {want_pos} {stat_key} closest to 69, "
                             "Sleeper stats feed")
            if len(tied) > 1:
                out["tie"] = [{"owner": o, "player": n, "value": v} for v, n, o in tied]
                out["note"] = ("Two or more starters are equally far from 69. "
                               "The constitution has no tiebreaker for this.")
        return out

    if week == 1:
        teams.sort(reverse=True)
        out.update(winner=teams[0][1], value=teams[0][0], unit="points",
                   runners_up=[{"owner": o, "value": v} for v, o in teams[1:3]],
                   basis="team points, matchups feed")
    elif week == 2:
        teams.sort()
        out.update(winner=teams[0][1], value=teams[0][0], unit="points",
                   runners_up=[{"owner": o, "value": v} for v, o in teams[1:3]],
                   basis="team points, matchups feed")
    elif week == 3:
        pool = player_pool(starters_only=False, exclude_pos=("QB",))
        if pool:
            v, n, p, o = pool[0]
            out.update(winner=o, value=v, unit="points", player=n, position=p,
                       runners_up=[{"owner": oo, "player": nn, "value": vv}
                                   for vv, nn, _, oo in pool[1:3]],
                       basis="bench players, non-QB, matchups feed")
    elif week == 4:
        pool = player_pool(starters_only=True, only_pos="DEF")
        if pool:
            v, n, p, o = pool[0]
            out.update(winner=o, value=v, unit="points", player=n,
                       basis="starting DEF, matchups feed")
    elif week in (7, 10):
        margins = []
        pair = collections.defaultdict(list)
        for e in entries:
            if e.get("matchup_id") is not None:
                pair[e["matchup_id"]].append(e)
        for both in pair.values():
            if len(both) != 2:
                continue
            a, b = both
            hi, lo = (a, b) if a["points"] >= b["points"] else (b, a)
            margins.append((round(hi["points"] - lo["points"], 2),
                            rid.get(hi["roster_id"]), rid.get(lo["roster_id"])))
        margins.sort(reverse=True)
        if margins:
            m, win, lose = margins[0]
            out.update(winner=lose if week == 7 else win, value=m, unit="point margin",
                       opponent=win if week == 7 else lose,
                       basis="matchup margins, matchups feed")
    elif week == 8:
        pool = player_pool(starters_only=True, exclude_pos=("QB",))
        if pool:
            v, n, p, o = pool[0]
            out.update(winner=o, value=v, unit="points", player=n, position=p,
                       runners_up=[{"owner": oo, "player": nn, "value": vv}
                                   for vv, nn, _, oo in pool[1:3]],
                       basis="starting players, non-QB, matchups feed")
    elif week == 12:
        pool = player_pool(starters_only=True, only_pos="K")
        if pool:
            v, n, p, o = pool[0]
            out.update(winner=o, value=v, unit="points", player=n,
                       basis="starting K, matchups feed")
    elif week == 14:
        pair = collections.defaultdict(list)
        for e in entries:
            if e.get("matchup_id") is not None:
                pair[e["matchup_id"]].append(e)
        losers = []
        for both in pair.values():
            if len(both) != 2:
                continue
            a, b = both
            lo = a if a["points"] < b["points"] else b
            losers.append((round(lo["points"], 2), rid.get(lo["roster_id"])))
        losers.sort(reverse=True)
        if losers:
            out.update(winner=losers[0][1], value=losers[0][0], unit="points",
                       basis="losing teams, matchups feed")

    if week in GUARDED_WEEKS:
        out["guard"] = {
            "rule": "No empty roster spots, no starting an IR or inactive player.",
            "empty_spots_checked": True,
            "inactive_checked": False,
            "note": "Empty starter slots are visible in the league pull. Inactive "
                    "and IR status is not, and has to come from the Sunday "
                    "pre-lock snapshot. Confirm by hand if the snapshot is absent.",
        }
        winner = out.get("winner")
        for e in entries:
            if rid.get(e["roster_id"]) == winner:
                empties = sum(1 for s in (e.get("starters") or []) if not s or s == "0")
                out["guard"]["empty_spots"] = empties
                out["guard"]["passes_empty_spot_test"] = empties == 0
    return out


def player_baselines(data, season, upto_week):
    """Each player's own scoring baseline before this week, for judging whether
    he over or underachieved.

    Zeros are excluded from the baseline. In Sleeper a player on bye, inactive,
    or not yet rostered still appears at 0.0, and averaging those in drags every
    baseline toward zero and makes ordinary weeks look like heroics. Only weeks
    a player actually produced count.

    A baseline needs at least two prior scoring weeks. Below that it is one
    number pretending to be a trend, and the caller gets None instead.
    """
    mt = load(data, season, "matchups") or {}
    scores = collections.defaultdict(list)
    for wk in range(1, upto_week):
        for e in (mt.get(str(wk)) or []):
            for pid, v in (e.get("players_points") or {}).items():
                if v:
                    scores[str(pid)].append(v)
    return {pid: sum(v) / len(v) for pid, v in scores.items() if len(v) >= 2}


def replacement_levels(entries, pos):
    """Worst score among players actually started at each position this week.
    The bar a starter had to clear to have been worth the slot."""
    started = collections.Counter()
    pool = collections.defaultdict(list)
    for e in entries:
        st = set(e.get("starters") or [])
        for pid, v in (e.get("players_points") or {}).items():
            p = pos.get(str(pid))
            if not p:
                continue
            pool[p].append(v)
            if pid in st:
                started[p] += 1
    out = {}
    for p, sc in pool.items():
        n = started.get(p, 0)
        if n:
            sc.sort(reverse=True)
            out[p] = round(sc[min(n, len(sc)) - 1], 2)
    return out


def performances(entry, rid, pos, names, base, repl, stats=None):
    """Every player this team actually started, with two independent readings of
    whether he delivered: against his own prior form, and against what a
    replacement at his position returned that week."""
    out = []
    pts = entry.get("players_points") or {}
    for pid in (entry.get("starters") or []):
        if not pid or pid == "0":
            continue
        got = round(pts.get(pid, 0.0), 2)
        p = pos.get(str(pid))
        b = base.get(str(pid))
        r = repl.get(p)
        out.append({
            "player": names.get(str(pid), f"player#{pid}"),
            "position": p,
            "points": got,
            "own_average": round(b, 2) if b is not None else None,
            "vs_own_average": round(got - b, 2) if b is not None else None,
            "stat_line": stat_line(pid, stats),
            "replacement_level": r,
            "vs_replacement": round(got - r, 2) if r is not None else None,
            "basis": "started players only; own average excludes weeks the player "
                     "scored nothing, which in this feed also means bye, inactive "
                     "or unrostered",
        })
    out.sort(key=lambda x: -(x["vs_own_average"] if x["vs_own_average"] is not None
                             else x["vs_replacement"] if x["vs_replacement"] is not None
                             else 0))
    return out


def bullseye_advisory(entries, rid, proj):
    """Week 11 ranked by half-PPR projection, clearly labelled as advisory.

    Published projections do not include this league's yardage bonuses, so this
    can order two close teams wrongly. It is here to inform an argument, not to
    settle a payout.
    """
    rows = []
    for e in entries:
        total = 0.0
        for pid in (e.get("starters") or []):
            if pid and pid != "0":
                total += float((proj.get(str(pid)) or {}).get("pts_half_ppr") or 0.0)
        rows.append({"owner": rid.get(e["roster_id"]),
                     "projected": round(total, 2),
                     "actual": round(e["points"], 2),
                     "miss": round(abs(e["points"] - total), 2)})
    rows.sort(key=lambda x: x["miss"])
    return {"ranking": rows, "rule_grade": False,
            "basis": "sum of starters' published half-PPR projections against the "
                     "actual team score; league scoring adds yardage bonuses this "
                     "projection does not model"}


def stat_line(pid, stats):
    """A readable box score line, so a point total carries its reasons."""
    st = (stats or {}).get(str(pid)) or {}
    bits = []
    if st.get("pass_att"):
        line = f"{int(st.get('pass_cmp', 0))} of {int(st['pass_att'])}, {int(st.get('pass_yd', 0))} pass yds"
        if st.get("pass_td"):
            line += f", {int(st['pass_td'])} TD"
        if st.get("pass_int"):
            line += f", {int(st['pass_int'])} INT"
        bits.append(line)
    if st.get("rush_att"):
        line = f"{int(st['rush_att'])} car, {int(st.get('rush_yd', 0))} rush yds"
        if st.get("rush_td"):
            line += f", {int(st['rush_td'])} TD"
        bits.append(line)
    if st.get("rec") or st.get("rec_tgt"):
        line = (f"{int(st.get('rec', 0))} of {int(st.get('rec_tgt', 0))} tgt, "
                f"{int(st.get('rec_yd', 0))} rec yds")
        if st.get("rec_td"):
            line += f", {int(st['rec_td'])} TD"
        bits.append(line)
    if st.get("fga"):
        bits.append(f"{int(st.get('fgm', 0))} of {int(st['fga'])} FG")
    if st.get("fum_lost"):
        bits.append(f"{int(st['fum_lost'])} lost fumble")
    return "; ".join(bits) or None


def build_matchups(entries, rid, pos, names, base, repl, stats=None):
    pair = collections.defaultdict(list)
    for e in entries:
        if e.get("matchup_id") is not None:
            pair[e["matchup_id"]].append(e)
    out = []
    for mid, both in sorted(pair.items()):
        if len(both) != 2:
            continue
        a, b = both
        hi, lo = (a, b) if a["points"] >= b["points"] else (b, a)
        out.append({
            "matchup_id": mid,
            "winner": rid.get(hi["roster_id"]),
            "loser": rid.get(lo["roster_id"]),
            "winner_points": round(hi["points"], 2),
            "loser_points": round(lo["points"], 2),
            "margin": round(hi["points"] - lo["points"], 2),
            "basis": "matchups feed",
            "performances": {
                rid.get(hi["roster_id"]): performances(hi, rid, pos, names, base,
                                                       repl, stats),
                rid.get(lo["roster_id"]): performances(lo, rid, pos, names, base,
                                                       repl, stats),
            },
        })
    out.sort(key=lambda x: -x["margin"])
    return out


def build_start_sit(data, season, week, entries, rid, pos, names, snapshot):
    """Worst start/sit calls, with pre-lock context where a snapshot exists."""
    lg = load(data, season, "league") or {}
    slots = [s for s in (lg.get("roster_positions") or []) if s != "BN"]
    rookies = LE.rookie_set(data, season) if LE.SLOT_RULES.get(season) else None
    snap_players = {}
    if snapshot:
        for team in (snapshot.get("teams") or []):
            for p in (team.get("players") or []):
                if p.get("player_id"):
                    snap_players[str(p["player_id"])] = p
    pair = collections.defaultdict(list)
    for e in entries:
        if e.get("matchup_id") is not None:
            pair[e["matchup_id"]].append(e)
    rows = []
    for both in pair.values():
        if len(both) != 2:
            continue
        for me, opp in ((both[0], both[1]), (both[1], both[0])):
            pts = me.get("players_points") or {}
            opt, assign = LE.best_lineup(slots, list(pts), pts, pos, season, rookies)
            if opt is None:
                continue
            act = round(me["points"], 2)
            started = set(me.get("starters") or [])
            missed = []
            for _, pid in assign:
                if pid in started:
                    continue
                rec = {"player": names.get(str(pid), f"player#{pid}"),
                       "position": pos.get(str(pid), "?"),
                       "points": round(pts.get(pid, 0.0), 2)}
                snap = snap_players.get(str(pid))
                if snap:
                    rec["prelock"] = {
                        "injury_status": snap.get("injury_status"),
                        "on_bye": snap.get("on_bye"),
                        "was_started": snap.get("started"),
                    }
                missed.append(rec)
            missed.sort(key=lambda x: (-x["points"], x["player"]))
            rows.append({
                "matchup_id": me.get("matchup_id"),
                "owner": rid.get(me["roster_id"]),
                "actual": act,
                "optimal": opt,
                "left_on_bench": round(opt - act, 2),
                # Scoring more than the best legal lineup allows means the
                # lineup as set was not legal. In this league that means a
                # champion rule the platform cannot enforce, so it is recorded
                # rather than smoothed over. Left on bench goes negative here,
                # which is correct and is the tell.
                "illegal_lineup": act > opt + 0.02,
                "opponent": rid.get(opp["roster_id"]),
                "opponent_points": round(opp["points"], 2),
                "result": "W" if act > opp["points"] else "L",
                # A flip is a game the optimal lineup would have won and the
                # actual one lost. Within reach, not thrown away.
                "flipped": act < opp["points"] <= opt,
                "should_have_started": missed[:3],
                "prelock_available": bool(snapshot),
                "basis": "optimal legal lineup vs lineup as set"
                         + (", with Sunday pre-lock snapshot" if snapshot else
                            "; NO pre-lock snapshot, graded on outcome only"),
            })
    rows.sort(key=lambda x: -x["left_on_bench"])
    return rows


def build_transactions(data, season, week, entries, rid, pos, names):
    """Moves made ahead of this week, scored on what they returned in it.

    Deliberately not the season-long transaction grade. That one needs
    rest-of-season production and cannot be computed until the season ends.
    What can be said on a Tuesday is narrower and honest: here is what the
    player you added actually did in the week you added him for, measured
    against what a replacement at his position started that week.
    """
    tx = load(data, season, "transactions") or {}
    moves = [t for t in (tx.get(str(week)) or []) if t.get("status") == "complete"]
    pts_by_player = {}
    started_count = collections.Counter()
    pool = collections.defaultdict(list)
    for e in entries:
        st = set(e.get("starters") or [])
        for pid, v in (e.get("players_points") or {}).items():
            pts_by_player[str(pid)] = round(v, 2)
            p = pos.get(str(pid))
            if not p:
                continue
            pool[p].append(v)
            if pid in st:
                started_count[p] += 1
    # Replacement level: the worst score among the players actually started at
    # that position this week. A pickup only counts as help if it beat that.
    repl = {}
    for p, sc in pool.items():
        n = started_count.get(p, 0)
        if n:
            sc.sort(reverse=True)
            repl[p] = round(sc[min(n, len(sc)) - 1], 2)
    out = []
    for t in moves:
        for pid, roster_id in (t.get("adds") or {}).items():
            p = pos.get(str(pid))
            got = pts_by_player.get(str(pid))
            if got is None:
                continue
            base = repl.get(p)
            out.append({
                "owner": rid.get(roster_id),
                "type": t.get("type"),
                "player": names.get(str(pid), f"player#{pid}"),
                "position": p,
                "points_that_week": got,
                "replacement_level": base,
                "above_replacement": round(got - base, 2) if base is not None else None,
                "faab_spent": (t.get("settings") or {}).get("waiver_bid"),
                "basis": "week transactions, scored against started-player "
                         "replacement level for the same week",
            })
    out.sort(key=lambda x: -(x["above_replacement"] if x["above_replacement"] is not None else -999))
    return out


def build_standings(data, season, week, rid):
    """Records through this week, rebuilt from matchups rather than the roster
    settings block, because roster settings hold the season's final state and
    would report the wrong record for any week but the last."""
    mt = load(data, season, "matchups") or {}
    rec = collections.defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0})
    for wk in range(1, week + 1):
        pair = collections.defaultdict(list)
        for e in (mt.get(str(wk)) or []):
            if e.get("matchup_id") is not None and e.get("points") is not None:
                pair[e["matchup_id"]].append(e)
        for both in pair.values():
            if len(both) != 2:
                continue
            for me, opp in ((both[0], both[1]), (both[1], both[0])):
                o = rid.get(me["roster_id"])
                r = rec[o]
                r["pf"] += me["points"]
                r["pa"] += opp["points"]
                if me["points"] > opp["points"]:
                    r["w"] += 1
                elif me["points"] < opp["points"]:
                    r["l"] += 1
                else:
                    r["t"] += 1
    rows = [{"owner": o, "wins": v["w"], "losses": v["l"], "ties": v["t"],
             "points_for": round(v["pf"], 2), "points_against": round(v["pa"], 2),
             "basis": "matchups weeks 1 to %d" % week}
            for o, v in rec.items()]
    rows.sort(key=lambda x: (-x["wins"], -x["points_for"]))
    return rows


def build_sacko_race(standings):
    """Bottom of the table. There is no written sacko tiebreaker, so where the
    last two are level on record this reports the tie rather than inventing a
    rule to break it."""
    tail = standings[-3:]
    bottom = tail[-1] if tail else None
    tied = [r for r in standings
            if bottom and r["wins"] == bottom["wins"] and r["losses"] == bottom["losses"]]
    return {
        "contenders": tail,
        "current_holder": bottom["owner"] if bottom else None,
        "tie": [r["owner"] for r in tied] if len(tied) > 1 else [],
        "note": "No sacko tiebreaker exists in the constitution. If this is level "
                "at the end of the season it is a commissioner ruling, not a lookup.",
        "basis": "standings through this week",
    }


def build_memory(data, season, week, matchups, entries, rid):
    """One recovered memory, derived rather than remembered.

    Compares this week's biggest margin against every completed week already in
    the dataset, so the callback is a fact about the league's own history and
    lands in the ledger like everything else.
    """
    if not matchups:
        return None
    big = matchups[0]
    history = []
    for y in sorted(LE.seasons(data)):
        lg = load(data, y, "league") or {}
        reg = (lg.get("settings") or {}).get("playoff_week_start", 15) - 1
        mt = load(data, y, "matchups") or {}
        for wk, ents in mt.items():
            # Regular season only. Weeks 15 to 17 include consolation games that
            # eliminated teams stop setting lineups for, and a team that fields
            # one starter manufactures a margin no one earned. The largest
            # "blowout" in the raw data is exactly that: 2024 week 17, won by
            # 165 against a roster with a single player in it.
            if int(wk) > reg:
                continue
            pair = collections.defaultdict(list)
            for e in ents:
                if e.get("matchup_id") is not None and e.get("points") is not None:
                    pair[e["matchup_id"]].append(e)
            for both in pair.values():
                if len(both) != 2:
                    continue
                # Both sides must have actually fielded a team.
                if any(sum(1 for s in (x.get("starters") or []) if not s or s == "0")
                       for x in both):
                    continue
                a, b = both
                history.append((round(abs(a["points"] - b["points"]), 2), y, int(wk)))
    if not history:
        return None
    history.sort(reverse=True)
    bigger = [h for h in history if h[0] > big["margin"]]
    return {
        "kind": "margin_in_context",
        "this_week_margin": big["margin"],
        "winner": big["winner"], "loser": big["loser"],
        "bigger_margins_on_record": len(bigger),
        "record_margin": {"margin": history[0][0], "season": history[0][1],
                          "week": history[0][2]},
        "basis": "contested regular season games only, all completed seasons "
                 "in the pull; playoff and consolation weeks and any game with "
                 "an incomplete lineup are excluded. Note the pull starts at "
                 "2023 and the league started in 2016.",
    }


def build(data, season, week, snapdir):
    entries = week_entries(data, season, week)
    if not entries:
        raise SystemExit(f"No scored matchups for {season} week {week}.")
    rid = owner_map(data, season)
    years = sorted(set(LE.seasons(data)) | {season})
    pos, names, _ = LE.build_positions(data, years)
    snapshot = load_snapshot(snapdir, season, week)
    stats = (load(data, season, "stats") or {}).get(str(week))
    proj = (load(data, season, "projections") or {}).get(str(week))
    base = player_baselines(data, season, week)
    repl = replacement_levels(entries, pos)
    matchups = build_matchups(entries, rid, pos, names, base, repl, stats)
    standings = build_standings(data, season, week, rid)
    return {
        "season": season,
        "week": week,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                                  .replace(microsecond=0).isoformat(),
        "prelock_snapshot": bool(snapshot),
        "box_scores": bool(stats),
        "theme": resolve_theme(data, season, week, entries, rid, pos, names,
                               stats, proj),
        "matchups": matchups,
        "start_sit": build_start_sit(data, season, week, entries, rid, pos,
                                     names, snapshot),
        "transactions": build_transactions(data, season, week, entries, rid,
                                           pos, names),
        "standings": standings,
        "sacko_race": build_sacko_race(standings),
        "memory": build_memory(data, season, week, matchups, entries, rid),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--season", required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--snapshots", default="snapshots")
    ap.add_argument("--out", default="recaps")
    a = ap.parse_args()

    pack = build(a.data, a.season, a.week, a.snapshots)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{a.season}-wk{a.week:02d}.json")
    with open(path, "w") as f:
        json.dump(pack, f, indent=2)
    t = pack["theme"]
    print(f"Wrote {path}")
    print(f"  theme      {t.get('name')}: "
          + (f"{t.get('winner')} ({t.get('value')})" if t.get("computable")
             else "NOT SETTLEABLE FROM THE PULL"))
    print(f"  matchups   {len(pack['matchups'])}, biggest margin "
          f"{pack['matchups'][0]['margin']}")
    ss = pack["start_sit"][0]
    print(f"  start/sit  {ss['owner']} left {ss['left_on_bench']}"
          + ("  <-- cost him the game" if ss["flipped"] else ""))
    print(f"  prelock    {'yes' if pack['prelock_snapshot'] else 'NO - graded on outcome only'}")


if __name__ == "__main__":
    main()
