"""Aggregates the intent panel into the tables the article publishes.

Reads study/aio_panel.json (one row per query) and produces study/panel_tables.json:
trigger rate by bucket, SERP-feature matrix by bucket, citation domains overall and per
bucket, sources per answer, and the featured-snippet co-citation rate (share of queries
where the snippet's domain also appears among the AI answer's sources).
Also writes study/aio-dataset.csv, the publishable per-query dataset.
"""
import collections
import csv
import json
import pathlib
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"      # the shipped panels
OUT = ROOT / "aggregates"  # derived tables land next to the shipped ones
rows = json.loads((DATA / "aio_panel.json").read_text(encoding="utf-8"))

BUCKETS = ["symptom", "tech", "howto", "diy", "informational", "navigational",
           "local", "events", "purchase", "comparison", "recipe"]
FEATURES = ["featured_snippet", "paa", "knowledge_graph", "local_results", "recipes",
            "shopping", "inline_shopping", "immersive_products", "videos",
            "discussions", "events_results", "perspectives"]

by_bucket = collections.defaultdict(list)
for r in rows:
    by_bucket[r["bucket"]].append(r)

tables = {"n": len(rows), "buckets": {}}
for b in BUCKETS:
    rs = by_bucket.get(b, [])
    if not rs:
        continue
    n = len(rs)
    entry = {"n": n, "aio_pct": round(100 * sum(r["aio"] for r in rs) / n, 1)}
    for f in FEATURES:
        entry[f + "_pct"] = round(100 * sum(bool(r.get(f)) for r in rs) / n, 1)
    tables["buckets"][b] = entry

# citations
domains_all = collections.Counter()
domains_by_bucket = {}
refcounts = []
answers_with_sources = 0
fs_and_aio = fs_cocited = 0
for b, rs in by_bucket.items():
    cnt = collections.Counter()
    for r in rs:
        srcs = r.get("aio_sources") or []
        if r["aio"] and srcs:
            answers_with_sources += 1
            refcounts.append(len(srcs))
            for d in srcs:
                cnt[d] += 1
                domains_all[d] += 1
        if r["aio"] and r.get("featured_snippet"):
            fs_and_aio += 1
            if r.get("fs_domain") and r["fs_domain"] in srcs:
                fs_cocited += 1
    domains_by_bucket[b] = cnt.most_common(10)

tables["citations"] = dict(
    answers_with_sources=answers_with_sources,
    sources_mean=round(statistics.mean(refcounts), 1) if refcounts else 0,
    sources_median=statistics.median(refcounts) if refcounts else 0,
    unique_domains=len(domains_all),
    top_domains=domains_all.most_common(30),
    by_bucket=domains_by_bucket,
    fs_and_aio=fs_and_aio,
    fs_cocited=fs_cocited,
    fs_cocited_pct=round(100 * fs_cocited / fs_and_aio, 1) if fs_and_aio else None,
)

(OUT / "panel_tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

with open(DATA / "aio-dataset.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["intent", "query", "ai_overview", "sources",
                *FEATURES])
    for b in BUCKETS:
        for r in by_bucket.get(b, []):
            w.writerow([b, r["query"], int(r["aio"]), ";".join(r.get("aio_sources") or []),
                        *[int(bool(r.get(f))) for f in FEATURES]])

print(json.dumps({b: v["aio_pct"] for b, v in tables["buckets"].items()}, indent=1))
print("citations:", tables["citations"]["answers_with_sources"], "answers,",
      tables["citations"]["unique_domains"], "domains, fs co-cited",
      tables["citations"]["fs_cocited_pct"], "%")
print("saved panel_tables.json and aio-dataset.csv")
