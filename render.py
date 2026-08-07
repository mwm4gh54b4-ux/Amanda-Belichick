#!/usr/bin/env python3
"""
Stage three: turn a verified fact pack into a page.

Two jobs, and the second one is the important one.

1. Render. Reads recaps/<season>-wk<NN>.json and writes a static HTML page,
   plus an index across every week rendered. No build step, no framework.

2. Guard the prose. Every number appearing in the written recap must also
   appear in the fact pack. A figure the pack cannot account for does not get
   published, and the process exits non-zero so the job fails rather than
   shipping it.

That guard is the entire reason a model is allowed to write the recap at all.
The pack is verified against Sleeper by verify_recap.py; the prose is checked
against the pack; so the only numbers that can reach the league are ones that
were re-derived from source. A hallucinated stat cannot survive the pipeline,
and neither can an honest typo. The writing can be as vicious as it likes,
because it cannot be wrong about a number.

THE PROSE FILE. prose/<season>-wk<NN>.json, every field optional:

    {
      "cold_open": "one or two paragraphs setting the week",
      "matchups": { "winner|loser": "the narrative of that game" },
      "sacko":     "the race for last",
      "archive":   "the historical callback",
      "roast":     "the closing statement, at the bottom"
    }

Matchup keys are "winner|loser" using Sleeper display names, so they survive a
reordering of the pack. A key matching no game is reported rather than quietly
dropped, because a blurb written about a game that did not happen is exactly
the kind of error this pipeline exists to catch.

    python3 render.py --pack recaps/2025-wk07.json --out site
    python3 render.py --all recaps --prose-dir prose --out site
"""

import argparse
import glob
import html
import json
import os
import re

NUM = re.compile(r"-?\d+(?:\.\d+)?")


# ---------------------------------------------------------------- the guard

