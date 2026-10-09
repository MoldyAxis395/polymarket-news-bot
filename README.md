# Bot news → Polymarket (paper trading)

Legge notizie (14 feed RSS + GDELT) ogni 45s → trova mercato Polymarket collegato →
compra il lato che la notizia favorisce (solo prezzi 0.20-0.75) → rivende quando il prezzo
sale (+6c), scende (-5c), dopo 6 ore o a mercato risolto. Soldi FINTI: $100, max $10 a trade, posizioni senza limite finché c'è cassa (min $2).
Prezzi e fill simulati sul vero order book CLOB di Polymarket.

- Gira su **GitHub Actions** (repo pubblico): turni da 5h45 ogni 6h, stato salvato in `data/` con commit ogni 30 min.
  Avvio manuale: tab Actions → paper-bot → Run workflow. Stop: Actions → paper-bot → `...` → Disable workflow.
- Notifiche Telegram a ogni apertura/chiusura (secrets `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`).
- In locale: `start.bat` avvia nascosto · `stop.bat` ferma · `report.bat` riepilogo
  (prima `git pull` per avere lo stato aggiornato; non farlo girare in locale E su GitHub insieme)
- Log: `data/bot.log` · trade: `data/trades.csv` · segnali (anche scartati): `data/signals.jsonl`
- Impostazioni: `bot/config.py`

## Cervello
- `rules` (default, gratis): parole chiave (injured, ruled out, wins...) vicino a un nome
  di squadra/persona presente nel mercato. Esempio: "Jefferson injury, Vikings" → compra Dolphins.
  Limite: non sa che Messi gioca per l'Argentina se la notizia non nomina l'Argentina.
- `llm` (attivo): Groq gpt-oss-120b → gpt-oss-20b, secret `GROQ_API_KEY`. Controlli per ogni notizia
  (ogni scarto finisce nel log come `drop[motivo]`, conteggi in `status.json` → `llm.rejects`):
  1. filtro fuffa (`FLUFF`): preview, fantasy, rumor, "could/might", timeline, titoli-domanda
  2. LLM estrae il fatto: chi, squadra, lega, confermato? nuovo? titolare? buono/cattivo
  3. squadra estratta deve comparire nel titolo (l'LLM sbaglia le rose)
  4. fonte: editore affidabile (`TRUSTED_PUBLISHERS`) oppure 2ª fonte diversa entro 60 min
  5. mercati cercati per squadra + filtro lega; lato verificato con regola fissa
  6. verifica scettica LLM (reasoning medium) prima di comprare
  Se l'LLM non risponde i segnali delle regole vengono solo registrati, non tradati (`RULES_CAN_TRADE`).
  Fonti veloci (`bot/fastfeeds.py`, più rapide degli articoli): cambi di stato infortuni ESPN
  (NFL NBA NHL MLB WNBA CFB, ogni 60s), esclusioni MLB dalla formazione, formazioni ufficiali calcio
  vs partita precedente, post Bluesky degli insider (`BLUESKY_ACCOUNTS`). Le notizie SPORTIVE da RSS
  vengono solo registrate per misura, non tradate (`RSS_SPORTS_TRADE`): arrivano tardi.
  Misura: `python tools/reactions.py [giorni]` → per fonte, quanto il prezzo si era già mosso quando
  il bot ha visto la notizia e quanto si muove dopo (+5/+15/+60 min).
  Test: Actions → brain-dryrun (minuti, oppure `replay` per rigiocare i titoli dei trade passati).

## BOT PENGWIN (2° bot, stesso processo)
Copia i pronostici calcio di Kristian Pengwin (mondopengwin.it, escono ~24h prima della partita).
Portafoglio separato da $100 in `data/pengwin/` (trades.csv, status.json), $10 a pronostico, tenuto
fino a fine partita (niente TP/SL). Telegram con prefisso "🐧 BOT PENGWIN", PnL separato.
L'LLM mappa il pronostico su UN mercato Polymarket della partita: `esatto` (1, X2, GG, over 2.5...)
o `approssimato` (una gamba di combo/multigol). Tiri, corner, cartellini → saltato (notifica ⚪).
Impostazioni `PENGWIN_*` in `bot/config.py`. Test: Actions → brain-dryrun con `pengwin`.

## Soldi veri — NON implementato
Serve: wallet Polygon con USDC, chiave privata, `py-clob-client`, verifica che Polymarket
sia accessibile dall'Italia.
