"""Настройки приложения."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "helpdesk"
    page_size: int = 20
    max_title_length: int = 120
    support_email: str = "support@example.com"
