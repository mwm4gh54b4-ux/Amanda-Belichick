#!/usr/bin/env python3
"""
Pull outside coverage. Keep it quarantined.

Everything else in this pipeline is verifiable: a number goes in the fact pack,
verify_recap.py re-derives it from Sleeper data, and the number guard blocks any
figure in the prose the pack cannot account for. That guarantee is the reason a
model is allowed to write the recap at all.

Media breaks that guarantee, because a headline cannot be re-derived from
anything. So it does not go in the fact pack. It goes in its own file, marked
external, and the renderer displays it in its own section. Critically, media
numbers are NEVER added to the number guard's allowlist. If a podcast title
contains "48 carries" and the writer repeats it as fact, the guard blocks the
page, which is correct: nobody in this pipeline verified that.

The rule, stated once: **facts come from Sleeper, context comes from media, and
they are never mixed.**

WHAT IS STORED, AND WHY SO LITTLE. Headline, source, link, timestamp, and a
short excerpt for the writer's context only. The renderer shows the headline
and a link, never the excerpt. Reproducing a publisher's summary on a public
page is their copy on your site; linking to it is a citation. Write around it
in your own words or link out.

    python3 pull_media.py --validate                 # which feeds actually work
    python3 pull_media.py --out media --days 8
    python3 pull_media.py --out media --sources media_sources.json
"""

import argparse
import datetime
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# Seed list. Feeds move and die, so every one of these is a candidate rather
# than a promise. Run --validate before trusting any of them, and keep the
# working set in media_sources.json.
DEFAULT_SOURCES = [
    {"name": "Sharp Football", "url": "https://feeds.simplecast.com/UiNmc8XS",
     "kind": "podcast", "note": "confirmed working"},
    {"name": "Sharp Football Analysis",
     "url": "https://www.sharpfootballanalysis.com/feed/", "kind": "articles"},
    {"name": "The Fantasy Footballers",
     "url": "https://www.thefantasyfootballers.com/feed/", "kind": "articles"},
    {"name": "FantasyPros", "url": "https://www.fantasypros.com/nfl/rss/news.php",
     "kind": "news"},
    {"name": "The Ringer NFL", "url": "https://www.theringer.com/rss/nfl/index.xml",
     "kind": "articles"},
    {"name": "Barstool Sports", "url": "https://www.barstoolsports.com/rss",
     "kind": "articles"},
]

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def clean(text, limit=280):
    """Strip markup and squeeze whitespace. Truncated hard, because this is
    context for a writer, not content for a page."""
    if not text:
        return None
    t = WS.sub(" ", TAG.sub(" ", text)).strip()
    return (t[:limit].rstrip() + "...") if len(t) > limit else t


def parse_date(s):
    """RSS and Atom disagree about dates, and publishers disagree with both."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%a, %d %b %Y %H:%M:%S"):
        try:
            d = datetime.datetime.strptime(s.replace("GMT", "+0000"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


def strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_feed(xml_text, source):
    """Handle RSS 2.0 and Atom without a third-party parser, because a
    dependency that has to install on every CI run is a failure mode."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return [], f"not valid XML: {e}"
    items = []
    nodes = [n for n in root.iter() if strip_ns(n.tag) in ("item", "entry")]
    for n in nodes:
        rec = {}
        for child in n:
            t = strip_ns(child.tag)
            if t == "title":
                rec["title"] = clean(child.text, 200)
            elif t == "link":
                rec["link"] = (child.get("href") or child.text or "").strip()
            elif t in ("pubDate", "published", "updated") and "published" not in rec:
                rec["published_raw"] = (child.text or "").strip()
            elif t in ("description", "summary") and "excerpt" not in rec:
                rec["excerpt"] = clean(child.text)
        if not rec.get("title"):
            continue
        d = parse_date(rec.pop("published_raw", None))
        rec["published"] = d.isoformat() if d else None
        rec["source"] = source["name"]
        rec["kind"] = source.get("kind", "articles")
        rec["verified"] = False
        items.append(rec)
    if not items:
        return [], "parsed, but no items found"
    return items, None


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; amanda-belichick/1.0)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def load_sources(path):
    if path and os.path.exists(path):
        return json.load(open(path))
    return DEFAULT_SOURCES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="media")
    ap.add_argument("--sources", default="media_sources.json")
    ap.add_argument("--days", type=int, default=8,
                    help="how far back to keep items")
    ap.add_argument("--per-source", type=int, default=6)
    ap.add_argument("--validate", action="store_true",
                    help="report which feeds resolve, write nothing")
    a = ap.parse_args()

    sources = load_sources(a.sources)
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=a.days))
    all_items, report = [], []

    for src in sources:
        try:
            body = fetch(src["url"])
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            report.append((src["name"], "UNREACHABLE", str(e)[:70]))
            continue
        items, err = parse_feed(body, src)
        if err:
            report.append((src["name"], "BAD FEED", err[:70]))
            continue
        fresh = [i for i in items
                 if not i["published"]
                 or datetime.datetime.fromisoformat(i["published"]) >= cutoff]
        report.append((src["name"], "ok",
                       f"{len(items)} items, {len(fresh)} in the last {a.days} days"))
        all_items.extend(fresh[:a.per_source])

    print(f"{'SOURCE':<28}{'STATUS':<13}DETAIL")
    print("-" * 78)
    for name, status, detail in report:
        print(f"{name:<28}{status:<13}{detail}")
    print()

    if a.validate:
        working = [s for s, st, _ in report if st == "ok"]
        print(f"{len(working)} of {len(sources)} feeds resolved.")
        print("Keep the working ones in media_sources.json and drop the rest.")
        return 0

    all_items.sort(key=lambda x: x["published"] or "", reverse=True)
    os.makedirs(a.out, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(a.out, f"{stamp}.json")
    payload = {
        "pulled_utc": datetime.datetime.now(datetime.timezone.utc)
                               .replace(microsecond=0).isoformat(),
        "window_days": a.days,
        "verified": False,
        "warning": "External coverage. Nothing here has been verified against "
                   "any source of truth, and no number appearing in this file "
                   "is admissible as a fact in a recap. Context only.",
        "items": all_items,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    # Also write a stable filename. A workflow cannot reference a date-stamped
    # path without shelling out to find it, and a build step that has to guess
    # at a filename is a build step that breaks on the first slow day.
    latest = os.path.join(a.out, "latest.json")
    with open(latest, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {path} and {latest}: {len(all_items)} items from "
          f"{len({i['source'] for i in all_items})} sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
