#!/usr/bin/env python3
"""
Fetch Sleeper's NFL player dictionary and write a trimmed copy.

Why this exists. The league feed gives fantasy points per player id and nothing
else. Positions have to be inferred from which slot a player occupied, which
works for fixed slots but not for FLEX or SUPER_FLEX, because those slots do not
reveal what a player is. A waiver pickup who only ever started in a flex slot
therefore ends up with no position at all, gets dropped from the optimal lineup
pool, and quietly understates that team's optimal for every week he played.
Sixteen players and thirty-two team-weeks of 2025 were affected. It also leaves
those players printed as "player#12474" in any writeup.

Sleeper serves the full player dictionary at one endpoint. It is large and it
does not change often, so this is a once-a-week fetch, trimmed before it is
written.

IMPORTANT, and the reason this file only keeps three fields:

    years_exp in this file is experience AS OF TODAY, not as of a past season.

Anything that needs to know who was a rookie in a given season must derive that
from that season's own draft metadata, never from here. Using this file for
rookie status would misjudge the 2025 rookies-only Superflex rule immediately
and would get worse every year that passes. Position and name are stable facts;
experience is not.

    python3 pull_players.py --out sleeper_history
"""

import argparse
import json
import os
import urllib.request

URL = "https://api.sleeper.app/v1/players/nfl"

# Positions the league can actually start, across every roster shape it has
# used: 2023 ran three IDP flex slots, 2024 and later run a team defence.
# Everything else in the file is roster filler that no fantasy slot can hold.
KEEP = {"QB", "RB", "WR", "TE", "K", "DEF", "LB", "DL", "DB"}


def name_of(rec):
    """Team defences have no full_name; they carry the city and nickname split
    across the first and last name fields."""
    nm = rec.get("full_name")
    if nm:
        return nm
    parts = [rec.get("first_name") or "", rec.get("last_name") or ""]
    return " ".join(p for p in parts if p).strip() or None


def fetch(url=URL, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "amanda-belichick"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def trim(raw):
    out = {}
    for pid, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        p = rec.get("position")
        if p not in KEEP:
            continue
        entry = {"position": p}
        nm = name_of(rec)
        if nm:
            entry["name"] = nm
        # fantasy_positions is kept because a player can be listed at one
        # position and be flex-eligible at another; the optimal lineup solver
        # can use it where it disagrees with the primary position.
        fp = rec.get("fantasy_positions")
        if fp and fp != [p]:
            entry["fantasy_positions"] = fp
        out[str(pid)] = entry
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sleeper_history")
    ap.add_argument("--url", default=URL)
    a = ap.parse_args()

    raw = fetch(a.url)
    if not isinstance(raw, dict) or len(raw) < 1000:
        # A truncated or error response must not overwrite a good cache.
        raise SystemExit(f"Refusing to write: response held {len(raw)} entries, "
                         "which is far too few to be the real player file.")
    small = trim(raw)
    if len(small) < 500:
        raise SystemExit(f"Refusing to write: only {len(small)} players survived "
                         "the position filter. The feed shape may have changed.")

    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "players_nfl.json")
    with open(path, "w") as f:
        json.dump(small, f, separators=(",", ":"), sort_keys=True)
    size = os.path.getsize(path) / 1e6
    print(f"Wrote {path}: {len(small)} players kept of {len(raw)}, {size:.1f} MB")


if __name__ == "__main__":
    main()
