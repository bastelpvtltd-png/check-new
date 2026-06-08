"""
main.py — Runs bot + dashboard together in one process.
Render/Railway: set start command to  `python main.py`
"""
import threading
import os
from bot import run as run_bot
from dashboard import app

def start_dashboard():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Dashboard in background thread
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()

    # Bot in main thread
    run_bot()
