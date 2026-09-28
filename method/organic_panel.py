"""Second pass over the AI Overview panel: organic results next to the AI answer.

Re-fetches every query of the September panel (the exact 1,100 rows recorded in
study/aio_panel.json) plus the travel bucket the 2025 study had and the panel did
not re-measure, 100 template-generated queries in the same 20x5 shape. One Google
SERP API call per query, desktop, US, Austin location, same as the first pass.

Per query it records the organic results (domain and position) alongside the same
feature flags and AI Overview sources as the first pass, so two questions become
answerable from the same SERP: whether the domains an AI answer cites also rank
in that SERP's organic results, and how stable AI Overview presence and its
source list are between two passes days apart.

Organic depth is the first five pages (positions 1-50, paginated with start=).
Pages 2-5 are fetched only when the response carries an AI answer with parsed
sources, because their sole purpose is locating cited domains; rows with no
citations keep page one, which is all the stability and travel questions need.

Runs in a single stream with a delay and a 429 retry (the account allows one
concurrent request). Writes study/organic_panel.json incrementally, one row per
query, so a crash or a restart keeps everything already recorded.
"""
import itertools
import json
import os
import pathlib
import sys
import time
import urllib.parse

import requests as rq

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
S = pathlib.Path(__file__).resolve().parent / "out"
FIRST_PASS = S / "aio_panel.json"
OUT = S / "organic_panel.json"
KEY = os.environ["HASDATA_API_KEY"]
PER_BUCKET = 100


def travel_queries():
    places = ["lisbon", "kyoto", "iceland", "costa rica", "new orleans", "santorini", "banff",
              "marrakech", "prague", "bali", "patagonia", "amalfi coast", "yellowstone", "havana",
              "seoul", "cape town", "scottish highlands", "mexico city", "azores", "slovenia"]
    tmpl = ["best time to visit {}", "{} travel guide", "how many days in {}",
            "is {} expensive to visit", "things to do in {}"]
    return [t.format(p) for p, t in itertools.product(places, tmpl)][:PER_BUCKET]


def domain(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


def call(url, params, tries=4):
    for attempt in range(tries):
        try:
            r = rq.get(url, params=params, headers={"x-api-key": KEY}, timeout=90)
        except rq.RequestException:
            time.sleep(6)
            continue
        if r.status_code == 429:
            time.sleep(8)
            continue
        try:
            return r.json()
        except ValueError:
            time.sleep(4)
    return {}


FEATURES = {
    "featured_snippet": "answerBox",
    "paa": "relatedQuestions",
    "knowledge_graph": "knowledgeGraph",
    "local_results": "localResults",
    "recipes": "recipesResults",
    "shopping": "shoppingResults",
    "inline_shopping": "inlineShoppingResults",
    "immersive_products": "immersiveProducts",
    "videos": "inlineVideos",
    "discussions": "discussionsAndForums",
    "events_results": "eventsResults",
    "perspectives": "perspectives",
}

first = json.loads(FIRST_PASS.read_text(encoding="utf-8"))
queries = [(r["bucket"], r["query"]) for r in first]
queries += [("travel", q) for q in travel_queries()]

rows = []
if OUT.exists():
    rows = json.loads(OUT.read_text(encoding="utf-8"))
done = {(r["bucket"], r["query"]) for r in rows}
total = len(queries)
print(f"second pass: {total} queries, {len(done)} already recorded")

for bucket, query in queries:
    if (bucket, query) in done:
        continue
    serp = call("https://api.hasdata.com/scrape/google/serp",
                {"q": query, "location": "Austin,Texas,United States", "deviceType": "desktop"})
    row = dict(bucket=bucket, query=query)
    for label, key in FEATURES.items():
        row[label] = bool(serp.get(key))
    def organic_entries(payload, page):
        entries = []
        for i, o in enumerate(payload.get("organicResults") or []):
            if not isinstance(o, dict):
                continue
            raw = o.get("position") or 0
            pos = raw if raw > page * 10 else page * 10 + (raw or i + 1)
            entries.append({"position": pos, "domain": domain(o.get("link") or "")})
        return entries

    row["organic"] = organic_entries(serp, 0)
    aio = serp.get("aiOverview")
    row["aio"] = bool(aio)
    row["aio_sources"] = []
    if isinstance(aio, dict):
        refs = aio.get("references") or aio.get("sources") or []
        token = aio.get("pageToken")
        if not refs and token:
            ao = call("https://api.hasdata.com/scrape/google/ai-overview", {"pageToken": token})
            block = ao.get("aiOverview") or ao
            refs = (block.get("references") or block.get("sources") or []) if isinstance(block, dict) else []
            row["aio_fetched"] = bool(refs)
        row["aio_sources"] = sorted({domain(x.get("link") or x.get("url") or "")
                                     for x in refs if isinstance(x, dict)} - {""})
    if row["aio_sources"]:
        for page in range(1, 5):
            time.sleep(1.0)
            deeper = call("https://api.hasdata.com/scrape/google/serp",
                          {"q": query, "location": "Austin,Texas,United States",
                           "deviceType": "desktop", "start": page * 10})
            entries = organic_entries(deeper, page)
            row["organic"] += entries
            if len(entries) < 8:
                break
        row["organic"] = row["organic"][:50]
    rows.append(row)
    OUT.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    n = len(rows)
    if n % 25 == 0:
        print(f"{n}/{total} done, last: [{bucket}] {query} aio={row['aio']} organic={len(row['organic'])}")
    time.sleep(1.2)

print(f"\nfinished: {len(rows)} rows saved to organic_panel.json")
