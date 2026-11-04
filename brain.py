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
BULLETIN_STATEMENT_URL = "https://thebulletin.org/doomsday-clock/current-time/"

START_SECONDS = 85        # official reading when the project started (the anchor)
MAX_MOVE = 0.5            # max seconds the clock can move in one day, all pressures together
MIN_SECONDS = 1.0         # never reach midnight
MAX_SECONDS = 17 * 60     # the furthest the real clock has ever been

# Pressures: the board keeps a short list of open dangers beyond the official
# baseline, each with a weight in seconds. Clock = anchor - sum of weights.
MAX_OPEN_PRESSURES = 8
MAX_PRESSURE = 10.0       # one pressure can never weigh more than this
PRESSURE_STEP = 0.3       # max change of one pressure in one day
FADE_AFTER_DAYS = 7       # not in the news for this long: the weight starts to fade
FADE_RATE = 0.9           # ...by this factor per day
CLOSE_BELOW = 0.1         # weights under this are closed
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


def bulletin_text():
    """Plain text of the Bulletin's clock pages, for summarising a new setting."""
    out = []
    for url in (BULLETIN_STATEMENT_URL, BULLETIN_URL):
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; MidnightWatch/1.0)"})
            r.raise_for_status()
            page = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", r.text, flags=re.S | re.I)
            out.append(clean(page))
        except Exception as ex:
            log(f"bulletin text failed: {url}: {ex}")
    return "\n\n".join(out)[:9000]


def official_reason(year, seconds, previous):
    """Ask the model to summarise, from the Bulletin's own pages, why the clock
    was set this year. Returns a short string, or None if it cannot."""
    text = bulletin_text()
    if len(text) < 500:
        return None
    unit = f"{seconds} seconds" if seconds < 120 else f"{seconds / 60:g} minutes"
    system = """You summarise the Bulletin of the Atomic Scientists' annual Doomsday Clock statement.
Use ONLY the page text given. Do not add events that are not in it. Do not invent names or numbers.
Write one or two plain sentences, under 45 words, naming the main reasons the Bulletin gives for this year's setting.
Answer ONLY with a JSON object: {"reason": "<sentences>"}"""
    user = (f"Year: {year}. New setting: {unit} to midnight (previous setting: {previous} seconds).\n\n"
            f"Page text:\n{text}")
    try:
        out = ask(system, user, max_tokens=1200)
        reason = tidy(strip_cites(str(out.get("reason", "")).strip()))
        return reason[:400] if len(reason) > 20 else None
    except Exception as ex:
        log(f"official reason failed: {ex}")
        return None


def update_official(now):
    """Keep data/official.json current. Entries are [year, seconds] or
    [year, seconds, reason]; the reason is written automatically when the
    Bulletin sets a new time."""
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
    # A new setting without a reason yet (or one from an earlier run that failed): write it.
    tail = official[-1]
    if len(tail) == 2 and tail[0] > OFFICIAL_SEED[-1][0]:
        prev = official[-2][1] if len(official) > 1 else tail[1]
        reason = official_reason(tail[0], tail[1], prev)
        if reason:
            tail.append(reason)
            log(f"official reason written for {tail[0]}: {reason}")
        else:
            log(f"official reason for {tail[0]} not written yet, will retry tomorrow")
    DATA_DIR.mkdir(exist_ok=True)
    OFFICIAL_FILE.write_text(json.dumps(official, ensure_ascii=False))
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


ANCHOR_INFO = {"year": OFFICIAL_SEED[-1][0], "seconds": OFFICIAL_SEED[-1][1], "reason": ""}


def set_anchor_info(official):
    """Remember the latest official setting so the prompts talk about the right baseline."""
    tail = official[-1]
    ANCHOR_INFO.update({"year": tail[0], "seconds": tail[1], "reason": tail[2] if len(tail) > 2 else ""})


