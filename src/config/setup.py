# -*- coding: utf-8 -*-
"""Environment setup: .env loading and bootstrap capture."""

import logging
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
    env_path = Config._resolve_env_path()
    load_dotenv(dotenv_path=env_path, override=override)
