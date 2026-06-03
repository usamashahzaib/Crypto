# Crypto Analysis Bot

Automated Python bot.
Runs every 15 minutes.
Sends Telegram alerts when Groq confidence is above 65%.
Sends daily summary at 8:00 AM Pakistan time.

## Setup

1. Open this folder:
   `C:\Users\Tier-3\Documents\New project\crypto_analysis_bot`

2. Install Python 3.10+.

3. Double-click:

```text
setup_windows.bat
```

This creates `.venv`, installs libraries, and creates `.env`.

Manual install:

```powershell
pip install -r requirements.txt
```

4. Put your real keys inside `.env`.

5. Run:

```powershell
python crypto_analysis_bot.py
```

Or double-click:

```text
run_bot.bat
```

## Files

- `crypto_analysis_bot.py`: main bot
- `.env`: your private keys
- `signals_log.txt`: all sent signals
- `bot_state.json`: duplicate-alert memory
- `.venv`: local Python environment

## Notes

- Institutional layer uses 4H 200 EMA, 15M RSI/MACD, Binance Futures funding, and open interest.
- Groq simulates Technical Chartist, Sentiment/Flow Analyst, and Risk Manager agents internally.
- Capital engine assumes `$30` total capital, max 3 active positions, and `$10-$15` per trade.
- Trade tracker closes BUY positions at take profit or stop loss and reports dollar P&L.
- `PAPER_MODE = True` records signals and simulated P&L without live execution.
- Signal filter requires confidence, RSI, Fear & Greed, and 4-hour same-coin cooldown.
- Weekly report sends every Monday at 9:00 AM Pakistan time.
- News uses NewsAPI plus Cointelegraph, CoinDesk, Decrypt, and Bitcoin Magazine RSS.
- Etherscan is Ethereum-only. Real BTC whale checks use free mempool.space.
- Etherscan is also checked for large ETH transfers.
- If one API fails, the bot skips it and continues.
- This is not financial advice.