def rules():
    a = ANCHOR_INFO
    known = ("That baseline already includes everything true then: the wars in Ukraine and Gaza, nuclear "
             "modernisation, climate change, the AI race, weak institutions." if a["year"] <= 2026 else
             f"The Bulletin's own reasons: {a['reason']}" if a["reason"] else
             "That baseline already includes everything that was true when it was set.")
    return f"""You are part of a simulated Doomsday Clock board. The clock shows seconds to midnight;
fewer seconds means closer to catastrophe. The Bulletin of the Atomic Scientists set the official clock
to {a['seconds']} seconds in January {a['year']}. {known} The board tracks only
what has changed SINCE then, as a short list of open "pressures", each weighing some seconds.
News is bad every day; ongoing situations, routine tension, familiar warnings and scheduled events are
not reasons to move. Only a new development counts: a new war or attack, a weapon test, a treaty collapse,
an outbreak crossing borders, a major cyber or AI incident; or a danger clearly easing: a ceasefire that
holds, talks that succeed, an outbreak contained. Most days the honest answer is "hold". Cite headlines by their [number].
Never invent events. Use only names, numbers and places that appear in the headlines; if a headline says
"Pope" do not guess which pope. Be concrete and calm. Answer ONLY with a JSON object."""


def build_lede(chair, before, after):
    """The chair gives only the reason; the code states the move, so the numbers are always right."""
    reason = tidy(strip_cites(str(chair.get("reason") or chair.get("lede") or "").strip())).rstrip(".")
    reason = re.sub(r"^(we|the board|the chair|today)\s+(hold|holds|lower|lowers|raise|raises|move|moves|keep|keeps)[^,:]*?(,|:)\s*", "", reason, flags=re.I)
    reason = reason[:1].lower() + reason[1:] if reason else ""
    d = round(after - before, 1)
    if d == 0:
        head = f"The clock holds at {after:.1f} seconds"
    else:
        head = f"The clock moves to {after:.1f} seconds, {abs(d):.1f} {'closer to' if d < 0 else 'further from'} midnight than yesterday"
    return (f"{head}: {reason}." if reason else f"{head}.")[:400]


def tidy(text):
    """Clean up punctuation left behind after removing citation markers."""
    text = re.sub(r"\s+([.,;:])", r"\1", text)      # "word ," -> "word,"
    text = re.sub(r"[,;:]+(?=[.,;:])", "", text)      # ",,." -> "."
    text = re.sub(r"\(\s*\)", "", text)             # "()" leftovers
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def pressures_block(pressures):
    if not pressures:
        return "(none open: the clock sits on the official baseline)"
    return "\n".join(f"- {p['id']} | {p['area']} | {p['name']} | weight {p['weight']:.1f} s | since {p['since']} | last in the news {p['last_seen']}"
                     for p in pressures)


def ask_expert(role, focus, scope, news, seconds, pressures):
    system = rules() + f"""
You are the {role} expert. Your scope: {scope}.
Say whether today's news adds a NEW danger in your area, makes an open pressure worse or better, or changes nothing.
Return JSON: {{"vote": "worse"|"hold"|"better", "score": -3..3, "say": "<2-3 plain sentences, under 70 words, naming the headline numbers you relied on>", "cites": [headline numbers]}}
score: -3 = a clearly new or sharply worse danger, 0 = nothing new beyond what the open pressures already cover, +3 = a danger clearly easing. A vote of worse/better requires at least one cite."""
    user = (f"Current reading: {seconds} seconds to midnight.\n\nOpen pressures already counted in the reading:\n{pressures_block(pressures)}"
            f"\n\nNews from the last 24 hours:\n{news}")
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
    say = tidy(say)[:500]
    return {"role": role, "focus": focus, "vote": vote, "score": score,
            "say": say, "cites": cites}


