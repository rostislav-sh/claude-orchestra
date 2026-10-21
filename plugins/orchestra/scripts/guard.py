#!/usr/bin/env python3
"""PreToolUse-хук плагина orchestra: ограничивает, что может делать каждый агент.

Срабатывает только для сабагентов этого плагина (agent_type вида "orchestra:<роль>").
Основную сессию (тимлида) и любые другие агенты не трогает.

Код выхода 2 блокирует вызов инструмента, текст из stderr видит агент.
Любая другая ошибка скрипта не блокирует (fail-open), чтобы не сломать сессию.

Настройки проекта: .orchestra/config.json
    {"tests_dir": "tests", "source_dirs": ["src"]}
"""
from __future__ import annotations

import json
import os
import re
import sys

PLUGIN_PREFIX = "orchestra"
STATE_DIR = ".orchestra"
REPORTS_DIR = ".orchestra/reports"
DEFAULT_CONFIG = {"tests_dir": "tests", "source_dirs": ["src", "app"]}

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}

FORBIDDEN_GIT = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?"
    r"(commit|push|reset\s+--hard|checkout\s+--|restore|stash|rebase|merge|cherry-pick|clean\s+-[a-z]*f)(?![\w-])"
)
READ_COMMANDS = re.compile(
    r"\b(cat|head|tail|less|more|grep|egrep|rg|ag|sed|awk|find|ls|tree|bat|nl|strings|xxd|od|cp|diff|"
    r"python3?\s+-c|git\s+(show|diff|log\s+-p|blame))\b"
)


def block(reason: str) -> None:
    print(f"[orchestra guard] {reason}", file=sys.stderr)
    sys.exit(2)


def load_config(project_dir: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    path = os.path.join(project_dir, STATE_DIR, "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("tests_dir"), str):
            cfg["tests_dir"] = data["tests_dir"]
        if isinstance(data.get("source_dirs"), list):
            cfg["source_dirs"] = [str(d) for d in data["source_dirs"]]
    except (OSError, ValueError):
        pass
    cfg["tests_dir"] = norm_dir(cfg["tests_dir"])
    cfg["source_dirs"] = [norm_dir(d) for d in cfg["source_dirs"] if d]
    return cfg


def norm_dir(d: str) -> str:
    return d.replace("\\", "/").strip("/").removeprefix("./")


def rel_to_project(path: str, cwd: str, project_dir: str) -> str | None:
    """Путь относительно корня проекта или None, если он вне проекта."""
    if not path:
        return ""
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    root = os.path.realpath(project_dir)
    if full != root and not full.startswith(root + os.sep):
        return None
    return os.path.relpath(full, root).replace("\\", "/").removeprefix("./")


def under(rel: str, directory: str) -> bool:
    if rel in ("", "."):
        return False
    return rel == directory or rel.startswith(directory + "/")


def is_parent_of(rel: str, directory: str) -> bool:
    """rel — корень проекта или папка, внутри которой лежит directory."""
    return rel in ("", ".") or directory.startswith(rel.rstrip("/") + "/")


def tool_paths(tool: str, tin: dict) -> list[str]:
    if tool == "NotebookEdit":
        return [tin.get("notebook_path", "")]
    if tool in ("Write", "Edit", "MultiEdit", "Read"):
        return [tin.get("file_path", "")]
    if tool == "Grep":
        return [tin.get("path", "")]
    if tool == "Glob":
        base = tin.get("path", "") or ""
        pattern = tin.get("pattern", "") or ""
        joined = os.path.join(base, pattern) if base else pattern
        # Для glob берём часть шаблона до первого спецсимвола.
        prefix = re.split(r"[*?\[{]", joined, maxsplit=1)[0]
        return [prefix]
    return []


def mentions_dir(command: str, directory: str) -> bool:
    pattern = r"(^|[\s'\"=/(:])" + re.escape(directory) + r"(/|\s|$|['\");])"
    return re.search(pattern, command) is not None


def check_write(role: str, rel: str | None, cfg: dict) -> None:
    if rel is None:
        block("Запись вне проекта запрещена.")
    tests = cfg["tests_dir"]
    if role == "tester":
        if under(rel, tests) or under(rel, REPORTS_DIR):
            return
        block(f"Тестер пишет только в {tests}/ (и отчёты в {REPORTS_DIR}/). Путь: {rel}")
    if role == "coder":
        if under(rel, tests):
            block(f"Кодер не трогает тесты ({tests}/). Если тест кажется неверным, опиши это в отчёте тимлиду.")
        if under(rel, STATE_DIR) and not under(rel, REPORTS_DIR):
            block(f"Файлы {STATE_DIR}/ ведёт тимлид. Кодер может писать только отчёты в {REPORTS_DIR}/.")
        return
    if role == "architect":
        if under(rel, tests):
            block(f"Архитектор не пишет тесты ({tests}/), их пишет тестер по контрактам.")
        return
    if role in ("reviewer", "debugger"):
        if under(rel, REPORTS_DIR):
            return
        block(f"Роль {role} не меняет код. Результат верни в ответе или сохрани в {REPORTS_DIR}/.")


def check_tester_read(rel: str | None, cfg: dict, search: bool) -> None:
    if rel is None:
        return
    for src in cfg["source_dirs"]:
        if under(rel, src):
            block(
                f"Тестер не читает реализацию ({src}/). Работай по {STATE_DIR}/contracts.md и "
                f"{STATE_DIR}/architecture.md. Если в контракте чего-то не хватает, сообщи тимлиду."
            )
        if search and is_parent_of(rel, src):
            # Поиск по корню или по папке, внутри которой лежит src, вскрыл бы реализацию.
            block(f"Сузь поиск до {cfg['tests_dir']}/ или {STATE_DIR}/: он захватывает {src}/.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return

    agent_type = str(data.get("agent_type") or "")
    if ":" not in agent_type:
        return
    prefix, role = agent_type.rsplit(":", 1)
    if prefix.split(":")[0] != PLUGIN_PREFIX:
        return

    tool = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    cfg = load_config(project_dir)

    if tool == "Bash":
        command = str(tin.get("command", ""))
        if FORBIDDEN_GIT.search(command):
            block(
                "Коммиты, пуши и откаты агентам запрещены: коммит делает тимлид после одобрения "
                "пользователя. Оставь изменения в рабочей копии."
            )
        if role == "tester" and READ_COMMANDS.search(command):
            for src in cfg["source_dirs"]:
                if mentions_dir(command, src):
                    block(f"Тестер не читает реализацию ({src}/) через shell. Запускать тесты можно.")
        return

    for path in tool_paths(tool, tin):
        rel = rel_to_project(path or cwd, cwd, project_dir)
        if tool in WRITE_TOOLS:
            check_write(role, rel, cfg)
        elif tool in READ_TOOLS and role == "tester":
            check_tester_read(rel, cfg, search=tool in ("Grep", "Glob"))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail-open: ошибка хука не должна ломать работу
        print(f"[orchestra guard] internal error: {exc}", file=sys.stderr)
        sys.exit(0)
