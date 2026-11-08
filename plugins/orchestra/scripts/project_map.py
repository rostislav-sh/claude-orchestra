#!/usr/bin/env python3
"""Карта проекта для агентов orchestra: что где лежит и какие тесты есть.

    python3 project_map.py build    [--root DIR]     # собрать или обновить карту
    python3 project_map.py describe [--root DIR]     # описания файлов: JSON {путь: "описание"} на stdin
    python3 project_map.py need     [--root DIR]     # каким файлам нужно описание

Карта лежит в .orchestra/map/ и собирается кодом, без модели:

- code.md   — файлы кода по папкам: одна строка «что делает», публичные классы и функции,
              HTTP-маршруты, точки входа, инфраструктура (Dockerfile, compose, миграции).
              Читают архитектор, кодеры, ревьюер, дебаггер. Тестеру закрыт хуком: в нём
              устройство реализации, а тестер работает по контрактам.
- tests.md  — тестовые файлы: сколько тестов, какие модули проекта они импортируют, какие
              фикстуры используют; фикстуры conftest.py; команда запуска; итог последнего
              прогона run_tests.py; модули без тестов. Читают все.
- code.json — служебное: хеш и описание каждого файла (тестеру тоже закрыт).

Описание файла: ручное (из describe) → иначе первая строка docstring модуля. Если файл
изменился после того, как описание написали, оно помечается «(устарело)». Без описания
и без docstring файл попадает в список need — его описывает архитектор или тимлид одной
строкой через describe. Так модель тратит токены только на новые и изменённые файлы.

build печатает короткий JSON: сколько файлов, что изменилось с прошлой сборки, кому нужно
описание. Сборка идёт за доли секунды — запускай её после каждого пункта плана.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time

STATE_DIR = ".orchestra"
MAP_DIR = os.path.join(STATE_DIR, "map")
CODE_MD, TESTS_MD, CODE_JSON = "code.md", "tests.md", "code.json"
DEFAULT_CONFIG = {"tests_dir": "tests", "source_dirs": ["src", "app"]}

SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".venv", "venv",
    "env", "node_modules", ".hypothesis", ".cache", "htmlcov", ".idea", ".vscode", "dist", "build", ".eggs",
    STATE_DIR, ".eval_hidden", "site-packages",
}
SKIP_SUFFIXES = (".pyc", ".pyo", ".so", ".dll", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff",
                 ".woff2", ".ttf", ".zip", ".gz", ".tar", ".db", ".sqlite", ".sqlite3", ".lock", ".map", ".min.js")
INFRA_FILES = re.compile(
    r"^(Dockerfile(\..+)?|.*\.dockerfile|(docker-)?compose(\..+)?\.ya?ml|Makefile|Procfile|alembic\.ini|"
    r"pyproject\.toml|setup\.cfg|setup\.py|requirements(-\w+)?\.txt|\.env\.example|manage\.py|tox\.ini|pytest\.ini)$",
    re.I,
)
EXTRA_DIRS = ("migrations", "alembic", "deploy", "docker", "scripts")
ENTRY_NAMES = {"main.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py", "cli.py", "bot.py", "worker.py"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "route", "websocket", "api_route"}
MAX_FILES = 3000
MAX_SYMBOLS = 12
MAX_TEST_NAMES = 12


# ---------------------------------------------------------------- проект


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
        if isinstance(data.get("test_command"), str):
            cfg["test_command"] = data["test_command"]
    cfg["tests_dir"] = cfg["tests_dir"].replace("\\", "/").strip("/") or "tests"
    cfg["source_dirs"] = [d.replace("\\", "/").strip("/") for d in cfg["source_dirs"] if d.strip("/")]
    return cfg


def rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def walk(root: str, top: str):
    top_path = os.path.join(root, top)
    if os.path.isfile(top_path):
        yield rel(root, top_path)
        return
    for dirpath, dirnames, filenames in os.walk(top_path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if fn.endswith(SKIP_SUFFIXES) or fn.startswith("."):
                continue
            yield rel(root, os.path.join(dirpath, fn))


def collect(root: str, cfg: dict) -> tuple[list[str], list[str]]:
    """Файлы кода (с инфраструктурой) и тестовые файлы."""
    tests_dir = cfg["tests_dir"]
    code, tests, seen = [], [], set()

    def add(path: str) -> None:
        if path in seen or len(seen) >= MAX_FILES:
            return
        seen.add(path)
        if path == tests_dir or path.startswith(tests_dir + "/"):
            tests.append(path)
        else:
            code.append(path)

    for fn in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, fn)) and INFRA_FILES.match(fn):
            add(fn)
    sources = [d for d in cfg["source_dirs"] if os.path.isdir(os.path.join(root, d))]
    if sources:
        for d in sources + [d for d in EXTRA_DIRS if os.path.isdir(os.path.join(root, d))]:
            for p in walk(root, d):
                add(p)
    else:  # исходники лежат не в source_dirs — берём весь проект
        for p in walk(root, "."):
            if p.endswith(".py") or INFRA_FILES.match(os.path.basename(p)):
                add(p)
    if os.path.isdir(os.path.join(root, tests_dir)):
        for p in walk(root, tests_dir):
            add(p)
    return code, tests


def file_hash(root: str, path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(os.path.join(root, path), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- разбор Python


def parse_py(root: str, path: str) -> ast.Module | None:
    try:
        with open(os.path.join(root, path), encoding="utf-8") as f:
            return ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return None


def first_line(text: str | None) -> str:
    for line in (text or "").strip().splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= 140 else line[:139] + "…"
    return ""


def route_of(dec: ast.expr) -> str | None:
    """@router.get("/items") → "GET /items"."""
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in HTTP_METHODS:
        arg = dec.args[0] if dec.args else None
        path = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "?"
        method = dec.func.attr.upper() if dec.func.attr not in ("route", "api_route") else "ROUTE"
        return f"{method} {path}"
    return None


def short_list(items: list[str], limit: int = 8) -> str:
    return ", ".join(items[:limit]) + (", …" if len(items) > limit else "")


def py_summary(tree: ast.Module) -> dict:
    symbols, routes = [], []
    main_guard = False
    # Маршруты ищем по всему файлу: их часто объявляют внутри register(router) или фабрики приложения.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                r = route_of(dec)
                if r:
                    routes.append(f"{r} → `{node.name}`")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and not n.name.startswith("_")]
            # Поля dataclass/модели (x: int) и члены Enum или константы класса (X = ...).
            fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                      and not n.target.id.startswith("_")]
            fields += [t.id for n in node.body if isinstance(n, ast.Assign) for t in n.targets
                       if isinstance(t, ast.Name) and not t.id.startswith("_")]
            text = f"`{node.name}`"
            if fields:
                text += "{" + short_list(fields) + "}"
            if methods:
                text += "(" + short_list(methods) + ")"
            symbols.append(text)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(f"`{node.name}()`")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Константы модуля (TRANSITIONS, TEMPLATES, DEFAULT_*) — частое место правок.
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1 and t.id != "__all__":
                    symbols.append(f"`{t.id}`")
        elif isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"):
                main_guard = True
    if len(symbols) > MAX_SYMBOLS:
        symbols = symbols[:MAX_SYMBOLS] + [f"… ещё {len(symbols) - MAX_SYMBOLS}"]
    return {"doc": first_line(ast.get_docstring(tree)), "symbols": symbols, "routes": routes, "main": main_guard}


def module_names(path: str, source_dirs: list[str]) -> set[str]:
    """src/shop/cart.py → {"src.shop.cart", "shop.cart"}; __init__.py — имя пакета."""
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    names = {".".join(parts)} if parts else set()
    for d in source_dirs:
        dp = d.split("/")
        if parts[: len(dp)] == dp and len(parts) > len(dp):
            names.add(".".join(parts[len(dp):]))
    return names


def imports_of(tree: ast.Module) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def fixtures_of(tree: ast.Module) -> list[dict]:
    found = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name == "fixture":
                scope = "function"
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                            scope = str(kw.value.value)
                found.append({"name": node.name, "scope": scope, "doc": first_line(ast.get_docstring(node))})
    return found


def tests_of(tree: ast.Module) -> list[tuple[str, list[str]]]:
    """[(имя теста, аргументы)] — функции test_* и методы классов Test*."""
    out = []

    def args(fn):
        return [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out.append((node.name, args(node)))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    out.append((f"{node.name}.{sub.name}", args(sub)))
    return out


# ---------------------------------------------------------------- сборка


def load_state(root: str) -> dict:
    try:
        with open(os.path.join(root, MAP_DIR, CODE_JSON), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else {"files": {}}
    except (OSError, ValueError):
        return {"files": {}}


def write(root: str, name: str, text: str) -> None:
    path = os.path.join(root, MAP_DIR, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def describe_entry(entry: dict, info: dict) -> tuple[str, bool]:
    """Текст описания и нужно ли его (пере)писать."""
    desc, desc_hash = entry.get("desc"), entry.get("desc_hash")
    if desc and desc_hash == entry.get("hash"):
        return desc, False
    if desc:
        # Устаревшее ручное описание показываем с пометкой; переписывать обязательно, только если нет docstring.
        return f"(устарело) {desc}", not info.get("doc")
    if info.get("doc"):
        return info["doc"], False
    return "", True


def build(root: str, cfg: dict) -> dict:
    state = load_state(root)
    old = state["files"]
    code, tests = collect(root, cfg)
    files, changed, added = {}, [], []
    infos: dict[str, dict] = {}
    for path in code:
        h = file_hash(root, path)
        prev = old.get(path, {})
        entry = {"hash": h, "desc": prev.get("desc"), "desc_hash": prev.get("desc_hash")}
        if not prev:
            added.append(path)
        elif prev.get("hash") != h:
            changed.append(path)
        files[path] = entry
        info = {}
        if path.endswith(".py"):
            tree = parse_py(root, path)
            info = py_summary(tree) if tree else {"doc": "", "symbols": ["(не разобрался: синтаксическая ошибка)"],
                                                  "routes": [], "main": False}
        infos[path] = info
    removed = sorted(set(old) - set(files))
    need = []
    for path, entry in files.items():
        _, needs = describe_entry(entry, infos[path])
        if needs and (path.endswith(".py") or INFRA_FILES.match(os.path.basename(path))):
            # Пустые __init__.py и инфраструктуру с понятным именем описывать не нужно.
            base = os.path.basename(path)
            if base == "__init__.py" and not infos[path].get("symbols"):
                continue
            if INFRA_FILES.match(base) and not entry.get("desc"):
                continue
            need.append(path)

    write(root, CODE_MD, render_code(root, cfg, files, infos, need))
    write(root, TESTS_MD, render_tests(root, cfg, code, tests))
    state = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": files}
    write(root, CODE_JSON, json.dumps(state, ensure_ascii=False, indent=1))
    return {
        "code_files": len(code), "test_files": len(tests),
        "added": added if old else [], "changed": changed, "removed": removed,
        "need_description": need,
        "map": [f"{MAP_DIR}/{CODE_MD}", f"{MAP_DIR}/{TESTS_MD}"],
        **({"truncated_at": MAX_FILES} if len(code) + len(tests) >= MAX_FILES else {}),
    }


def render_code(root: str, cfg: dict, files: dict, infos: dict, need: list[str]) -> str:
    entries, infra, routes = [], [], []
    for path in files:
        info = infos[path]
        base = os.path.basename(path)
        if base in ENTRY_NAMES or info.get("main"):
            entries.append(path)
        if INFRA_FILES.match(base) or path.split("/")[0] in ("migrations", "alembic", "deploy", "docker"):
            infra.append(path)
        routes += [f"{r} · `{path}`" for r in info.get("routes", [])]

    lines = [
        "# Карта кода",
        "",
        f"Собрано: {time.strftime('%Y-%m-%d %H:%M')} · файлов: {len(files)} · без описания: {len(need)}. "
        "Собирается скриптом `project_map.py build`; описания — `project_map.py describe`.",
        "Читай карту прежде, чем искать по проекту: Glob/Grep — только для того, чего в ней нет. "
        "Обозначения: `Класс`{поля или члены Enum}(публичные методы), `функция()`.",
        "",
    ]
    if entries:
        lines += ["**Точки входа:** " + ", ".join(f"`{p}`" for p in entries), ""]
    if routes:
        lines += ["**HTTP-маршруты:**", ""] + [f"- {r}" for r in routes] + [""]
    if infra:
        lines += ["**Инфраструктура и конфиги:** " + ", ".join(f"`{p}`" for p in infra), ""]

    by_dir: dict[str, list[str]] = {}
    for path in files:
        if path in infra and "/" not in path:
            continue
        by_dir.setdefault(os.path.dirname(path) or ".", []).append(path)
    for d in sorted(by_dir):
        lines.append(f"## {d}/")
        for path in by_dir[d]:
            info = infos[path]
            desc, _ = describe_entry(files[path], info)
            if os.path.basename(path) == "__init__.py" and not desc and not info.get("symbols"):
                continue  # пустой пакетный файл
            parts = [f"`{os.path.basename(path)}`"]
            parts.append(f"— {desc}" if desc else "— **нет описания**")
            if info.get("symbols"):
                parts.append("· " + ", ".join(info["symbols"]))
            lines.append("- " + " ".join(parts))
        lines.append("")
    if need:
        lines += ["## Нужно описать", "", "Новые или изменённые файлы без актуального описания — по одной строке через "
                  "`project_map.py describe`:", ""] + [f"- `{p}`" for p in need] + [""]
    return "\n".join(lines)


def render_tests(root: str, cfg: dict, code: list[str], tests: list[str]) -> str:
    tests_dir = cfg["tests_dir"]
    fixtures: list[tuple[str, dict]] = []
    test_files = []
    imported: set[str] = set()
    for path in tests:
        if not path.endswith(".py"):
            continue
        tree = parse_py(root, path)
        if tree is None:
            test_files.append((path, None, set(), []))
            continue
        if os.path.basename(path) == "conftest.py":
            fixtures += [(path, fx) for fx in fixtures_of(tree)]
            continue
        fixtures += [(path, fx) for fx in fixtures_of(tree)]
        imps = imports_of(tree)
        imported |= imps
        test_files.append((path, tests_of(tree), imps, fixtures_of(tree)))

    src_modules: dict[str, set[str]] = {}
    for path in code:
        if path.endswith(".py"):
            src_modules[path] = module_names(path, cfg["source_dirs"])
    fixture_names = {fx["name"] for _, fx in fixtures} | {"tmp_path", "monkeypatch", "capsys", "caplog", "request"}

    total = sum(len(t) for _, t, _, _ in test_files if t)
    lines = ["# Карта тестов", "", f"Собрано: {time.strftime('%Y-%m-%d %H:%M')} · тестовых файлов: {len(test_files)} · "
             f"тестов: {total}", ""]
    cmd = cfg.get("test_command") or f"python -m pytest {tests_dir}"
    lines += [f"**Запуск:** `run_tests.py` (команда: `{cmd}`); один файл — `run_tests.py {tests_dir}/<файл>`, "
              "один тест — `run_tests.py -k <имя>`.", ""]
    last = None
    try:
        with open(os.path.join(root, STATE_DIR, "reports", "last-test-run.json"), encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        pass
    if isinstance(last, dict):
        lines += [f"**Последний прогон** ({last.get('finished_at', '?')}): {last.get('status')} — "
                  f"{last.get('passed', 0)} прошло, {last.get('failed', 0)} упало, {last.get('errors', 0)} ошибок, "
                  f"{last.get('skipped', 0)} пропущено.", ""]
        if last.get("failures"):
            lines += [f"- ✗ `{f.get('test')}` — {f.get('message', '')}" for f in last["failures"][:10]] + [""]

    if fixtures:
        lines += ["## Фикстуры", ""]
        for path, fx in fixtures:
            scope = f", {fx['scope']}" if fx["scope"] != "function" else ""
            lines.append(f"- `{fx['name']}` ({path}{scope})" + (f" — {fx['doc']}" if fx["doc"] else ""))
        lines.append("")

    by_dir: dict[str, list] = {}
    for item in test_files:
        by_dir.setdefault(os.path.dirname(item[0]) or ".", []).append(item)
    for d in sorted(by_dir):
        lines.append(f"## {d}/")
        for path, cases, imps, _ in by_dir[d]:
            if cases is None:
                lines.append(f"- `{os.path.basename(path)}` — не разбирается (синтаксическая ошибка)")
                continue
            covered = sorted({min(names, key=len) for p, names in src_modules.items()
                              if names & imps and not p.endswith("__init__.py")})
            used = sorted({a for _, args in cases for a in args if a in fixture_names})
            names = [n for n, _ in cases]
            shown = ", ".join(names[:MAX_TEST_NAMES]) + (f", … ещё {len(names) - MAX_TEST_NAMES}" if len(names) > MAX_TEST_NAMES else "")
            line = f"- `{os.path.basename(path)}` — {len(cases)} тест(ов)"
            if covered:
                line += " · модули: " + ", ".join(f"`{m}`" for m in covered)
            if used:
                line += " · фикстуры: " + ", ".join(used)
            lines.append(line)
            if names:
                lines.append(f"  - {shown}")
        lines.append("")

    untested = []
    for path, names in sorted(src_modules.items()):
        if os.path.basename(path) in ("__init__.py", "__main__.py", "conftest.py", "setup.py", "manage.py"):
            continue
        if not any(n == i or i.startswith(n + ".") for n in names for i in imported):
            untested.append(min(names, key=len))
    if untested:
        lines += ["## Модули без тестов", "", "Ни один тест их не импортирует напрямую:", ""]
        lines += [f"- `{m}`" for m in untested] + [""]
    return "\n".join(lines)


def describe(root: str, cfg: dict, data: dict) -> dict:
    state = load_state(root)
    unknown = []
    for path, desc in data.items():
        path = str(path).replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        entry = state["files"].get(path)
        if entry is None or not isinstance(desc, str) or not desc.strip():
            unknown.append(path)
            continue
        entry["desc"] = " ".join(desc.split())[:200]
        entry["desc_hash"] = file_hash(root, path)
    write(root, CODE_JSON, json.dumps(state, ensure_ascii=False, indent=1))
    result = build(root, cfg)
    if unknown:
        result["unknown"] = unknown
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["build", "describe", "need"])
    ap.add_argument("--root")
    args = ap.parse_args(argv)
    root = find_root(args.root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if not os.path.isdir(os.path.join(root, STATE_DIR)):
        print(json.dumps({"error": f"нет папки {STATE_DIR}/ в {root}"}, ensure_ascii=False))
        return 1
    cfg = load_config(root)
    if args.cmd == "describe":
        try:
            data = json.load(sys.stdin)
        except ValueError as exc:
            print(json.dumps({"error": f"ожидался JSON {{путь: описание}}: {exc}"}, ensure_ascii=False))
            return 2
        if not isinstance(data, dict):
            print(json.dumps({"error": "ожидался объект {путь: описание}"}, ensure_ascii=False))
            return 2
        result = describe(root, cfg, data)
    else:
        result = build(root, cfg)
        if args.cmd == "need":
            result = {"need_description": result["need_description"]}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
