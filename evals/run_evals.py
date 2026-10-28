#!/usr/bin/env python3
"""Eval плагина orchestra: тимлид решает задачи в маленьких проектах, результат проверяет код.

    python3 evals/run_evals.py run [--cases ГЛОБ] [-k 3] [--model opus] [-j 1] [--max-budget-usd 20] [--keep]
    python3 evals/run_evals.py self-check            # проверить сами кейсы, без Claude и без затрат
    python3 evals/run_evals.py compare A/summary.json B/summary.json

Каждый прогон: копия проекта из fixtures/ (+ overlay кейса) → git-репозиторий с одним
коммитом → `claude -p "/orchestra:teamlead <задача>"` с плагином из plugins/orchestra →
проверки кодом:

- hidden_tests  — приёмочные тесты кейса, которых агенты не видели;
- project_tests — тесты проекта, включая написанные тестером (через scripts/run_tests.py);
- committed     — работа закоммичена в рабочую ветку, main не тронут, автор — пользователь,
                  без приписок о Claude, в рабочей копии нет незакоммиченных правок;
- untouched     — файлы из must_not_change не изменились;
- new_tests     — тестер добавил не меньше min_new_tests тестов (информативно);
- claude_ok     — сессия завершилась без ошибки.

Прогон засчитан, если прошли все обязательные проверки (все, кроме new_tests).
Метрики: доля успешных прогонов, pass^k (задача решена во всех k попытках — оценка
как в τ-bench), стоимость и токены по ролям из журнала .orchestra/reports/usage.jsonl,
число блокировок и откатов хука.

Прогон стоит денег: тимлид и ревьюер на Opus. Начинайте с одного кейса и -k 1.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLUGIN = REPO / "plugins" / "orchestra"
SCRIPTS = PLUGIN / "scripts"
CASES = HERE / "cases"
FIXTURES = HERE / "fixtures"

AUTHOR_NAME = "Eval User"
AUTHOR_EMAIL = "eval@example.com"
REQUIRED = ("claude_ok", "hidden_tests", "project_tests", "committed", "untouched")
FORBIDDEN_IN_COMMITS = re.compile(r"co-authored-by|claude-session|generated with \[?claude", re.I)

EVAL_NOTE = """\
Это автоматический прогон eval плагина orchestra. Пользователя рядом нет.
- Все согласования (уровень, план, разбиение L, итоги ревью плана, архитектура) считаются одобренными: не жди ответа и не задавай вопросов, принимай разумные решения сам и записывай их в .orchestra/status.md.
- Plan mode и вопросы пользователю недоступны: план пиши сразу в .orchestra/plan.md.
- Режим — облако: коммить сам после зелёных тестов и ревью кода. Удалённого репозитория нет, пуш пропусти.
- Ручную проверку, которую окружение не позволяет провести, записывай в todo.
"""


def load_usage_module():
    spec = importlib.util.spec_from_file_location("orchestra_usage", SCRIPTS / "usage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


usage = load_usage_module()


# ---------------------------------------------------------------- кейсы


def load_cases(pattern: str) -> list[dict]:
    cases = []
    for case_file in sorted(CASES.glob("*/case.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        case["dir"] = case_file.parent
        if fnmatch.fnmatch(case["name"], pattern):
            cases.append(case)
    return cases


def overlay(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def git(work: Path, *args: str, check: bool = True) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME=AUTHOR_NAME, GIT_AUTHOR_EMAIL=AUTHOR_EMAIL,
               GIT_COMMITTER_NAME=AUTHOR_NAME, GIT_COMMITTER_EMAIL=AUTHOR_EMAIL)
    proc = subprocess.run(["git", *args], cwd=work, capture_output=True, encoding="utf-8", env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def prepare(case: dict, work: Path) -> dict:
    """Проект кейса в work: фикстура + overlay, git с одним коммитом в main, конфиг orchestra."""
    shutil.copytree(FIXTURES / case["fixture"], work)
    overlay(case["dir"] / "overlay", work)
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", AUTHOR_NAME)
    git(work, "config", "user.email", AUTHOR_EMAIL)
    git(work, "config", "commit.gpgsign", "false")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "initial")
    base = git(work, "rev-parse", "HEAD")
    # Папка orchestra заранее: режим облака (коммит без вопроса) и журнал расхода с первого шага.
    (work / ".orchestra" / "reports").mkdir(parents=True)
    (work / ".orchestra" / "config.json").write_text(json.dumps({"mode": "cloud"}), encoding="utf-8")
    with open(work / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
        f.write("\n.orchestra/\n")
    return {"base": base, "tests_before": count_tests(work)}


def count_tests(work: Path, tests_dir: str | None = None) -> int:
    tests_dir = tests_dir or tests_dir_of(work)
    total = 0
    root = work / tests_dir
    if not root.is_dir():
        return 0
    for path in root.rglob("test*.py"):
        try:
            total += len(re.findall(r"^\s*(?:async\s+)?def test_", path.read_text(encoding="utf-8"), re.M))
        except OSError:
            pass
    return total


def tests_dir_of(work: Path) -> str:
    try:
        cfg = json.loads((work / ".orchestra" / "config.json").read_text(encoding="utf-8"))
        if isinstance(cfg.get("tests_dir"), str):
            return cfg["tests_dir"]
    except (OSError, ValueError):
        pass
    return "tests"


# ---------------------------------------------------------------- проверки


def run_tests(work: Path, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_tests.py"), "--root", str(work), "--no-cache", *args],
        capture_output=True, encoding="utf-8",
    )
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"status": "error", "output_tail": (proc.stdout + proc.stderr)[-2000:]}


def short_tests(res: dict) -> dict:
    keep = ("status", "passed", "failed", "errors", "skipped")
    out = {k: res.get(k) for k in keep}
    if res.get("failures"):
        out["failures"] = [f"{f['test']}: {f['message']}" for f in res["failures"][:10]]
    if res.get("output_tail") and res.get("status") not in ("passed", "failed"):
        out["output_tail"] = res["output_tail"][-1500:]
    return out


def check(case: dict, work: Path, prep: dict, claude: dict) -> dict:
    checks: dict[str, dict] = {}
    checks["claude_ok"] = {"ok": bool(claude) and not claude.get("is_error"),
                           "detail": claude.get("terminal_reason") or claude.get("error")}

    hidden_dir = work / ".eval_hidden"
    if hidden_dir.exists():
        shutil.rmtree(hidden_dir)
    shutil.copytree(case["dir"] / "hidden", hidden_dir)
    hidden = run_tests(work, ".eval_hidden")
    shutil.rmtree(hidden_dir, ignore_errors=True)
    checks["hidden_tests"] = {"ok": hidden.get("status") == "passed", "detail": short_tests(hidden)}

    project = run_tests(work)
    checks["project_tests"] = {"ok": project.get("status") == "passed", "detail": short_tests(project)}

    checks["committed"] = check_commits(work, prep["base"])

    changed = [p for p in case.get("must_not_change", [])
               if subprocess.run(["git", "diff", "--quiet", prep["base"], "--", p], cwd=work).returncode != 0]
    checks["untouched"] = {"ok": not changed, "detail": changed}

    added = count_tests(work) - prep["tests_before"]
    need = int(case.get("min_new_tests", 0))
    checks["new_tests"] = {"ok": added >= need, "detail": f"+{added} (нужно {need})"}
    return checks


def check_commits(work: Path, base: str) -> dict:
    problems = []
    if git(work, "rev-parse", "main", check=False) != base:
        problems.append("main изменён")
    branch = git(work, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    if branch in ("main", "master", "HEAD"):
        problems.append(f"работа не в рабочей ветке ({branch})")
    log = git(work, "log", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e", f"{base}..HEAD", check=False)
    commits = [c.strip().split("\x1f") for c in log.split("\x1e") if c.strip()]
    if not commits:
        problems.append("нет коммитов поверх base")
    for c in commits:
        sha, an, ae, cn, ce, body = (c + [""] * 6)[:6]
        if (an, ae) != (AUTHOR_NAME, AUTHOR_EMAIL) or (cn, ce) != (AUTHOR_NAME, AUTHOR_EMAIL):
            problems.append(f"{sha[:7]}: автор {an} <{ae}>, коммиттер {cn} <{ce}>")
        if FORBIDDEN_IN_COMMITS.search(body):
            problems.append(f"{sha[:7]}: приписка о Claude в сообщении")
    dirty = [ln for ln in git(work, "status", "--porcelain", check=False).splitlines()
             if ln.strip() and not ln[3:].startswith((".orchestra", ".eval_hidden"))]
    if dirty:
        problems.append(f"незакоммичено: {', '.join(ln[3:] for ln in dirty[:10])}")
    return {"ok": not problems, "detail": problems or f"{len(commits)} коммит(ов) в {branch}"}


def guard_events(work: Path) -> dict:
    events: dict[str, int] = {}
    try:
        lines = (work / ".orchestra" / "reports" / "guard.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for ln in lines:
        m = re.match(r"^\S+ \S+ (\S+): (заблокировано|откачено|не откачено)", ln)
        if m:
            key = f"{m.group(1)} {m.group(2)}"
            events[key] = events.get(key, 0) + 1
    return events


# ---------------------------------------------------------------- прогон


def claude_command(case: dict, args) -> list[str]:
    cmd = [
        args.claude, "-p", f"/orchestra:teamlead {case['task']}",
        "--output-format", "json",
        "--plugin-dir", str(PLUGIN),
        "--model", args.model,
        "--permission-mode", args.permission_mode,
        "--disallowedTools", "EnterPlanMode", "ExitPlanMode", "AskUserQuestion",
        "--append-system-prompt", EVAL_NOTE,
        "--setting-sources", "project,local",
        "--strict-mcp-config",
    ]
    if args.max_budget_usd:
        cmd += ["--max-budget-usd", str(args.max_budget_usd)]
    return cmd


def run_claude(case: dict, work: Path, args, log_path: Path) -> dict:
    timeout = int(case.get("timeout_s", 1800))
    started = time.time()
    try:
        proc = subprocess.run(claude_command(case, args), cwd=work, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out, err, code = str(exc.stdout or ""), f"timeout after {timeout}s", -1
    log_path.write_text(out + ("\n--- stderr ---\n" + err if err else ""), encoding="utf-8")
    result = {}
    for line in reversed(out.strip().splitlines()):
        try:
            result = json.loads(line)
            break
        except ValueError:
            continue
    if not result:
        result = {"is_error": True, "error": (err or out)[-500:] or f"exit {code}"}
    result["wall_s"] = round(time.time() - started, 1)
    return result


def one_run(case: dict, idx: int, args, out_dir: Path) -> dict:
    run_dir = out_dir / case["name"] / f"run-{idx}"
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"orchestra-eval-{case['name']}-"))
    work = tmp / "project"
    try:
        prep = prepare(case, work)
        claude = run_claude(case, work, args, run_dir / "claude.json")
        checks = check(case, work, prep, claude)
        rows = usage.load_journal(str(work))
        by_role = usage.aggregate(rows, "role")
        record = {
            "case": case["name"], "run": idx,
            "passed": all(checks[k]["ok"] for k in REQUIRED),
            "checks": checks,
            "cost_usd": claude.get("total_cost_usd"),
            "turns": claude.get("num_turns"),
            "wall_s": claude.get("wall_s"),
            "subagents": (claude.get("subagent_stats") or {}).get("spawned"),
            "usage_by_role": by_role,
            "guard": guard_events(work),
            "branch": git(work, "rev-parse", "--abbrev-ref", "HEAD", check=False),
        }
        if (work / ".orchestra").is_dir():
            shutil.copytree(work / ".orchestra", run_dir / "orchestra", dirs_exist_ok=True)
        (run_dir / "git-log.txt").write_text(
            git(work, "log", "--stat", "--format=%h %an <%ae>%n%B", f"{prep['base']}..HEAD", check=False), encoding="utf-8")
        (run_dir / "diff.patch").write_text(git(work, "diff", prep["base"], check=False), encoding="utf-8")
    except Exception as exc:  # прогон не должен ронять весь eval
        record = {"case": case["name"], "run": idx, "passed": False, "error": repr(exc), "checks": {}}
    finally:
        if args.keep:
            record_path = run_dir / "workdir.txt"
            record_path.write_text(str(work), encoding="utf-8")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    (run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    mark = "✓" if record["passed"] else "✗"
    failed = [k for k, v in record.get("checks", {}).items() if not v["ok"]]
    print(f"{mark} {case['name']} #{idx}  ${record.get('cost_usd') or 0:.2f}  "
          f"{record.get('wall_s') or 0:.0f}s  {'не прошло: ' + ', '.join(failed) if failed else ''}"
          f"{record.get('error', '')}", flush=True)
    return record


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Вероятность решить задачу во всех k попытках (τ-bench): C(c,k)/C(n,k)."""
    return comb(c, k) / comb(n, k) if n >= k else float("nan")


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 4) if values else None


