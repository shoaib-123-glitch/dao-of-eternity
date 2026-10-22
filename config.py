
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent

def load_config():
    environment = os.getenv("APP_ENV", "production").lower()

    if environment == "test":
        # Explicitly load test settings instead of the live .env file.
        load_dotenv(PROJECT_DIR / ".env.test", override=True)
    else:
        load_dotenv(PROJECT_DIR / ".env")

    database_url = os.getenv("DATABASE_URL")

    if environment == "test":
        if not database_url:
            raise RuntimeError("Test DATABASE_URL is missing.")

        database_name = unquote(urlparse(database_url).path.lstrip("/"))

        if database_name != "dao_eternity_test":
            raise RuntimeError(
                "SAFETY BLOCK: Test mode requires dao_eternity_test."
            )

    return database_url
