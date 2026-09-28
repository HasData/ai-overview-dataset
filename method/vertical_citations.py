"""Citation stats from the July 2026 audit snapshot: whom AI Overviews cite in our vertical.

Reads the per-article audit files in blog-audit/data/*.json. Each carries a `serp` snapshot
of the article's top GSC queries (live Google SERP, gl=us) with `ai_overview` presence and
`ao_refs`, the sources the AI answer cited. This aggregates: answers with parsed sources,
citations per answer, the most-cited domains, and where HasData content appears.
Writes study/vertical_citations.json.
"""
import collections
import glob
import json
import pathlib
import statistics
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[5]
S = HERE.parent / "out"
S.mkdir(parents=True, exist_ok=True)


def dom(u):
    try:
        h = urllib.parse.urlparse(u).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except ValueError:
        return ""


answers = with_refs = 0
domains = collections.Counter()
refcounts = []
hasdata_queries = []
for f in glob.glob(str(ROOT / "blog-audit" / "data" / "*.json")):
    d = json.load(open(f, encoding="utf-8"))
    serp = d.get("serp") or {}
    if not isinstance(serp, dict):
        continue
    for q, s in serp.items():
        if not isinstance(s, dict) or not s.get("ai_overview"):
            continue
        answers += 1
        refs = s.get("ao_refs") or []
        if not refs:
            continue
        with_refs += 1
        ds = {dom(r.get("link") or "") for r in refs if isinstance(r, dict)} - {""}
        refcounts.append(len(refs))
        for x in ds:
            domains[x] += 1
        ours = [r.get("link") for r in refs if isinstance(r, dict)
                and "hasdata" in json.dumps(r).lower()]
        if ours:
            hasdata_queries.append(dict(query=q, refs=ours))

out = dict(
    answers=answers, with_refs=with_refs,
    refs_mean=round(statistics.mean(refcounts), 1), refs_median=statistics.median(refcounts),
    top_domains=domains.most_common(25),
    hasdata_cited_queries=len(hasdata_queries), hasdata_refs=hasdata_queries,
)
(S / "vertical_citations.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
print(f"answers {answers}, with refs {with_refs}, hasdata cited in {len(hasdata_queries)} queries")
print("saved vertical_citations.json")