def numbers_in(obj, acc=None):
    """Every numeric value anywhere in the pack, in the forms a writer might
    reasonably use: 89.7, 89.70 and 90 all resolve to the same fact."""
    if acc is None:
        acc = set()
    if isinstance(obj, dict):
        for v in obj.values():
            numbers_in(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            numbers_in(v, acc)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        f = float(obj)
        acc.add(f"{f:.2f}".rstrip("0").rstrip("."))
        acc.add(f"{f:.1f}".rstrip("0").rstrip("."))
        acc.add(f"{f:.0f}")
        acc.add(str(obj))
    elif isinstance(obj, str):
        for m in NUM.findall(obj):
            acc.add(m)
    return acc


def prose_strings(prose):
    out = []
    if isinstance(prose, str):
        return [prose]
    for v in (prose or {}).values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            out.extend(x for x in v.values() if isinstance(x, str))
    return out


def guard_prose(pack, prose):
    allowed = numbers_in(pack)
    # Ordinals and small counts a writer cannot avoid in English: "both teams",
    # "all ten managers", "the third week". Ten is the league size. Anything
    # above that has to come from the pack.
    allowed |= {str(n) for n in range(0, 11)}
    unknown = []
    for text in prose_strings(prose):
        for tok in NUM.findall(text):
            if tok in allowed or tok.lstrip("-") in allowed:
                continue
            try:
                f = float(tok)
            except ValueError:
                unknown.append(tok)
                continue
            forms = {f"{f:.2f}".rstrip("0").rstrip("."),
                     f"{f:.1f}".rstrip("0").rstrip("."), f"{f:.0f}"}
            if forms & allowed:
                continue
            unknown.append(tok)
    return unknown


def orphan_matchup_keys(pack, prose):
    """Blurbs written about games that did not happen."""
    if not isinstance(prose, dict):
        return []
    real = {f'{m["winner"]}|{m["loser"]}' for m in pack["matchups"]}
    return [k for k in (prose.get("matchups") or {}) if k not in real]


# ---------------------------------------------------------------- rendering
# Palette, contrast against the paper measured rather than assumed:
#   ink #0D0E11 at 17.7:1, muted #454951 at 8.3:1, stamp #A50E17 at 7.2:1.
# The previous scheme ran secondary text at 4.95:1, which is why it was hard to
# read. Redaction is a solid ink bar, used only where the league's own rules
# require data that has never been collected. That is a true fact about weeks
# 5, 6, 9, 11 and 13, not a decoration.

CSS = """
:root{
  --paper:#F6F5F1; --ink:#0D0E11; --muted:#454951; --rule:#C7C5BC;
  --stamp:#A50E17; --surface:#EFEEE8;
}
/* A real dark palette rather than an auto-inverted light one. Browser
   inversion flattens the whole page toward the same grey and destroys the
   contrast the light scheme was tuned for. Measured against the dark
   background: text 15.3:1, secondary 8.2:1, stamp 7.4:1. */
@media (prefers-color-scheme:dark){
  :root{
    --paper:#15171C; --ink:#ECEDE9; --muted:#AAB0B9; --rule:#343842;
    --stamp:#FF8078; --surface:#1E2128;
  }
  .redact{background:var(--ink);color:var(--ink)}
  .basis{background:#000;color:#ECEDE9;border:1px solid var(--rule)}
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:"Source Serif 4",Georgia,serif;font-size:18px;line-height:1.6}
.wrap{max-width:44rem;margin:0 auto;padding:1.75rem 1.15rem 5rem}

header{border-top:6px solid var(--ink);padding-top:.7rem;margin-bottom:1.6rem}
.org{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.68rem;
  letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
  display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap}
h1{font-family:"Archivo Black",Impact,sans-serif;font-weight:400;
  font-size:clamp(2.6rem,12vw,4.4rem);line-height:.88;margin:.5rem 0 0;
  letter-spacing:-.03em;text-transform:uppercase}
.dek{font-size:.95rem;color:var(--muted);margin:.85rem 0 0;
  border-left:3px solid var(--stamp);padding-left:.75rem}

section{margin:2.6rem 0 0}
h2{font-family:"JetBrains Mono",ui-monospace,monospace;font-weight:600;
  font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;
  margin:0 0 .85rem;padding-bottom:.4rem;border-bottom:2px solid var(--ink);
  display:flex;justify-content:space-between;gap:.75rem}
h2 span{color:var(--muted)}

.item{background:var(--surface);padding:.75rem .9rem;margin:0 0 .55rem;
  border-left:3px solid var(--rule)}
.item.lead{border-left-color:var(--ink)}
.item.bad{border-left-color:var(--stamp)}
.who{font-family:"Archivo Black",Impact,sans-serif;font-size:1rem;
  letter-spacing:-.01em;line-height:1.25;display:block}
.line{font-size:.9rem;color:var(--muted);margin-top:.3rem;display:block}
.tag{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.62rem;
  letter-spacing:.13em;text-transform:uppercase;color:var(--stamp);
  display:block;margin-bottom:.3rem;font-weight:600}
.tag.ok{color:var(--muted)}
.narr{margin:.6rem 0 0;font-size:.96rem}
.narr p{margin:0 0 .6rem}
.narr p:last-child{margin-bottom:0}

/* Redaction. Only where the rules require data nobody collects. */
.redact{background:var(--ink);color:var(--ink);padding:.05rem .5rem;
  user-select:none;letter-spacing:.05em}
.why{font-size:.85rem;color:var(--muted);margin-top:.45rem}

/* Signature: every figure carries the derivation that produced it. */
.fact{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.9em;
  font-weight:600;color:var(--ink);background:none;border:0;
  border-bottom:2px solid var(--stamp);padding:0;cursor:help;position:relative}
.fact:hover .basis,.fact:focus-visible .basis{opacity:1;visibility:visible}
.fact:focus-visible{outline:2px solid var(--stamp);outline-offset:3px}
.basis{opacity:0;visibility:hidden;transition:opacity .1s linear;
  position:absolute;left:0;bottom:calc(100% + .4rem);z-index:9;
  width:min(20rem,74vw);background:var(--ink);color:var(--paper);
  font-family:"Source Serif 4",Georgia,serif;font-weight:400;font-size:.78rem;
  line-height:1.45;padding:.5rem .6rem;text-align:left}

footer{margin-top:3.5rem;border-top:2px solid var(--ink);padding-top:.8rem;
  font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.68rem;
  letter-spacing:.05em;color:var(--muted);line-height:1.8}
.idx a{display:block;padding:.7rem 0;border-bottom:1px solid var(--rule);
  color:var(--ink);text-decoration:none}
.idx a:hover .who{color:var(--stamp)}
.game{border-top:2px solid var(--ink);margin:2.4rem 0 0;padding-top:.55rem}
.game h2{border-bottom:0;margin-bottom:.5rem}
.score{font-family:"Archivo Black",Impact,sans-serif;font-size:1.35rem;
  line-height:1.2;margin:.1rem 0 .5rem;letter-spacing:-.02em}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:.55rem}
@media (max-width:33rem){.grid{grid-template-columns:1fr}}
.col h3{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.64rem;
  letter-spacing:.13em;text-transform:uppercase;color:var(--muted);
  margin:0 0 .35rem;font-weight:600}
.perf{font-size:.87rem;margin:0 0 .22rem;display:block;color:var(--muted)}
.perf b{font-family:"Source Serif 4",Georgia,serif;color:var(--ink);font-weight:600}
.up b{border-bottom:2px solid var(--rule)}
.down b{border-bottom:2px solid var(--stamp)}
.roast{border:2px solid var(--stamp);padding:1rem 1.1rem;margin-top:1rem}
.roast .narr{font-size:1.02rem}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=JetBrains+Mono:wght@600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap" rel="stylesheet">
<style>{css}</style></head><body><div class="wrap">
"""


def esc(x):
    return html.escape(str(x))


def fact(value, basis):
    return (f'<button type="button" class="fact">{esc(value)}'
            f'<span class="basis">{esc(basis)}</span></button>')


def narr(text):
    if not text:
        return ""
    paras = [p.strip() for p in str(text).split("\n\n") if p.strip()]
    return '<div class="narr">' + "".join(f"<p>{esc(p)}</p>" for p in paras) + "</div>"


def sec_theme(pack, prose):
    t = pack["theme"]
    if not t.get("computable"):
        out = [f'<div class="item bad"><span class="tag">Unsettled</span>'
               f'<span class="who">{esc(t.get("name", "No theme"))}</span>'
               f'<span class="line">{esc(t.get("rule", ""))}</span>'
               f'<span class="line"><span class="redact">RECORD NOT HELD</span></span>'
               f'<div class="why">{esc(t.get("reason", ""))}</div>']
        adv = t.get("advisory")
        if adv:
            out.append('<h3>Advisory only, not rule-grade</h3>')
            for row in adv["ranking"][:3]:
                out.append(f'<span class="perf">{esc(row["owner"])}: projected '
                           f'{fact(row["projected"], adv["basis"])}, scored '
                           f'{fact(row["actual"], adv["basis"])}, off by '
                           f'{fact(row["miss"], adv["basis"])}.</span>')
        out.append("</div>")
        return "".join(out)
    prize = (f' for {fact(t["payout_usd"], t.get("payout_basis", ""))} dollars'
             if t.get("payout_usd") else "")
    out = [f'<div class="item lead"><span class="tag ok">Paid{esc("")}</span>'
           f'<span class="who">{esc(t["name"])}: {esc(t.get("winner"))}{prize}</span>'
           f'<span class="line">{esc(t.get("rule"))} &mdash; '
           f'{fact(t.get("value"), t.get("basis", ""))} {esc(t.get("unit", ""))}']
    if t.get("player"):
        out.append(f' &middot; {esc(t["player"])}')
    if t.get("distance_from_69") is not None:
        out.append(f' &middot; {fact(t["distance_from_69"], t.get("basis", ""))} '
                   'from the number')
    out.append("</span>")
    if t.get("tie"):
        out.append('<div class="why"><strong>Tied.</strong> '
                   + esc(", ".join(f'{x["owner"]} ({x["player"]}, {x["value"]})'
                                   for x in t["tie"]))
                   + f'. {esc(t.get("note", ""))}</div>')
    g = t.get("guard")
    if g:
        state = ("No empty roster spots. Clear on the only half of the rule that can "
                 "be checked from the data." if g.get("passes_empty_spot_test")
                 else "Empty roster spots found. Referred to the commissioner.")
        out.append(f'<div class="why"><strong>Anti-tanking guard.</strong> {esc(state)} '
                   f'{esc(g.get("note", ""))}</div>')
    out.append("</div>")
    return "".join(out)


def perf_line(r, direction):
    """One started player, with the reading that justifies calling him out."""
    if r["vs_own_average"] is not None:
        delta = r["vs_own_average"]
        basis = (f'{r["points"]} scored against his own average of '
                 f'{r["own_average"]}. {r["basis"]}')
    elif r["vs_replacement"] is not None:
        delta = r["vs_replacement"]
        basis = (f'{r["points"]} scored against a replacement level of '
                 f'{r["replacement_level"]}. {r["basis"]}')
    else:
        delta, basis = None, r["basis"]
    sign = "+" if (delta or 0) >= 0 else ""
    # The box score line is what turns a verdict into evidence. Without it
    # "4.54" is an accusation; with it, it is an account of the afternoon.
    line = f' &mdash; {esc(r["stat_line"])}' if r.get("stat_line") else ""
    return (f'<span class="perf {direction}"><b>{esc(r["player"])}</b> '
            f'{esc(r["position"] or "?")} {fact(r["points"], basis)}'
            + (f' ({sign}{fact(round(delta, 2), basis)})' if delta is not None else "")
            + line + "</span>")


def team_column(owner, rows, ss, moves):
    """One side of a game: who delivered, who did not, and what it cost."""
    up = [r for r in rows if (r["vs_own_average"] or r["vs_replacement"] or 0) > 0][:2]
    down = [r for r in reversed(rows)
            if (r["vs_own_average"] if r["vs_own_average"] is not None
                else r["vs_replacement"] or 0) < 0][:2]
    out = [f'<div class="col item"><span class="who">{esc(owner)}</span>']
    if up:
        out.append('<h3>Delivered</h3>')
        out.extend(perf_line(r, "up") for r in up)
    if down:
        out.append('<h3>Did not</h3>')
        out.extend(perf_line(r, "down") for r in down)
    if ss:
        out.append('<h3>Lineup</h3>')
        if ss.get("illegal_lineup"):
            out.append('<span class="perf down">Scored more than the best legal '
                       'lineup allows, which means the lineup as set was not legal '
                       'under a champion rule the platform cannot enforce. '
                       'Referred, not graded.</span>')
        elif ss["left_on_bench"] <= 0.02:
            out.append('<span class="perf">Optimal. Nothing left on the bench.</span>')
        else:
            out.append(f'<span class="perf">Left '
                       f'{fact(ss["left_on_bench"], "optimal minus actual")} on the bench'
                       + (", which cost him the game" if ss["flipped"] else "")
                       + ".</span>")
        for miss in ss["should_have_started"][:2]:
            pl = miss.get("prelock") or {}
            tail = (f' Sunday morning: {esc(pl.get("injury_status") or "no designation")}'
                    f'{", on bye" if pl.get("on_bye") else ""}.') if pl else ""
            out.append(f'<span class="perf down">Benched <b>{esc(miss["player"])}</b> '
                       f'{esc(miss["position"])} '
                       f'{fact(miss["points"], "player points, matchups feed")}.{tail}</span>')
    if moves:
        out.append('<h3>Moves</h3>')
        for t in moves[:2]:
            faab = (f', {fact(t["faab_spent"], "waiver bid, transactions feed")} FAAB'
                    if t.get("faab_spent") else "")
            ar = t["above_replacement"]
            out.append(f'<span class="perf {"up" if (ar or 0) >= 0 else "down"}">'
                       f'Added <b>{esc(t["player"])}</b>{faab}: '
                       f'{fact(t["points_that_week"], t["basis"])}'
                       + (f', {fact(ar, "points that week minus replacement")} above '
                          f'replacement' if ar is not None else "") + ".</span>")
    out.append("</div>")
    return "".join(out)


def sec_provenance(pack, prose):
    """State plainly what this week's grades could and could not be based on.

    Both notices used to live in sections that the matchup restructure removed.
    Losing them would have left the page silently claiming more certainty than
    it has, which is the one failure this whole pipeline exists to prevent.
    """
    out = []
    if not pack.get("prelock_snapshot"):
        out.append('<div class="item bad"><span class="tag">No pre-lock capture</span>'
                   '<span class="line"><span class="redact">LINEUP STATE UNRECORDED'
                   '</span></span><div class="why">Lineup calls below are graded on '
                   'the outcome, not on what was knowable Sunday morning. Read them '
                   'as results, not as decisions.</div></div>')
    if not pack.get("box_scores"):
        out.append('<div class="item bad"><span class="tag">No box scores</span>'
                   '<span class="line"><span class="redact">STAT LINES UNAVAILABLE'
                   '</span></span><div class="why">Only fantasy points were on file '
                   'for this week. Run pull_stats.py to fill in what the players '
                   'actually did.</div></div>')
    return "".join(out)


def sec_games(pack, prose):
    """One block per game. The week is a set of five arguments, not one story."""
    blurbs = (prose or {}).get("matchups") or {}
    ss_by_owner = {s["owner"]: s for s in pack["start_sit"]}
    moves_by_owner = {}
    for t in pack["transactions"]:
        if t["above_replacement"] is not None:
            moves_by_owner.setdefault(t["owner"], []).append(t)
    ms = pack["matchups"]
    out = []
    for i, m in enumerate(ms):
        tag = ("Widest margin" if i == 0
               else "Closest" if i == len(ms) - 1 else f"Margin {m['margin']}")
        out.append(f'<div class="game"><h2>Game {i + 1} of {len(ms)}'
                   f'<span>{esc(tag)}</span></h2>'
                   f'<p class="score">{esc(m["winner"])} '
                   f'{fact(m["winner_points"], m["basis"])} &mdash; '
                   f'{fact(m["loser_points"], m["basis"])} {esc(m["loser"])}</p>')
        out.append(narr(blurbs.get(f'{m["winner"]}|{m["loser"]}')))
        perf = m.get("performances") or {}
        out.append('<div class="grid">')
        for owner in (m["winner"], m["loser"]):
            out.append(team_column(owner, perf.get(owner, []),
                                   ss_by_owner.get(owner),
                                   moves_by_owner.get(owner, [])))
        out.append("</div></div>")
    return "".join(out)


def sec_sacko(pack, prose):
    sr = pack["sacko_race"]
    out = [f'<div class="item bad"><span class="tag">Currently last</span>'
           f'<span class="who">{esc(sr["current_holder"])}</span>']
    if sr.get("tie"):
        out.append(f'<span class="line">Level with {esc(", ".join(sr["tie"]))}.</span>'
                   f'<div class="why">{esc(sr["note"])}</div>')
    out.append("</div>")
    for c in sr["contenders"][:-1]:
        out.append(f'<div class="item"><span class="who">{esc(c["owner"])}</span>'
                   f'<span class="line">{fact(c["wins"], c["basis"])} and '
                   f'{fact(c["losses"], c["basis"])}, '
                   f'{fact(c["points_for"], c["basis"])} scored.</span></div>')
    return "".join(out) + narr((prose or {}).get("sacko"))


def sec_archive(pack, prose):
    m = pack.get("memory")
    if not m:
        return ""
    if m["bigger_margins_on_record"] == 0:
        line = "Nothing on record is worse."
    else:
        line = (f'{fact(m["bigger_margins_on_record"], m["basis"])} worse beatings are on '
                f'record. The worst is {fact(m["record_margin"]["margin"], m["basis"])}, '
                f'{esc(m["record_margin"]["season"])} week {esc(m["record_margin"]["week"])}.')
    return (f'<div class="item"><span class="who">Margins, held against them</span>'
            f'<span class="line">{esc(m["winner"])} beat {esc(m["loser"])} by '
            f'{fact(m["this_week_margin"], "biggest margin this week")}. {line}</span>'
            f'<div class="why">{esc(m["basis"])}</div></div>'
            + narr((prose or {}).get("archive")))


def sec_roast(pack, prose):
    """Closing argument. Prose only, and the one place with no obligation to be
    balanced. Still cannot invent a number."""
    body = narr((prose or {}).get("roast"))
    if not body:
        return ('<div class="roast"><span class="tag">Nothing filed</span>'
                '<span class="line">No closing statement was submitted this week. '
                'The subjects got off lightly.</span></div>')
    return f'<div class="roast">{body}</div>'


# Matchups are the spine. Decisions and moves live inside the game they
# affected rather than in a league-wide league table, because a benched running
# back is only interesting next to the score it did or did not change.
SECTIONS = [
    ("Mandated payout", "Rule 4a", sec_theme),
    ("On the record", "What this is based on", sec_provenance),
    (None, None, sec_games),
    ("Terminal standing", "Sacko watch", sec_sacko),
    ("Archive", "Held on file", sec_archive),
    ("Roast", "Closing statement", sec_roast),
]


def render_page(pack, prose=None):
    out = [HEAD.format(title=f'Week {pack["week"]} {pack["season"]} - Amanda Belichick',
                       css=CSS)]
    out.append('<header><div class="org">'
               '<span>Amanda Belichick &middot; automated league oversight</span>'
               f'<span>Season {esc(pack["season"])} &middot; file '
               f'{esc(str(pack["week"]).zfill(2))} of 14</span></div>'
               f'<h1>Week {esc(pack["week"])}</h1>'
               '<p class="dek">Ten subjects. Every decision recorded, re-derived from '
               'source, and graded against what was knowable at kickoff. Tap any figure '
               'to see where it came from. Appeals are not heard.</p></header>')
    out.append(narr((prose or {}).get("cold_open")))
    for name, eyebrow, fn in SECTIONS:
        body = fn(pack, prose)
        if not body:
            continue
        if name is None:
            out.append(f"<section>{body}</section>")
        else:
            out.append(f'<section><h2>{esc(name)}<span>{esc(eyebrow)}</span></h2>'
                       f'{body}</section>')
    out.append('<footer>Compiled ' + esc(pack.get("generated_utc", "")) +
               '<br>Every figure re-derived from Sleeper data and checked against the '
               'ledger before publication.<br>The tone is editorial. The numbers are not.'
               '</footer></div></body></html>')
    return "".join(out)


def render_index(packs):
    out = [HEAD.format(title="Amanda Belichick", css=CSS)]
    out.append('<header><div class="org">'
               '<span>Amanda Belichick &middot; automated league oversight</span>'
               '<span>Case files</span></div><h1>Recaps</h1>'
               '<p class="dek">One file per week. Nothing is deleted.</p></header>'
               '<div class="idx">')
    for p in sorted(packs, key=lambda x: (x["season"], x["week"]), reverse=True):
        top = p["matchups"][0] if p["matchups"] else None
        sub = f'{top["winner"]} def. {top["loser"]} by {top["margin"]}' if top else ""
        out.append(f'<a href="{p["season"]}-wk{p["week"]:02d}.html">'
                   f'<span class="who">{p["season"]} week {p["week"]}</span>'
                   f'<span class="line">{esc(sub)}</span></a>')
    out.append("</div></div></body></html>")
    return "".join(out)


def load_prose(path):
    if not path or not os.path.exists(path):
        return None
    raw = open(path).read()
    if path.endswith(".json"):
        return json.loads(raw)
    return {"cold_open": raw}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack")
    ap.add_argument("--all", help="a directory of packs")
    ap.add_argument("--prose", help="prose file for the single pack given")
    ap.add_argument("--prose-dir", help="directory of <season>-wk<NN>.json files")
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    paths = ([a.pack] if a.pack else []) + (
        sorted(glob.glob(os.path.join(a.all, "*.json"))) if a.all else [])
    if not paths:
        print("Nothing to render. Pass --pack or --all.")
        return 1

    os.makedirs(a.out, exist_ok=True)
    packs, blocked = [], 0
    for p in paths:
        pack = json.load(open(p))
        stem = f'{pack["season"]}-wk{pack["week"]:02d}'
        cand = a.prose or (os.path.join(a.prose_dir, stem + ".json") if a.prose_dir else None)
        prose = load_prose(cand)
        if prose:
            unknown = guard_prose(pack, prose)
            orphans = orphan_matchup_keys(pack, prose)
            if unknown or orphans:
                blocked += 1
                print(f"  BLOCKED {stem}")
                for u in unknown[:10]:
                    print(f"          unverified number: {u}")
                for o in orphans:
                    print(f"          blurb for a game that did not happen: {o}")
                print("          Not published.")
                continue
        with open(os.path.join(a.out, stem + ".html"), "w") as f:
            f.write(render_page(pack, prose))
        packs.append(pack)
        print(f"  rendered {stem}.html" + ("  (with prose)" if prose else "  (facts only)"))

    if packs:
        with open(os.path.join(a.out, "index.html"), "w") as f:
            f.write(render_index(packs))
        print(f"\n{len(packs)} page(s) written to {a.out}/")
    if blocked:
        print(f"{blocked} page(s) blocked. DO NOT PUBLISH.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
