#!/usr/bin/env python3
"""
Weekly recap ledger. Re-derives every number in a recap fact pack straight from
the Sleeper data and asserts it, then checks the pack against itself.

Run this between recap_data.py and the renderer. A pack that fails here does
not get written up, and the job fails rather than publishing quietly.

The point of separating this from recap_data.py is that it must not agree by
construction. Where a value can be reached by a second route, this file takes
the second route: theme winners are recomputed from the raw feed rather than
read back, records are counted game by game, and totals are cross-footed
against the league-wide sum. Where only one route exists, it asserts the
invariants that would have to hold if the number were right - an optimal
lineup can never score less than the lineup actually set, a record must sum to
the number of weeks played, a benched player named in start/sit must actually
have been benched.

    python3 verify_recap.py --data ./sleeper_history --pack recaps/2025-wk07.json
    python3 verify_recap.py --data ./sleeper_history --all recaps
"""

import argparse
import collections
import glob
import json
import os
import sys

RESULTS = []


def check(label, got, expected, note=""):
    ok = got == expected
    RESULTS.append((ok, label, got, expected, note))
    return ok


def close(label, got, expected, tol=0.02, note=""):
    """Float comparison. Sleeper points carry two decimals and totals are summed
    in different orders in different places, so exact equality is the wrong test
    and a tolerance below a tenth of a point is tight enough to catch a real
    error without flagging float noise."""
    try:
        ok = abs(float(got) - float(expected)) <= tol
    except (TypeError, ValueError):
        ok = False
    RESULTS.append((ok, label, got, expected, note))
    return ok


