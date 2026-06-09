import threading, os
from bot import run as run_bot
from dashboard import app

def start_dashboard():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()
    run_bot()