def summarize(records: list[dict], k: int, meta: dict) -> dict:
    cases = {}
    for rec in records:
        cases.setdefault(rec["case"], []).append(rec)
    out_cases = {}
    for name, recs in cases.items():
        n, c = len(recs), sum(r["passed"] for r in recs)
        roles: dict[str, dict] = {}
        for r in recs:
            for role, g in (r.get("usage_by_role") or {}).items():
                acc = roles.setdefault(role, {"output": [], "input_total": [], "cache_read": [], "explore_calls": []})
                for key in acc:
                    acc[key].append(g.get(key))
        out_cases[name] = {
            "runs": n, "passed": c, "pass_rate": round(c / n, 3),
            "pass_hat": {str(i): round(pass_hat_k(n, c, i), 3) for i in range(1, n + 1)},
            "mean_cost_usd": mean([r.get("cost_usd") for r in recs]),
            "mean_wall_s": mean([r.get("wall_s") for r in recs]),
            "mean_turns": mean([r.get("turns") for r in recs]),
            "check_fail_counts": {
                key: sum(1 for r in recs if key in r.get("checks", {}) and not r["checks"][key]["ok"])
                for key in REQUIRED + ("new_tests",)
            },
            "mean_tokens_by_role": {role: {key: mean(vals) for key, vals in acc.items()} for role, acc in roles.items()},
            "guard_events": sum(sum((r.get("guard") or {}).values()) for r in recs),
        }
    n_all = len(records)
    return {
        "meta": meta,
        "overall": {
            "runs": n_all,
            "pass_rate": round(sum(r["passed"] for r in records) / n_all, 3) if n_all else None,
            "pass_hat_1": mean([c["pass_hat"].get("1") for c in out_cases.values()]),
            f"pass_hat_{k}": mean([c["pass_hat"].get(str(k)) for c in out_cases.values()]),
            "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in records), 2),
        },
        "cases": out_cases,
    }


