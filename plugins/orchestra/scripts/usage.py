#!/usr/bin/env python3
"""Журнал расхода токенов orchestra и сводка по нему.

Хук (hooks.json, события SubagentStop и Stop):
    python3 usage.py hook        < JSON события на stdin

    Читает транскрипт агента (у сабагента — agent_transcript_path, у основной сессии —
    transcript_path) с места, где остановился в прошлый раз, суммирует счётчики usage
    и дописывает строки в .orchestra/reports/usage.jsonl. Пишет только в проектах, где
    есть папка .orchestra/. Модель не вызывает, токенов не тратит. Ошибки не блокируют.

    Одна строка журнала — один отрезок работы агента на одной модели:
    {"ts", "session", "agent_id", "role", "model", "tags", "requests",
     "input", "cache_write", "cache_read", "output", "thinking",
     "tool_calls": {"Read": 3, ...}, "usage_missing": 0, "resumed": false}

    role — роль orchestra ("coder", "tester", ...), "teamlead" для основной сессии,
    иначе тип агента как есть. tags — метка из задания сабагенту, последняя строка вида
    [orchestra пункт=2 круг=1 фаза=проверка] (ключи и значения любые, без пробелов).
    input — вход без кеша, cache_write — запись в кеш, cache_read — чтение из кеша:
    три непересекающиеся части входа. Сообщения без usage считаются в usage_missing,
    а не нулём: неизвестный расход не должен выглядеть бесплатным.

Сводка:
    python3 usage.py report [--root DIR] [--by role|item|model|round] [--since ISO]
                            [--session ID] [--json]

    Цены (необязательно) — в .orchestra/config.json, долларов за 1 млн токенов:
    "prices": {"claude-opus-5-5": {"input": 4, "cache_write": 5, "cache_read": 0.2, "output": 20}}
    Для модели без цены стоимость не считается (null), а не ноль.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, OrderedDict
from decimal import Decimal

PLUGIN_PREFIX = "orchestra"
STATE_DIR = ".orchestra"
REPORTS_DIR = os.path.join(STATE_DIR, "reports")
JOURNAL = "usage.jsonl"
STATE_FILE = ".usage-state.json"
LOCK_FILE = ".usage.lock"
TOKEN_FIELDS = ("input", "cache_write", "cache_read", "output", "thinking")
EXPLORE_TOOLS = ("Read", "Grep", "Glob")
TAG_RE = re.compile(r"\[orchestra((?:\s+[^\s=\]]+=[^\s\]]+)+)\s*\]")


# ---------------------------------------------------------------- пути


def find_project(*candidates: str) -> str | None:
    """Первая папка с .orchestra/, вверх от каждого кандидата."""
    for start in candidates:
        if not start:
            continue
        d = os.path.realpath(start)
        while True:
            if os.path.isdir(os.path.join(d, STATE_DIR)):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


class Lock:
    """Межпроцессная блокировка файлом: параллельные сабагенты завершаются одновременно."""

    def __init__(self, path: str, wait: float = 3.0):
        self.path, self.wait, self.fd = path, wait, None

    def __enter__(self):
        deadline = time.time() + self.wait
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 30:  # брошенная блокировка
                        os.unlink(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    return self  # лучше записать без блокировки, чем потерять строку
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                os.unlink(self.path)
            except OSError:
                pass


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- транскрипт


def read_new_lines(path: str, offset: int) -> tuple[list[dict], int]:
    """Полные JSON-строки транскрипта после offset и новый offset (до последнего \\n)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:  # файл перезаписан — читаем заново
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    records = []
    for raw in chunk[: end + 1].splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records, offset + end + 1


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def parse_tags(text: str) -> dict | None:
    found = TAG_RE.findall(text or "")
    if not found:
        return None
    tags = OrderedDict()
    for pair in found[-1].split():
        k, v = pair.split("=", 1)
        tags[k] = v
    return dict(tags)


