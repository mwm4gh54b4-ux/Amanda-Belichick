#!/usr/bin/env python3
"""
Fetch weekly NFL stats and projections from Sleeper.

Why this exists. The league matchup feed carries fantasy points and nothing
else, which leaves two holes.

First, five of the fourteen payout themes are decided on raw yardage or on a
projection, so they cannot be settled from the league feed at all. Weeks 6, 9
and 13 need receiving and rushing yards. Week 11 needs a projected total.

Second, a bare point total explains nothing. "Justin Fields scored 4.54" is a
verdict without evidence. "Fields went 8 of 17 for 68 yards and an
interception" is the same fact with its reasons attached, and it is the
difference between a recap and a scoreboard.

Both endpoints are the same unauthenticated API the league pull already uses:

    /v1/stats/nfl/regular/<season>/<week>
    /v1/projections/nfl/regular/<season>/<week>

Output lands beside the season files as <season>_stats.json and
<season>_projections.json, each keyed by week then by player id.

    python3 pull_stats.py --out sleeper_history --seasons 2023 2024 2025 2026
    python3 pull_stats.py --out sleeper_history --seasons 2026 --weeks 1

A season in progress has no data for weeks that have not been played. Those
come back empty and are skipped rather than written as empty weeks, so a rerun
later fills them in.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://api.sleeper.app/v1"

# Only the stats the recap can actually use. The raw feed carries well over a
# hundred keys per player, most of them league-settings artifacts, and keeping
# all of them would commit several megabytes a week to the repo for no gain.
KEEP = [
    "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int", "pass_sack",
    "rush_att", "rush_yd", "rush_td",
    "rec", "rec_tgt", "rec_yd", "rec_td",
    "fum_lost",
    "fgm", "fga", "fgm_50p", "xpm", "xpa",
    "def_st_td", "def_td", "sack", "int", "ff", "fum_rec", "pts_allow", "yds_allow",
    "gp", "gms_active",
    # Projection-only scoring lines, kept so a projected total can be summed in
    # a scoring format that is at least named rather than assumed.
    "pts_std", "pts_half_ppr", "pts_ppr",
]


def get(url, timeout=90, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "amanda-belichick"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return None


def trim(raw):
    """Keep the usable stats, drop players who recorded nothing.

    A player with an all-zero line did not play, and writing him out inflates
    the file with rows that only ever mean absence. Absence is already knowable
    from the row not being there.
    """
    out = {}
    if not isinstance(raw, dict):
        return out
    for pid, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        keep = {k: rec[k] for k in KEEP if rec.get(k) not in (None, 0, 0.0)}
        if keep:
            out[str(pid)] = keep
    return out


def pull(kind, season, weeks, pause):
    got = {}
    for wk in weeks:
        url = f"{BASE}/{kind}/nfl/regular/{season}/{wk}"
        raw = get(url)
        small = trim(raw)
        if small:
            got[str(wk)] = small
            print(f"    {kind} {season} wk{wk:<2} {len(small)} players")
        else:
            print(f"    {kind} {season} wk{wk:<2} empty, skipped")
        time.sleep(pause)
    return got


def merge_write(path, fresh):
    """Merge into whatever is already on disk. A rerun mid-season must not wipe
    weeks that were fetched successfully on an earlier run."""
    existing = {}
    if os.path.exists(path):
        try:
            existing = json.load(open(path))
        except (ValueError, OSError):
            existing = {}
    existing.update(fresh)
    with open(path, "w") as f:
        json.dump(existing, f, separators=(",", ":"), sort_keys=True)
    return len(existing), os.path.getsize(path) / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sleeper_history")
    ap.add_argument("--seasons", nargs="+", required=True)
    ap.add_argument("--weeks", nargs="+", type=int,
                    help="default is 1 to 18")
    ap.add_argument("--skip-projections", action="store_true")
    ap.add_argument("--pause", type=float, default=0.4,
                    help="seconds between requests, to stay a polite client")
    a = ap.parse_args()

    weeks = a.weeks or list(range(1, 19))
    os.makedirs(a.out, exist_ok=True)
    for season in a.seasons:
        print(f"  {season}")
        stats = pull("stats", season, weeks, a.pause)
        if stats:
            n, mb = merge_write(os.path.join(a.out, f"{season}_stats.json"), stats)
            print(f"    -> {season}_stats.json, {n} weeks on file, {mb:.1f} MB")
        if not a.skip_projections:
            proj = pull("projections", season, weeks, a.pause)
            if proj:
                n, mb = merge_write(
                    os.path.join(a.out, f"{season}_projections.json"), proj)
                print(f"    -> {season}_projections.json, {n} weeks on file, {mb:.1f} MB")


if __name__ == "__main__":
    main()
