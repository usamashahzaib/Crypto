# Render Deployment

Important: Render Free web services sleep after 15 minutes without inbound traffic.
This is free, but not true 24/7. A paid Render Background Worker is the correct always-on setup.

## Free Web Service

1. Create a GitHub repo.

2. Push this folder to GitHub.

3. Go to `https://dashboard.render.com`.

4. Click `New` -> `Blueprint`.

5. Connect your repo.

6. Render will detect `render.yaml`.

7. Add all secret environment variables when Render asks:

```text
COINGECKO_KEY
NEWSAPI_KEY
GROQ_API_KEY
BINANCE_API_KEY
BINANCE_API_SECRET
GEMINI_API_KEY
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
ETHERSCAN_KEY
```

8. Click `Apply`.

9. Open the service URL.

10. Check logs. You should see:

```text
CryptoMind scheduler started.
```

## Manual Web Service Setup

Use these settings:

```text
Service Type: Web Service
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2
Plan: Free
```

## True 24/7 Render Setup

Use:

```text
Service Type: Background Worker
Start Command: python crypto_analysis_bot.py
```

But this requires a paid Render worker.
