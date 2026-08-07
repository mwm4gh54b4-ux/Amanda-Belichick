#!/usr/bin/env python3
"""
Lineup efficiency analysis: how many points each owner left on the bench,
respecting real positional eligibility.

Two things make this more accurate than a naive "highest bench player" check:

1. Position inference from slots. Sleeper orders the `starters` array to match
   `roster_positions`, so any player who ever occupied a fixed slot (QB, RB, WR,
   TE, K, DEF) reveals his position. This fills in players who were never drafted
   and would otherwise be unknown.

2. Optimal assignment. The best lineup is a bipartite matching problem, not a
   greedy sort - the highest scorer may only fit a slot another player needs.
   Solved exactly with the Hungarian algorithm.

The solver is implemented here in pure Python rather than pulled from scipy.
This module runs unattended in CI, and a dependency that has to be installed on
every run is a failure mode with no upside at this problem size: the matrices
are at most about thirty by thirty and a season solves in under a second. The
implementation was verified against scipy.optimize.linear_sum_assignment on
every team-week of 2023-2025, producing identical optimal totals in all cases.

Usage:
    python3 lineup_efficiency.py --data ./sleeper_history
    python3 lineup_efficiency.py --data ./sleeper_history --owner mcryns14
    python3 lineup_efficiency.py --data ./sleeper_history --owner mcryns14 --detail
"""

import argparse
import collections
import json
import os

FLEX_OK = {"RB", "WR", "TE"}
# Seasons where a slot carries a league rule the platform cannot encode.
# Extend this as new champion rules land. Keys: season -> slot -> restriction.
SLOT_RULES = {
    "2025": {"SUPER_FLEX": "rookies_only"},
}
SUPERFLEX_OK = {"QB", "RB", "WR", "TE"}
# Cost charged for parking a player in a slot he is not eligible for. Large
# enough that the solver never prefers an illegal lineup, small enough to stay
# well inside float precision when summed across a full matrix.
ILLEGAL = 1e6
# Nudge applied per player index to break exact scoring ties in a fixed order.
TIE = 1e-9


