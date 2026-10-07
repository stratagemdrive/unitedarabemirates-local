"""
unitedarabemirates_news.py
Pulls United Arab Emirates news headlines from RSS feeds (no APIs), translates them from
Arabic into English, categorizes each story as Diplomacy, Military, Energy,
Economy, or Local Events, and writes docs/unitedarabemirates_news.json.

Rules:
- Up to 20 stories per category, United Arab Emirates as the primary subject.
- No story older than 7 days.
- New stories replace the oldest entries first; if fewer than 20 new relevant
  stories are found, only what was found is added.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from dateutil import parser as dateparser
from deep_translator import GoogleTranslator

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COUNTRY = "United Arab Emirates"
OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "unitedarabemirates_news.json")
MAX_PER_CATEGORY = 20
MAX_AGE_DAYS = 7
MAX_ENTRIES_PER_FEED = 40
CATEGORIES = ["Diplomacy", "Military", "Energy", "Economy", "Local Events"]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# national=True  -> domestic/national section; uncategorized stories default to Local Events.
# national=False -> general feed; only kept if it matches a category keyword.
# All stories must mention United Arab Emirates or at least not be clearly about another country.
FEEDS = [
    {"source": "WAM (Emirates News Agency)", "url": "https://www.wam.ae/ar/rss", "national": True},
    {"source": "Al Khaleej", "url": "https://www.alkhaleej.ae/rss.xml", "national": False},
    {"source": "Al Khaleej", "url": "https://www.alkhaleej.ae/rss", "national": False},
    {"source": "Al Bayan", "url": "https://www.albayan.ae/rss", "national": False},
    {"source": "Al Bayan", "url": "https://www.albayan.ae/rss/across-the-uae", "national": False},
    {"source": "Al Ittihad", "url": "https://www.alittihad.ae/rss", "national": False},
    {"source": "Emarat Al Youm", "url": "https://www.emaratalyoum.com/rss", "national": False},
    {"source": "Al Roeya", "url": "https://www.alroeya.com/rss", "national": False},
    {"source": "Erem News", "url": "https://www.eremnews.com/feed", "national": False},
    {"source": "The National", "url": "https://www.thenationalnews.com/arc/outboundfeeds/rss/category/uae/?outputType=xml", "national": True},
    {"source": "The National", "url": "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml", "national": False},
    {"source": "Gulf News", "url": "https://gulfnews.com/feed/uae", "national": False},
    {"source": "Khaleej Times", "url": "https://www.khaleejtimes.com/rss", "national": False},
    {"source": "Gulf Today", "url": "https://www.gulftoday.ae/rss", "national": False},
]

# English terms (post-translation) that mark United Arab Emirates as the subject.
COUNTRY_TERMS = [
    "uae", "u.a.e.", "emirates", "emirati", "emiratis", "abu dhabi",
    "dubai", "sharjah", "ajman", "fujairah", "ras al khaimah", "umm al quwain",
    "al ain", "mohamed bin zayed", "mohammed bin zayed", "mohammed bin rashid", "mohamed bin rashid", "bin zayed",
    "bin rashid", "hamdan bin mohammed", "abdullah bin zayed", "mansour bin zayed", "federal national council", "adnoc",
    "masdar", "dewa", "adnec", "mubadala", "adq", "emirates airline",
    "etihad", "dirham", "dirhams", "dfm", "adx", "expo city",
]

# Terms that signal a story is mainly about another country.
FOREIGN_TERMS = [
    "united states", "u.s.", "america", "american", "washington", "trump",
    "white house", "china", "chinese", "beijing", "russia", "russian",
    "moscow", "putin", "ukraine", "ukrainian", "kyiv", "zelensky", "israel",
    "israeli", "gaza", "netanyahu", "iran", "iranian", "tehran", "india",
    "indian", "japan", "japanese", "north korea", "south korea", "germany",
    "german", "berlin", "france", "french", "paris", "britain", "british",
    "london", "venezuela", "brazil", "argentina", "mexico", "canada",
    "australia", "pakistan", "syria", "lebanon", "turkey", "egypt",
    "saudi", "qatar", "taiwan",
]
FOREIGN_TERMS = [t for t in FOREIGN_TERMS if t not in COUNTRY_TERMS]

# Off-topic stories to drop (sports, entertainment, lifestyle).
EXCLUDE_TERMS = [
    "football", "soccer", "champions league", "europa league", "serie a",
    "la liga", "laliga", "ekstraklasa", "premier league", "tennis", "golf",
    "formula 1", "f1", "motogp", "grand prix", "basketball", "volleyball",
    "boxing", "ufc", "muay thai", "cricket", "olympic", "goalkeeper",
    "striker", "coach", "match", "derby", "horoscope", "zodiac", "recipe",
    "celebrity", "actor", "actress", "singer", "concert", "film", "movie",
    "tv series", "netflix", "fashion", "lottery", "lotto", "gossip",
    "influencer", "reality show", "big brother",
]

CATEGORY_KEYWORDS = {
    "Diplomacy": [
        "diplomacy", "diplomatic", "diplomat", "foreign minister",
        "foreign ministry", "foreign affairs", "foreign policy", "embassy",
        "ambassador", "consul", "consulate", "envoy", "treaty", "bilateral",
        "multilateral", "summit", "united nations", "un general assembly",
        "security council", "european union", "eu", "european commission",
        "brussels", "asean", "gcc", "arab league", "g7", "g20", "sanctions",
        "state visit", "official visit", "talks with", "agreement with",
        "memorandum", "mou", "relations with", "ties with", "cooperation with",
        "president of", "prime minister of", "met with", "meets",
        "visa", "migration pact", "mediation", "ceasefire", "peace",
        "abdullah bin zayed", "ministry of foreign affairs", "gcc", "brics", "cepa", "comprehensive economic partnership", "received", "receives", "phone call", "congratulat",
    ],
    "Military": [
        "military", "army", "navy", "naval", "air force", "armed forces",
        "defence", "defense", "defence minister", "defense minister",
        "troops", "soldier", "soldiers", "weapon", "weapons", "missile",
        "drone", "drones", "fighter jet", "f-35", "tank", "tanks",
        "submarine", "warship", "frigate", "nato", "military exercise",
        "drill", "border clash", "artillery", "ammunition", "conscription",
        "general staff", "commander", "airspace", "air defense",
        "air defence", "military base", "arms", "war", "attack", "terror",
        "terrorist", "insurgent", "security forces", "coast guard",
        "ministry of defence", "uae armed forces", "edge group", "national service", "houthi", "sudan",
    ],
    "Energy": [
        "energy", "electricity", "power grid", "power plant", "blackout",
        "oil", "crude", "gas", "natural gas", "lng", "pipeline", "fuel",
        "petrol", "diesel", "gasoline", "renewable", "solar", "wind farm",
        "wind power", "offshore wind", "hydro", "hydrogen", "nuclear power",
        "nuclear plant", "reactor", "coal", "emissions", "decarbon",
        "energy transition", "energy price", "energy bill", "tariff",
        "refinery", "opec", "battery", "electric vehicle",
        "adnoc", "masdar", "dewa", "taqa", "enec", "barakah", "opec+",
    ],
    "Economy": [
        "economy", "economic", "gdp", "inflation", "interest rate",
        "central bank", "budget", "fiscal", "tax", "taxes", "vat",
        "unemployment", "employment", "jobs", "labor", "labour", "wage",
        "wages", "salary", "pension", "recession", "growth", "trade",
        "exports", "imports", "tariffs", "investment", "investor",
        "stock", "stocks", "stock exchange", "shares", "bond", "bonds",
        "debt", "deficit", "bank", "banks", "banking", "finance minister",
        "ministry of finance", "treasury", "company", "companies", "firm",
        "business", "industry", "industrial", "manufacturing", "retail",
        "consumer", "prices", "cost of living", "housing market", "real estate",
        "property", "tourism", "tourists", "startup", "merger", "acquisition",
        "ipo", "profit", "revenue", "credit rating", "imf", "world bank",
        "dfm", "adx", "mubadala", "adq", "dirham", "non-oil", "free zone", "cepa", "real estate",
    ],
    "Local Events": [
        "police", "court", "trial", "judge", "arrest", "arrested", "crime",
        "murder", "killed", "dead", "death", "injured", "accident", "crash",
        "fire", "flood", "floods", "storm", "earthquake", "heatwave",
        "weather", "rain", "drought", "wildfire", "landslide", "protest",
        "protesters", "strike", "demonstration", "rally", "election",
        "elections", "vote", "parliament", "mayor", "governor", "city",
        "municipal", "regional", "province", "village", "hospital",
        "health", "school", "university", "students", "teachers",
        "transport", "train", "railway", "airport", "metro", "road",
        "traffic", "housing", "migrants", "migration", "immigration",
        "government", "minister", "opposition", "party", "coalition",
        "corruption", "scandal", "festival", "church", "mosque", "temple",
        "municipality", "emirate", "ruler", "crown prince", "dubai police", "abu dhabi police", "sharjah police", "rta", "ncm",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_term(text, terms):
    for term in terms:
        if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text):
            return True
    return False


def count_terms(text, terms):
    return sum(
        1 for term in terms
        if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text)
    )


def looks_english(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    ascii_letters = sum(1 for c in letters if c.isascii())
    return ascii_letters / len(letters) > 0.97 and True


_translator = GoogleTranslator(source="auto", target="en")


def translate(text):
    """Translate to English. Returns the original text if translation fails."""
    text = (text or "").strip()
    if not text or looks_english(text):
        return text
    for attempt in range(3):
        try:
            result = _translator.translate(text[:4500])
            if result:
                return result.strip()
        except Exception as exc:
            log.warning("Translation attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    return text


def parse_date(entry):
    for key in ("published", "updated", "created", "dc_date"):
        raw = entry.get(key)
        if raw:
            try:
                dt = dateparser.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            return datetime(*struct[:6], tzinfo=timezone.utc)
    return None


def classify(text):
    scores = {cat: count_terms(text, kws) for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(CATEGORIES, key=lambda c: scores[c])
    return best if scores[best] > 0 else None


def is_about_country(text, national):
    if has_term(text, COUNTRY_TERMS):
        return True
    return not has_term(text, FOREIGN_TERMS)


def norm_title(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_feed(feed, known_urls, cutoff):
    source, url, national = feed["source"], feed["url"], feed.get("national", False)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as exc:
        log.warning("FEED FAILED  %-28s %s (%s)", source, url, exc)
        return []

    if not parsed.entries:
        log.warning("FEED EMPTY   %-28s %s", source, url)
        return []

    stories = []
    for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
        link = (entry.get("link") or "").strip()
        if not link or link in known_urls:
            continue
        pub = parse_date(entry)
        if pub is None or pub < cutoff or pub > datetime.now(timezone.utc) + timedelta(hours=6):
            continue
        title_raw = strip_html(entry.get("title", ""))
        if not title_raw:
            continue
        desc_raw = strip_html(entry.get("summary", ""))[:300]

        title = translate(title_raw)
        desc = translate(desc_raw) if desc_raw else ""
        text = (title + " " + desc).lower()

        if has_term(text, EXCLUDE_TERMS):
            continue
        if not is_about_country(text, national):
            continue
        category = classify(text)
        if category is None:
            if not national:
                continue
            category = "Local Events"

        stories.append({
            "title": title,
            "source": source,
            "url": link,
            "published_date": pub.isoformat(),
            "category": category,
        })
        known_urls.add(link)

    log.info("FEED OK      %-28s %3d entries, %3d kept  %s",
             source, len(parsed.entries), len(stories), url)
    return stories


# ---------------------------------------------------------------------------
# Load / merge / write
# ---------------------------------------------------------------------------

def load_existing():
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("stories", []) if isinstance(data, dict) else data
    except Exception:
        return []


def merge(existing, fresh, cutoff):
    grouped = {cat: [] for cat in CATEGORIES}
    seen_urls, seen_titles = set(), set()
    for story in fresh + existing:
        cat = story.get("category")
        if cat not in grouped:
            continue
        try:
            pub = dateparser.parse(story["published_date"]).astimezone(timezone.utc)
        except Exception:
            continue
        if pub < cutoff:
            continue
        key_t = norm_title(story.get("title"))
        if story.get("url") in seen_urls or key_t in seen_titles:
            continue
        seen_urls.add(story.get("url"))
        seen_titles.add(key_t)
        grouped[cat].append(story)

    for cat in CATEGORIES:
        # Newest first; anything beyond 20 (the oldest) is dropped.
        grouped[cat].sort(key=lambda s: s["published_date"], reverse=True)
        grouped[cat] = grouped[cat][:MAX_PER_CATEGORY]
    return grouped


def write_output(grouped):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stories = [s for cat in CATEGORIES for s in grouped[cat]]
    out = {
        "country": COUNTRY,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "story_count": len(stories),
        "stories": stories,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log.info("Wrote %d stories to %s", len(stories), OUTPUT_FILE)


def main():
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    existing = load_existing()
    known_urls = {s.get("url") for s in existing if s.get("url")}
    log.info("Loaded %d existing stories", len(existing))

    fresh = []
    for feed in FEEDS:
        fresh.extend(fetch_feed(feed, known_urls, cutoff))
        time.sleep(0.5)
    log.info("Found %d new relevant stories", len(fresh))

    grouped = merge(existing, fresh, cutoff)
    log.info("Category totals: %s", {c: len(grouped[c]) for c in CATEGORIES})
    write_output(grouped)


if __name__ == "__main__":
    main()
