"""Текстовые утилиты."""
import re


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.U)
    return re.sub(r"[\s_-]+", "-", value).strip("-")


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