def load(d, y, k):
    p = os.path.join(d, f"{y}_{k}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def owners(d, y):
    us = {u["user_id"]: u.get("display_name") for u in (load(d, y, "users") or [])}
    return {r["roster_id"]: us.get(r["owner_id"]) for r in (load(d, y, "rosters") or [])}


def entries_for(d, y, wk):
    mt = load(d, y, "matchups") or {}
    return [e for e in (mt.get(str(wk)) or []) if e.get("points") is not None]


def pairs(entries):
    p = collections.defaultdict(list)
    for e in entries:
        if e.get("matchup_id") is not None:
            p[e["matchup_id"]].append(e)
    return [b for b in p.values() if len(b) == 2]


# --------------------------------------------------------------- the checks
def verify(d, pack, tag):
    y, wk = pack["season"], pack["week"]
    rid = owners(d, y)
    names = set(v for v in rid.values() if v)
    ents = entries_for(d, y, wk)
    pts = {rid.get(e["roster_id"]): round(e["points"], 2) for e in ents}

    # --- structure -------------------------------------------------------
    for k in ("season", "week", "theme", "matchups", "start_sit",
              "transactions", "standings", "sacko_race", "memory"):
        check(f"{tag} pack contains {k}", k in pack, True)
    check(f"{tag} filename matches contents",
          os.path.basename(pack.get("_path", "")) or f"{y}-wk{wk:02d}.json",
          f"{y}-wk{wk:02d}.json")

    # --- every owner named anywhere is a real owner -----------------------
    named = set()
    for m in pack["matchups"]:
        named.update([m["winner"], m["loser"]])
    for s in pack["start_sit"]:
        named.update([s["owner"], s["opponent"]])
    for s in pack["standings"]:
        named.add(s["owner"])
    for t in pack["transactions"]:
        named.add(t["owner"])
    if pack["theme"].get("winner"):
        named.add(pack["theme"]["winner"])
    check(f"{tag} every owner named exists in the league", named - names, set())

    # --- matchups ---------------------------------------------------------
    check(f"{tag} matchup count", len(pack["matchups"]), len(pairs(ents)))
    check(f"{tag} every team appears exactly once",
          sorted([m["winner"] for m in pack["matchups"]]
                 + [m["loser"] for m in pack["matchups"]]),
          sorted(pts.keys()))
    check(f"{tag} matchups are sorted by margin descending",
          [m["margin"] for m in pack["matchups"]],
          sorted([m["margin"] for m in pack["matchups"]], reverse=True))
    for m in pack["matchups"]:
        close(f"{tag} margin arithmetic {m['winner']} vs {m['loser']}",
              m["margin"], m["winner_points"] - m["loser_points"])
        close(f"{tag} {m['winner']} points match the feed",
              m["winner_points"], pts.get(m["winner"]))
        close(f"{tag} {m['loser']} points match the feed",
              m["loser_points"], pts.get(m["loser"]))
        check(f"{tag} winner outscored loser: {m['winner']}",
              m["winner_points"] >= m["loser_points"], True)
    close(f"{tag} matchup points cross-foot to the league total",
          sum(m["winner_points"] + m["loser_points"] for m in pack["matchups"]),
          sum(pts.values()), tol=0.05)

    # --- theme, recomputed by a second route ------------------------------
    th = pack["theme"]
    if th.get("computable"):
        if wk == 1:
            check(f"{tag} theme winner is the week's high score",
                  th["winner"], max(pts, key=lambda k: pts[k]))
            close(f"{tag} theme value is the high score", th["value"], max(pts.values()))
        elif wk == 2:
            check(f"{tag} theme winner is the week's low score",
                  th["winner"], min(pts, key=lambda k: pts[k]))
            close(f"{tag} theme value is the low score", th["value"], min(pts.values()))
        elif wk == 7:
            margins = [(round(abs(a["points"] - b["points"]), 2),
                        rid.get((a if a["points"] < b["points"] else b)["roster_id"]))
                       for a, b in pairs(ents)]
            margins.sort(reverse=True)
            check(f"{tag} theme winner took the worst beating", th["winner"], margins[0][1])
            close(f"{tag} theme value is the largest margin", th["value"], margins[0][0])
        elif wk == 10:
            margins = [(round(abs(a["points"] - b["points"]), 2),
                        rid.get((a if a["points"] > b["points"] else b)["roster_id"]))
                       for a, b in pairs(ents)]
            margins.sort(reverse=True)
            check(f"{tag} theme winner had the largest win", th["winner"], margins[0][1])
            close(f"{tag} theme value is the largest margin", th["value"], margins[0][0])
        elif wk == 14:
            losers = [(round(min(a["points"], b["points"]), 2),
                       rid.get((a if a["points"] < b["points"] else b)["roster_id"]))
                      for a, b in pairs(ents)]
            losers.sort(reverse=True)
            check(f"{tag} theme winner is the top-scoring loser", th["winner"], losers[0][1])
            close(f"{tag} theme value is that score", th["value"], losers[0][0])
        else:
            # Player-level themes: assert the claimed player really scored the
            # claimed amount for the claimed owner, and that nobody eligible
            # beat him. The eligibility rule differs per week, so this checks
            # the value and ownership rather than re-implementing the rule.
            if th.get("player") and th.get("value") is not None:
                found = None
                for e in ents:
                    for pid, v in (e.get("players_points") or {}).items():
                        if abs(v - th["value"]) < 0.005:
                            if rid.get(e["roster_id"]) == th["winner"]:
                                found = pid
                check(f"{tag} theme player scored that for that owner",
                      found is not None, True,
                      f"{th.get('player')} {th.get('value')} for {th.get('winner')}")
        check(f"{tag} theme winner is a real owner", th["winner"] in names, True)
    else:
        check(f"{tag} uncomputable theme gives a reason",
              bool(th.get("reason")), True)
        check(f"{tag} uncomputable theme names no winner",
              th.get("winner") is None, True)

    if wk in (2, 7) and th.get("computable"):
        check(f"{tag} guarded week records the anti-tanking guard",
              "guard" in th, True)
        check(f"{tag} guard is honest that inactives were not checked",
              th["guard"].get("inactive_checked"), False)

    # --- start / sit ------------------------------------------------------
    check(f"{tag} start_sit covers every team", len(pack["start_sit"]), len(pts))
    starters_by_owner = {rid.get(e["roster_id"]): set(e.get("starters") or [])
                         for e in ents}
    for s in pack["start_sit"]:
        close(f"{tag} {s['owner']} actual matches the feed", s["actual"], pts.get(s["owner"]))
        # The optimal legal lineup normally cannot score less than the lineup
        # actually set. It can when the lineup as set was not legal: the 2025
        # rookies-only Superflex rule is invisible to Sleeper and was broken
        # four times, so those teams outscored what the rule permitted. That is
        # a rules finding, not an arithmetic error, and it is reported as one.
        if s["optimal"] < s["actual"] - 0.02:
            check(f"{tag} {s['owner']} optimal below actual is flagged as a "
                  f"possible rule violation", s.get("illegal_lineup"), True,
                  f"actual {s['actual']} exceeds legal optimal {s['optimal']}")
        else:
            check(f"{tag} {s['owner']} optimal is not below actual",
                  s["optimal"] >= s["actual"] - 0.02, True)
        close(f"{tag} {s['owner']} bench arithmetic",
              s["left_on_bench"], s["optimal"] - s["actual"])
        expected_flip = s["actual"] < s["opponent_points"] <= s["optimal"]
        check(f"{tag} {s['owner']} flip flag", s["flipped"], expected_flip)
        if s["flipped"]:
            check(f"{tag} {s['owner']} flip implies a loss", s["result"], "L")
        for miss in s["should_have_started"]:
            check(f"{tag} {s['owner']} named a genuinely benched player",
                  miss["player"] not in starters_by_owner.get(s["owner"], set()), True,
                  miss["player"])
        check(f"{tag} {s['owner']} misses are ordered by points",
              [m["points"] for m in s["should_have_started"]],
              sorted([m["points"] for m in s["should_have_started"]], reverse=True))
        if not pack.get("prelock_snapshot"):
            check(f"{tag} {s['owner']} basis admits there is no pre-lock snapshot",
                  "NO pre-lock snapshot" in s["basis"], True)
    check(f"{tag} start_sit sorted by points left on the bench",
          [s["left_on_bench"] for s in pack["start_sit"]],
          sorted([s["left_on_bench"] for s in pack["start_sit"]], reverse=True))

    # --- per-matchup player performances ----------------------------------
    started_by_owner = {rid.get(e["roster_id"]): [p for p in (e.get("starters") or [])
                                                  if p and p != "0"] for e in ents}
    pts_by_pid = {}
    for e in ents:
        for pid, v in (e.get("players_points") or {}).items():
            pts_by_pid[str(pid)] = round(v, 2)
    for m in pack["matchups"]:
        perf = m.get("performances") or {}
        check(f"{tag} {m['winner']} vs {m['loser']} performances cover both teams",
              sorted(perf.keys()), sorted([m["winner"], m["loser"]]))
        for own, rows in perf.items():
            check(f"{tag} {own} performance count equals starters set",
                  len(rows), len(started_by_owner.get(own, [])))
            close(f"{tag} {own} performances sum to the team score",
                  sum(r["points"] for r in rows),
                  m["winner_points"] if own == m["winner"] else m["loser_points"],
                  tol=0.05)
            for r in rows:
                if r["vs_own_average"] is not None:
                    close(f"{tag} {own} {r['player']} vs-own-average arithmetic",
                          r["vs_own_average"], r["points"] - r["own_average"])
                if r["vs_replacement"] is not None:
                    close(f"{tag} {own} {r['player']} vs-replacement arithmetic",
                          r["vs_replacement"], r["points"] - r["replacement_level"])
            check(f"{tag} {own} performances ordered best to worst",
                  [r["vs_own_average"] for r in rows
                   if r["vs_own_average"] is not None],
                  sorted([r["vs_own_average"] for r in rows
                          if r["vs_own_average"] is not None], reverse=True))

    # Every start_sit row must belong to a real game, so the page can group by it.
    ids = {m["matchup_id"] for m in pack["matchups"]}
    check(f"{tag} every start_sit row maps to a real matchup",
          {s.get("matchup_id") for s in pack["start_sit"]} - ids, set())
    per_game = collections.Counter(s.get("matchup_id") for s in pack["start_sit"])
    check(f"{tag} exactly two teams per matchup in start_sit",
          sorted(set(per_game.values())), [2])

    # --- standings, counted game by game ----------------------------------
    rec = collections.defaultdict(lambda: [0, 0, 0, 0.0])
    for w in range(1, wk + 1):
        for a, b in pairs(entries_for(d, y, w)):
            for me, opp in ((a, b), (b, a)):
                o = rid.get(me["roster_id"])
                rec[o][3] += me["points"]
                if me["points"] > opp["points"]:
                    rec[o][0] += 1
                elif me["points"] < opp["points"]:
                    rec[o][1] += 1
                else:
                    rec[o][2] += 1
    for s in pack["standings"]:
        r = rec[s["owner"]]
        check(f"{tag} {s['owner']} record", (s["wins"], s["losses"], s["ties"]),
              (r[0], r[1], r[2]))
        close(f"{tag} {s['owner']} points for", s["points_for"], r[3], tol=0.05)
        check(f"{tag} {s['owner']} games played equals weeks elapsed",
              s["wins"] + s["losses"] + s["ties"], wk)
    check(f"{tag} league wins equal league losses",
          sum(s["wins"] for s in pack["standings"]),
          sum(s["losses"] for s in pack["standings"]))
    close(f"{tag} points for and points against balance league-wide",
          sum(s["points_for"] for s in pack["standings"]),
          sum(s["points_against"] for s in pack["standings"]), tol=0.1)

    # --- sacko race -------------------------------------------------------
    sr = pack["sacko_race"]
    check(f"{tag} sacko contenders are the bottom of the table",
          [c["owner"] for c in sr["contenders"]],
          [s["owner"] for s in pack["standings"][-3:]])
    check(f"{tag} sacko holder is last", sr["current_holder"],
          pack["standings"][-1]["owner"])
    if sr.get("tie"):
        bottom = pack["standings"][-1]
        check(f"{tag} declared tie really is level on record",
              all(any(s["owner"] == o and s["wins"] == bottom["wins"]
                      and s["losses"] == bottom["losses"] for s in pack["standings"])
                  for o in sr["tie"]), True)

    # --- transactions -----------------------------------------------------
    for t in pack["transactions"]:
        if t["above_replacement"] is not None:
            close(f"{tag} {t['owner']} {t['player']} above-replacement arithmetic",
                  t["above_replacement"], t["points_that_week"] - t["replacement_level"])
    check(f"{tag} transactions sorted by value added",
          [t["above_replacement"] for t in pack["transactions"]
           if t["above_replacement"] is not None],
          sorted([t["above_replacement"] for t in pack["transactions"]
                  if t["above_replacement"] is not None], reverse=True))

    # --- memory -----------------------------------------------------------
    mem = pack.get("memory")
    if mem:
        close(f"{tag} memory margin matches the week's biggest",
              mem["this_week_margin"], pack["matchups"][0]["margin"])
        check(f"{tag} record margin is not below this week's",
              mem["record_margin"]["margin"] >= mem["this_week_margin"], True)
        # The record must come from a contested regular season game with both
        # lineups filled. This is the check that would have caught the 2024
        # week 17 blowout against a roster containing one player.
        ry, rw = mem["record_margin"]["season"], mem["record_margin"]["week"]
        lg = load(d, ry, "league") or {}
        reg = (lg.get("settings") or {}).get("playoff_week_start", 15) - 1
        check(f"{tag} record margin is from the regular season", rw <= reg, True)
        ok = False
        for a, b in pairs(entries_for(d, ry, rw)):
            if abs(abs(a["points"] - b["points"]) - mem["record_margin"]["margin"]) < 0.02:
                ok = not any(sum(1 for s in (x.get("starters") or [])
                                 if not s or s == "0") for x in (a, b))
        check(f"{tag} record margin game had full lineups on both sides", ok, True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--pack", help="a single recap json")
    ap.add_argument("--all", help="a directory of recap json files")
    a = ap.parse_args()

    paths = []
    if a.pack:
        paths.append(a.pack)
    if a.all:
        paths.extend(sorted(glob.glob(os.path.join(a.all, "*.json"))))
    if not paths:
        print("Nothing to verify. Pass --pack or --all.")
        return 1

    for p in paths:
        pack = json.load(open(p))
        pack["_path"] = p
        verify(a.data, pack, os.path.basename(p).replace(".json", ""))

    fails = [r for r in RESULTS if not r[0]]
    for ok, label, got, exp, note in RESULTS:
        if not ok:
            print(f"  FAIL  {label}"
                  + (f"  [{note}]" if note else "")
                  + f"\n          got      {got}\n          expected {exp}")
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} recap claims verified "
          f"across {len(paths)} pack(s).")
    if fails:
        print("DO NOT PUBLISH.")
        return 1
    print("Recap ledger clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
