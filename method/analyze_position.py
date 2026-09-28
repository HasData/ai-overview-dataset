"""The reverse question: given an organic position, what are the odds of being cited?

For every second-pass SERP whose AI answer carried parsed sources, take each organic
position N and check whether the domain holding it appears in that same answer's
cited domains. The per-position share across all such SERPs is P(cited | position).
Positions 1-10 exist on every such SERP; 11-50 exist because the pass fetched five
pages for sourced answers. Also computed: the share of SERPs whose #1 domain is NOT
cited (the user's question: ranking top does not guarantee citation), the same for
the whole top 3, and per-intent rates for position 1. Writes study/position_odds.json.
"""
import io
import json
import pathlib
import sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"      # the shipped panels
OUT = ROOT / "aggregates"  # derived tables land next to the shipped ones
rows = json.loads((DATA / "organic_panel.json").read_text(encoding="utf-8"))

# Bare google.com entries in this pass are a parser artifact (see analyze_organic.py):
# dropped from both the source lists and the organic lists before matching, so every
# rate here is a floor. Subdomains like support.google.com stay.
NOISE = {"google.com"}

pos_total = defaultdict(int)
pos_cited = defaultdict(int)
slot1_known = 0
top1_missed = 0
top3_known = 0
top3_all_missed = 0
answers = 0
intent_top1 = defaultdict(lambda: [0, 0])  # cited, total

for r in rows:
    r = dict(r, aio_sources=[d for d in (r.get("aio_sources") or []) if d not in NOISE],
             organic=[o for o in (r.get("organic") or []) if o["domain"] and o["domain"] not in NOISE])
    if not (r["aio"] and r["aio_sources"]):
        continue
    organic = r.get("organic") or []
    if not organic:
        continue
    answers += 1
    cited = set(r["aio_sources"])
    best = {}
    for o in organic:
        if o["domain"] and (o["domain"] not in best or o["position"] < best[o["domain"]]):
            best[o["domain"]] = o["position"]
    # per position: the domain OCCUPYING position n (first occurrence of that slot)
    seen_slots = set()
    for o in organic:
        n = o["position"]
        if not o["domain"] or n in seen_slots:
            continue
        seen_slots.add(n)
        pos_total[n] += 1
        if o["domain"] in cited:
            pos_cited[n] += 1
    slot1 = next((o["domain"] for o in organic if o["position"] == 1 and o["domain"]), None)
    if slot1:
        slot1_known += 1
        it = intent_top1[r["bucket"]]
        it[1] += 1
        if slot1 in cited:
            it[0] += 1
        else:
            top1_missed += 1
    top3 = [o["domain"] for o in organic if o["position"] <= 3 and o["domain"]]
    if top3:
        top3_known += 1
        if not any(d in cited for d in top3):
            top3_all_missed += 1

out = {
    "answers_with_organic": answers,
    "per_position": {str(n): {"n": pos_total[n], "cited": pos_cited[n],
                              "rate": round(pos_cited[n] / pos_total[n], 3)}
                     for n in sorted(pos_total) if pos_total[n] >= 30},
    "slot1_known": slot1_known,
    "top1_not_cited": top1_missed,
    "top1_not_cited_share": round(top1_missed / slot1_known, 3),
    "top3_known": top3_known,
    "top3_none_cited": top3_all_missed,
    "top3_none_cited_share": round(top3_all_missed / top3_known, 3),
    "intent_top1_cited_rate": {b: round(c / t, 3) for b, (c, t) in sorted(intent_top1.items()) if t >= 10},
}
(OUT / "position_odds.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
