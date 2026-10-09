"""Free news sources: RSS feeds + GDELT. Yields only fresh, never-seen items."""
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from . import config

_s = requests.Session()
_s.headers.update(config.UA)
_TAG = re.compile(r"<[^>]+>")


@dataclass
class NewsItem:
    source: str
    title: str
    summary: str
    url: str
    published: datetime
    publisher: str = ""

    @property
    def trusted(self):
        """Wire services / official league sites / major outlets: one report is enough."""
        p = self.publisher.lower().removeprefix("www.")
        return p in config.TRUSTED_PUBLISHERS or any(p.endswith("." + d) or p == d for d in config.TRUSTED_DOMAINS)

    @property
    def key(self):
        t = re.sub(r"\W+", " ", self.title.lower()).strip()
        return hashlib.sha1(t[:120].encode()).hexdigest()

    @property
    def age_min(self):
        return (datetime.now(timezone.utc) - self.published).total_seconds() / 60


def _clean(s):
    return html.unescape(_TAG.sub(" ", s or "")).strip()


def _rss(name, url):
    r = _s.get(url, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        # Google News appends " - Publisher" and names it in <source>; other feeds are one outlet
        publisher = config.FEED_PUBLISHER.get(name, name)
        if name.startswith("gnews"):
            publisher = _clean(it.findtext("source")) or (title.rsplit(" - ", 1)[1] if " - " in title else "")
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
        pd = it.findtext("pubDate")
        try:
            dt = parsedate_to_datetime(pd).astimezone(timezone.utc) if pd else None
        except Exception:
            dt = None
        if not title or not dt:
            continue
        yield NewsItem(name, title, _clean(it.findtext("description"))[:400], it.findtext("link") or "", dt, publisher)


def _gdelt():
    r = _s.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
        "query": config.GDELT_QUERY, "mode": "artlist", "format": "json",
        "maxrecords": 75, "timespan": "1h", "sort": "datedesc"}, timeout=30)
    if r.status_code != 200 or not r.text.startswith("{"):
        return
    for a in r.json().get("articles", []):
        try:
            dt = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        yield NewsItem("gdelt", a.get("title", ""), "", a.get("url", ""), dt, a.get("domain", ""))


class NewsFeed:
    SEEN = config.DATA / "seen.json"

    def __init__(self, log):
        self.log = log
        self.seen = {}  # key -> first-seen unix time (persisted so restarts don't lose/repeat news)
        if self.SEEN.exists():
            try:
                self.seen = json.loads(self.SEEN.read_text())
            except Exception:
                pass
        self.last_gdelt = 0.0
        self.errors = {}

    def _save(self):
        cutoff = time.time() - 48 * 3600
        self.seen = {k: t for k, t in self.seen.items() if t > cutoff}
        self.SEEN.write_text(json.dumps(self.seen))

    def _collect(self):
        items = []
        for name, url in config.RSS_FEEDS.items():
            try:
                items.extend(_rss(name, url))
                self.errors.pop(name, None)
            except Exception as e:
                n = self.errors[name] = self.errors.get(name, 0) + 1
                if n in (1, 10, 100):
                    self.log(f"feed {name} error x{n}: {e}")
        if time.time() - self.last_gdelt > config.GDELT_POLL_SEC:
            self.last_gdelt = time.time()
            try:
                items.extend(_gdelt())
            except Exception as e:
                self.log(f"gdelt error: {e}")
        return items

    def prime(self):
        """Mark everything currently published as seen, so we only trade on NEW news."""
        n = 0
        for it in self._collect():
            self.seen.setdefault(it.key, time.time())
            n += 1
        self._save()
        return n

    def poll(self):
        fresh = []
        for it in self._collect():
            if it.key in self.seen:
                continue
            self.seen[it.key] = time.time()
            if it.age_min <= config.NEWS_MAX_AGE_MIN:
                fresh.append(it)
        self._save()
        return fresh
