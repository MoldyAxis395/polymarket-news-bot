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
MAX_OPEN_POSITIONS = None             # None = no slot limit: buy while cash lasts
MIN_TRADE_USD = 2.0                 # don't open a position with less cash than this
TAKE_PROFIT = 0.06                  # sell when best bid >= entry + 6c (absolute: % targets were
STOP_LOSS = 0.05                    # sell when best bid <= entry - 5c  unreachable at 0.9, noise at 0.15)
MAX_HOLD_HOURS = 6                  # sell anyway after this
COOLDOWN_MIN_PER_MARKET = 60        # don't re-enter same market within N min of exit

# --- market filters ---
MARKETS_TO_INDEX = 2100             # top markets by 24h volume ending within MAX_DAYS_TO_END
MIN_LIQUIDITY = 3000.0
MIN_VOLUME_24H = 5000.0
MAX_SPREAD = 0.04                   # skip if ask - bid > 4c
ENTRY_PRICE_MIN = 0.20              # below: stop loss inside normal noise (6 trades, -$7.38)
ENTRY_PRICE_MAX = 0.75              # above: no room for take profit
ALREADY_MOVED = 0.05                # skip if price moved > 5c since last index (news priced in)
MIN_MINUTES_TO_END = 20             # don't enter markets about to close
MAX_DAYS_TO_END = 30                # one headline barely moves long-dated futures
SKIP_QUESTION = r"temperature|tweets?|spread:|o/u|over/under|up or down|price of|\bhit\b"

# --- news ---
NEWS_MAX_AGE_MIN = 5                # older news is already priced in
NEWS_POLL_SEC = 45
GDELT_POLL_SEC = 180
POSITION_CHECK_SEC = 20
MARKET_REFRESH_SEC = 600

# --- signal ---
MIN_CONFIDENCE = 0.65
RULES_CAN_TRADE = False             # brain="llm" + LLM down: rule signals are logged, not traded
                                    # (rules: 41 trades, 7 wins, -$9.28 on 2026-10-02..05)

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
    # Local Ollama (ci/ollama.sh) was tested 2026-10-02: qwen2.5:7b got 4/8 headlines wrong
    # (wrong team/sport/direction) and takes ~25 s per call -> not used; rules are the fallback.
]
