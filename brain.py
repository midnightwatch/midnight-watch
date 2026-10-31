#!/usr/bin/env python3
"""
Midnight Watch - the daily brain.

Runs once a day. Steps:
  1. Pull the last 24 hours of world news from RSS feeds (no API key).
  2. Keep only items about nuclear, war, climate, bio, AI, major politics.
  3. Five AI experts each read the news and vote: worse / hold / better.
  4. A sixth AI, the chair, merges the votes, compares to yesterday,
     and sets the new time.
  5. Hard rules in code (not left to the AI): max move +/-5 seconds a day,
     every move must cite a real headline, "no change" is allowed.
  6. Write data/latest.json and data/history.json for index.html.

LLM: Groq (primary) with Gemini as backup. Both use the OpenAI chat format.
Keys come from environment variables GROQ_API_KEY and GEMINI_API_KEY.

Usage:
  python brain.py                 # real run
  python brain.py --dry-run       # fetch news, fake the AI, write files
  python brain.py --news-only     # just print the filtered news and stop
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LATEST = DATA_DIR / "latest.json"
HISTORY = DATA_DIR / "history.json"
OFFICIAL_FILE = DATA_DIR / "official.json"

# The real Doomsday Clock, as set by the Bulletin, in seconds to midnight.
# Used to seed data/official.json; after that the daily run keeps it current
# by reading the Bulletin's own page.
OFFICIAL_SEED = [
    [1947, 420], [1949, 180], [1953, 120], [1960, 420], [1963, 720], [1968, 420],
    [1969, 600], [1972, 720], [1974, 540], [1980, 420], [1981, 240], [1984, 180],
    [1988, 360], [1990, 600], [1991, 1020], [1995, 840], [1998, 540], [2002, 420],
    [2007, 300], [2010, 360], [2012, 300], [2015, 180], [2017, 150], [2018, 120],
    [2020, 100], [2023, 90], [2025, 89], [2026, 85],
]
BULLETIN_URL = "https://thebulletin.org/doomsday-clock/"

START_SECONDS = 85        # official reading when the project started
MAX_MOVE = 5              # max seconds the clock can move in one day
MIN_SECONDS = 1           # never reach midnight
MAX_SECONDS = 17 * 60     # the furthest the real clock has ever been
NEWS_WINDOW_HOURS = 24
MAX_ITEMS_TO_MODEL = 45   # keep the prompt small for free-tier token limits

# Mixed sources, different regions, no single outlet dominates.
FEEDS = [
    ("AP", "https://news.google.com/rss/search?q=site:apnews.com+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("Reuters", "https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en"),
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("DW", "https://rss.dw.com/rdf/rss-en-world"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("Bulletin of the Atomic Scientists", "https://thebulletin.org/feed/"),
    ("IAEA", "https://www.iaea.org/feeds/topnews"),
    ("Carbon Brief", "https://www.carbonbrief.org/feed/"),
    ("NPR", "https://feeds.npr.org/1004/rss.xml"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
    ("France 24", "https://www.france24.com/en/rss"),
]

# Topic tags and the words that put a headline in that topic.
# Matched as whole words (so "war" does not match "Stewart").
TOPICS = {
    "Nuclear": [
        "nuclear", "warhead", "missile", "missiles", "icbm", "ballistic", "iaea", "uranium",
        "enrichment", "arms control", "new start", "npt", "non-proliferation", "deterrence",
        "atomic", "plutonium", "hypersonic", "nuclear test", "zaporizhzhia",
    ],
    "Climate": [
        "climate", "emissions", "carbon", "global warming", "heatwave", "heat wave",
        "wildfire", "wildfires", "floods", "flooding", "drought", "hurricane", "typhoon",
        "cyclone", "cop30", "cop31", "cop32", "paris agreement", "fossil fuel", "methane",
        "glacier", "sea level", "record temperature", "net zero", "deforestation",
    ],
    "Bio": [
        "pandemic", "outbreak", "virus", "pathogen", "bioweapon", "biolab",
        "h5n1", "bird flu", "ebola", "mpox", "cholera", "epidemic", "who declares",
        "antibiotic resistance", "biosecurity", "lab leak", "vaccine", "marburg",
    ],
    "AI & tech": [
        "artificial intelligence", "ai", "ai model", "openai", "anthropic",
        "deepmind", "frontier model", "autonomous weapons", "killer robots",
        "deepfake", "disinformation", "cyberattack", "cyber attack", "ransomware",
        "ai safety", "ai regulation", "ai act", "chip export", "chip exports", "hackers",
    ],
    "Geopolitics": [
        "war", "ceasefire", "invasion", "troops", "airstrike", "airstrikes", "air strike",
        "nato", "kremlin", "pentagon", "sanctions", "coup", "taiwan", "ukraine", "gaza",
        "israel", "iran", "north korea", "dprk", "pyongyang", "beijing", "moscow",
        "summit", "peace talks", "offensive", "escalation", "escalate", "military",
        "drone", "drones", "border clash", "martial law", "hormuz", "junta", "rebels",
        "security council", "genocide", "famine", "refugees", "displaced",
    ],
}

# Headlines with these words are sports, entertainment or markets, not world risk.
BLOCK = [
    "nfl", "nba", "mlb", "nhl", "world series", "super bowl", "playoff", "playoffs",
    "coach", "quarterback", "touchdown", "premier league", "champions league",
    "world cup", "grand prix", "box office", "star trek", "movie", "film", "album",
    "concert", "celebrity", "shares", "stock", "stocks", "nasdaq", "s&p", "earnings",
    "investors", "shareholders", "ipo", "crypto", "decision notes", "recipe", "offensive line",
]

EXPERTS = [
    ("Nuclear", "arsenals, treaties, threats",
     "nuclear weapons, arsenals, missile tests, arms-control treaties, nuclear threats and doctrine, nuclear power safety"),
    ("Climate", "emissions, extremes, policy",
     "greenhouse emissions, extreme weather, climate policy and pledges, rollbacks, climate-driven conflict or migration"),
    ("Bio", "outbreaks, labs, weapons",
     "disease outbreaks, pandemic preparedness, lab safety, biological weapons, antimicrobial resistance"),
    ("AI & tech", "capabilities, misuse, controls",
     "AI capabilities and releases, AI misuse, autonomous weapons, cyberattacks on infrastructure, disinformation, safety rules and regulation"),
    ("Geopolitics", "wars, alliances, leadership",
     "wars and ceasefires, relations between major powers, alliances, leadership failures, erosion of international institutions"),
]

# LLM providers: tried in order. Any OpenAI-compatible chat endpoint works.
PROVIDERS = [
    {
        "name": "groq",
        "url": os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1") + "/chat/completions",
        "key_env": "GROQ_API_KEY",
        "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
    },
    {
        "name": "gemini",
        "url": os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai") + "/chat/completions",
        "key_env": "GEMINI_API_KEY",
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
    },
]

# ----------------------------------------------------------------------------
# 1 + 2. News
# ----------------------------------------------------------------------------

def log(msg):
    print(f"[brain] {msg}", flush=True)


def clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"The post .* appeared first on .*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def entry_time(e):
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _has(word, low):
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low) is not None


def tag_for(text):
    """Return the topic tag with the most keyword hits, or None."""
    low = text.lower()
    if any(_has(w, low) for w in BLOCK):
        return None
    best, best_hits = None, 0
    for tag, words in TOPICS.items():
        hits = sum(1 for w in words if _has(w, low))
        if hits > best_hits:
            best, best_hits = tag, hits
    return best


def fetch_news(now):
    cutoff = now - timedelta(hours=NEWS_WINDOW_HOURS)
    items, seen = [], set()
    for source, url in FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; MidnightWatch/1.0; +https://github.com)"})
            feed = feedparser.parse(r.content)
        except Exception as ex:
            log(f"feed failed: {source}: {ex}")
            continue
        got = 0
        for e in feed.entries:
            t = entry_time(e)
            if t and t < cutoff:
                continue
            title = clean(e.get("title"))
            if not title:
                continue
            # Google News titles end with " - Outlet"
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title) if "news.google.com" in url else title
            summary = clean(e.get("summary"))[:240]
            if summary.lower().startswith(title.lower()[:40]):
                summary = ""  # Google News repeats the title
            tag = tag_for(title + " " + summary)
            if not tag:
                continue
            key = hashlib.md5(re.sub(r"\W+", "", title.lower())[:60].encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "id": len(items) + 1,
                "tag": tag,
                "title": title[:160],
                "summary": summary,
                "source": source,
                "url": e.get("link", ""),
                "time": (t or now).isoformat(),
            })
            got += 1
        log(f"{source}: {got} relevant items")
    # Balance topics: round-robin so one topic cannot crowd out the others.
    by_tag = {t: [i for i in items if i["tag"] == t] for t in TOPICS}
    balanced = []
    while len(balanced) < MAX_ITEMS_TO_MODEL and any(by_tag.values()):
        for t in TOPICS:
            if by_tag[t]:
                balanced.append(by_tag[t].pop(0))
            if len(balanced) >= MAX_ITEMS_TO_MODEL:
                break
    for n, it in enumerate(balanced, 1):
        it["id"] = n
    return balanced


def news_block(items):
    lines = []
    for it in items:
        s = f" - {it['summary']}" if it["summary"] else ""
        lines.append(f"[{it['id']}] ({it['tag']}, {it['source']}) {it['title']}{s}")
    return "\n".join(lines) if lines else "(no relevant news found in the last 24 hours)"

# ----------------------------------------------------------------------------
# Official clock check
# ----------------------------------------------------------------------------

def fetch_official():
    """Read the Bulletin's page and return the current official seconds, or None."""
    try:
        r = requests.get(BULLETIN_URL, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; MidnightWatch/1.0)"})
        r.raise_for_status()
        page = r.text
    except Exception as ex:
        log(f"bulletin page failed: {ex}")
        return None
    # Preferred anchor: the headline "It is N seconds/minutes to midnight".
    m = re.search(r"It is (?:now )?(\d+(?:\.\d+)?)\s+(seconds?|minutes?) to midnight", page, re.I)
    if not m:
        # Fallback: the most repeated "N seconds to midnight" phrase on the page.
        found = re.findall(r"(\d+(?:\.\d+)?)\s+(seconds?|minutes?) to midnight", page, re.I)
        if not found:
            return None
        counts = {}
        for n, unit in found:
            counts[(n, unit.lower())] = counts.get((n, unit.lower()), 0) + 1
        (n, unit), _ = max(counts.items(), key=lambda kv: kv[1])
        m = None
    else:
        n, unit = m.group(1), m.group(2)
    secs = float(n) * (60 if unit.lower().startswith("min") else 1)
    secs = int(round(secs))
    if not (1 <= secs <= 30 * 60):
        return None
    return secs


