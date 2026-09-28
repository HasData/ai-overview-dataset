"""Site-level view: do the sites that own a group's top positions get into AI Overviews
on their own turf?

Per intent group, takes the top-10 domains most often holding organic position 1 on
the panel (study/top1_domains.json, computed offline; google.com excluded since its
position-1 hits are its own properties). For each domain, one SERP call with
q=site:{domain} fetches the pages Google itself ranks highest for that site, page
titles become up to five natural queries (brand tails stripped), and each query gets
one SERP call recording: the domain's best organic position, whether the SERP carries
an AI Overview, and whether the domain is among the answer's cited sources (token
follow-up when needed, same protocol as the panel).

Sequential, one concurrent request, resumable: writes study/site_study.json after
every query. Aggregates per domain and per intent at the end.
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse

import requests as rq

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
S = pathlib.Path(__file__).resolve().parent / "out"
OUT = S / "site_study.json"
KEY = os.environ["HASDATA_API_KEY"]
PER_DOMAIN_QUERIES = 5
# platforms and UGC excluded per the study's design: the question is whether NICHE
# sites get cited from their own turf; platforms lead everywhere by default
SKIP = {"google.com", "support.google.com", "play.google.com", "apps.apple.com",
        "reddit.com", "youtube.com", "m.youtube.com", "medium.com", "quora.com",
        "facebook.com", "instagram.com", "tiktok.com", "pinterest.com",
        "twitter.com", "x.com", "linkedin.com", "amazon.com", "walmart.com",
        "yelp.com", "m.yelp.com", "en.wikipedia.org", "wikipedia.org"}
# a topical hint per bucket keeps site: results on the group's turf, otherwise
# platforms return their corporate pages instead of the content that ranks
HINT = {"comparison": "vs", "diy": "how to build", "events": "austin events",
        "howto": "how to", "informational": "what is", "local": "austin",
        "navigational": "", "purchase": "best", "recipe": "recipe",
        "symptom": "symptoms", "tech": "how to fix", "travel": "travel guide"}


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


def serp(q):
    return call("https://api.hasdata.com/scrape/google/serp",
                {"q": q, "location": "Austin,Texas,United States", "deviceType": "desktop"})


def domain_of(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


def title_to_query(title, dom):
    t = title or ""
    # strip brand tails: " - Brand", " | Brand", " – Brand", "... : r/sub"
    t = re.split(r"\s+[|\-–—:]\s+(?=[A-Z0-9])", t)[0] if len(t) > 60 else re.sub(r"\s+[|\-–—]\s+[^|\-–—]{2,40}$", "", t)
    t = re.sub(r"\s*[:\-]?\s*r/[A-Za-z0-9_]+\s*$", "", t)
    t = re.sub(r"[\"“”]+", "", t).strip(" .!?")
    words = t.split()
    if not (3 <= len(words) <= 12):
        return None
    if dom.split(".")[0].lower() in t.lower().replace(" ", ""):
        pass  # brand word inside is fine, queries can be navigational-ish
    return t


tops = json.loads((S / "top1_domains.json").read_text(encoding="utf-8"))
state = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"domains": {}}
domains = state["domains"]

targets = []
for bucket, counts in tops.items():
    kept = [d for d in counts if d not in SKIP][:10]
    targets += [(bucket, d) for d in kept]
print(f"targets: {len(targets)} niche domains")

for bucket, dom in targets:
    key = f"{bucket}/{dom}"
    entry = domains.get(key, {"bucket": bucket, "domain": dom, "queries": []})
    if entry.get("done"):
        continue

    if "pages" not in entry:
        sr = serp(f"site:{dom} {HINT.get(bucket, '')}".strip())
        organic = sr.get("organicResults") or []
        entry["pages"] = [{"title": o.get("title"), "url": o.get("link")} for o in organic[:10]]
        domains[key] = entry
        OUT.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        time.sleep(1)

    queries = []
    seen = {q["query"] for q in entry["queries"]}
    for pg in entry["pages"]:
        q = title_to_query(pg.get("title"), dom)
        if q and q not in seen and q not in queries:
            queries.append(q)
        if len(entry["queries"]) + len(queries) >= PER_DOMAIN_QUERIES:
            break

    for q in queries:
        sr = serp(q)
        organic = sr.get("organicResults") or []
        best = None
        for o in organic:
            if domain_of(o.get("link") or "") == dom:
                pos = o.get("position") or 99
                best = pos if best is None else min(best, pos)
        aio = sr.get("aiOverview")
        sources = []
        if isinstance(aio, dict):
            refs = aio.get("references") or aio.get("sources") or []
            token = aio.get("pageToken")
            if not refs and token:
                ao = call("https://api.hasdata.com/scrape/google/ai-overview", {"pageToken": token})
                block = ao.get("aiOverview") or ao
                refs = (block.get("references") or block.get("sources") or []) if isinstance(block, dict) else []
            sources = sorted({domain_of(x.get("link") or x.get("url") or "") for x in refs if isinstance(x, dict)} - {""})
        entry["queries"].append({"query": q, "best_position": best, "aio": bool(aio),
                                 "sources_parsed": bool(sources), "cited": dom in sources})
        domains[key] = entry
        OUT.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        time.sleep(1.2)

    entry["done"] = True
    domains[key] = entry
    OUT.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    n_done = sum(1 for v in domains.values() if v.get("done"))
    print(f"{n_done}/{len(targets)} {key}: {len(entry['queries'])} queries")

# aggregate (platform entries from earlier runs are excluded here too)
per_domain = {}
for key, e in domains.items():
    if e["domain"] in SKIP:
        continue
    qs = e["queries"]
    if not qs:
        continue
    top10 = [q for q in qs if q["best_position"] and q["best_position"] <= 10]
    aio_qs = [q for q in qs if q["aio"] and q["sources_parsed"]]
    top10_aio = [q for q in aio_qs if q["best_position"] and q["best_position"] <= 10]
    per_domain[key] = {
        "bucket": e["bucket"], "domain": e["domain"], "queries": len(qs),
        "in_top10": len(top10), "aio_serps": len(aio_qs),
        "cited_overall": sum(q["cited"] for q in aio_qs),
        "cited_when_top10": sum(q["cited"] for q in top10_aio),
        "top10_with_aio": len(top10_aio),
    }
state["aggregate"] = per_domain
OUT.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved site_study.json with aggregate")
