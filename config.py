"""Settings. Values come from the .env file (see .env.example)."""
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "smart_school")
SECRET_KEY = os.getenv("SECRET_KEY", "")

PAGE_SIZE = 25               # rows per page in admin lists
LOGIN_MAX_FAILS = 5          # wrong passwords allowed ...
LOGIN_WINDOW_SECONDS = 600   # ... within this many seconds
MIN_PASSWORD_LENGTH = 8