def update_official(now):
    """Keep data/official.json current. Returns the list of [year, seconds]."""
    official = load_json(OFFICIAL_FILE, None)
    if not isinstance(official, list) or not official:
        official = [list(x) for x in OFFICIAL_SEED]
    current = fetch_official()
    last = official[-1][1]
    if current is None:
        log(f"official clock: could not read, keeping {last} s")
    elif current != last:
        log(f"official clock CHANGED: {last} -> {current} s")
        official.append([now.year, current])
    else:
        log(f"official clock: {current} s (unchanged)")
    DATA_DIR.mkdir(exist_ok=True)
    OFFICIAL_FILE.write_text(json.dumps(official))
    return official

# ----------------------------------------------------------------------------
# LLM calls
# ----------------------------------------------------------------------------

def extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def ask(system, user, max_tokens=2500):
    """Ask the first provider that works. Returns parsed JSON.

    max_tokens is generous because reasoning models (gpt-oss) spend tokens
    thinking before they answer; a tight cap cuts the JSON off."""
    errors = []
    for p in PROVIDERS:
        key = os.environ.get(p["key_env"])
        if not key:
            continue
        for attempt in range(3):
            body = {
                "model": p["model"],
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if attempt == 0:
                body["response_format"] = {"type": "json_object"}
            if p["name"] == "groq" and "gpt-oss" in p["model"]:
                body["reasoning_effort"] = "low"
            try:
                r = requests.post(
                    p["url"],
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=120,
                )
                if r.status_code == 429:
                    wait = 20 * (attempt + 1)
                    log(f"{p['name']} rate-limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                content = r.json()["choices"][0]["message"]["content"]
                return extract_json(content)
            except Exception as ex:
                log(f"{p['name']} attempt {attempt + 1} failed: {str(ex)[:300]}")
                errors.append(f"{p['name']}: {str(ex)[:200]}")
                time.sleep(3)
    raise RuntimeError("All LLM providers failed: " + ("; ".join(errors) or "no API key set"))


RULES = """You are part of a simulated Doomsday Clock board. The clock shows seconds to midnight;
fewer seconds means closer to catastrophe. Judge only what changed in the LAST 24 HOURS compared
to the day before, not the overall state of the world (that is already priced into the current time).
Most days nothing meaningful changes and the honest answer is "hold". Cite headlines by their [number].
Never invent events. Use only names, numbers and places that appear in the headlines; if a headline says
"Pope" do not guess which pope. Be concrete and calm. Answer ONLY with a JSON object."""


def ask_expert(role, focus, scope, news, seconds):
    system = RULES + f"""
You are the {role} expert. Your scope: {scope}.
Return JSON: {{"vote": "worse"|"hold"|"better", "score": -3..3, "say": "<2-3 plain sentences, under 70 words, naming the headline numbers you relied on>", "cites": [headline numbers]}}
score: -3 = clearly more dangerous today, 0 = no real change, +3 = clearly safer. A vote of worse/better requires at least one cite."""
    user = f"Current reading: {seconds} seconds to midnight.\n\nNews from the last 24 hours:\n{news}"
    out = ask(system, user, max_tokens=2000)
    vote = out.get("vote", "hold")
    if vote not in ("worse", "hold", "better"):
        vote = "hold"
    cites = [int(c) for c in out.get("cites", []) if str(c).isdigit()]
    if vote != "hold" and not cites:
        vote = "hold"  # rule: no move without a headline
    try:
        score = max(-3, min(3, int(out.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    say = str(out.get("say", "")).strip()
    # Drop citation markers like [3], [3][8], (9,19,4), ([2]) from the displayed text.
    say = re.sub(r"\s*\(?\[\d+(?:[,\s]*\d+)*\]\)?", "", say)
    say = re.sub(r"\s*\(\d+(?:[,\s]+\d+)+\)", "", say)
    say = re.sub(r"\s+([.,;:])", r"\1", say).strip()[:500]
    return {"role": role, "focus": focus, "vote": vote, "score": score,
            "say": say, "cites": cites}


def ask_chair(experts, news, seconds, yesterday_lede):
    views = "\n".join(
        f"- {e['role']} (vote {e['vote']}, score {e['score']:+d}, cites {e['cites']}): {e['say']}"
        for e in experts)
    system = RULES + f"""
You are the chair. You read the five experts, weigh them, and set the new time.
Rules: move at most {MAX_MOVE} seconds in either direction; one or two mild negatives usually mean a move of 0 or 1 second;
a big move (3-5) needs a major event confirmed by more than one expert; "no change" is a normal outcome.
Do not repeat yesterday's reasoning if nothing new happened.
Return JSON: {{"delta": -{MAX_MOVE}..{MAX_MOVE} (negative = closer to midnight),
"lede": "<one sentence stating the decision and the single biggest reason>",
"body": ["<paragraph>", "<paragraph>", "<paragraph>"],
"headlines": [headline numbers that drove the decision, 3 to 6 of them]}}"""
    user = (f"Current reading: {seconds} seconds to midnight.\n"
            f"Yesterday's chair lede: {yesterday_lede or '(none, first day)'}\n\n"
            f"Expert views:\n{views}\n\nNews from the last 24 hours:\n{news}")
    return ask(system, user, max_tokens=3000)

# ----------------------------------------------------------------------------
# Fake AI for --dry-run (no key needed)
# ----------------------------------------------------------------------------

def fake_run(items, seconds):
    experts = []
    for role, focus, _ in EXPERTS:
        mine = [i for i in items if i["tag"] == role]
        cites = [i["id"] for i in mine[:2]]
        experts.append({"role": role, "focus": focus, "vote": "hold", "score": 0, "cites": cites,
                        "say": f"Dry run. {len(mine)} {role} items seen. No judgment made."})
    chair = {"delta": 0, "lede": "Dry run: the clock holds because no AI was asked.",
             "body": ["This file was written by brain.py --dry-run.",
                      "The news fetch and the file format are being tested.",
                      "A real run replaces this with the chair's reasoning."],
             "headlines": [i["id"] for i in items[:5]]}
    return experts, chair

# ----------------------------------------------------------------------------
# Rules + output
# ----------------------------------------------------------------------------

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def apply_rules(chair, experts, items, seconds):
    try:
        delta = int(chair.get("delta", 0))
    except (TypeError, ValueError):
        delta = 0
    delta = max(-MAX_MOVE, min(MAX_MOVE, delta))

    valid_ids = {i["id"] for i in items}
    cited = [int(h) for h in chair.get("headlines", []) if str(h).isdigit() and int(h) in valid_ids]
    if delta != 0 and not cited:
        log("chair moved the clock without citing a headline -> forced hold")
        delta = 0

    # The chair cannot move against a clear expert consensus.
    total = sum(e["score"] for e in experts)
    if delta < 0 and total > 0 or delta > 0 and total < 0:
        log(f"chair delta {delta} contradicts expert total {total:+d} -> forced hold")
        delta = 0

    # A big move (3-5) needs at least two experts voting that way.
    if abs(delta) >= 3:
        want = "worse" if delta < 0 else "better"
        agree = sum(1 for e in experts if e["vote"] == want)
        if agree < 2:
            log(f"big move {delta} backed by only {agree} expert(s) -> capped at 2")
            delta = 2 if delta > 0 else -2

    new_seconds = max(MIN_SECONDS, min(MAX_SECONDS, seconds + delta))
    return new_seconds, cited


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="skip the AI, write files anyway")
    ap.add_argument("--news-only", action="store_true", help="print filtered news and stop")
    ap.add_argument("--date", help="override the reading date (YYYY-MM-DD)")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    today = args.date or now.strftime("%Y-%m-%d")

    prev = load_json(LATEST, None)
    history = load_json(HISTORY, [])
    if prev and not prev.get("sample") and prev.get("date") == today:
        # Re-run on the same day: start from where that day started,
        # so the reading is replaced, not stacked.
        seconds = int(prev["previous"])
        yesterday_lede = prev.get("yesterday_lede", "")
        log("re-run on the same day, replacing today's reading")
    elif prev and not prev.get("sample"):
        seconds = int(prev["seconds"])
        yesterday_lede = prev.get("chair", {}).get("lede", "")
    else:
        seconds = START_SECONDS
        yesterday_lede = ""
    log(f"date {today}, starting from {seconds} seconds")
    update_official(now)

    items = fetch_news(now)
    log(f"{len(items)} items sent to the board")
    news = news_block(items)
    if args.news_only:
        print(news)
        return

    if args.dry_run:
        experts, chair = fake_run(items, seconds)
    else:
        experts = []
        for role, focus, scope in EXPERTS:
            log(f"asking {role} expert")
            experts.append(ask_expert(role, focus, scope, news, seconds))
            time.sleep(int(os.environ.get("CALL_DELAY", "8")))  # stay under free-tier tokens-per-minute
        log("asking chair")
        chair = ask_chair(experts, news, seconds, yesterday_lede)

    new_seconds, cited = apply_rules(chair, experts, items, seconds)
    log(f"verdict: {seconds} -> {new_seconds}")

    by_id = {i["id"]: i for i in items}
    headlines = [by_id[h] for h in cited if h in by_id]
    # Fill up with each expert's cites so the page always shows what was read.
    for e in experts:
        for c in e["cites"]:
            if c in by_id and by_id[c] not in headlines:
                headlines.append(by_id[c])
    headlines = headlines[:8]

    body = chair.get("body", [])
    if isinstance(body, str):
        body = [body]
    body = [re.sub(r"\s*\(?\[\d+(?:[,\s]*\d+)*\]\)?", "", str(p)).strip() for p in body if str(p).strip()][:4]

    latest = {
        "sample": False,
        "date": today,
        "seconds": new_seconds,
        "previous": seconds,
        "generated_at": now.isoformat(timespec="seconds"),
        "yesterday_lede": yesterday_lede,
        "chair": {"lede": str(chair.get("lede", "")).strip()[:400], "body": body},
        "experts": [{"role": e["role"], "focus": e["focus"], "vote": e["vote"], "say": e["say"]}
                    for e in experts],
        "headlines": [{"tag": h["tag"], "title": h["title"], "source": h["source"], "url": h["url"]}
                      for h in headlines],
    }

    # History: one entry per date; re-running the same day overwrites it.
    history = [h for h in history if h.get("date") != today]
    history.append({"date": today, "seconds": new_seconds})
    history.sort(key=lambda h: h["date"])
    history = history[-365:]

    DATA_DIR.mkdir(exist_ok=True)
    LATEST.write_text(json.dumps(latest, indent=2, ensure_ascii=False))
    HISTORY.write_text(json.dumps(history, indent=0, ensure_ascii=False))
    log(f"wrote {LATEST.name} and {HISTORY.name}")

    # Archive: one file per day, never overwritten by later days.
    days_dir = DATA_DIR / "days"
    days_dir.mkdir(exist_ok=True)
    (days_dir / f"{today}.json").write_text(json.dumps(latest, indent=2, ensure_ascii=False))

    # Picture shown when the link is shared in chat apps.
    try:
        from share_image import draw_share_image
        nice_date = datetime.strptime(today, "%Y-%m-%d").strftime("%-d %B %Y")
        draw_share_image(new_seconds, seconds, nice_date, DATA_DIR / "share.png")
        log("wrote share.png")
    except Exception as ex:
        log(f"share image skipped: {ex}")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        log(f"FAILED: {ex}")
        sys.exit(1)