def render_summary(s: dict) -> str:
    k = s["meta"]["k"]
    lines = [f"# Eval orchestra — {s['meta']['started']}", "",
             f"Модель тимлида: {s['meta']['model']}, попыток на кейс: {k}, коммит плагина: {s['meta']['commit']}", "",
             "| кейс | успешно | pass^1 | pass^k | $ в среднем | время, с | что не прошло |",
             "|---|---|---|---|---|---|---|"]
    for name, c in s["cases"].items():
        fails = ", ".join(f"{key}×{v}" for key, v in c["check_fail_counts"].items() if v) or "—"
        lines.append(f"| {name} | {c['passed']}/{c['runs']} | {c['pass_hat']['1']} | {c['pass_hat'].get(str(c['runs']))} | "
                     f"{round(c['mean_cost_usd'], 2) if c['mean_cost_usd'] is not None else '—'} | {round(c['mean_wall_s']) if c['mean_wall_s'] is not None else '—'} | {fails} |")
    o = s["overall"]
    hat_k = f", pass^{k} {o.get(f'pass_hat_{k}')}" if k > 1 else ""
    lines += ["", f"Итого: успешно {o['pass_rate']}, pass^1 {o['pass_hat_1']}{hat_k}, "
                  f"потрачено ${o['total_cost_usd']}", "", "## Токены по ролям (среднее на прогон)", ""]
    for name, c in s["cases"].items():
        lines += [f"**{name}**", "", "| роль | вход всего | чтение кеша | выход | Read/Grep/Glob |", "|---|---|---|---|---|"]
        roles = sorted(c["mean_tokens_by_role"].items(), key=lambda kv: (kv[0] == "ИТОГО", kv[0] != "teamlead", kv[0]))
        for role, g in roles:
            t = lambda v: usage.fmt_tokens(int(v)) if isinstance(v, (int, float)) else "—"
            lines.append(f"| {role} | {t(g['input_total'])} | {t(g['cache_read'])} | {t(g['output'])} | {round(g['explore_calls'], 1) if g['explore_calls'] is not None else '—'} |")
        lines.append("")
    return "\n".join(lines)


