#!/usr/bin/env python3
"""Full league pull. Walks backward through previous_league_id and saves every season."""
import json, os, time, urllib.request, sys

LEAGUE_ID = "1388695778494517248"
BASE = "https://api.sleeper.app/v1"
OUT = "sleeper_history"
MAX_WEEK = 18


def get(path, tries=3):
    for i in range(tries):
        try:
            time.sleep(0.05)
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "amanda/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if getattr(e, "code", None) == 404:
                return None
            if i == tries - 1:
                print(f"  ! {path}: {e}")
                return None
            time.sleep(3)


def save(name, data):
    if data is not None:
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(data, f, indent=1)


def season(lid):
    lg = get(f"/league/{lid}")
    if not lg:
        return None
    y = str(lg.get("season"))
    print(f"=== {y}  {lg.get('name')} ===")
    save(f"{y}_league.json", lg)
    for e in ("users", "rosters", "winners_bracket", "losers_bracket", "traded_picks", "drafts"):
        save(f"{y}_{e}.json", get(f"/league/{lid}/{e}"))
    m, t = {}, {}
    for wk in range(1, MAX_WEEK + 1):
        a = get(f"/league/{lid}/matchups/{wk}")
        if a: m[str(wk)] = a
        b = get(f"/league/{lid}/transactions/{wk}")
        if b: t[str(wk)] = b
    save(f"{y}_matchups.json", m)
    save(f"{y}_transactions.json", t)
    picks = {}
    for d in (get(f"/league/{lid}/drafts") or []):
        if d.get("draft_id"):
            picks[d["draft_id"]] = get(f"/draft/{d['draft_id']}/picks")
    save(f"{y}_picks.json", picks)
    print(f"    {len(m)} weeks of matchups, {sum(len(v) for v in t.values())} transactions")
    prev = lg.get("previous_league_id")
    return prev if prev not in (None, "", "0") else None


def main():
    os.makedirs(OUT, exist_ok=True)
    lid, seen, n = LEAGUE_ID, set(), 0
    while lid and lid not in seen:
        seen.add(lid)
        lid = season(lid)
        n += 1
    print(f"\nDone. {n} season(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