def ask_chair(experts, news, seconds, yesterday_lede, pressures, anchor):
    views = "\n".join(
        f"- {e['role']} (vote {e['vote']}, score {e['score']:+d}, cites {e['cites']}): {e['say']}"
        for e in experts)
    system = rules() + f"""
You are the chair. You read the five experts and update the list of open pressures. The clock is then
{anchor} seconds minus the total weight of the pressures; you do not set the time directly.
Weights are in seconds. Guide: 0.3 = a worrying new development; 1.0 = a serious ongoing danger beyond the
baseline; 3.0 = a crisis with a real chance of catastrophe; 5.0 or more = war between nuclear powers or
a global pandemic. The whole year 2025 moved the official clock by 4 seconds, so weights must be modest.
Keep existing pressures and their ids unless something changed. Raise a weight only for a cited headline that
makes it worse. Lower it or close it when the danger clearly eases, with a cited headline. Add a pressure only
for a new development since January {ANCHOR_INFO['year']} that the baseline does not cover; a new pressure starts small.
Do not add a pressure for something already listed; update that one instead. "same" for every pressure is a
normal day. Do not repeat yesterday's reasoning if nothing new happened.
Never state the new time or how many seconds the clock moves, in the reason or the body: the code applies
hard caps after you answer, so any number you write would be wrong. Describe what changed and why in words.
Return JSON: {{"pressures": [{{"id": "<short-slug, keep existing ids>", "area": "Nuclear|Climate|Bio|AI & tech|Geopolitics",
"name": "<5-8 words>", "weight": <seconds, one decimal>, "status": "new|up|down|same|closed", "headlines": [numbers that justify a change]}}],
"reason": "<the single biggest reason for today's decision, one clause, no numbers of seconds>",
"body": ["<paragraph>", "<paragraph>", "<paragraph>"],
"headlines": [headline numbers that drove the decision, 3 to 6 of them]}}"""
    user = (f"Official baseline: {anchor} seconds. Current reading: {seconds} seconds to midnight.\n"
            f"Open pressures:\n{pressures_block(pressures)}\n"
            f"Yesterday's chair lede: {yesterday_lede or '(none, first day)'}\n\n"
            f"Expert views:\n{views}\n\nNews from the last 24 hours:\n{news}")
    return ask(system, user, max_tokens=3500)

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
    chair = {"pressures": [], "lede": "Dry run: the clock holds because no AI was asked.",
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


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "pressure"


def apply_rules(chair, experts, items, pressures, anchor, today):
    """Turn the chair's answer into the new pressures list, with the hard rules
    applied in code. Returns (new_seconds, new_pressures, cited, notes)."""
    valid_ids = {i["id"] for i in items}
    cited = [int(h) for h in chair.get("headlines", []) if str(h).isdigit() and int(h) in valid_ids]
    total = sum(e["score"] for e in experts)
    worse = sum(1 for e in experts if e["vote"] == "worse")
    better = sum(1 for e in experts if e["vote"] == "better")
    old = {p["id"]: p for p in pressures}
    notes = []

    proposed = chair.get("pressures", [])
    if not isinstance(proposed, list):
        proposed = []
    out, seen = [], set()
    for q in proposed:
        if not isinstance(q, dict):
            continue
        pid = slug(q.get("id") or q.get("name"))
        if pid in seen:
            continue
        seen.add(pid)
        status = str(q.get("status", "same")).lower()
        heads = [int(h) for h in q.get("headlines", []) if str(h).isdigit() and int(h) in valid_ids]
        try:
            want = float(q.get("weight", 0))
        except (TypeError, ValueError):
            want = 0.0
        want = max(0.0, min(MAX_PRESSURE, want))
        prev = old.get(pid)

        if prev is None:
            # New pressure: needs a cited headline and an expert voting worse.
            if status == "closed" or not heads or worse == 0:
                if status != "closed":
                    notes.append(f"new pressure '{pid}' rejected: no headline or no expert voted worse")
                continue
            if len(out) >= MAX_OPEN_PRESSURES:
                notes.append(f"new pressure '{pid}' rejected: too many open")
                continue
            w = min(want, PRESSURE_STEP)
            out.append({"id": pid, "area": str(q.get("area", "Geopolitics"))[:20], "name": str(q.get("name", pid))[:80],
                        "weight": w, "since": today, "last_seen": today, "status": "new", "delta": w})
            continue

        base = float(prev["weight"])
        if status == "closed":
            w, last_seen = 0.0, today
            if not heads:
                w, status = base, "same"
                notes.append(f"'{pid}' close rejected: no headline")
        elif status in ("up", "down"):
            if not heads:
                w, status, last_seen = base, "same", prev["last_seen"]
                notes.append(f"'{pid}' {status} rejected: no headline")
            else:
                w = max(base - PRESSURE_STEP, min(base + PRESSURE_STEP, want))
                last_seen = today
                if status == "up" and w <= base:
                    status = "same"
                if status == "down" and w >= base:
                    status = "same"
        else:
            w, status, last_seen = base, "same", prev["last_seen"]
        out.append({"id": pid, "area": prev["area"], "name": str(q.get("name") or prev["name"])[:80],
                    "weight": w, "since": prev["since"], "last_seen": last_seen, "status": status, "delta": w - base})

    # Pressures the chair forgot to list stay open unchanged.
    for pid, prev in old.items():
        if pid not in seen:
            out.append(dict(prev, status="same", delta=0.0))

    # The chair cannot move against a clear expert consensus.
    net = sum(p["delta"] for p in out)            # positive = clock moves closer to midnight
    if (net > 0 and total > 0) or (net < 0 and total < 0):
        notes.append(f"net change {net:+.1f} contradicts expert total {total:+d} -> forced hold")
        for p in out:
            p["weight"] -= p["delta"]; p["delta"] = 0.0; p["status"] = "same" if p["weight"] > 0 else p["status"]
        out = [p for p in out if p["weight"] > 0 or p["status"] != "new"]

    # Fade: a pressure that has not been in the news for a while loses weight by itself.
    for p in out:
        if p["status"] == "same" and p["weight"] > 0:
            quiet = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(p["last_seen"], "%Y-%m-%d")).days
            if quiet >= FADE_AFTER_DAYS:
                faded = p["weight"] * FADE_RATE
                p["delta"] += faded - p["weight"]; p["weight"] = faded; p["status"] = "fading"

    # Round each change to a tenth, then cap the total exactly: trim the largest changes first until the sum fits.
    for p in out:
        p["delta"] = round(p["delta"], 1)
    net = round(sum(p["delta"] for p in out), 1)
    if abs(net) > MAX_MOVE:
        notes.append(f"total change {net:+.1f} capped at {MAX_MOVE}")
        sign = 1 if net > 0 else -1
        excess = round(abs(net) - MAX_MOVE, 1)
        while excess > 0:   # shave 0.1 at a time from the largest change, so the trim is spread evenly
            movers = [q for q in out if q["delta"] * sign > 0]
            if not movers:
                break
            q = max(movers, key=lambda q: abs(q["delta"]))
            q["delta"] = round(q["delta"] - sign * 0.1, 1)
            excess = round(excess - 0.1, 1)
    for p in out:
        base = float(old.get(p["id"], {}).get("weight", 0.0))
        p["weight"] = round(max(0.0, min(MAX_PRESSURE, base + p["delta"])), 1)
        p["delta"] = round(p["weight"] - base, 1)
    out = [p for p in out if not (p["status"] == "new" and p["weight"] <= 0)]   # a new pressure trimmed to nothing never existed
    closed = [p for p in out if p["weight"] < CLOSE_BELOW]
    for p in closed:
        p["status"] = "closed"; p["delta"] = round(-float(old.get(p["id"], {}).get("weight", 0)), 1)
    out.sort(key=lambda p: -p["weight"])
    new_seconds = round(max(MIN_SECONDS, min(MAX_SECONDS, anchor - sum(p["weight"] for p in out))), 1)
    return new_seconds, out, cited, notes