def cmd_run(args) -> int:
    cases = load_cases(args.cases)
    if not cases:
        print(f"Нет кейсов по шаблону {args.cases!r}", file=sys.stderr)
        return 2
    started = time.strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = Path(args.out) if args.out else HERE / "results" / started
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                            encoding="utf-8").stdout.strip() or "?"
    dirty = subprocess.run(["git", "status", "--porcelain", "plugins"], cwd=REPO, capture_output=True,
                           encoding="utf-8").stdout.strip()
    meta = {"started": started, "model": args.model, "k": args.k, "commit": commit + ("+изменения" if dirty else ""),
            "cases": [c["name"] for c in cases]}
    print(f"Кейсов: {len(cases)}, попыток: {args.k}, результаты: {out_dir}", flush=True)
    jobs = [(case, i) for case in cases for i in range(1, args.k + 1)]
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        records = list(pool.map(lambda job: one_run(job[0], job[1], args, out_dir), jobs))
    summary = summarize(records, args.k, meta)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    print("\n" + render_summary(summary))
    return 0 if summary["overall"]["pass_rate"] == 1 else 1


# ---------------------------------------------------------------- self-check


def self_check(pattern: str) -> list[str]:
    """Кейсы осмысленны: на исходном проекте приёмочные тесты падают, на эталоне — проходят."""
    problems = []
    for case in load_cases(pattern):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "project"
            prep = prepare(case, work)
            fake = {"is_error": False, "terminal_reason": "completed"}
            before = check(case, work, prep, fake)
            if before["hidden_tests"]["ok"]:
                problems.append(f"{case['name']}: приёмочные тесты проходят без решения")
            if not before["project_tests"]["ok"]:
                problems.append(f"{case['name']}: тесты исходного проекта красные: {before['project_tests']['detail']}")
            git(work, "checkout", "-q", "-b", "feature/solution")
            overlay(case["dir"] / "solution", work)
            git(work, "add", "-A")
            git(work, "commit", "-q", "-m", "fix: эталонное решение")
            after = check(case, work, prep, fake)
            for key in REQUIRED + ("new_tests",):
                if not after[key]["ok"]:
                    problems.append(f"{case['name']}: эталон не проходит {key}: {after[key]['detail']}")
    return problems