def _hungarian(cost):
    """Exact minimum-cost assignment on a square matrix.

    Jonker-Volgenant style shortest augmenting path with potentials, O(n^3).
    Returns (rows, cols) as parallel lists, matching the shape scipy returns,
    so the call site is unchanged.
    """
    n = len(cost)
    if n == 0:
        return [], []
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            row = cost[i0 - 1]
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = row[j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    cols = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            cols[p[j] - 1] = j - 1
    return list(range(n)), cols


def load(data, season, kind):
    p = os.path.join(data, f"{season}_{kind}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def seasons(data):
    """Completed seasons only. A season in progress has partial weeks, and a
    half-played year averaged in with finished ones is a silently wrong number."""
    out = []
    for fn in sorted(os.listdir(data)):
        if fn.endswith("_league.json"):
            y = fn.split("_")[0]
            lg = load(data, y, "league") or {}
            if lg.get("status") == "complete":
                out.append(y)
    return out


def build_positions(data, years, players_file=None):
    """Position map from draft metadata, then enriched from fixed starter slots."""
    pos, names = {}, {}
    for y in years:
        for _, plist in (load(data, y, "picks") or {}).items():
            for p in (plist or []):
                pid = p.get("player_id")
                md = p.get("metadata") or {}
                if pid and md.get("position"):
                    pos[str(pid)] = md["position"]
                if pid:
                    nm = (md.get("first_name", "") + " " + md.get("last_name", "")).strip()
                    if nm:
                        names[str(pid)] = nm
    inferred = 0
    for y in years:
        lg = load(data, y, "league") or {}
        slots = [s for s in (lg.get("roster_positions") or []) if s != "BN"]
        for wk, entries in (load(data, y, "matchups") or {}).items():
            for e in entries:
                st = e.get("starters") or []
                # Position inference depends entirely on starters[i] lining up
                # with roster_positions[i]. If the lengths disagree the roster
                # shape changed mid-season and every position read off this
                # entry would be shifted by one, poisoning the map for every
                # other season too. Skip rather than guess.
                if len(st) != len(slots):
                    continue
                for slot, pid in zip(slots, st):
                    if not pid or pid == "0":
                        continue
                    if slot in ("FLEX", "SUPER_FLEX", "REC_FLEX"):
                        continue
                    if slot == "IDP_FLEX":
                        # IDP_FLEX takes LB, DL or DB interchangeably, so occupancy
                        # proves IDP eligibility without identifying which. That is
                        # enough to fill the slot in an optimal lineup. Without this,
                        # defensive players are dropped from the pool entirely and the
                        # optimal cannot fill its own IDP slots, which silently inflates
                        # every efficiency number in an IDP season.
                        if str(pid) not in pos:
                            pos[str(pid)] = "IDP"
                            inferred += 1
                        continue
                    if str(pid) not in pos:
                        pos[str(pid)] = slot
                        inferred += 1
    if players_file and os.path.exists(players_file):
        for pid, rec in json.load(open(players_file)).items():
            if isinstance(rec, dict):
                if rec.get("position") and str(pid) not in pos:
                    pos[str(pid)] = rec["position"]
                nm = rec.get("full_name")
                if nm and str(pid) not in names:
                    names[str(pid)] = nm
    return pos, names, inferred


def rookie_set(data, season):
    """Players eligible under a rookies_only slot rule for a given season.

    Two sources, both empirical: players whose draft metadata shows zero years
    of experience, and players actually observed occupying the restricted slot
    that season (which captures undrafted rookies added off waivers).

    The second source assumes the manager complied with the rule. That is the
    right assumption here because the rule was enforced by the league in real
    time, but it means a violation would be absorbed as eligibility rather than
    flagged. If a rules dispute ever turns on this, check the slot by hand.
    """
    rookies = set()
    known_exp = {}
    for _, plist in (load(data, season, "picks") or {}).items():
        for p in (plist or []):
            pid = p.get("player_id")
            md = p.get("metadata") or {}
            if not pid:
                continue
            if md.get("years_exp") is not None:
                known_exp[str(pid)] = str(md["years_exp"])
            if str(md.get("years_exp")) == "0":
                rookies.add(str(pid))
    lg = load(data, season, "league") or {}
    slots = [s for s in (lg.get("roster_positions") or []) if s != "BN"]
    for slot_name, rule in (SLOT_RULES.get(season) or {}).items():
        if rule != "rookies_only" or slot_name not in slots:
            continue
        idx = slots.index(slot_name)
        for wk, entries in (load(data, season, "matchups") or {}).items():
            for e in entries:
                st = e.get("starters") or []
                if len(st) != len(slots):
                    continue
                if st[idx] and st[idx] != "0":
                    pid = str(st[idx])
                    if known_exp.get(pid, "0") == "0":
                        rookies.add(pid)
    return rookies


def eligible(slot, p, pid=None, restricted=None):
    if restricted is not None and pid is not None and str(pid) not in restricted:
        return False
    if slot == "FLEX":
        return p in FLEX_OK
    if slot == "SUPER_FLEX":
        return p in SUPERFLEX_OK
    if slot == "IDP_FLEX":
        return p in {"LB", "DL", "DB", "IDP"}
    return slot == p


def _ok(slot, pid, pos, season, rookies):
    rule = (SLOT_RULES.get(season) or {}).get(slot)
    if rule == "rookies_only" and rookies is not None and str(pid) not in rookies:
        return False
    return eligible(slot, pos[str(pid)])


def best_lineup(slots, roster, points, pos, season=None, rookies=None):
    """Exact optimal legal lineup. Returns (total, [(slot, player_id), ...]).

    The assignment is a list of pairs, not a dict keyed by slot name. Every
    roster shape this league has used repeats slot names - two RB, three WR,
    three IDP_FLEX in 2023 - so a dict keeps only the last player placed in
    each named slot and silently discards three to five starters per team-week.
    The total was never affected because it is summed before the assignment is
    recorded, but anything derived from the lineup itself, such as which
    benched player should have started, was reading a truncated roster.
    """
    # Sorted, so the player pool is in the same order no matter what order the
    # Sleeper API happened to return players_points in on a given pull. Without
    # this, two identical pulls can name different players on an exact scoring
    # tie, and a recap that changes its accusation between runs is worthless.
    players = sorted(p for p in roster if p and p != "0" and pos.get(str(p)))
    if not players:
        return None, {}
    n, m = len(slots), len(players)
    size = max(n, m)
    # Square out the matrix so every slot and every player gets a partner.
    # Padding rows are phantom slots and padding columns are phantom players,
    # both free, so surplus on either side costs nothing. Real pairings that
    # are illegal are priced at ILLEGAL and filtered out again after solving,
    # which means a roster with no legal starter for a slot leaves it empty
    # rather than filling it with someone ineligible.
    cost = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i < n and j < m:
                # TIE breaks exact scoring ties toward the earlier player in
                # sorted order. Scores carry two decimals, so the smallest real
                # gap is 0.01 and a nudge of at most 3e-8 across a full matrix
                # cannot reorder two players who genuinely differ.
                if _ok(slots[i], players[j], pos, season, rookies):
                    cost[i][j] = -points.get(players[j], 0.0) + TIE * j
                else:
                    cost[i][j] = ILLEGAL
    r, c = _hungarian(cost)
    total, assign = 0.0, []
    for i, j in zip(r, c):
        if i < n and j < m and _ok(slots[i], players[j], pos, season, rookies):
            total += points.get(players[j], 0.0)
            assign.append((slots[i], players[j]))
    assign.sort(key=lambda x: (slots.index(x[0]), x[1]))
    return round(total, 2), assign


def analyse(data, years, pos, names, owner=None, quiet=False):
    rows = collections.defaultdict(lambda: {"actual": 0.0, "optimal": 0.0,
                                            "weeks": 0, "flips": 0, "details": []})
    for y in years:
        lg = load(data, y, "league") or {}
        slots = [s for s in (lg.get("roster_positions") or []) if s != "BN"]
        reg = (lg.get("settings") or {}).get("playoff_week_start", 15) - 1
        users = {u["user_id"]: u.get("display_name") for u in (load(data, y, "users") or [])}
        rid = {r["roster_id"]: users.get(r["owner_id"]) for r in (load(data, y, "rosters") or [])}
        mt = load(data, y, "matchups") or {}
        rookies = rookie_set(data, y) if SLOT_RULES.get(y) else None
        if rookies and not quiet:
            print(f"  [{y}] slot rules active: {SLOT_RULES[y]}  ({len(rookies)} eligible players)")
        for wk in range(1, reg + 1):
            entries = mt.get(str(wk)) or []
            pair = collections.defaultdict(list)
            for e in entries:
                if e.get("matchup_id") is not None:
                    pair[e["matchup_id"]].append(e)
            for _, both in pair.items():
                if len(both) != 2:
                    continue
                for me, opp in ((both[0], both[1]), (both[1], both[0])):
                    o = rid.get(me["roster_id"])
                    if owner and o != owner:
                        continue
                    # An unplayed or unscored week has no lineup to grade.
                    # Counting it as a zero would drag the owner's efficiency
                    # down for a week that never happened.
                    if me.get("points") is None or opp.get("points") is None:
                        continue
                    pts = me.get("players_points") or {}
                    roster = list(pts.keys())
                    opt, assign = best_lineup(slots, roster, pts, pos, y, rookies)
                    if opt is None:
                        continue
                    act = round(me["points"], 2)
                    k = (o, y)
                    rows[k]["actual"] += act
                    rows[k]["optimal"] += opt
                    rows[k]["weeks"] += 1
                    lost = round(opt - act, 2)
                    # A flip is a game the optimal lineup would have won and the
                    # actual one lost. Strictly a game that was within reach, not
                    # a game anyone threw away.
                    flipped = act < opp["points"] <= opt
                    if flipped:
                        rows[k]["flips"] += 1
                    started = set(me.get("starters") or [])
                    missed = [(pts.get(p, 0), p) for _, p in assign if p not in started]
                    missed.sort(key=lambda x: (-x[0], x[1]))
                    rows[k]["details"].append({
                        "week": wk, "actual": act, "optimal": opt, "lost": lost,
                        "opp": rid.get(opp["roster_id"]),
                        "opp_pts": round(opp["points"], 2),
                        "result": "W" if act > opp["points"] else "L",
                        "flipped": flipped,
                        "should_have_started": [(names.get(str(p), f"player#{p}"),
                                                 pos.get(str(p), "?"), round(v, 1))
                                                for v, p in missed[:3]],
                    })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--owner")
    ap.add_argument("--detail", action="store_true", help="print every week")
    ap.add_argument("--players")
    a = ap.parse_args()

    years = seasons(a.data)
    pos, names, inferred = build_positions(a.data, years, a.players)
    print(f"Seasons: {', '.join(years)}   positions known: {len(pos)} "
          f"({inferred} inferred from starter slots)\n")

    rows = analyse(a.data, years, pos, names, a.owner)

    agg = collections.defaultdict(lambda: {"actual": 0.0, "optimal": 0.0, "flips": 0, "weeks": 0})
    print(f"{'owner':<20}{'season':<9}{'actual':>10}{'optimal':>10}{'left':>10}{'eff':>8}{'flips':>7}")
    for (o, y), v in sorted(rows.items(), key=lambda x: (x[0][0], x[0][1])):
        eff = v["actual"] / v["optimal"] if v["optimal"] else 0
        print(f"{o:<20}{y:<9}{v['actual']:>10.1f}{v['optimal']:>10.1f}"
              f"{v['optimal']-v['actual']:>10.1f}{eff:>7.1%}{v['flips']:>7}")
        for k in ("actual", "optimal", "flips", "weeks"):
            agg[o][k] += v[k]

    print(f"\n{'owner':<20}{'actual':>10}{'optimal':>10}{'left':>10}{'eff':>8}{'flips':>7}")
    for o, v in sorted(agg.items(), key=lambda x: -(x[1]["optimal"] - x[1]["actual"])):
        eff = v["actual"] / v["optimal"] if v["optimal"] else 0
        print(f"{o:<20}{v['actual']:>10.1f}{v['optimal']:>10.1f}"
              f"{v['optimal']-v['actual']:>10.1f}{eff:>7.1%}{v['flips']:>7}")

    if a.owner:
        print(f"\n-- COSTLIEST WEEKS FOR {a.owner} --")
        allw = []
        for (o, y), v in rows.items():
            for d in v["details"]:
                allw.append((d["lost"], y, d))
        allw.sort(key=lambda x: -x[0])
        for lost, y, d in allw[:10]:
            flag = "  <-- COST HIM THE GAME" if d["flipped"] else ""
            print(f"  {y} wk{d['week']:<2} scored {d['actual']:>6} vs {d['opp']:<18}"
                  f"{d['opp_pts']:>7} {d['result']}  optimal {d['optimal']:>6} "
                  f"(left {lost:>5}){flag}")
            for n, p, v in d["should_have_started"]:
                print(f"        should have started {n} ({p}) {v}")
        if a.detail:
            print(f"\n-- EVERY WEEK --")
            for (o, y), v in sorted(rows.items()):
                for d in v["details"]:
                    print(f"  {y} wk{d['week']:<2} {d['actual']:>6} / {d['optimal']:>6} "
                          f"({d['lost']:>5}) {d['result']} vs {d['opp']}")


if __name__ == "__main__":
    main()
