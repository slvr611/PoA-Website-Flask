import os
from app_core import app, mongo, discord, category_data, json_data
from routes import register_routes
from flask import request, redirect

register_routes(app, mongo, discord)

@app.before_request
def before_request():
    if not request.is_secure and not app.debug:
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)

if __name__ == "__main__":
    # Only reached when running `python app.py` directly (local dev) —
    # production serves `app` via gunicorn (see Procfile), which imports
    # this module without executing this block at all, so app.debug there
    # is Flask's own safe default (False) regardless of this flag. Debug
    # mode defaults OFF here too and must be opted into explicitly, since
    # Werkzeug's interactive debugger is a real remote-code-execution risk
    # if this script is ever run somewhere reachable from the internet.
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