def cmd_self_check(args) -> int:
    problems = self_check(args.cases)
    for p in problems:
        print("✗", p)
    if not problems:
        print(f"✓ кейсы в порядке: {', '.join(c['name'] for c in load_cases(args.cases))}")
    return 1 if problems else 0


# ---------------------------------------------------------------- compare


def cmd_compare(args) -> int:
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))

    def delta(x, y):
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return "—"
        return f"{x} → {y}" + (f" ({(y - x) / x * 100:+.0f}%)" if x else "")

    print(f"A: {a['meta']['commit']} ({a['meta']['started']})  B: {b['meta']['commit']} ({b['meta']['started']})\n")
    print("| кейс | успешно | $ в среднем | время, с |\n|---|---|---|---|")
    for name in sorted(set(a["cases"]) | set(b["cases"])):
        ca, cb = a["cases"].get(name, {}), b["cases"].get(name, {})
        print(f"| {name} | {delta(ca.get('pass_rate'), cb.get('pass_rate'))} | "
              f"{delta(ca.get('mean_cost_usd'), cb.get('mean_cost_usd'))} | {delta(ca.get('mean_wall_s'), cb.get('mean_wall_s'))} |")
    print("\n| кейс | роль | вход всего | выход | Read/Grep/Glob |\n|---|---|---|---|---|")
    for name in sorted(set(a["cases"]) & set(b["cases"])):
        ra, rb = a["cases"][name]["mean_tokens_by_role"], b["cases"][name]["mean_tokens_by_role"]
        for role in sorted(set(ra) | set(rb)):
            ga, gb = ra.get(role, {}), rb.get(role, {})
            print(f"| {name} | {role} | {delta(ga.get('input_total'), gb.get('input_total'))} | "
                  f"{delta(ga.get('output'), gb.get('output'))} | {delta(ga.get('explore_calls'), gb.get('explore_calls'))} |")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Eval плагина orchestra")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="прогнать кейсы через Claude Code")
    r.add_argument("--cases", default="*", help="шаблон имени кейса, например 's-*'")
    r.add_argument("-k", type=int, default=3, help="попыток на кейс")
    r.add_argument("--model", default="opus", help="модель тимлида (основной сессии)")
    r.add_argument("-j", "--jobs", type=int, default=1, help="прогонов параллельно")
    r.add_argument("--max-budget-usd", type=float, default=None, help="предел трат на один прогон")
    r.add_argument("--permission-mode", default="bypassPermissions")
    r.add_argument("--claude", default="claude", help="путь к CLI Claude Code")
    r.add_argument("--out", help="папка результатов (по умолчанию evals/results/<время>)")
    r.add_argument("--keep", action="store_true", help="не удалять рабочие копии проектов")
    s = sub.add_parser("self-check", help="проверить кейсы без Claude")
    s.add_argument("--cases", default="*")
    c = sub.add_parser("compare", help="сравнить два summary.json")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "self-check": cmd_self_check, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
