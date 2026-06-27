# -*- coding: utf-8 -*-
"""Environment setup: .env loading and bootstrap capture."""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def setup_env(override: bool = False):
    """
    Initialize environment variables from .env file.

    Args:
        override: If True, overwrite existing environment variables with values
                  from .env file.
    """
    # Import here to avoid circular import
    from src.config.config_dataclass import Config
    Config._capture_bootstrap_runtime_env_overrides()
    env_file = os.getenv("ENV_FILE")
    if env_file:
        env_path = Path(env_file)
    else:
        env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(dotenv_path=env_path, override=override)