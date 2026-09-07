"""Configuration adapter for the relocated, existing RSSHub reader APIs."""

from types import SimpleNamespace
from market_data_service.settings import get_settings


class Config:
    @staticmethod
    def get_instance():
        return SimpleNamespace(rsshub_base_url=get_settings().rsshub_url)
