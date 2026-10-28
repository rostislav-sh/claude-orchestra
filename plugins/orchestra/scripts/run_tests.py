#!/usr/bin/env python3
"""Запуск тестов для агентов orchestra: вместо простыни вывода — короткий JSON.

    python3 run_tests.py [--root DIR] [--no-cache] [--max-failures N] [аргументы pytest...]

Что делает:
- запускает pytest проекта (команда — ниже), по умолчанию по папке тестов из
  .orchestra/config.json; аргументы после опций уходят в pytest как есть
  (`tests/test_api.py -k export`);
- разбирает результат из JUnit XML, а если его нет — из текстовой сводки pytest;
- печатает JSON: счётчики, статус и только упавшие тесты с сообщением, местом и
  сокращённым трейсбеком. Зелёный прогон — несколько строк, а не сотни;
- кладёт тот же JSON в .orchestra/reports/last-test-run.json;
- кеш: если с последнего зелёного прогона не изменились ни исходники, ни тесты,
  ни конфиги, ни аргументы — тесты не запускаются, возвращается прошлый результат
  с "cached": true. Красные прогоны не кешируются: окружение могли починить.

Какой командой запускать (первое найденное):
1. "test_command" в .orchestra/config.json — строка, например "uv run pytest";
2. python из .venv / venv проекта: `<python> -m pytest`;
3. тот же python, которым запущен скрипт: `<python> -m pytest`.

Коды выхода — как у pytest: 0 зелёный, 1 есть падения, 5 тестов не нашлось и т. д.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

STATE_DIR = ".orchestra"
REPORTS_DIR = os.path.join(STATE_DIR, "reports")
LAST_RUN = "last-test-run.json"
CACHE_FILE = "test-cache.json"
DEFAULT_CONFIG = {"tests_dir": "tests", "source_dirs": ["src", "app"]}

SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".venv", "venv", "node_modules", ".hypothesis", ".cache", "htmlcov", ".idea", ".vscode",
    "dist", "build", ".eggs", STATE_DIR,
}
SKIP_SUFFIXES = (".pyc", ".pyo")
# Файлы в корне, от которых зависит прогон: зависимости, настройки pytest, общие фикстуры.
ROOT_INPUTS = (
    "pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini", "conftest.py",
    "requirements.txt", "requirements-dev.txt", "uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock",
)

MAX_MESSAGE = 300
MAX_DETAIL_LINES = 25
MAX_DETAIL_CHARS = 2500
TAIL_LINES = 40

EXIT_STATUS = {0: "passed", 1: "failed", 2: "interrupted", 3: "internal_error", 4: "usage_error", 5: "no_tests"}


# ---------------------------------------------------------------- проект и конфиг


def find_root(start: str) -> str:
    d = os.path.realpath(start)
    while True:
        if os.path.isdir(os.path.join(d, STATE_DIR)) or os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.realpath(start)
        d = parent


def load_config(root: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(os.path.join(root, STATE_DIR, "config.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        if isinstance(data.get("tests_dir"), str):
            cfg["tests_dir"] = data["tests_dir"]
        if isinstance(data.get("source_dirs"), list):
            cfg["source_dirs"] = [str(d) for d in data["source_dirs"]]
        if isinstance(data.get("test_command"), str) and data["test_command"].strip():
            cfg["test_command"] = data["test_command"].strip()
    cfg["tests_dir"] = cfg["tests_dir"].replace("\\", "/").strip("/") or "tests"
    return cfg


def venv_python(root: str) -> str | None:
    for name in (".venv", "venv"):
        for rel in (("bin", "python"), ("Scripts", "python.exe")):
            path = os.path.join(root, name, *rel)
            if os.path.isfile(path):
                return path
    return None


def base_command(root: str, cfg: dict) -> list[str]:
    if cfg.get("test_command"):
        return shlex.split(cfg["test_command"], posix=os.name != "nt")
    return [venv_python(root) or sys.executable, "-m", "pytest"]


def runs_in_container(cmd: list[str]) -> bool:
    return bool(cmd) and os.path.basename(cmd[0]) in ("docker", "docker-compose", "podman")


# ---------------------------------------------------------------- кеш


def iter_input_files(root: str, cfg: dict):
    for name in ROOT_INPUTS:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            yield name
    dirs = [cfg["tests_dir"]] + list(cfg["source_dirs"])
    seen = set()
    for top in dirs:
        top_path = os.path.join(root, top)
        if not os.path.isdir(top_path):
            continue
        for dirpath, dirnames, filenames in os.walk(top_path):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if fn.endswith(SKIP_SUFFIXES):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
                if rel not in seen:
                    seen.add(rel)
                    yield rel


def inputs_key(root: str, cfg: dict, cmd: list[str]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(cmd, ensure_ascii=False).encode())
    for rel in iter_input_files(root, cfg):
        h.update(rel.encode() + b"\0")
        try:
            with open(os.path.join(root, rel), "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path: str, data) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


# ---------------------------------------------------------------- разбор результата


def short(text: str | None, limit: int = MAX_MESSAGE) -> str:
    text = (text or "").strip()
    first = text.splitlines()[0] if text else ""
    return first if len(first) <= limit else first[: limit - 1] + "…"


def trim_details(text: str | None) -> str:
    lines = (text or "").strip("\n").splitlines()
    if len(lines) > MAX_DETAIL_LINES:
        lines = ["…"] + lines[-MAX_DETAIL_LINES:]
    out = "\n".join(lines)
    if len(out) > MAX_DETAIL_CHARS:
        out = "…" + out[-MAX_DETAIL_CHARS:]
    return out


def location_from(details: str, file_attr: str | None, line_attr: str | None) -> str | None:
    # Место падения — последняя строка трейсбека вида "path.py:12: AssertionError".
    found = re.findall(r"^([^\s:][^:\n]*\.py):(\d+):", details or "", flags=re.M)
    if found:
        path, line = found[-1]
        return f"{path.replace(os.sep, '/')}:{line}"
    if file_attr:
        return f"{file_attr}:{int(line_attr) + 1}" if line_attr and line_attr.isdigit() else file_attr
    return None


def test_id(case: ET.Element) -> str:
    name = case.get("name", "")
    file_attr = case.get("file")
    classname = case.get("classname", "")
    if file_attr:
        # classname = "tests.test_api.TestExport" → класс "TestExport", если он есть.
        mod = os.path.splitext(file_attr.replace("\\", "/"))[0].replace("/", ".")
        cls = classname[len(mod) + 1:] if classname.startswith(mod + ".") else ""
        return "::".join(p for p in (file_attr.replace("\\", "/"), cls, name) if p)
    return f"{classname}::{name}" if classname else name


def parse_junit(path: str) -> dict | None:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return None
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures = []
    for case in tree.iter("testcase"):
        problem = None
        for tag in ("failure", "error"):
            el = case.find(tag)
            if el is not None:
                problem = (tag, el)
                break
        if problem is None:
            if case.find("skipped") is not None:
                counts["skipped"] += 1
            else:
                counts["passed"] += 1
            continue
        tag, el = problem
        counts["failed" if tag == "failure" else "errors"] += 1
        details = el.text or ""
        failures.append({
            "test": test_id(case),
            "kind": tag,
            "message": short(el.get("message") or details),
            "location": location_from(details, case.get("file"), case.get("line")),
            "details": trim_details(details),
        })
    return {"counts": counts, "failures": failures}


SUMMARY_ITEM = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")
SHORT_LINE = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")


def parse_text(output: str) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    summary = [ln for ln in output.splitlines() if re.search(r"\d+ (passed|failed|errors?|skipped)", ln)]
    if summary:
        for num, kind in SUMMARY_ITEM.findall(summary[-1]):
            key = {"error": "errors", "errors": "errors"}.get(kind, kind)
            if key in counts:
                counts[key] += int(num)
    failures = []
    for ln in output.splitlines():
        m = SHORT_LINE.match(ln.strip())
        if m:
            failures.append({
                "test": m.group(2),
                "kind": "failure" if m.group(1) == "FAILED" else "error",
                "message": short(m.group(3)),
                "location": None,
                "details": "",
            })
    return {"counts": counts, "failures": failures}


def tail(text: str, n: int = TAIL_LINES) -> str:
    lines = text.rstrip().splitlines()
    return "\n".join((["…"] if len(lines) > n else []) + lines[-n:])


# ---------------------------------------------------------------- запуск


def run(root: str, cfg: dict, pytest_args: list[str], timeout: int) -> dict:
    cmd = base_command(root, cfg)
    is_pytest = any("pytest" in os.path.basename(a) for a in cmd)
    args = list(pytest_args)
    if not args and is_pytest and os.path.isdir(os.path.join(root, cfg["tests_dir"])):
        args = [cfg["tests_dir"]]
    junit = None
    extra = ["-q", "-rfE", "--tb=short", "-p", "no:cacheprovider"] if is_pytest else []
    if is_pytest and not runs_in_container(cmd):
        fd, junit = tempfile.mkstemp(prefix="orchestra-junit-", suffix=".xml")
        os.close(fd)
        extra += [f"--junitxml={junit}", "-o", "junit_family=xunit1"]
    full = cmd + extra + args
    started = time.time()
    try:
        proc = subprocess.run(
            full, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code, output = -1, f"timeout after {timeout}s\n" + str(exc.stdout or "")
    except OSError as exc:
        code, output = -2, f"cannot start {full[0]!r}: {exc}"
    duration = round(time.time() - started, 2)

    parsed = parse_junit(junit) if junit else None
    if junit:
        try:
            os.unlink(junit)
        except OSError:
            pass
    if parsed is None:
        parsed = parse_text(output)

    if code == -1:
        status = "timeout"
    elif code == -2:
        status = "cannot_start"
    else:
        status = EXIT_STATUS.get(code, "error")
    result = {
        "status": status,
        "exit_code": code,
        "command": " ".join(shlex.quote(a) for a in cmd + args),
        **parsed["counts"],
        "duration_s": duration,
        "failures": parsed["failures"],
    }
    # Если разобрать не удалось или прогон упал не на тестах — хвост вывода, чтобы было видно причину.
    if status not in ("passed", "failed") or (status == "failed" and not parsed["failures"]):
        result["output_tail"] = tail(output)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="корень проекта (по умолчанию ищется от текущей папки)")
    parser.add_argument("--no-cache", action="store_true", help="не брать прошлый зелёный результат")
    parser.add_argument("--max-failures", type=int, default=20, help="сколько падений показывать подробно")
    parser.add_argument("--timeout", type=int, default=1800, help="предел времени прогона, секунд")
    args, pytest_args = parser.parse_known_args(argv)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    start = args.root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root = find_root(start)
    cfg = load_config(root)
    reports = os.path.join(root, REPORTS_DIR)
    has_state = os.path.isdir(os.path.join(root, STATE_DIR))

    key = inputs_key(root, cfg, base_command(root, cfg) + pytest_args)
    cache_path = os.path.join(reports, CACHE_FILE)
    if not args.no_cache:
        cached = read_json(cache_path)
        if isinstance(cached, dict) and cached.get("key") == key and isinstance(cached.get("result"), dict):
            result = dict(cached["result"], cached=True)
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0

    result = run(root, cfg, pytest_args, args.timeout)
    shown = result["failures"][: max(args.max_failures, 0)]
    if len(result["failures"]) > len(shown):
        result["failures_not_shown"] = len(result["failures"]) - len(shown)
    result["failures"] = shown

    if has_state:
        write_json(os.path.join(reports, LAST_RUN), dict(result, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
        if result["status"] == "passed":
            write_json(cache_path, {"key": key, "result": result})
    print(json.dumps(result, ensure_ascii=False, indent=1))
    code = result["exit_code"]
    return code if 0 <= code <= 255 else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
