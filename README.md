# Bot news → Polymarket (paper trading)

Legge notizie (14 feed RSS + GDELT) ogni 45s → trova mercato Polymarket collegato →
compra il lato che la notizia favorisce → rivende quando il prezzo sale (+15%),
scende (-10%) o dopo 6 ore. Soldi FINTI: $100, max $10 a trade, max 5 posizioni.
Prezzi e fill simulati sul vero order book CLOB di Polymarket.

- `start.bat` avvia nascosto · `stop.bat` ferma · `report.bat` riepilogo
- Log: `data/bot.log` · trade: `data/trades.csv` · segnali (anche scartati): `data/signals.jsonl`
- Impostazioni: `bot/config.py`

## Cervello
- `rules` (default, gratis): parole chiave (injured, ruled out, wins...) vicino a un nome
  di squadra/persona presente nel mercato. Esempio: "Jefferson injury, Vikings" → compra Dolphins.
  Limite: non sa che Messi gioca per l'Argentina se la notizia non nomina l'Argentina.
- `llm`: qualsiasi API compatibile OpenAI (Groq free tier, Gemini, Ollama locale).
  `BRAIN = "llm"` in config + variabile d'ambiente `LLM_API_KEY`.

## Soldi veri — NON implementato
Serve: wallet Polygon con USDC, chiave privata, `py-clob-client`, verifica che Polymarket
sia accessibile dall'Italia.
