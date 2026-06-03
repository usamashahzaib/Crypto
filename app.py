from flask import Flask, jsonify

import crypto_analysis_bot as bot

app = Flask(__name__)


@app.get("/")
def home():
    return jsonify({
        "name": "CryptoMind Bot",
        "status": "running",
        "paper_mode": bot.PAPER_MODE,
        "active_trades": len(bot.ACTIVE_TRADES),
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


bot.start_scheduler(run_immediately=False)
