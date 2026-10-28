"""Тесты обвязки eval (без вызова Claude). Запуск: python3 -m pytest dev/ -q"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import run_evals  # noqa: E402


def test_cases_are_meaningful():
    """На исходном проекте приёмочные тесты красные, на эталонном решении всё зелёное."""
    assert run_evals.self_check("*") == []


@pytest.fixture
def repo(tmp_path):
    case = run_evals.load_cases("s-bugfix-promo")[0]
    work = tmp_path / "project"
    prep = run_evals.prepare(case, work)
    run_evals.git(work, "checkout", "-q", "-b", "bugfix/promo")
    return work, prep


def commit(work, msg, name=run_evals.AUTHOR_NAME, email=run_evals.AUTHOR_EMAIL):
    (work / "src/shop/note.txt").write_text(msg, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", msg],
                   cwd=work, check=True)


def test_commit_check_ok(repo):
    work, prep = repo
    commit(work, "fix: скидка один раз")
    res = run_evals.check_commits(work, prep["base"])
    assert res["ok"], res


@pytest.mark.parametrize("msg", [
    "fix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    "fix: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
])
def test_commit_check_rejects_attribution(repo, msg):
    work, prep = repo
    commit(work, msg)
    assert not run_evals.check_commits(work, prep["base"])["ok"]


def test_commit_check_rejects_wrong_author(repo):
    work, prep = repo
    commit(work, "fix: x", name="Claude", email="noreply@anthropic.com")
    assert not run_evals.check_commits(work, prep["base"])["ok"]


def test_commit_check_rejects_no_commits_and_dirty_tree(repo):
    work, prep = repo
    (work / "src/shop/cart.py").write_text("# changed\n", encoding="utf-8")
    res = run_evals.check_commits(work, prep["base"])
    assert not res["ok"]
    assert any("нет коммитов" in p for p in res["detail"]) and any("незакоммичено" in p for p in res["detail"])


def test_commit_check_rejects_main(tmp_path):
    case = run_evals.load_cases("s-bugfix-promo")[0]
    work = tmp_path / "project"
    prep = run_evals.prepare(case, work)
    commit(work, "fix: прямо в main")
    res = run_evals.check_commits(work, prep["base"])
    assert not res["ok"] and any("main" in p for p in res["detail"])


def test_orchestra_dir_is_ignored_by_git(repo):
    work, prep = repo
    commit(work, "fix: x")
    (work / ".orchestra/status.md").write_text("статус", encoding="utf-8")
    assert run_evals.check_commits(work, prep["base"])["ok"]


def test_pass_hat_k():
    assert run_evals.pass_hat_k(3, 3, 3) == 1
    assert run_evals.pass_hat_k(3, 2, 1) == pytest.approx(2 / 3)
    assert run_evals.pass_hat_k(3, 2, 2) == pytest.approx(1 / 3)
    assert run_evals.pass_hat_k(3, 2, 3) == 0


def test_summarize_and_compare(tmp_path, capsys):
    def rec(case, passed, cost):
        return {"case": case, "run": 1, "passed": passed, "cost_usd": cost, "wall_s": 10, "turns": 5,
                "checks": {"hidden_tests": {"ok": passed}},
                "usage_by_role": {"coder": {"output": 100, "input_total": 1000, "cache_read": 900, "explore_calls": 4}},
                "guard": {"tester заблокировано": 1}}
    meta = {"started": "t", "model": "opus", "k": 2, "commit": "abc", "cases": ["a"]}
    a = run_evals.summarize([rec("a", True, 2.0), rec("a", False, 4.0)], 2, meta)
    assert a["cases"]["a"]["pass_rate"] == 0.5
    assert a["cases"]["a"]["pass_hat"] == {"1": 0.5, "2": 0.0}
    assert a["cases"]["a"]["mean_cost_usd"] == 3.0
    assert a["cases"]["a"]["check_fail_counts"]["hidden_tests"] == 1
    assert a["cases"]["a"]["guard_events"] == 2
    assert "| a | 1/2 |" in run_evals.render_summary(a)
    b = run_evals.summarize([rec("a", True, 1.5), rec("a", True, 1.5)], 2, dict(meta, commit="def"))
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    run_evals.main(["compare", str(pa), str(pb)])
    out = capsys.readouterr().out
    assert "0.5 → 1.0 (+100%)" in out and "3.0 → 1.5 (-50%)" in out


def test_guard_events(tmp_path):
    (tmp_path / ".orchestra/reports").mkdir(parents=True)
    (tmp_path / ".orchestra/reports/guard.log").write_text(
        "2026-10-29 01:00:00 tester: заблокировано Read | src/a.py | ...\n"
        "2026-10-29 01:00:01 coder: откачено tests/x.py (изменён) | sed -i\n"
        "2026-10-29 01:00:02 tester: заблокировано Bash | cat src/a.py | ...\n", encoding="utf-8")
    assert run_evals.guard_events(tmp_path) == {"tester заблокировано": 2, "coder откачено": 1}


def test_claude_command_is_headless_and_isolated():
    case = run_evals.load_cases("s-bugfix-promo")[0]
    args = run_evals.main.__globals__["argparse"].Namespace(
        claude="claude", model="opus", permission_mode="bypassPermissions", max_budget_usd=5)
    cmd = run_evals.claude_command(case, args)
    assert cmd[:3] == ["claude", "-p", "/orchestra:teamlead " + case["task"]]
    for flag in ("--plugin-dir", "--strict-mcp-config", "--append-system-prompt", "--max-budget-usd"):
        assert flag in cmd
    assert "EnterPlanMode" in cmd and "AskUserQuestion" in cmd
