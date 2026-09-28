"""Analysis of the second panel pass (study/organic_panel.json) against the first.

Three questions, one output file (study/organic_analysis.json):

  overlap    For every AI answer with parsed sources, do the cited domains also
             rank in the same SERP's organic results? Per answer: does at least
             one cited domain sit in the organic top 10. Per citation: the
             organic position of each cited domain (1-10, 11-20, absent).
             Overall, per intent, and for the most-cited domains.

  stability  The same 1,100 queries fetched twice, days apart. AI Overview
             presence flips (present both passes / appeared / disappeared /
             absent both), trigger rate per intent per pass, and source churn:
             Jaccard similarity of the cited-domain sets where both passes
             carried an answer with parsed sources.

  travel     The bucket the 2025 study had and the first pass skipped:
             trigger rate, feature rates, and citation leaders.

Exclusion: during the second-pass collection window the SERP parser returned
google.com as the link for some organic slots and some answer sources (a fresh
fetch of the same queries shows real domains in those slots, and bare google.com
appears at organic positions 1-5 on SERPs where that is impossible). Those
entries carry no attributable domain, so bare "google.com" is dropped from both
the organic lists and the cited-source lists before any matching. Subdomains
(support.google.com, translate.google.com) are real pages and stay. Every
coupling number is therefore a floor: a mangled entry that was in truth a match
counts as a miss.
"""
import io
import json
import pathlib
import sys
from collections import Counter, defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"      # the shipped panels
OUT = ROOT / "aggregates"  # derived tables land next to the shipped ones

NOISE = {"google.com"}


def clean_sources(row):
    return [d for d in (row.get("aio_sources") or []) if d not in NOISE]


def clean_organic(row):
    return [o for o in (row.get("organic") or []) if o["domain"] and o["domain"] not in NOISE]


first = json.loads((DATA / "aio_panel.json").read_text(encoding="utf-8"))
second = json.loads((DATA / "organic_panel.json").read_text(encoding="utf-8"))
out = {"second_pass_rows": len(second)}

# ---------- overlap: cited domains vs same-SERP organic ----------
POS_ABSENT = "absent"
per_intent = defaultdict(lambda: dict(answers=0, answers_top10=0, citations=0,
                                      cit_top10=0, cit_11_20=0, cit_21_50=0, cit_absent=0))
pos_hist = Counter()
domain_stats = defaultdict(lambda: dict(citations=0, top10=0))
for r in second:
    sources = clean_sources(r)
    if not (r["aio"] and sources):
        continue
    ranks: dict = {}
    for o in clean_organic(r):
        ranks[o["domain"]] = min(ranks.get(o["domain"], 999), o["position"])
    b = per_intent[r["bucket"]]
    b["answers"] += 1
    hit10 = False
    for d in sources:
        b["citations"] += 1
        domain_stats[d]["citations"] += 1
        pos = ranks.get(d)
        if pos is None:
            b["cit_absent"] += 1
            pos_hist[POS_ABSENT] += 1
        elif pos <= 10:
            b["cit_top10"] += 1
            pos_hist[pos] += 1
            domain_stats[d]["top10"] += 1
            hit10 = True
        elif pos <= 20:
            b["cit_11_20"] += 1
            pos_hist["11-20"] += 1
        else:
            b["cit_21_50"] += 1
            pos_hist["21-50"] += 1
    if hit10:
        b["answers_top10"] += 1

tot = dict(answers=0, answers_top10=0, citations=0, cit_top10=0, cit_11_20=0, cit_21_50=0, cit_absent=0)
for b in per_intent.values():
    for k in tot:
        tot[k] += b[k]
