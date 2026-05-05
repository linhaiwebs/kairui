import logging
import os
import sys

from flask import Flask, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager

from config import config
from models import init_db
from routes import register_routes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(
        __name__,
        static_folder=None,
    )

    # Configuration
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["JWT_SECRET_KEY"] = config.JWT_SECRET_KEY
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = 86400  # 24 hours

    # 1Panel Configuration
    app.config["PANEL_HOST"] = config.PANEL_HOST
    app.config["PANEL_PORT"] = config.PANEL_PORT
    app.config["PANEL_API_KEY"] = config.PANEL_API_KEY

    # Extensions
    CORS(app, supports_credentials=True)
    JWTManager(app)

    # Initialize database
    init_db()

    # Register API routes
    register_routes(app)

    # Serve frontend static files
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")

    @app.route("/")
    def index():
        return send_from_directory(os.path.join(frontend_dir, "templates"), "index.html")

    @app.route("/<path:path>")
    def static_files(path):
        # Try templates first
        templates_dir = os.path.join(frontend_dir, "templates")
        if os.path.isfile(os.path.join(templates_dir, path)):
            return send_from_directory(templates_dir, path)
        # Then static
        static_dir = os.path.join(frontend_dir, "static")
        if os.path.isfile(os.path.join(static_dir, path)):
            return send_from_directory(static_dir, path)
        # Fallback to index.html for SPA
        return send_from_directory(templates_dir, "index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