# ----------------------------------------------------------------------------
# Monthly report
# ----------------------------------------------------------------------------

MONTHS_DIR = DATA_DIR / "months"


def month_label(ym):
    return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")


def strip_cites(text):
    return tidy(re.sub(r"\s*\(?\[\d+(?:[,\s]*\d+)*\]\)?", "", str(text)))


def fix_direction(text, delta):
    """The model sometimes says 'back'/'forward' about the clock. Rewrite those
    to the unambiguous 'closer to midnight' / 'further from midnight'."""
    closer = delta < 0
    toward = "closer to midnight" if closer else "further from midnight"
    verbs = r"(moved|moves|moving|move|set|pushed|pushes|pushing|push|ticked|ticks|ticking|tick|turned|turns|wound)"
    text = re.sub(verbs + r"\s+(the\s+clock\s+)?(back|backward|backwards|forward|forwards|ahead)\b",
                  lambda m: f"{m.group(1)} {m.group(2) or ''}{toward}", text, flags=re.I)
    text = re.sub(r"\b(clock\s+)(back|backward|backwards|forward|forwards|ahead)\b", r"\1" + toward, text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def build_month_report(ym):
    """Gather the month's daily files, compute the numbers, ask the chair for a
    longer piece, and write data/months/YYYY-MM.json."""
    set_anchor_info(load_json(OFFICIAL_FILE, None) or [list(x) for x in OFFICIAL_SEED])
    days = sorted((DATA_DIR / "days").glob(f"{ym}-*.json"))
    if not days:
        log(f"month {ym}: no daily files, skipping")
        return
    readings = sorted((json.loads(p.read_text()) for p in days), key=lambda r: r["date"])

    start = readings[0]["previous"]
    end = readings[-1]["seconds"]
    secs = [r["seconds"] for r in readings]
    moves = [{"date": r["date"], "from": r["previous"], "to": r["seconds"],
              "lede": fix_direction(r["chair"]["lede"], r["seconds"] - r["previous"])}
             for r in readings if r["seconds"] != r["previous"]]
    biggest = sorted(moves, key=lambda m: abs(m["to"] - m["from"]), reverse=True)[:5]
    votes, split_days = {}, 0
    for r in readings:
        vs = set()
        for e in r["experts"]:
            votes.setdefault(e["role"], {"worse": 0, "hold": 0, "better": 0})
            votes[e["role"]][e["vote"]] = votes[e["role"]].get(e["vote"], 0) + 1
            vs.add(e["vote"])
        if "worse" in vs and "better" in vs:
            split_days += 1

    stats = {
        "month": ym, "label": month_label(ym), "days": len(readings),
        "first_day": readings[0]["date"], "last_day": readings[-1]["date"],
        "start": start, "end": end, "low": min(secs), "high": max(secs),
        "moves": len(moves), "split_days": split_days, "votes": votes, "biggest": biggest,
        "official": (load_json(OFFICIAL_FILE, None) or OFFICIAL_SEED)[-1][1],
    }

    lines = []
    for r in readings:
        v = ", ".join(f"{e['role']}={e['vote']}" for e in r["experts"])
        lines.append(f"{r['date']}: {r['previous']}->{r['seconds']} | {v} | {r['chair']['lede']}")
    seen, heads = set(), []
    for r in readings:
        for h in r["headlines"][:3]:
            k = h["title"].lower()[:60]
            if k not in seen:
                seen.add(k)
                heads.append(f"{r['date']} ({h['tag']}, {h['source']}): {h['title']}")
    heads = heads[:40]

    system = rules() + f"""
You are the chair writing the monthly report for {stats['label']}. You have the daily verdicts and votes below.
Write for a general reader, calm and concrete, no hype. Refer only to dates and events in the material.
Wording: fewer seconds means CLOSER to midnight (more danger); more seconds means FURTHER from midnight (safer).
Say "closer to midnight" or "further from midnight". Never say "forward", "back", "up" or "down" about the clock.
Disagreement: the material lists for each day which experts voted worse, hold or better. A day counts as a
disagreement when at least one expert voted "worse" and another voted "better". Name the experts and the day.
If the month has few days of data (the project may have started mid-month), say so plainly and keep it short.
Return JSON:
{{"title": "<short headline for the month, under 10 words>",
  "summary": "<2-3 sentences: where the clock started and ended and the single biggest reason>",
  "sections": [
    {{"heading": "What moved the clock", "body": ["<paragraph>", "<paragraph>"]}},
    {{"heading": "Where the experts disagreed", "body": ["<paragraph>"]}},
    {{"heading": "What to watch next month", "body": ["<paragraph>"]}}
  ]}}"""
    splits = []
    for r in readings:
        w = [e["role"] for e in r["experts"] if e["vote"] == "worse"]
        b = [e["role"] for e in r["experts"] if e["vote"] == "better"]
        if w and b:
            splits.append(f"{r['date']}: worse={', '.join(w)}; better={', '.join(b)}")
    split_text = "\n".join(splits) if splits else "(none)"
    user = (f"Month: {stats['label']}. Days with readings: {stats['days']} ({stats['first_day']} to {stats['last_day']}).\n"
            f"Clock: started at {start} s, ended at {end} s, lowest {stats['low']} s, highest {stats['high']} s. "
            f"Days with a move: {stats['moves']}. Days where experts voted in opposite directions: {split_days}.\n"
            f"Official Bulletin clock: {stats['official']} s.\n\nDays with disagreement:\n{split_text}\n\nDaily log:\n" + "\n".join(lines)
            + "\n\nHeadlines cited:\n" + "\n".join(heads))
    report = ask(system, user, max_tokens=3500)

    sections = []
    for sec in report.get("sections", [])[:5]:
        body = sec.get("body", [])
        if isinstance(body, str):
            body = [body]
        body = [fix_direction(strip_cites(p), end - start) for p in body if str(p).strip()]
        if body:
            sections.append({"heading": str(sec.get("heading", "")).strip()[:80], "body": body[:4]})
    out = dict(stats)
    out.update({
        "title": fix_direction(str(report.get("title", stats["label"])).strip(), end - start)[:120],
        "summary": fix_direction(strip_cites(report.get("summary", "")), end - start),
        "sections": sections,
        "series": [{"date": r["date"], "seconds": r["seconds"]} for r in readings],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    MONTHS_DIR.mkdir(parents=True, exist_ok=True)
    (MONTHS_DIR / f"{ym}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    months = sorted([p.stem for p in MONTHS_DIR.glob("????-??.json")], reverse=True)
    (MONTHS_DIR / "index.json").write_text(json.dumps(
        [{"month": m, "label": month_label(m)} for m in months]))
    log(f"month {ym}: wrote report ({len(readings)} days, {start} -> {end})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="skip the AI, write files anyway")
    ap.add_argument("--news-only", action="store_true", help="print filtered news and stop")
    ap.add_argument("--date", help="override the reading date (YYYY-MM-DD)")
    ap.add_argument("--month", help="only build the monthly report for YYYY-MM and stop")
    ap.add_argument("--restart", action="store_true", help="keep the archive, but start today from the official clock with no open pressures")
    args = ap.parse_args()

    if args.month:
        build_month_report(args.month)
        return

    now = datetime.now(timezone.utc)
    # The job runs at 23:00 UTC so the reading is ready before midnight.
    # A run in the 23:00 hour is dated for the next day; the site keeps it
    # hidden until 00:00 UTC. A run at any other hour is dated today.
    reading_day = now + timedelta(hours=1) if now.hour == 23 else now
    today = args.date or reading_day.strftime("%Y-%m-%d")
    early = today > now.strftime("%Y-%m-%d")
    if early:
        log(f"early run: reading dated {today}, published by the site at 00:00 UTC")

    prev = load_json(LATEST, None)
    history = load_json(HISTORY, [])
    official = update_official(now)
    set_anchor_info(official)
    anchor = float(official[-1][1])
    if args.restart:
        seconds, pressures, yesterday_lede = anchor, [], ""
        log("restart: today starts from the official clock, earlier readings are kept")
    elif prev and not prev.get("sample") and prev.get("date") == today:
        # Re-run on the same day: start from where that day started,
        # so the reading is replaced, not stacked.
        seconds = float(prev["previous"])
        pressures = prev.get("pressures_before", [])
        yesterday_lede = prev.get("yesterday_lede", "")
        log("re-run on the same day, replacing today's reading")
    elif prev and not prev.get("sample"):
        seconds = float(prev["seconds"])
        pressures = [p for p in prev.get("pressures", []) if p.get("weight", 0) > 0]
        yesterday_lede = prev.get("chair", {}).get("lede", "")
    else:
        seconds = anchor
        pressures = []
        yesterday_lede = ""
    # The Bulletin set a new time: start clean from it. Every open pressure is now part of the baseline.
    if pressures and prev and float(prev.get("anchor", anchor)) != anchor:
        log(f"official clock changed since yesterday ({prev.get('anchor')} -> {anchor}): closing {len(pressures)} open pressures")
        for p in pressures:
            p["status"] = "closed"; p["delta"] = -float(p["weight"]); p["weight"] = 0.0
        closed_by_reset = pressures
        pressures = []
        shown_yesterday = seconds   # what the site showed yesterday, kept as "previous"
        seconds = anchor
    elif prev and prev.get("official_reset") and prev.get("date") == today:
        closed_by_reset, shown_yesterday = [], float(prev["previous"])
        seconds = anchor            # re-run on the reset day starts from the new official time
    else:
        closed_by_reset, shown_yesterday = [], seconds
    pressures_before = [dict(p) for p in pressures]
    log(f"date {today}, starting from {seconds} seconds with {len(pressures)} open pressures (anchor {anchor})")

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
            experts.append(ask_expert(role, focus, scope, news, seconds, pressures))
            time.sleep(int(os.environ.get("CALL_DELAY", "8")))  # stay under free-tier tokens-per-minute
        log("asking chair")
        chair = ask_chair(experts, news, seconds, yesterday_lede, pressures, anchor)

    new_seconds, new_pressures, cited, notes = apply_rules(chair, experts, items, pressures, anchor, today)
    for n in notes:
        log(n)
    new_pressures = new_pressures + closed_by_reset
    for p in new_pressures:
        log(f"  pressure {p['id']}: {p['weight']:.1f} s ({p['status']}, {p['delta']:+.1f})")
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
    body = [tidy(re.sub(r"\s*\(?\[\d+(?:[,\s]*\d+)*\]\)?", "", str(p))) for p in body if str(p).strip()][:4]

    latest = {
        "sample": False,
        "date": today,
        "seconds": new_seconds,
        "previous": shown_yesterday,
        "official_reset": bool(closed_by_reset) or bool(prev and prev.get("official_reset") and prev.get("date") == today),
        "anchor": anchor,
        "pressures": new_pressures,
        "pressures_before": pressures_before,
        "generated_at": now.isoformat(timespec="seconds"),
        "yesterday_lede": yesterday_lede,
        "chair": {"lede": build_lede(chair, seconds, new_seconds),
                  "body": [fix_direction(p, new_seconds - seconds) for p in body]},
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

    # On the 1st, write last month's report (never blocks the daily reading).
    if reading_day.day == 1 and not args.date:
        prev_month = (reading_day.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        try:
            build_month_report(prev_month)
        except Exception as ex:
            log(f"monthly report failed: {ex}")

    # Picture shown when the link is shared in chat apps.
    try:
        from share_image import draw_share_image
        nice_date = datetime.strptime(today, "%Y-%m-%d").strftime("%-d %B %Y")
        # An early run writes share-next.png; the publish workflow renames it
        # to share.png at 00:00 UTC so link previews don't change early.
        name = "share-next.png" if early else "share.png"
        draw_share_image(new_seconds, seconds, nice_date, DATA_DIR / name)
        log(f"wrote {name}")
    except Exception as ex:
        log(f"share image skipped: {ex}")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        log(f"FAILED: {ex}")
        sys.exit(1)
