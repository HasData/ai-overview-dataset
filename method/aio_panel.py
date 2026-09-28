"""AI Overview trigger rate and citation share by intent, measured on a fixed panel.

Replays the 2025 study's protocol on a smaller, honest sample: 11 intent buckets
mirroring the original's categories, 100 template-generated queries per bucket,
one Google SERP API call per query (desktop, US). When the SERP carries an
aiOverview block with a pageToken, the AI Overview API fetches the answer content
and its source list within the token's 4-minute window.

Per query it records which SERP features appeared (AI Overview, featured snippet,
PAA, knowledge graph, local results, shopping/product blocks, recipes, videos,
discussions) and, when an AI answer is present, the cited source domains.

Runs in a single stream with a delay and a 429 retry (the account allows one
concurrent request). Writes study/aio_panel.json incrementally, one row per query,
so a crash keeps partial results. Restarting skips queries already recorded.
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
S.mkdir(parents=True, exist_ok=True)
OUT = S / "aio_panel.json"
KEY = os.environ["HASDATA_API_KEY"]
PER_BUCKET = 100


def build_queries():
    q = {}

    symptoms = ["headache behind the eyes", "lower back pain", "sore throat", "ringing in ears",
                "numbness in fingers", "chest tightness", "dizziness when standing up", "dry cough at night",
                "swollen ankles", "eye twitching", "stomach cramps after eating", "shortness of breath",
                "night sweats", "frequent urination", "muscle spasms in legs", "tingling in feet",
                "heart palpitations", "bloating after meals", "joint stiffness in the morning", "blurry vision"]
    tmpl = ["what causes {}", "why do i have {}", "is {} serious", "how to relieve {}", "{} that wont go away"]
    q["symptom"] = [t.format(s) for s, t in itertools.product(symptoms, tmpl)][:PER_BUCKET]

    tech = ["wifi keeps disconnecting", "laptop wont turn on", "printer offline", "bluetooth not pairing",
            "phone battery draining fast", "windows update stuck", "blue screen error", "usb device not recognized",
            "slow internet on one computer", "external monitor not detected", "audio crackling", "webcam not working",
            "hard drive not showing up", "keyboard typing wrong characters", "screen flickering", "pc overheating",
            "router red light", "email not syncing", "apps crashing on android", "touchpad not responding"]
    tmpl = ["how to fix {}", "{} fix", "why is my {}", "{} after update", "{} windows 11"]
    q["tech"] = [t.format(s) for s, t in itertools.product(tech, tmpl)][:PER_BUCKET]

    howto = ["tie a tie", "write a resume", "boil eggs", "remove a stripped screw", "jump start a car",
             "fold a fitted sheet", "unclog a drain", "sharpen a knife", "iron a shirt", "polish shoes",
             "descale a kettle", "clean white sneakers", "wrap a gift", "sew a button", "parallel park",
             "make cold brew", "whistle loudly", "shuffle cards", "read faster", "meditate"]
    tmpl = ["how to {}", "best way to {}", "how to {} step by step", "how to {} for beginners", "easiest way to {}"]
    q["howto"] = [t.format(s) for s, t in itertools.product(howto, tmpl)][:PER_BUCKET]

    diy = ["build a bookshelf", "paint kitchen cabinets", "install a ceiling fan", "tile a bathroom floor",
           "build a raised garden bed", "hang drywall", "replace a faucet", "install laminate flooring",
           "build a deck", "soundproof a room", "install a dimmer switch", "refinish a table",
           "build a fire pit", "insulate a garage", "install shelves", "repair a fence",
           "make concrete countertops", "frame a wall", "install a backsplash", "build stairs"]
    tmpl = ["how to {}", "{} yourself", "{} diy", "{} cost diy", "{} without professional"]
    q["diy"] = [t.format(s) for s, t in itertools.product(diy, tmpl)][:PER_BUCKET]

    info = ["photosynthesis", "the electoral college", "black holes", "inflation", "dna replication",
            "plate tectonics", "the immune system", "machine learning", "the water cycle", "quantum computing",
            "the french revolution", "supply and demand", "the roman empire", "climate change", "evolution",
            "the stock market", "antibiotics resistance", "renewable energy", "the big bang", "cryptocurrency"]
    tmpl = ["what is {}", "how does {} work", "{} explained", "{} for kids", "why is {} important"]
    q["informational"] = [t.format(s) for s, t in itertools.product(info, tmpl)][:PER_BUCKET]

    brands = ["netflix", "spotify", "amazon", "gmail", "facebook", "youtube", "instagram", "paypal",
              "zoom", "dropbox", "linkedin", "twitter", "reddit", "ebay", "walmart", "target",
              "bank of america", "chase", "delta airlines", "fedex"]
    tmpl = ["{} login", "{} customer service", "{} app", "{} account settings", "{} help center"]
    q["navigational"] = [t.format(s) for s, t in itertools.product(brands, tmpl)][:PER_BUCKET]

    places = ["coffee shop", "pizza", "urgent care", "car wash", "dry cleaner", "gym", "pharmacy",
              "hair salon", "dentist", "sushi restaurant", "hardware store", "vet clinic", "bakery",
              "tire shop", "nail salon", "thai food", "oil change", "locksmith", "farmers market", "brunch"]
    tmpl = ["{} near me", "best {} near me", "{} open now", "24 hour {}", "cheap {} nearby"]
    q["local"] = [t.format(s) for s, t in itertools.product(places, tmpl)][:PER_BUCKET]

    events = ["concerts", "comedy shows", "food festivals", "art exhibitions", "farmers markets",
              "live music", "theater shows", "sports games", "craft fairs", "wine tastings",
              "car shows", "book signings", "trivia nights", "outdoor movies", "marathons",
              "music festivals", "job fairs", "flea markets", "poetry readings", "karaoke nights"]
    tmpl = ["{} this weekend", "{} near me tonight", "upcoming {}", "{} in austin", "free {} today"]
    q["events"] = [t.format(s) for s, t in itertools.product(events, tmpl)][:PER_BUCKET]

    products = ["wireless earbuds", "running shoes", "air fryer", "standing desk", "robot vacuum",
                "office chair", "gaming laptop", "espresso machine", "electric toothbrush", "yoga mat",
                "mechanical keyboard", "hiking boots", "blender", "monitor", "backpack",
                "winter jacket", "smartwatch", "air purifier", "cordless drill", "mattress"]
    tmpl = ["buy {}", "best {} 2026", "{} deals", "{} under 100", "{} sale"]
    q["purchase"] = [t.format(s) for s, t in itertools.product(products, tmpl)][:PER_BUCKET]

    pairs = ["iphone vs samsung", "airpods vs bose", "dyson vs shark", "macbook vs thinkpad",
             "nike vs adidas running shoes", "instant pot vs crockpot", "kindle vs ipad for reading",
             "sony vs lg oled", "hydro flask vs yeti", "peloton vs echelon", "roomba vs roborock",
             "canon vs nikon", "xbox vs playstation", "fitbit vs apple watch", "keurig vs nespresso",
             "weber vs traeger", "ryobi vs dewalt", "casper vs purple mattress", "jbl vs bose speaker",
             "garmin vs apple watch running"]
    tmpl = ["{}", "{} which is better", "{} reddit", "{} comparison", "{} pros and cons"]
    q["comparison"] = [t.format(s) for s, t in itertools.product(pairs, tmpl)][:PER_BUCKET]

    recipes = ["banana bread", "chicken parmesan", "chocolate chip cookies", "beef stew", "pancakes",
               "lasagna", "chili", "meatloaf", "carrot cake", "fried rice", "pot roast", "brownies",
               "mac and cheese", "apple pie", "salmon", "pizza dough", "cheesecake", "tacos",
               "french onion soup", "cinnamon rolls"]
    tmpl = ["{} recipe", "easy {} recipe", "best {} recipe", "homemade {}", "{} recipe from scratch"]
    q["recipe"] = [t.format(s) for s, t in itertools.product(recipes, tmpl)][:PER_BUCKET]

    return q


def domain(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


def call(url, params, tries=4):
    for attempt in range(tries):
        r = rq.get(url, params=params, headers={"x-api-key": KEY}, timeout=90)
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

rows = []
if OUT.exists():
    rows = json.loads(OUT.read_text(encoding="utf-8"))
done = {(r["bucket"], r["query"]) for r in rows}

queries = build_queries()
total = sum(len(v) for v in queries.values())
print(f"panel: {total} queries in {len(queries)} buckets, {len(done)} already recorded")

for bucket, qs in queries.items():
    for query in qs:
        if (bucket, query) in done:
            continue
        serp = call("https://api.hasdata.com/scrape/google/serp",
                    {"q": query, "location": "Austin,Texas,United States", "deviceType": "desktop"})
        row = dict(bucket=bucket, query=query, keys=sorted(serp.keys()))
        for label, key in FEATURES.items():
            row[label] = bool(serp.get(key))
        ab = serp.get("answerBox") or {}
        row["fs_domain"] = domain(ab.get("link") or ab.get("url") or "")
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
        rows.append(row)
        OUT.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        n = len(rows)
        if n % 25 == 0:
            print(f"{n}/{total} done, last: [{bucket}] {query} aio={row['aio']}")
        time.sleep(1.2)

print(f"\nfinished: {len(rows)} rows saved to aio_panel.json")
