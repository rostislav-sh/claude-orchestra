#!/usr/bin/env python3
"""Хук плагина orchestra: держит каждую роль в её границах.

Ограничения действуют только на сабагентов этого плагина (agent_type "orchestra:<роль>"):

- Write / Edit / MultiEdit / NotebookEdit — куда роли можно писать (проверка по пути, до вызова).
- Read / Grep / Glob тестера — исходники закрыты.
- Bash, git — разрешены только читающие подкоманды (status, diff, log, show, ...).
  Команда разбирается на простые команды, глобальные опции git (-C, -c, --no-pager, ...)
  и вложенные `sh -c` / `eval` учитываются.
- Bash, запись в чужие папки — до команды снимается копия папок, которые роли менять
  нельзя, после команды они сравниваются со снимком. Изменения, сделанные командой,
  откатываются, агент получает сообщение, тимлид — строку в .orchestra/reports/guard.log.
- Bash, чтение исходников тестером — эвристика: команда, упоминающая папку исходников,
  разрешена только для запуска тестов. Это защита от случайного подглядывания,
  а не от целенаправленного обхода: тесты всё равно импортируют код.

Основную сессию и чужих агентов хук не ограничивает. В проектах с папкой .orchestra/
он только журналирует их записи и Bash-команды, чтобы снимок сабагента не принял
чужие параллельные правки за нарушение.

Коды выхода: 2 на PreToolUse блокирует вызов; на PostToolUse / PostToolUseFailure
показывает stderr агенту (вызов уже прошёл). Внутренние ошибки не блокируют (fail-open).

Настройки проекта: .orchestra/config.json
    {"tests_dir": "tests", "source_dirs": ["src"]}
"""
from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time

PLUGIN_PREFIX = "orchestra"
STATE_DIR = ".orchestra"
REPORTS_DIR = ".orchestra/reports"
GUARD_LOG = ".orchestra/reports/guard.log"
DEFAULT_CONFIG = {"tests_dir": "tests", "source_dirs": ["src", "app"]}

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}

# Что не попадает в снимок: кэши, окружения, артефакты сборки и тестов.
SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".venv", "venv", "node_modules", ".hypothesis", ".cache", "htmlcov", ".idea", ".vscode",
    "dist", "build", ".eggs",
}
SKIP_FILE_SUFFIXES = (".pyc", ".pyo")
SKIP_FILE_PREFIXES = (".coverage",)
SKIP_FILE_NAMES = {".DS_Store", "coverage.xml"}
MAX_FILE_COPY = 2 * 1024 * 1024
MAX_TOTAL_COPY = 64 * 1024 * 1024
STALE_SECONDS = 2 * 3600
JOURNAL_MAX_BYTES = 2 * 1024 * 1024

# Какие папки роль не должна менять через Bash (проверяется снимком).
# Защищённые пути, куда роли писать можно (например, тесты внутри src/), отфильтровываются.
ROLE_HINTS = {
    "coder": "Тесты ведёт тестер.",
    "architect": "Тесты пишет тестер по контрактам.",
    "tester": "Тестер не меняет реализацию.",
    "reviewer": "Ревьюер не меняет код.",
    "debugger": "Дебаггер не меняет код.",
}


class Blocked(Exception):
    pass


def block(reason: str) -> None:
    raise Blocked(reason)


# ---------------------------------------------------------------- конфиг и пути


def norm_dir(d: str) -> str:
    d = d.replace("\\", "/").strip("/")
    while d.startswith("./"):
        d = d[2:]
    return d


