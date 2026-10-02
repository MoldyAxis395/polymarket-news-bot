"""Telegram notifications. Token/chat id come from env (GitHub secrets), never from code."""
import os

import requests


def telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                      data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"})
    except Exception:
        pass
