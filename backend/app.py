import logging
import os
import sys
import traceback

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from flask import Flask, jsonify, send_from_directory
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

    # Crawlbase (Walmart / Amazon / TikTok product scraping)
    app.config["CRAWLBASE_TOKEN"] = config.CRAWLBASE_TOKEN

    # Extensions
    CORS(app, supports_credentials=True)
    JWTManager(app)

    # Initialize database
    init_db()

    # Register API routes
    register_routes(app)

    # Global error handlers to prevent crashes
    @app.errorhandler(Exception)
    def handle_unhandled_exception(e):
        logger.error(f"Unhandled exception: {traceback.format_exc()}")
        return jsonify({"code": 500, "message": f"服务器内部错误: {str(e)[:100]}"}), 500

    @app.errorhandler(404)
    def handle_404(e):
        return jsonify({"code": 404, "message": "资源不存在"}), 404

    @app.errorhandler(405)
    def handle_405(e):
        return jsonify({"code": 405, "message": "方法不允许"}), 405

    @app.errorhandler(500)
    def handle_500(e):
        return jsonify({"code": 500, "message": "服务器内部错误"}), 500

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
            resp = send_from_directory(templates_dir, path)
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return resp
        # Then static
        static_dir = os.path.join(frontend_dir, "static")
        if os.path.isfile(os.path.join(static_dir, path)):
            resp = send_from_directory(static_dir, path)
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return resp
        # Fallback to index.html for SPA
        resp = send_from_directory(templates_dir, "index.html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    use_reloader = os.environ.get("FLASK_RELOADER", "1") != "0"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=use_reloader)