def load_config(*roots: str) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    for root in roots:
        path = os.path.join(root, STATE_DIR, "config.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data.get("tests_dir"), str):
            cfg["tests_dir"] = data["tests_dir"]
        if isinstance(data.get("source_dirs"), list):
            cfg["source_dirs"] = [str(d) for d in data["source_dirs"]]
        break
    cfg["tests_dir"] = norm_dir(cfg["tests_dir"])
    cfg["source_dirs"] = [norm_dir(d) for d in cfg["source_dirs"] if d and norm_dir(d)]
    return cfg


def find_root(cwd: str, project_dir: str) -> str:
    """Корень рабочей копии, в которой работает агент.

    Идём вверх от cwd: первая папка с .git (у worktree это файл) или сам корень проекта.
    Так worktree внутри проекта (.claude/worktrees/...) и снаружи считается своим корнем.
    """
    proj = os.path.realpath(project_dir) if project_dir else None
    d = os.path.realpath(cwd)
    while True:
        if d == proj or os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return proj or os.path.realpath(cwd)
        d = parent


MSYS_DRIVE = re.compile(r"^/(?:cygdrive/)?([a-zA-Z])(?:/|$)")


def native_path(path: str) -> str:
    """На Windows Bash агента — Git Bash: /c/Users/... и /cygdrive/c/... там означают C:/Users/..."""
    if os.name == "nt":
        return MSYS_DRIVE.sub(lambda m: m.group(1) + ":/", path, count=1)
    return path


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def is_inside(full: str, root: str) -> bool:
    """full совпадает с root или лежит внутри; на Windows без учёта регистра и вида слешей."""
    f, r = os.path.normcase(os.path.normpath(full)), os.path.normcase(os.path.normpath(root))
    return f == r or f.startswith(r.rstrip(os.sep) + os.sep)


def rel_to_root(path: str, cwd: str, root: str) -> str | None:
    """Путь относительно корня или None, если он вне корня."""
    path = native_path(path) if path else cwd
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    if not is_inside(full, root):
        return None
    rel = os.path.relpath(full, root).replace("\\", "/")
    return "" if rel == "." else rel


def under(rel: str | None, directory: str) -> bool:
    if not rel or not directory:
        return False
    return rel == directory or rel.startswith(directory + "/")


def is_parent_of(rel: str, directory: str) -> bool:
    """rel — корень или папка, внутри которой лежит directory."""
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
        return [re.split(r"[*?\[{]", joined, maxsplit=1)[0]]
    return []


# ---------------------------------------------------------------- права записи


def write_violation(role: str, rel: str | None, cfg: dict) -> str | None:
    """Почему роли нельзя писать в rel, или None, если можно."""
    if rel is None:
        return "Запись вне проекта запрещена."
    tests = cfg["tests_dir"]
    if role == "tester":
        if under(rel, tests) or under(rel, REPORTS_DIR):
            return None
        return f"Тестер пишет только в {tests}/ (и отчёты в {REPORTS_DIR}/). Путь: {rel}"
    if role == "coder":
        if under(rel, tests):
            return f"Кодер не трогает тесты ({tests}/). Если тест кажется неверным, опиши это в отчёте тимлиду."
        if under(rel, STATE_DIR) and not under(rel, REPORTS_DIR):
            return f"Файлы {STATE_DIR}/ ведёт тимлид. Кодер может писать только отчёты в {REPORTS_DIR}/."
        return None
    if role == "architect":
        if under(rel, tests):
            return f"Архитектор не пишет тесты ({tests}/), их пишет тестер по контрактам."
        return None
    if role in ("reviewer", "debugger"):
        if under(rel, REPORTS_DIR):
            return None
        return f"Роль {role} не меняет код. Результат верни в ответе или сохрани в {REPORTS_DIR}/."
    return None


def bash_protected_dirs(role: str, cfg: dict) -> list[str]:
    tests, src = [cfg["tests_dir"]], list(cfg["source_dirs"])
    return {
        "coder": tests,
        "architect": tests,
        "tester": src,
        "reviewer": src + tests,
        "debugger": src + tests,
    }.get(role, [])


# ---------------------------------------------------------------- разбор shell


SHELL_OPS = set("();<>|&")
PREFIX_WORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "{", "}", "time"}
WRAPPERS = {
    "sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time", "nice", "ionice",
    "stdbuf", "timeout", "xargs", "watch", "chronic",
}
WRAPPER_OPTS_WITH_VALUE = {"-u", "-g", "-n", "-I", "-L", "-P", "-d", "-E", "-s", "-k", "-C", "-p"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
NUMBERISH = re.compile(r"^\d+(\.\d+)?[smhd]?$")


def tokenize(command: str) -> list[str]:
    lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def simple_commands(command: str, depth: int = 0) -> list[list[str]]:
    """Разбивает строку shell на простые команды (argv), раскрывая sh -c / eval.

    Обёртки (sudo, env, xargs, timeout, VAR=...) снимаются, argv начинается с исполняемого.
    """
    try:
        tokens = tokenize(command)
    except ValueError:
        # Незакрытые кавычки и т. п.: грубо режем по пробелам и разделителям.
        tokens = re.findall(r"[();<>|&]+|[^\s();<>|&]+", command.replace("\n", " ; "))

    commands: list[list[str]] = []
    cur: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok and set(tok) <= SHELL_OPS:
            if "<" in tok or ">" in tok:
                skip_next = True  # дальше имя файла редиректа
                continue
            if cur:
                commands.append(cur)
            cur = []
            continue
        cur.append(tok)
    if cur:
        commands.append(cur)

    result: list[list[str]] = []
    for argv in commands:
        argv = strip_wrappers(argv)
        if not argv:
            continue
        exe = argv[0].lstrip("`")
        base = os.path.basename(exe)
        if depth < 5 and re.search(r"\s", exe):
            result.extend(simple_commands(" ".join([exe] + argv[1:]), depth + 1))
            continue
        if depth < 5 and base in SHELLS:
            script = shell_c_script(argv[1:])
            if script is not None:
                result.extend(simple_commands(script, depth + 1))
                continue
        if depth < 5 and base == "eval":
            result.extend(simple_commands(" ".join(argv[1:]), depth + 1))
            continue
        result.append([exe] + argv[1:])
    return result


def strip_wrappers(argv: list[str]) -> list[str]:
    i = 0
    in_wrapper = False
    while i < len(argv):
        tok = argv[i]
        if tok in PREFIX_WORDS or ASSIGNMENT.match(tok):
            i += 1
            continue
        if os.path.basename(tok) in WRAPPERS:
            in_wrapper = True
            i += 1
            continue
        if in_wrapper and tok.startswith("-"):
            i += 2 if tok in WRAPPER_OPTS_WITH_VALUE else 1
            continue
        if in_wrapper and NUMBERISH.match(tok):
            i += 1
            continue
        break
    return argv[i:]


def shell_c_script(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
            return args[i + 1] if i + 1 < len(args) else ""
        if not a.startswith("-"):
            return None
    return None


# ---------------------------------------------------------------- git


GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
GIT_OPTS_FLAGS = {
    "--no-pager", "-p", "--paginate", "-P", "--bare", "--no-replace-objects", "--literal-pathspecs",
    "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks",
    "--no-lazy-fetch", "--no-advice",
}
GIT_INFO = {"--version", "--help", "-h", "--exec-path", "--html-path", "--man-path", "--info-path"}
GIT_READ = {
    "status", "diff", "log", "show", "blame", "annotate", "ls-files", "ls-tree", "ls-remote",
    "rev-parse", "rev-list", "grep", "shortlog", "describe", "cat-file", "merge-base", "show-ref",
    "for-each-ref", "name-rev", "check-ignore", "check-attr", "check-mailmap", "count-objects",
    "var", "version", "help", "whatchanged", "show-branch", "range-diff", "diff-tree",
    "diff-files", "diff-index", "cherry", "verify-commit", "verify-tag",
}
# Подкоманды, которые печатают содержимое файлов: тестеру — только по путям тестов.
GIT_CONTENT = {"diff", "show", "blame", "annotate", "grep", "cat-file", "range-diff", "diff-tree", "diff-index", "diff-files", "whatchanged"}


def _flags_ok(rest: list[str], bad: set[str], listing: set[str], takes_value: set[str]) -> bool:
    has_listing = any(a in listing for a in rest)
    prev = ""
    for a in rest:
        name = a.split("=", 1)[0]
        if a.startswith("-"):
            if name in bad:
                return False
        elif not has_listing and prev not in takes_value:
            return False  # позиционный аргумент без режима списка — создание/изменение
        prev = a
    return True


def _git_branch_ok(rest: list[str]) -> bool:
    bad = {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy", "--force",
           "-u", "--set-upstream-to", "--unset-upstream", "--edit-description", "-t", "--track", "--no-track"}
    return _flags_ok(rest, bad, {"-l", "--list"},
                     {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format"})


def _git_tag_ok(rest: list[str]) -> bool:
    bad = {"-d", "--delete", "-f", "--force", "-a", "--annotate", "-s", "--sign", "-u",
           "--local-user", "-m", "--message", "-F", "--file", "-e", "--edit"}
    return _flags_ok(rest, bad, {"-l", "--list"},
                     {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format", "-n"})


def _git_config_ok(rest: list[str]) -> bool:
    if rest and rest[0] in ("get", "list"):
        return True
    read = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch", "--get-color", "--get-colorbool"}
    write = {"--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section", "-e", "--edit"}
    return any(a in read for a in rest) and not any(a in write for a in rest)


GIT_CONDITIONAL = {
    "branch": _git_branch_ok,
    "tag": _git_tag_ok,
    "config": _git_config_ok,
    "stash": lambda r: bool(r) and r[0] in ("list", "show"),
    "worktree": lambda r: bool(r) and r[0] == "list",
    "remote": lambda r: not r or r[0] in ("-v", "--verbose", "show", "get-url"),
    "reflog": lambda r: not r or r[0] not in ("expire", "delete", "drop"),
    "notes": lambda r: not r or r[0] in ("list", "show"),
    "submodule": lambda r: not r or r[0] in ("status", "summary"),
    "apply": lambda r: "--check" in r or (any(a in ("--stat", "--numstat", "--summary") for a in r) and "--apply" not in r),
}


def git_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """(подкоманда, её аргументы); подкоманда None — только информационные опции.

    Неизвестная глобальная опция возвращается как подкоманда '<опция>'.
    """
    i = 0
    while i < len(args):
        a = args[i]
        if a in GIT_INFO:
            return None, []
        if a in GIT_OPTS_WITH_VALUE:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if (a.startswith("-c") or a.startswith("-C")) and len(a) > 2:
            i += 1
            continue
        if a in GIT_OPTS_FLAGS:
            i += 1
            continue
        if a.startswith("-"):
            return a, []
        return a, args[i + 1:]
    return None, []


def check_git(role: str, args: list[str], cwd: str, root: str, cfg: dict) -> None:
    sub, rest = git_subcommand(args)
    if sub is None:
        return
    allowed = sub in GIT_READ or (sub in GIT_CONDITIONAL and GIT_CONDITIONAL[sub](rest))
    if not allowed:
        block(
            f"git {sub} агентам запрещён: разрешены только читающие команды "
            "(status, diff, log, show, blame, ls-files, ...). Коммиты и откаты делает тимлид "
            "после одобрения пользователя — оставь изменения в рабочей копии."
        )
    if role == "tester":
        prints_content = sub in GIT_CONTENT or (
            sub == "log" and any(a in ("-p", "--patch", "-u") or a.startswith("-L") for a in rest)
        )
        if prints_content:
            paths = rest[rest.index("--") + 1:] if "--" in rest else []
            ok_dirs = (cfg["tests_dir"], STATE_DIR)
            if not paths or not all(
                any(under(rel_to_root(p, cwd, root), d) for d in ok_dirs) for p in paths
            ):
                block(
                    f"Тестер не смотрит реализацию через git {sub}. Ограничь вывод путями: "
                    f"git {sub} -- {cfg['tests_dir']}/"
                )


# ---------------------------------------------------------------- тестер и shell

TEST_RUNNERS = {"pytest", "py.test", "tox", "nox", "coverage", "make", "just", "nosetests", "ward"}
TEST_MODULES = {"pytest", "unittest", "coverage", "nose", "tox", "nox"}
PROJECT_RUNNERS = {"uv", "poetry", "pdm", "hatch", "pipenv", "rye"}


# Флаги `docker compose run/exec`, у которых есть значение (его не путаем с именем сервиса).
COMPOSE_VALUE_FLAGS = {
    "-e", "--env", "-w", "--workdir", "-u", "--user", "--entrypoint", "-v", "--volume",
    "-p", "--publish", "--name", "-l", "--label", "--env-file", "--index",
}


def _docker_target(rest: list[str]) -> str | None:
    for i, a in enumerate(rest):
        if a == "--target" and i + 1 < len(rest):
            return rest[i + 1]
        if a.startswith("--target="):
            return a.split("=", 1)[1]
    return None


def is_docker_test_run(argv: list[str]) -> bool:
    """Тесты в контейнере: `docker [buildx] build --target test` или `docker compose run|exec svc pytest`.

    Сборка с исходниками в контексте их не показывает: тестер видит вывод тестов, а не код.
    """
    if os.path.basename(argv[0]) not in ("docker", "docker-compose", "podman"):
        return False
    rest = argv[1:]
    if rest[:1] == ["buildx"]:
        rest = rest[1:]
    if rest[:1] == ["build"]:
        target = _docker_target(rest[1:])
        return target is not None and re.fullmatch(r"tests?", target) is not None
    if os.path.basename(argv[0]) != "docker-compose":
        if rest[:1] != ["compose"]:
            return False
        rest = rest[1:]
    i = 0
    while i < len(rest) and rest[i].startswith("-"):  # глобальные флаги compose: -f file, -p name
        i += 2 if rest[i] in ("-f", "--file", "-p", "--project-name", "--profile", "--env-file") else 1
    if i >= len(rest) or rest[i] not in ("run", "exec"):
        return False
    i += 1
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in COMPOSE_VALUE_FLAGS else 1
    cmd = rest[i + 1:]  # после имени сервиса
    return bool(cmd) and is_test_run(cmd)


def is_test_run(argv: list[str]) -> bool:
    if is_docker_test_run(argv):
        return True
    base = os.path.basename(argv[0])
    rest = argv[1:]
    if base in PROJECT_RUNNERS and rest[:1] == ["run"]:
        rest = rest[1:]
        if not rest:
            return False
        base, rest = os.path.basename(rest[0]), rest[1:]
    if base in TEST_RUNNERS:
        return True
    if re.match(r"^python(\d(\.\d+)?)?(\.exe)?$", base) and rest:
        if len(rest) >= 2 and rest[0] == "-m":
            return rest[1] in TEST_MODULES
        # Скрипт плагина: запускает только pytest проекта (команду берёт из .orchestra/config.json).
        return os.path.basename(rest[0]) == "run_tests.py"
    return False


RECURSIVE_SEARCH = {"rg", "ag", "ack", "tree", "find", "fd"}
CODE_CHARS = re.compile(r"[\s'\"(),;]")


def path_hits(rel: str | None, src: str, tests: str) -> bool:
    if rel is None or under(rel, tests) or under(rel, STATE_DIR):
        return False
    return under(rel, src) or is_parent_of(rel, src)


def token_hits(tok: str, src: str, tests: str, vcwd: str, root: str) -> bool:
    if not tok or (tok.startswith("-") and "=" not in tok):
        return False
    parts = [tok] + (tok.split("=")[1:] if "=" in tok else [])
    for part in parts:
        if not part:
            continue
        if CODE_CHARS.search(part):
            # Кусок кода в кавычках (python -c "open('src/a.py')"): ищем упоминание папки.
            if re.search(r"(^|[\s'\"=(:,/\\])" + re.escape(src) + r"([/\\]|\s|$|['\");,])", part):
                return True
            continue
        if path_hits(rel_to_root(part, vcwd, root), src, tests):
            return True
    return False


def check_tester_shell(argvs: list[list[str]], cwd: str, root: str, cfg: dict) -> None:
    """Эвристика: команды тестера, которые касаются исходников, разрешены только для запуска тестов.

    Учитывается `cd` внутри команды, относительные и абсолютные пути, рекурсивный поиск
    без пути (grep -r, rg, find) из папки, внутри которой лежат исходники.
    """
    tests = cfg["tests_dir"]
    vcwd = cwd
    for argv in argvs:
        base = os.path.basename(argv[0])
        if base in ("cd", "pushd"):
            target = native_path(next((a for a in argv[1:] if not a.startswith("-")), os.path.expanduser("~")))
            vcwd = os.path.realpath(target if os.path.isabs(target) else os.path.join(vcwd, target))
            continue
        if base == "git" or is_test_run(argv):
            continue  # git проверяется отдельно
        recursive = base in RECURSIVE_SEARCH or (
            base in ("grep", "egrep", "fgrep", "ls")
            and any(a in ("-r", "-R", "--recursive") or (re.match(r"^-[a-zA-Z]+$", a) and ("r" in a or "R" in a)) for a in argv[1:])
        )
        for src in cfg["source_dirs"]:
            hit = any(token_hits(t, src, tests, vcwd, root) for t in argv[1:]) or token_hits(argv[0], src, tests, vcwd, root)
            if not hit and recursive:
                has_path = any(
                    not a.startswith("-") and os.path.exists(os.path.join(vcwd, native_path(a)))
                    for a in argv[1:]
                )
                hit = not has_path and path_hits(rel_to_root(vcwd, vcwd, root), src, tests)
            if hit:
                block(
                    f"Тестер не читает реализацию ({src}/) через shell. Запускать тесты можно "
                    f"(pytest, python -m pytest, ...). Если не хватает контракта — сообщи тимлиду."
                )


def check_tester_read(rel: str | None, cfg: dict, search: bool) -> None:
    if rel is None:
        return
    if under(rel, cfg["tests_dir"]) or under(rel, STATE_DIR):
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


# ---------------------------------------------------------------- состояние: журнал и снимки


def state_dir(project_dir: str) -> str:
    base = os.environ.get("ORCHESTRA_GUARD_STATE") or os.path.join(tempfile.gettempdir(), "orchestra-guard")
    key = hashlib.sha1(os.path.realpath(project_dir).encode("utf-8")).hexdigest()[:16]
    d = os.path.join(base, key)
    os.makedirs(d, exist_ok=True)
    return d


def journal_append(sdir: str, entry: dict) -> None:
    path = os.path.join(sdir, "journal.jsonl")
    entry = dict(entry, t=time.time())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        if os.path.getsize(path) > JOURNAL_MAX_BYTES:
            cutoff = time.time() - STALE_SECONDS
            keep = [e for e in journal_read(sdir) if e.get("t", 0) >= cutoff]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
    except OSError:
        pass


def journal_read(sdir: str) -> list[dict]:
    entries = []
    try:
        with open(os.path.join(sdir, "journal.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return entries


def skip_file(name: str) -> bool:
    return (
        name in SKIP_FILE_NAMES
        or name.endswith(SKIP_FILE_SUFFIXES)
        or name.startswith(SKIP_FILE_PREFIXES)
    )


def walk_scope(root: str, dirs: list[str]) -> dict[str, os.stat_result]:
    found: dict[str, os.stat_result] = {}
    for d in dirs:
        base = os.path.join(root, d)
        if os.path.isfile(base) or os.path.islink(base):
            try:
                found[d] = os.lstat(base)
            except OSError:
                pass
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.endswith(".egg-info")]
            for name in filenames:
                if skip_file(name):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    found[os.path.relpath(full, root).replace(os.sep, "/")] = os.lstat(full)
                except OSError:
                    pass
    return found


def snap_path(sdir: str, tool_use_id: str) -> str:
    return os.path.join(sdir, "snap-" + re.sub(r"[^A-Za-z0-9_.-]", "_", tool_use_id))


def cleanup_stale(sdir: str) -> None:
    cutoff = time.time() - STALE_SECONDS
    for name in os.listdir(sdir):
        if not name.startswith("snap-"):
            continue
        path = os.path.join(sdir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def take_snapshot(sdir: str, tool_use_id: str, meta: dict, root: str, dirs: list[str]) -> None:
    cleanup_stale(sdir)
    snap = snap_path(sdir, tool_use_id)
    shutil.rmtree(snap, ignore_errors=True)
    os.makedirs(os.path.join(snap, "files"))
    started = time.time()
    files = {}
    total = 0
    for rel, st in walk_scope(root, dirs).items():
        entry = {"size": st.st_size, "mtime": st.st_mtime_ns, "saved": False}
        if st.st_size <= MAX_FILE_COPY and total + st.st_size <= MAX_TOTAL_COPY:
            dst = os.path.join(snap, "files", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copy2(os.path.join(root, rel), dst, follow_symlinks=False)
                entry["saved"] = True
                total += st.st_size
            except OSError:
                pass
        files[rel] = entry
    manifest = dict(meta, t=started, root=root, dirs=dirs, files=files)
    with open(os.path.join(snap, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)


def detect_changes(snap: str, manifest: dict) -> list[tuple[str, str]]:
    root = manifest["root"]
    before = manifest["files"]
    now = walk_scope(root, manifest["dirs"])
    changes = []
    for rel, e in before.items():
        st = now.get(rel)
        if st is None:
            changes.append((rel, "удалён"))
        elif (st.st_size, st.st_mtime_ns) != (e["size"], e["mtime"]):
            saved = os.path.join(snap, "files", rel)
            if e["saved"] and os.path.isfile(saved) and filecmp.cmp(saved, os.path.join(root, rel), shallow=False):
                continue  # содержимое то же (например, touch)
            changes.append((rel, "изменён"))
    for rel in now:
        if rel not in before:
            changes.append((rel, "создан"))
    return changes


def concurrent_activity(sdir: str, me: str, t0: float) -> tuple[set[str], bool]:
    """Пути, записанные другими агентами через инструменты, и шли ли параллельно чужие Bash."""
    others_writes: set[str] = set()
    windows: dict[str, list] = {}
    for e in journal_read(sdir):
        if e.get("agent") == me:
            continue
        kind = e.get("kind")
        if kind == "write" and e.get("t", 0) >= t0 - 1:
            others_writes.add(e.get("path", ""))
        elif kind == "bash_start":
            windows.setdefault(e.get("id", ""), [e.get("t", 0), None])
        elif kind == "bash_end" and e.get("id") in windows:
            windows[e["id"]][1] = e.get("t", 0)
    now = time.time()
    overlap = any(
        start <= now and (end is None or end >= t0) and now - start < STALE_SECONDS
        for start, end in windows.values()
    )
    return others_writes, overlap


def restore(snap: str, manifest: dict, changes: list[tuple[str, str]]) -> list[str]:
    """Откатывает изменения, возвращает пути, которые откатить не удалось."""
    root = manifest["root"]
    failed = []
    for rel, kind in changes:
        full = os.path.join(root, rel)
        try:
            if kind == "создан":
                if os.path.islink(full) or os.path.isfile(full):
                    os.remove(full)
                continue
            entry = manifest["files"][rel]
            saved = os.path.join(snap, "files", rel)
            if not entry["saved"] or not os.path.lexists(saved):
                failed.append(rel)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if os.path.lexists(full):
                os.remove(full)
            shutil.copy2(saved, full, follow_symlinks=False)
        except OSError:
            failed.append(rel)
    return failed


def guard_log(root: str, project_dir: str, line: str) -> None:
    for base in (root, project_dir):
        if base and os.path.isdir(os.path.join(base, STATE_DIR)):
            path = os.path.join(base, GUARD_LOG)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
            except OSError:
                pass
            return


def fmt_changes(changes: list[tuple[str, str]]) -> str:
    shown = ", ".join(f"{rel} ({kind})" for rel, kind in changes[:10])
    return shown + (f" и ещё {len(changes) - 10}" if len(changes) > 10 else "")


# ---------------------------------------------------------------- события


def parse_role(data: dict) -> str | None:
    agent_type = str(data.get("agent_type") or "")
    if ":" not in agent_type:
        return None
    prefix, role = agent_type.rsplit(":", 1)
    if prefix.split(":")[0] != PLUGIN_PREFIX:
        return None
    return role


def pre_tool_use(data: dict, ctx: dict) -> None:
    role, tool, tin = ctx["role"], ctx["tool"], ctx["tin"]
    cwd, root, cfg = ctx["cwd"], ctx["root"], ctx["cfg"]

    if role:
        if tool == "Bash":
            command = str(tin.get("command", ""))
            argvs = simple_commands(command)
            for argv in argvs:
                if os.path.basename(argv[0]) == "git":
                    check_git(role, argv[1:], cwd, root, cfg)
            if role == "tester":
                check_tester_shell(argvs, cwd, root, cfg)
        else:
            for path in tool_paths(tool, tin):
                rel = rel_to_root(path, cwd, root)
                if tool in WRITE_TOOLS:
                    reason = write_violation(role, rel, cfg)
                    if reason:
                        block(reason)
                elif tool in READ_TOOLS and role == "tester":
                    check_tester_read(rel, cfg, search=tool in ("Grep", "Glob"))

    # Вызов разрешён: журналируем и снимаем снимок.
    sdir = ctx["sdir"]
    if sdir is None:
        return
    if tool in WRITE_TOOLS:
        for path in tool_paths(tool, tin):
            if path:
                full = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
                journal_append(sdir, {"kind": "write", "agent": ctx["agent"], "path": full})
    elif tool == "Bash":
        tool_use_id = str(data.get("tool_use_id") or "")
        dirs = bash_protected_dirs(role, cfg) if role else []
        if role and tool_use_id and dirs:
            meta = {"agent": ctx["agent"], "role": role, "command": str(tin.get("command", ""))[:300]}
            take_snapshot(sdir, tool_use_id, meta, root, dirs)
        if tool_use_id:
            journal_append(sdir, {"kind": "bash_start", "agent": ctx["agent"], "id": tool_use_id})


def post_tool_use(data: dict, ctx: dict) -> None:
    if ctx["tool"] != "Bash" or ctx["sdir"] is None:
        return
    sdir = ctx["sdir"]
    tool_use_id = str(data.get("tool_use_id") or "")
    if not tool_use_id:
        return
    journal_append(sdir, {"kind": "bash_end", "agent": ctx["agent"], "id": tool_use_id})
    role = ctx["role"]
    snap = snap_path(sdir, tool_use_id)
    if not role or not os.path.isdir(snap):
        return
    try:
        with open(os.path.join(snap, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        root, cfg = manifest["root"], ctx["cfg"]
        changes = [
            (rel, kind) for rel, kind in detect_changes(snap, manifest)
            if write_violation(role, rel, cfg)
        ]
        if not changes:
            return
        others_writes, overlap = concurrent_activity(sdir, ctx["agent"], manifest["t"])
        changes = [
            (r, k) for r, k in changes
            if not any(same_path(os.path.join(root, r), w) for w in others_writes)
        ]
        if not changes:
            return
        hint = ROLE_HINTS.get(role, "")
        command = manifest.get("command", "")
        if overlap:
            guard_log(root, ctx["project_dir"],
                      f"{role}: не откачено (параллельно работали другие агенты): {fmt_changes(changes)} | {command}")
            print(
                f"[orchestra guard] Пока шла твоя команда, изменились файлы, которые роли {role} менять нельзя: "
                f"{fmt_changes(changes)}. Параллельно работали другие агенты, поэтому изменения не откачены. "
                f"Если это сделала твоя команда — верни файлы как было и напиши об этом в отчёте тимлиду. {hint}",
                file=sys.stderr,
            )
            sys.exit(2)
        failed = restore(snap, manifest, changes)
        guard_log(root, ctx["project_dir"],
                  f"{role}: откачено {fmt_changes(changes)}"
                  + (f"; не удалось откатить: {', '.join(failed)}" if failed else "") + f" | {command}")
        msg = (
            f"[orchestra guard] Команда изменила файлы, которые роли {role} менять нельзя: {fmt_changes(changes)}. "
            f"Изменения откачены. {hint} Если правка нужна, опиши её в отчёте тимлиду."
        )
        if failed:
            msg += f" Откатить не удалось (файлы слишком большие для снимка): {', '.join(failed)} — верни их сам."
        print(msg, file=sys.stderr)
        sys.exit(2)
    finally:
        shutil.rmtree(snap, ignore_errors=True)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(data, dict):
        return

    role = parse_role(data)
    cwd = data.get("cwd") or os.getcwd()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    root = find_root(cwd, project_dir)
    orchestra_project = os.path.isdir(os.path.join(root, STATE_DIR)) or os.path.isdir(
        os.path.join(project_dir, STATE_DIR)
    )
    if not role and not orchestra_project:
        return

    ctx = {
        "role": role,
        "tool": data.get("tool_name", ""),
        "tin": data.get("tool_input") or {},
        "cwd": cwd,
        "root": root,
        "project_dir": project_dir,
        "cfg": load_config(root, project_dir),
        "agent": str(data.get("agent_id") or "main"),
        "sdir": None,
    }
    try:
        ctx["sdir"] = state_dir(project_dir)
    except OSError:
        pass

    event = data.get("hook_event_name") or "PreToolUse"
    if event == "PreToolUse":
        try:
            pre_tool_use(data, ctx)
        except Blocked as exc:
            # Блокировки тоже в журнал: по ним видно дыры в контрактах и считается eval.
            target = ctx["tin"].get("command") or ctx["tin"].get("file_path") or ctx["tin"].get("path") or ""
            guard_log(root, project_dir, f"{role}: заблокировано {ctx['tool']} | {' '.join(str(target).split())[:200]} | {exc}")
            print(f"[orchestra guard] {exc}", file=sys.stderr)
            sys.exit(2)
    elif event in ("PostToolUse", "PostToolUseFailure"):
        post_tool_use(data, ctx)


if __name__ == "__main__":
    # Claude Code обменивается с хуком в UTF-8, а на Windows потоки по умолчанию в кодировке
    # локали (cp1251): кириллица в путях и сообщениях агенту превращается в мусор.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail-open: ошибка хука не должна ломать работу
        print(f"[orchestra guard] internal error: {exc}", file=sys.stderr)
        sys.exit(0)
