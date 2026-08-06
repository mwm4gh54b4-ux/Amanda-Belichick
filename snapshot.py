#!/usr/bin/env python3
"""
Sunday pre-lock snapshot.

Captures what was KNOWABLE before kickoff: every lineup as currently set, plus
the injury designation and bye status of every rostered player. This cannot be
reconstructed after the fact, which is why it runs on a schedule.

Without this file, a lineup can only be graded on what happened. With it, a
lineup can be graded on what the manager knew, which is the only fair version.

Writes: snapshots/<season>-wk<week>-sunday.json
"""

import json
import os
import time
import urllib.request

LEAGUE_ID = "1388695778494517248"
BASE = "https://api.sleeper.app/v1"
OUT = "snapshots"


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "amanda/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  retry {i+1} after {e}")
            time.sleep(5)


def main():
    os.makedirs(OUT, exist_ok=True)

    state = get("https://api.sleeper.app/v1/state/nfl")
    season, week = state["season"], state["week"]
    print(f"NFL state: season {season}, week {week}")

    league = get(f"{BASE}/league/{LEAGUE_ID}")
    users = get(f"{BASE}/league/{LEAGUE_ID}/users")
    rosters = get(f"{BASE}/league/{LEAGUE_ID}/rosters")
    matchups = get(f"{BASE}/league/{LEAGUE_ID}/matchups/{week}")

    owner = {u["user_id"]: u.get("display_name") for u in users}
    by_roster = {r["roster_id"]: owner.get(r["owner_id"]) for r in rosters}

    # every player currently on a roster in this league
    rostered = set()
    for r in rosters:
        rostered.update(r.get("players") or [])

    print(f"Fetching player metadata for {len(rostered)} rostered players...")
    allp = get("https://api.sleeper.app/v1/players/nfl")
    players = {}
    for pid in rostered:
        p = allp.get(str(pid))
        if not p:
            continue
        players[str(pid)] = {
            "name": p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "position": p.get("position"),
            "team": p.get("team"),
            "injury_status": p.get("injury_status"),
            "injury_body_part": p.get("injury_body_part"),
            "practice_participation": p.get("practice_participation"),
            "status": p.get("status"),
            "years_exp": p.get("years_exp"),
        }

    lineups = {}
    for e in matchups:
        o = by_roster.get(e["roster_id"])
        lineups[o] = {
            "roster_id": e["roster_id"],
            "matchup_id": e.get("matchup_id"),
            "starters": e.get("starters") or [],
            "all_players": e.get("players") or [],
        }

    snap = {
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "season": season,
        "week": week,
        "roster_positions": [p for p in league.get("roster_positions", []) if p != "BN"],
        "owners": by_roster,
        "lineups": lineups,
        "players": players,
    }

    path = os.path.join(OUT, f"{season}-wk{int(week):02d}-sunday.json")
    with open(path, "w") as f:
        json.dump(snap, f, indent=1)

    flagged = sum(1 for p in players.values() if p["injury_status"])
    print(f"Wrote {path}")
    print(f"  {len(lineups)} lineups, {len(players)} players, {flagged} carrying a designation")


if __name__ == "__main__":
    main()
