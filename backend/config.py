import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "wp-manager-secret-key-change-in-production")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-secret-key-change-in-production")

    # Default admin credentials
    ADMIN_USERNAME = "adsadmin"
    ADMIN_PASSWORD = "Mm123567.."

    # 1Panel API Configuration
    PANEL_HOST = os.environ.get("PANEL_HOST", "167.172.142.95")
    PANEL_PORT = int(os.environ.get("PANEL_PORT", 3500))
    PANEL_API_KEY = os.environ.get("PANEL_API_KEY", "gk7FQSSTtnudJbImg0E8MdXbmU3v7qF6")

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///wp_manager.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @property
    def panel_base_url(self):
        return f"http://{self.PANEL_HOST}:{self.PANEL_PORT}"


config = Config()
