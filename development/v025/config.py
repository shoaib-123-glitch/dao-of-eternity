
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent


def load_config():
    """
    Load the correct environment configuration.

    Production:
        Uses .env

    Test:
        Uses .env.test and requires the approved
        test database and restricted test user.

    Private gameplay:
        Explicit guarded settings; never loads a dotenv file or ambient DSN.
    """

    environment = os.getenv("APP_ENV", "production").strip().lower()
    if environment!="private-playtest":
        raise RuntimeError('V025_CANDIDATE_REQUIRES_GATED_PRIVATE_LAUNCH')

    if environment == "private-playtest":
        from private_playtest import PrivatePlaytestConfig, sanitize_environment
        config = PrivatePlaytestConfig.from_environment()
        from candidate_policy_v025 import authorize
        authorize(config,os.environ)
        sanitize_environment()
        return config

    if environment == "test":
        # Use the separate test configuration.
        load_dotenv(PROJECT_DIR / ".env.test", override=True)

    elif environment == "production":
        # Preserve the existing live game configuration.
        load_dotenv(PROJECT_DIR / ".env")

    else:
        raise RuntimeError(
            f"Unknown APP_ENV: {environment}. "
            "Expected 'production', 'test' or 'private-playtest'."
        )

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is missing.")

    if environment == "test":
        parsed = urlparse(database_url)

        database_name = unquote(parsed.path.lstrip("/"))
        database_user = unquote(parsed.username or "")

        if database_name != "dao_eternity_test":
            raise RuntimeError(
                "SAFETY BLOCK: Test mode requires "
                "the dao_eternity_test database."
            )

        if database_user != "dao_test_user":
            raise RuntimeError(
                "SAFETY BLOCK: Test mode requires "
                "the dao_test_user account."
            )

        if parsed.scheme not in ("postgresql", "postgres"):
            raise RuntimeError(
                "SAFETY BLOCK: Invalid PostgreSQL connection URL."
            )

    return database_url
