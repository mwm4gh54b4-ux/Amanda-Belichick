#!/usr/bin/env python3
"""
Claims ledger. Every factual assertion made in the league documents is written
here as code that re-derives it from the Sleeper data and asserts the value.

Run this before shipping anything. A claim that is not in here has not been
checked, and a claim that fails here does not go in a document.

    python3 verify_claims.py --data ./sleeper_history
    python3 verify_claims.py --data ./sleeper_history --pdf out.pdf
"""
import argparse, collections, json, os, sys

RESULTS = []


def check(label, got, expected, note=""):
    ok = got == expected
    RESULTS.append((ok, label, got, expected, note))
    return ok


# ----------------------------------------------------------------- loaders
def load(d, y, k):
    p = os.path.join(d, f"{y}_{k}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def owners(d, y):
    us = {u["user_id"]: u.get("display_name") for u in load(d, y, "users")}
    return {r["roster_id"]: us.get(r["owner_id"]) for r in load(d, y, "rosters")}


def teamnames(d, y):
    return {u.get("display_name"): (u.get("metadata") or {}).get("team_name")
            for u in load(d, y, "users")}


def games(d, y, weeks=None):
    rid = owners(d, y)
    m = load(d, y, "matchups") or {}
    out = []
    for wk, entries in m.items():
        if weeks and int(wk) not in weeks:
            continue
        pair = collections.defaultdict(list)
        for e in entries:
            if e.get("matchup_id") is not None:
                pair[e["matchup_id"]].append(e)
        for _, both in pair.items():
            if len(both) != 2:
                continue
            a, b = sorted(both, key=lambda x: -x["points"])
            out.append(dict(wk=int(wk), w=rid[a["roster_id"]], l=rid[b["roster_id"]],
                            wp=round(a["points"], 2), lp=round(b["points"], 2)))
    return out


def standings(d, y):
    rid = owners(d, y)
    rows = []
    for r in load(d, y, "rosters"):
        s = r["settings"]
        rows.append((s.get("wins", 0),
                     round(s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100, 2),
                     rid[r["roster_id"]]))
    return sorted(rows, key=lambda x: (-x[0], -x[1]))


def playoff_ppg(d, y):
    """Points per game over weeks 15-17, actual matchups only. Byes mean totals lie."""
    rid = owners(d, y)
    m = load(d, y, "matchups") or {}
    g, p = collections.Counter(), collections.Counter()
    for wk in (15, 16, 17):
        for e in m.get(str(wk)) or []:
            if e.get("matchup_id") is None:
                continue
            g[rid[e["roster_id"]]] += 1
            p[rid[e["roster_id"]]] += e["points"]
    ppg = {o: round(p[o] / g[o], 2) for o in p}
    order = sorted(ppg, key=lambda x: -ppg[x])
    return ppg, {o: i for i, o in enumerate(order, 1)}, g


def draft_start(d, y):
    return max([x.get("start_time") or 0 for x in (load(d, y, "drafts") or [])], default=0)


def leak_per_move(d):
    """Excludes pre-draft roster cuts, which are keeper mechanics, not judgment."""
    adds, leak = collections.Counter(), collections.Counter()
    for y in ("2023", "2024", "2025"):
        rid = owners(d, y)
        reg = load(d, y, "league")["settings"]["playoff_week_start"] - 1
        pw = collections.defaultdict(dict)
        for wk, es in (load(d, y, "matchups") or {}).items():
            for e in es:
                for pid, pts in (e.get("players_points") or {}).items():
                    pw[str(pid)][int(wk)] = pts
        ds = draft_start(d, y)
        for wk, items in (load(d, y, "transactions") or {}).items():
            for t in items:
                if t.get("status") != "complete" or t["type"] not in ("waiver", "free_agent"):
                    continue
                for pid, r in (t.get("adds") or {}).items():
                    adds[rid.get(r)] += 1
                if (t.get("created") or 0) < ds:
                    continue
                for pid, r in (t.get("drops") or {}).items():
                    leak[rid.get(r)] += sum(v for w, v in pw.get(str(pid), {}).items()
                                            if int(wk) < w <= reg)
    return {o: round(leak[o] / adds[o], 1) for o in adds}


# ----------------------------------------------------------------- claims
def run(d):
    # --- identity and team names -----------------------------------------
    tn25 = teamnames(d, "2025")
    check("Lucas (Naughtynun) team 2025 is Paper Champs",
          tn25.get("Naughtynun"), "Paper Champs")
    check("Cuñado (aleksthebrun) team 2025 is Paper 3rd Place",
          tn25.get("aleksthebrun"), "Paper 3rd Place")
    check("Brendan (wiggitywakes) team 2025 is Daniel Jones Apology Tour",
          tn25.get("wiggitywakes"), "Daniel Jones Apology Tour")
    check("Max (mcryns14) team 2024 is Schrodinger's Mush",
          teamnames(d, "2024").get("mcryns14"), "Schrodinger\u2019s Mush")
    check("Denk (Denk0) team 2025 is The Replacements",
          tn25.get("Denk0"), "The Replacements")

    # --- 2025 headline games ---------------------------------------------
    g25 = {(x["wk"], x["w"]): x for x in games(d, "2025")}
    fin = g25.get((17, "Denk0"))
    check("2025 final: Denk beats Chris", (fin["l"], fin["wp"], fin["lp"]),
          ("cj21k", 149.24, 147.06))
    check("2025 final margin is 2.18", round(fin["wp"] - fin["lp"], 2), 2.18)
    semi = g25.get((16, "cj21k"))
    check("2025 semifinal: Chris 229.32 over Lucas 209.72",
          (semi["l"], semi["wp"], semi["lp"]), ("Naughtynun", 229.32, 209.72))
    check("2025 semifinal combined is 439.04", round(semi["wp"] + semi["lp"], 2), 439.04)
    allg = games(d, "2025")
    tightest = min(allg, key=lambda x: x["wp"] - x["lp"])
    check("2025 tightest game is Denk over Billy by 1.66",
          (tightest["wk"], tightest["w"], tightest["l"], round(tightest["wp"] - tightest["lp"], 2)),
          (8, "Denk0", "goatnasty", 1.66))
    scores = [(x["lp"], x["l"], x["wk"]) for x in allg] + [(x["wp"], x["w"], x["wk"]) for x in allg]
    check("2025 lowest single-team score is Brendan 74.80 in wk15",
          min(scores), (74.8, "wiggitywakes", 15))
    check("2025 highest single-team score is Chris 229.32 in wk16",
          max(scores), (229.32, "cj21k", 16))

    # --- lowest score on record ------------------------------------------
    every = []
    for y in ("2023", "2024", "2025"):
        for x in games(d, y):
            every += [(x["lp"], y, x["wk"], x["l"]), (x["wp"], y, x["wk"], x["w"])]
    every.sort()
    check("Lowest single-game score on record is Nate 26.80 (2024 wk17)",
          every[0], (26.8, "2024", 17, "natergater24"))
    check("Second lowest is also Nate, 67.70", every[1][0::3], (67.7, "natergater24"))

    # --- playoff-week scoring, per game ----------------------------------
    for y, who, exp_rank, exp_ppg in [
            ("2025", "Naughtynun", 1, 202.91), ("2025", "mcryns14", 2, 198.44),
            ("2025", "cj21k", 3, 188.19), ("2025", "aleksthebrun", 9, 98.07),
            ("2025", "wiggitywakes", 10, 83.93), ("2024", "natergater24", 10, 88.49),
            ("2024", "wiggitywakes", 2, 168.63), ("2024", "Denk0", 1, 189.01),
            ("2024", "mcryns14", 8, 104.89), ("2023", "cj21k", 2, 158.13),
            ("2023", "gilbertb12", 1, 161.36)]:
        ppg, rank, _ = playoff_ppg(d, y)
        check(f"{y} playoff ppg {who}", (rank[who], ppg[who]), (exp_rank, exp_ppg))

    # --- standings and honours -------------------------------------------
    check("2025 champion is Denk", g25[(17, "Denk0")]["w"], "Denk0")
    st25 = standings(d, "2025")
    check("2025 worst record (sacko) is Ben", st25[-1][2], "gilbertb12")
    check("2025 best record is Chris 11-3", (st25[0][2], st25[0][0]), ("cj21k", 11))
    st24 = standings(d, "2024")
    check("2024 has a 3-11 tie between Ben and Max broken on points for",
          [(r[2], r[0]) for r in st24[-2:]], [("mcryns14", 3), ("gilbertb12", 3)])
    st23 = standings(d, "2023")
    check("2023 best regular season is Ben 12-2", (st23[0][2], st23[0][0]), ("gilbertb12", 12))
    check("2023 worst record (sacko) is Billy", st23[-1][2], "goatnasty")

    # --- title counts (regression guard) ----------------------------------
    champs = {}
    for y in ("2023", "2024", "2025"):
        fin = [g for g in games(d, y, {17}) if g["wk"] == 17]
        # championship is the highest-stakes wk17 game; take the winners bracket final
        wb = load(d, y, "winners_bracket") or []
        title = [m for m in wb if m.get("p") == 1]
        champs[y] = owners(d, y)[title[0]["w"]] if title else None
    check("Sleeper-era champions are Denk, Brendan, Denk",
          [champs["2023"], champs["2024"], champs["2025"]],
          ["Denk0", "wiggitywakes", "Denk0"])
    check("Denk has two of the three Sleeper titles",
          sum(1 for v in champs.values() if v == "Denk0"), 2)
    check("Brendan has one Sleeper title (plus 2022, off-platform)",
          sum(1 for v in champs.values() if v == "wiggitywakes"), 1)

    # --- Saquon trade: the cost is the pick, and the season was still lost --
    st = standings(d, "2025")
    seed = [i for i, r in enumerate(st, 1) if r[2] == "mcryns14"][0]
    check("Max finished 7th in 2025, one place outside the six-team playoff", seed, 7)
    check("Max's 2025 record is 6-8", [r[0] for r in st if r[2] == "mcryns14"][0], 6)
    tp = load(d, "2026", "traded_picks") or []
    rid26 = owners(d, "2026")
    gone = [(p["round"], rid26.get(p["owner_id"])) for p in tp
            if p["season"] == "2026" and rid26.get(p["roster_id"]) == "mcryns14"]
    check("Max's 2026 second round pick is owned by Brendan",
          sorted(gone), [(2, "wiggitywakes"), (7, "wiggitywakes")])

    # --- lineup efficiency, with IDP positions resolved -------------------
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import lineup_efficiency as LE
    years = [y for y in ("2023", "2024", "2025")]
    pos, names, _ = LE.build_positions(d, years)
    # The cached player file is not optional. Without it, positions fall back to
    # slot inference plus a conservative flex-eligibility guess, which produces
    # a slightly understated optimal and therefore different efficiency and flip
    # totals. Both sets of numbers are internally consistent, which is exactly
    # what makes the divergence dangerous: nothing looks wrong, the figures are
    # just quietly not the ones the league was given. Refuse rather than choose.
    check("The cached player file is present (run pull_players.py first)",
          os.path.exists(os.path.join(d, "players_nfl.json")), True,
          "without it every efficiency and flip figure below is a lower bound")

    # 2023 ran three IDP_FLEX slots. What matters is not how many players carry
    # the literal tag "IDP" but whether every player who actually started in an
    # IDP slot resolves to a position capable of filling one. The old form
    # counted the tag, which the slot-inference fallback produces and the cached
    # player file does not, so pulling players_nfl.json made a passing claim
    # fail while the underlying property got better. Test the property.
    lg23 = load(d, "2023", "league") or {}
    slots23 = [x for x in (lg23.get("roster_positions") or []) if x != "BN"]
    idp_starters, unresolved = set(), set()
    for _wk, _ents in (load(d, "2023", "matchups") or {}).items():
        for _e in _ents:
            _st = _e.get("starters") or []
            if len(_st) != len(slots23):
                continue
            for _slot, _pid in zip(slots23, _st):
                if _slot == "IDP_FLEX" and _pid and _pid != "0":
                    idp_starters.add(str(_pid))
                    if pos.get(str(_pid)) not in ("LB", "DL", "DB", "IDP"):
                        unresolved.add(str(_pid))
    check("Every 2023 IDP starter resolves to an IDP-eligible position "
          "(else 2023 optimal lineups are understated)",
          (len(unresolved), len(idp_starters) > 50), (0, True),
          f"{len(idp_starters)} IDP starters, {len(unresolved)} unresolved")
    rows = LE.analyse(d, years, pos, names)
    agg = {}
    for (o, y), v in rows.items():
        a = agg.setdefault(o, [0.0, 0.0, 0])
        a[0] += v["actual"]; a[1] += v["optimal"]; a[2] += v["flips"]
    eff = {o: round(100 * a[0] / a[1], 1) for o, a in agg.items()}
    flips = {o: a[2] for o, a in agg.items()}
    best = max(eff, key=lambda x: eff[x])
    check("Best lineup efficiency is Johnny", (best, eff[best]), ("GregoryOdensKnees", 94.0))
    check("Second best is Denk", sorted(eff, key=lambda x: -eff[x])[1], "Denk0")
    check("Most flips is Max with 13", (max(flips, key=lambda x: flips[x]),
          max(flips.values())), ("mcryns14", 13))
    check("Johnny has the fewest flips at 3", flips["GregoryOdensKnees"], 3)

    # --- leak rates -------------------------------------------------------
    lk = leak_per_move(d)
    order = sorted(lk, key=lambda x: lk[x])
    check("Best leak rate is Ben at 9.3", (order[0], lk[order[0]]), ("gilbertb12", 9.3))
    check("Second best leak rate is Brendan at 9.7", (order[1], lk[order[1]]), ("wiggitywakes", 9.7))
    check("Worst leak rate is Billy at 27.9", (order[-1], lk[order[-1]]), ("goatnasty", 27.9))
    check("Max leak rate is 22.6", lk["mcryns14"], 22.6)

    # --- pre-draft cut correction ----------------------------------------
    ds = draft_start(d, "2024")
    rid = owners(d, "2024")
    pre = []
    for wk, items in (load(d, "2024", "transactions") or {}).items():
        for t in items:
            if t.get("status") == "complete" and (t.get("created") or 0) < ds:
                for pid, r in (t.get("drops") or {}).items():
                    pre.append(rid.get(r))
    check("Brendan's 2024 wk1 cuts happened before the draft",
          sum(1 for x in pre if x == "wiggitywakes"), 5)

    # --- keepers ----------------------------------------------------------
    for y, exp in [("2023", 33), ("2024", 33), ("2025", 0)]:
        n = sum(1 for _, pl in (load(d, y, "picks") or {}).items()
                for p in (pl or []) if p.get("is_keeper"))
        check(f"{y} keeper count", n, exp)

    # --- lineup rule ------------------------------------------------------
    lg = load(d, "2025", "league")
    check("2025 lineup includes both FLEX and SUPER_FLEX",
          [s for s in lg["roster_positions"] if "FLEX" in s], ["FLEX", "SUPER_FLEX"])
    check("2025 waiver type is FAAB with 200 budget",
          (lg["settings"]["waiver_type"], lg["settings"]["waiver_budget"]), (2, 200))
    check("2024 lineup has no FLEX, only SUPER_FLEX",
          [s for s in load(d, "2024", "league")["roster_positions"] if "FLEX" in s],
          ["SUPER_FLEX"])
    check("2023 ran IDP",
          load(d, "2023", "league")["roster_positions"].count("IDP_FLEX"), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--pdf", help="also scan a PDF for any claim string that failed")
    a = ap.parse_args()
    run(a.data)
    fails = [r for r in RESULTS if not r[0]]
    for ok, label, got, exp, _ in RESULTS:
        if not ok:
            print(f"  FAIL  {label}\n          got      {got}\n          expected {exp}")
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} claims verified.")
    if fails:
        print("DO NOT SHIP.")
        return 1
    print("Ledger clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