def summarize(records: list[dict], skip_sidechain: bool) -> tuple[dict, dict | None]:
    """Счётчики по моделям и последняя метка [orchestra ...] из сообщений пользователя."""
    usage_by_id: "OrderedDict[str, tuple[str, dict | None]]" = OrderedDict()
    tools: dict[str, Counter] = {}
    tags = None
    anon = 0
    for rec in records:
        if skip_sidechain and rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        if rec.get("type") == "user":
            t = parse_tags(message_text(msg.get("content")))
            if t:
                tags = t
            continue
        if rec.get("type") != "assistant":
            continue
        model = str(msg.get("model") or "unknown")
        if model == "<synthetic>":
            continue
        # Одно сообщение API пишется в транскрипт несколькими строками (по блоку на строку)
        # с одинаковым id и одинаковым usage: считаем его один раз, блоки — все.
        mid = msg.get("id") or rec.get("requestId")
        if not mid:
            anon += 1
            mid = f"anon-{anon}"
        u = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
        prev = usage_by_id.get(mid)
        usage_by_id[mid] = (model, u if u is not None else (prev[1] if prev else None))
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tools.setdefault(model, Counter())[str(block.get("name"))] += 1

    per_model: dict[str, dict] = {}
    for model, u in usage_by_id.values():
        row = per_model.setdefault(model, {"requests": 0, "usage_missing": 0, **{k: 0 for k in TOKEN_FIELDS}})
        row["requests"] += 1
        if not u or u.get("input_tokens") is None or u.get("output_tokens") is None:
            row["usage_missing"] += 1
            continue
        row["input"] += int(u.get("input_tokens") or 0)
        row["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
        row["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        row["output"] += int(u.get("output_tokens") or 0)
        details = u.get("output_tokens_details") or {}
        row["thinking"] += int(details.get("thinking_tokens") or 0) if isinstance(details, dict) else 0
    for model, counter in tools.items():
        per_model.setdefault(model, {"requests": 0, "usage_missing": 0, **{k: 0 for k in TOKEN_FIELDS}})
        per_model[model]["tool_calls"] = dict(sorted(counter.items()))
    return per_model, tags


def role_of(data: dict, event: str) -> str:
    if event == "Stop":
        return "teamlead"
    agent_type = str(data.get("agent_type") or "")
    if ":" in agent_type:
        prefix, role = agent_type.rsplit(":", 1)
        if prefix.split(":")[0] == PLUGIN_PREFIX:
            return role
    return agent_type or "subagent"


def hook(data: dict) -> None:
    event = data.get("hook_event_name") or ""
    if event == "SubagentStop":
        transcript = data.get("agent_transcript_path")
        skip_sidechain = False
    elif event == "Stop":
        transcript = data.get("transcript_path")
        skip_sidechain = True  # у старых версий сабагенты лежат в основном транскрипте
    else:
        return
    if not transcript or not os.path.isfile(transcript):
        return
    project = find_project(os.environ.get("CLAUDE_PROJECT_DIR", ""), data.get("cwd") or "")
    if not project:
        return
    reports = os.path.join(project, REPORTS_DIR)
    os.makedirs(reports, exist_ok=True)

    with Lock(os.path.join(reports, LOCK_FILE)):
        state_path = os.path.join(reports, STATE_FILE)
        state = read_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        key = os.path.realpath(transcript)
        prev = state.get(key) or {}
        offset = int(prev.get("offset") or 0)
        records, new_offset = read_new_lines(transcript, offset)
        per_model, tags = summarize(records, skip_sidechain)
        tags = tags or prev.get("tags")  # продолжение агента без новой метки — та же метка
        lines = []
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        for model, row in per_model.items():
            if not row["requests"] and not row.get("tool_calls"):
                continue
            lines.append({
                "ts": ts,
                "session": data.get("session_id"),
                "agent_id": data.get("agent_id") or "main",
                "role": role_of(data, event),
                "model": model,
                "tags": tags or {},
                **row,
                "tool_calls": row.get("tool_calls", {}),
                "resumed": offset > 0,
            })
        if lines:
            with open(os.path.join(reports, JOURNAL), "a", encoding="utf-8") as f:
                f.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
        state[key] = {"offset": new_offset, "tags": tags}
        write_json(state_path, state)


# ---------------------------------------------------------------- сводка


def load_journal(project: str) -> list[dict]:
    rows = []
    try:
        with open(os.path.join(project, REPORTS_DIR, JOURNAL), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def cost_of(row: dict, prices: dict) -> Decimal | None:
    p = prices.get(row.get("model"))
    if not isinstance(p, dict) or row.get("usage_missing"):
        return None
    try:
        total = sum(Decimal(str(row.get(k, 0))) * Decimal(str(p[k])) for k in ("input", "cache_write", "cache_read", "output"))
    except (KeyError, ArithmeticError, ValueError):
        return None
    return total / Decimal(1_000_000)


def group_key(row: dict, by: str) -> str:
    tags = row.get("tags") or {}
    if by == "role":
        return row.get("role") or "?"
    if by == "model":
        return row.get("model") or "?"
    if by == "item":
        return f"пункт {tags.get('пункт', tags.get('item', '—'))}"
    if by == "round":
        item = tags.get("пункт", tags.get("item", "—"))
        rnd = tags.get("круг", tags.get("round", "—"))
        return f"пункт {item} / круг {rnd} / {row.get('role')}"
    raise ValueError(by)


def aggregate(rows: list[dict], by: str = "role", prices: dict | None = None) -> dict:
    prices = prices or {}
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows:
        g = groups.setdefault(group_key(row, by), {
            "runs": 0, "requests": 0, "usage_missing": 0, **{k: 0 for k in TOKEN_FIELDS},
            "tool_calls": 0, "explore_calls": 0, "cost_usd": Decimal(0), "cost_known": True,
        })
        g["runs"] += 1
        for k in ("requests", "usage_missing") + TOKEN_FIELDS:
            g[k] += int(row.get(k) or 0)
        calls = row.get("tool_calls") or {}
        g["tool_calls"] += sum(int(v) for v in calls.values())
        g["explore_calls"] += sum(int(calls.get(t, 0)) for t in EXPLORE_TOOLS)
        c = cost_of(row, prices)
        if c is None:
            g["cost_known"] = False
        else:
            g["cost_usd"] += c
    total = {k: sum(g[k] for g in groups.values()) for k in
             ("runs", "requests", "usage_missing", "tool_calls", "explore_calls") + TOKEN_FIELDS}
    total["cost_known"] = all(g["cost_known"] for g in groups.values()) and bool(groups)
    total["cost_usd"] = sum((g["cost_usd"] for g in groups.values()), Decimal(0))
    out = OrderedDict()
    for name, g in list(groups.items()) + [("ИТОГО", total)]:
        full_in = g["input"] + g["cache_write"] + g["cache_read"]
        out[name] = {
            **{k: g[k] for k in ("runs", "requests", "usage_missing") + TOKEN_FIELDS + ("tool_calls", "explore_calls")},
            "input_total": full_in,
            "cache_hit": round(g["cache_read"] / full_in, 3) if full_in else None,
            "cost_usd": float(round(g["cost_usd"], 4)) if g["cost_known"] else None,
        }
    return out


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def render(table: dict, by: str) -> str:
    head = f"| {by} | записей | запросов | вход | запись кеша | чтение кеша | выход | доля кеша | вызовов (Read/Grep/Glob) | $ |"
    lines = [head, "|" + "---|" * 10]
    for name, g in table.items():
        hit = f"{g['cache_hit'] * 100:.0f}%" if g["cache_hit"] is not None else "—"
        cost = f"{g['cost_usd']:.2f}" if g["cost_usd"] is not None else "—"
        miss = f" (+{g['usage_missing']} без usage)" if g["usage_missing"] else ""
        bold = "**" if name == "ИТОГО" else ""
        lines.append(
            f"| {bold}{name}{bold} | {g['runs']} | {g['requests']}{miss} | {fmt_tokens(g['input'])} | "
            f"{fmt_tokens(g['cache_write'])} | {fmt_tokens(g['cache_read'])} | {fmt_tokens(g['output'])} | "
            f"{hit} | {g['tool_calls']} ({g['explore_calls']}) | {cost} |"
        )
    return "\n".join(lines)


def report(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="usage.py report")
    ap.add_argument("--root")
    ap.add_argument("--by", choices=["role", "item", "model", "round"], default="role")
    ap.add_argument("--since", help="ISO-время: строки журнала не раньше")
    ap.add_argument("--session")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    project = find_project(args.root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if not project:
        print("Нет папки .orchestra/ — журнала тоже нет.", file=sys.stderr)
        return 1
    rows = load_journal(project)
    if args.since:
        rows = [r for r in rows if str(r.get("ts", "")) >= args.since]
    if args.session:
        rows = [r for r in rows if r.get("session") == args.session]
    cfg = read_json(os.path.join(project, STATE_DIR, "config.json"), {})
    prices = cfg.get("prices") if isinstance(cfg, dict) and isinstance(cfg.get("prices"), dict) else {}
    table = aggregate(rows, args.by, prices)
    if args.json:
        print(json.dumps(table, ensure_ascii=False, indent=1))
    elif not rows:
        print("Журнал пуст.")
    else:
        print(render(table, args.by))
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "report":
        return report(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "hook":
        try:
            data = json.load(sys.stdin)
        except ValueError:
            return 0
        if isinstance(data, dict):
            hook(data)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail-open: журнал не должен ломать работу
        print(f"[orchestra usage] internal error: {exc}", file=sys.stderr)
        sys.exit(0)
