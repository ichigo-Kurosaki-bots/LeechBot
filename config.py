import os

class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")

    # Optional: Pyrogram User Session String (with Telegram Premium) for up to 4GB single-file uploads
    SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

    # Admin, Groups & Channels
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    # Required if using SESSION_STRING (4GB mode): bot & user session must both be admins here
    LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "0"))
    # Comma-separated allowed group IDs, e.g. "-1001234567890,-1009876543210"
    # Accepts numeric group IDs (-100...) AND/OR invite links (https://t.me/+...)
    _raw_allowed_groups = [
        x.strip() for x in os.getenv("ALLOWED_GROUPS", "").split(",") if x.strip()
    ]
    ALLOWED_GROUPS = [int(x) for x in _raw_allowed_groups if x.lstrip("-").isdigit()]
    ALLOWED_GROUP_LINKS = [x for x in _raw_allowed_groups if not x.lstrip("-").isdigit()]

    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "")
    DB_NAME = os.getenv("DB_NAME", "AeroLeechBot")

    # Paths & Limits
    DOWNLOAD_DIR = os.path.abspath("./downloads")
    # 4GB limit when SESSION_STRING + LOG_CHANNEL are set; otherwise 2GB
    MAX_UPLOAD_SIZE = (
        3950 * 1024 * 1024 if (SESSION_STRING and LOG_CHANNEL) else 1980 * 1024 * 1024
    )
    PORT = int(os.getenv("PORT", "8080"))
