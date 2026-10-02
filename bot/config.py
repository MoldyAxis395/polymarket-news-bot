"""Bot settings. Edit here, restart bot."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

MODE = "paper"                      # only paper trading is implemented

# --- money (paper) ---
START_CASH = 100.0
MAX_PER_TRADE = 10.0
MAX_OPEN_POSITIONS = 5
TAKE_PROFIT = 0.15                  # sell when best bid >= entry * 1.15
STOP_LOSS = 0.10                    # sell when best bid <= entry * 0.90
MAX_HOLD_HOURS = 6                  # sell anyway after this
COOLDOWN_MIN_PER_MARKET = 60        # don't re-enter same market within N min of exit

# --- market filters ---
MARKETS_TO_INDEX = 2100             # top markets by 24h volume ending within MAX_DAYS_TO_END
MIN_LIQUIDITY = 3000.0
MIN_VOLUME_24H = 5000.0
MAX_SPREAD = 0.04                   # skip if ask - bid > 4c
ENTRY_PRICE_MIN = 0.05              # don't buy dust / near-certain outcomes
ENTRY_PRICE_MAX = 0.90
ALREADY_MOVED = 0.05                # skip if price moved > 5c since last index (news priced in)
MIN_MINUTES_TO_END = 20             # don't enter markets about to close
MAX_DAYS_TO_END = 30                # one headline barely moves long-dated futures
SKIP_QUESTION = r"temperature|tweets?|spread:|o/u|over/under|up or down|price of|\bhit\b"

# --- news ---
NEWS_MAX_AGE_MIN = 20               # ignore items older than this
NEWS_POLL_SEC = 45
GDELT_POLL_SEC = 180
POSITION_CHECK_SEC = 20
MARKET_REFRESH_SEC = 600

# --- signal ---
MIN_CONFIDENCE = 0.5

UA = {"User-Agent": "Mozilla/5.0 (polymarket-news-paperbot)"}

RSS_FEEDS = {
    "gnews_top": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "gnews_sport": "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en",
    "gnews_world": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en",
    "gnews_business": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
    "gnews_reuters": "https://news.google.com/rss/search?q=when:1h+source:reuters&hl=en-US&gl=US&ceid=US:en",
    "gnews_injury": "https://news.google.com/rss/search?q=when:1h+(injury+OR+%22ruled+out%22+OR+suspended)&hl=en-US&gl=US&ceid=US:en",
    "bbc_world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_sport": "https://feeds.bbci.co.uk/sport/rss.xml",
    "skysports": "https://www.skysports.com/rss/12040",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "politico": "https://rss.politico.com/politics-news.xml",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}

GDELT_QUERY = ('(injured OR injury OR "ruled out" OR suspended OR resigns OR resigned '
               'OR withdraws OR arrested OR indicted OR ceasefire OR wins OR elected) sourcelang:english')

# --- brain ---
# "llm"   = LLM backends tried in order; a backend is skipped when its key is missing,
#           its daily cap is hit, it is rate limited (429) or unreachable. All fail -> rule brain.
# "rules" = free keyword heuristics only.
BRAIN = "llm"
LLM_BACKENDS = [
    {"name": "groq-120b", "url": "https://api.groq.com/openai/v1/chat/completions",
     "model": "openai/gpt-oss-120b", "key_env": "GROQ_API_KEY", "daily_cap": 950,
     "extra": {"reasoning_effort": "low"}},
    {"name": "groq-20b", "url": "https://api.groq.com/openai/v1/chat/completions",
     "model": "openai/gpt-oss-20b", "key_env": "GROQ_API_KEY", "daily_cap": 950,
     "extra": {"reasoning_effort": "low"}},
    {"name": "ollama", "url": "http://localhost:11434/v1/chat/completions",   # started by ci/ollama.sh
     "model": os.environ.get("LLM_MODEL", "qwen2.5:7b"), "daily_cap": 100000, "timeout": 180},
]