out["overlap"] = {
    "total": tot,
    "answers_top10_share": round(tot["answers_top10"] / tot["answers"], 3) if tot["answers"] else None,
    "citations_top10_share": round(tot["cit_top10"] / tot["citations"], 3) if tot["citations"] else None,
    "citations_absent_share": round(tot["cit_absent"] / tot["citations"], 3) if tot["citations"] else None,
    "position_histogram": {str(k): pos_hist[k] for k in list(range(1, 11)) + ["11-20", "21-50", POS_ABSENT]},
    "per_intent": {k: dict(v, answers_top10_share=round(v["answers_top10"] / v["answers"], 3) if v["answers"] else None)
                   for k, v in sorted(per_intent.items())},
    "top_domains": {d: dict(s, top10_share=round(s["top10"] / s["citations"], 3))
                    for d, s in sorted(domain_stats.items(), key=lambda kv: -kv[1]["citations"])[:15]},
}

# ---------- stability: pass 1 vs pass 2 on the shared 1,100 ----------
first_by = {(r["bucket"], r["query"]): r for r in first}
flips = Counter()
per_bucket = defaultdict(lambda: Counter())
jacc = []
retention = []
jacc_by_bucket = defaultdict(list)
zero_common = 0
both_sources = 0
for r in second:
    key = (r["bucket"], r["query"])
    f = first_by.get(key)
    if f is None:
        continue
    state = ("both" if f["aio"] and r["aio"] else "appeared" if r["aio"] else
             "disappeared" if f["aio"] else "neither")
    flips[state] += 1
    per_bucket[r["bucket"]][state] += 1
    per_bucket[r["bucket"]]["n"] += 1
    a, b2 = set(clean_sources(f)), set(clean_sources(r))
    if f["aio"] and r["aio"] and a and b2:
        j = len(a & b2) / len(a | b2)
        jacc.append(j)
        jacc_by_bucket[r["bucket"]].append(j)
        retention.append(len(a & b2) / len(a))
        both_sources += 1
        if not a & b2:
            zero_common += 1

jacc.sort()
retention.sort()
out["stability"] = {
    "matched_queries": sum(flips.values()),
    "flips": dict(flips),
    "per_intent": {b: {"n": c["n"], "both": c["both"], "appeared": c["appeared"],
                       "disappeared": c["disappeared"], "neither": c["neither"],
                       "rate_pass1": round((c["both"] + c["disappeared"]) / c["n"], 3),
                       "rate_pass2": round((c["both"] + c["appeared"]) / c["n"], 3)}
                   for b, c in sorted(per_bucket.items())},
    "source_churn": {
        "answers_with_sources_both_passes": both_sources,
        "median_jaccard": round(jacc[len(jacc) // 2], 3) if jacc else None,
        "median_retained_share": round(retention[len(retention) // 2], 3) if retention else None,
        "replaced_over_half": sum(1 for x in retention if x < 0.5),
        "replaced_over_half_share": round(sum(1 for x in retention if x < 0.5) / len(retention), 3) if retention else None,
        "zero_common_sources": zero_common,
        "zero_common_share": round(zero_common / both_sources, 3) if both_sources else None,
        "median_jaccard_per_intent": {b: round(sorted(v)[len(v) // 2], 3)
                                      for b, v in sorted(jacc_by_bucket.items()) if v},
    },
}

# ---------- travel: the bucket the first pass skipped ----------
travel = [r for r in second if r["bucket"] == "travel"]
if travel:
    n = len(travel)
    feats = ["featured_snippet", "paa", "knowledge_graph", "local_results",
             "videos", "discussions", "perspectives", "shopping", "immersive_products"]
    cited = Counter()
    answers = 0
    for r in travel:
        srcs = clean_sources(r)
        if r["aio"] and srcs:
            answers += 1
            for d in srcs:
                cited[d] += 1
    out["travel"] = {
        "n": n,
        "aio_rate": round(sum(r["aio"] for r in travel) / n, 3),
        "feature_rates": {f: round(sum(bool(r.get(f)) for r in travel) / n, 3) for f in feats},
        "answers_with_sources": answers,
        "top_cited": dict(cited.most_common(10)),
    }

(OUT / "organic_analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: out[k] for k in out if k != "overlap"}, ensure_ascii=False, indent=1)[:1200])
print("...")
print("overlap:", json.dumps({k: v for k, v in out["overlap"].items() if k not in ("per_intent", "top_domains")},
                             ensure_ascii=False, indent=1))
print("saved organic_analysis.json")
