import os
from app_core import app, mongo, discord, category_data, json_data
from routes import register_routes

register_routes(app, mongo, discord)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
