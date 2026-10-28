"""Тесты scripts/usage.py (журнал расхода). Запуск: python3 -m pytest dev/ -q"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins/orchestra/scripts/usage.py"


def assistant(mid, model="claude-sonnet-5-5", usage=None, blocks=("text",), sidechain=False):
    content = [{"type": b, "name": b.split(":", 1)[1]} if b.startswith("tool_use:") else {"type": b} for b in blocks]
    for c in content:
        if c["type"].startswith("tool_use"):
            c["type"] = "tool_use"
    return {"type": "assistant", "isSidechain": sidechain,
            "message": {"id": mid, "model": model, "content": content, "usage": usage}}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def u(inp=10, cw=100, cr=1000, out=50, thinking=5):
    return {"input_tokens": inp, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
            "output_tokens": out, "output_tokens_details": {"thinking_tokens": thinking}}


def write_transcript(path, records, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".orchestra").mkdir(parents=True)
    return proj


def hook(project, payload):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    proc = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=json.dumps(payload),
                          capture_output=True, encoding="utf-8", env=env)
    assert proc.returncode == 0, proc.stderr
    return proc


def journal(project):
    path = project / ".orchestra/reports/usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def stop_sub(project, transcript, agent_type="orchestra:coder", agent_id="a1"):
    return hook(project, {"hook_event_name": "SubagentStop", "session_id": "s", "agent_id": agent_id,
                          "agent_type": agent_type, "agent_transcript_path": str(transcript), "cwd": str(project)})


def test_subagent_usage_is_logged_once_per_message(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [
        user("Сделай задачу.\n[orchestra пункт=2 круг=1 фаза=реализация]"),
        assistant("m1", usage=u(), blocks=("thinking",)),
        assistant("m1", usage=u(), blocks=("tool_use:Read",)),   # то же сообщение, второй блок
        assistant("m2", usage=u(inp=1, cw=0, cr=2000, out=10, thinking=0), blocks=("tool_use:Grep", "tool_use:Edit")),
    ])
    stop_sub(project, t)
    rows = journal(project)
    assert len(rows) == 1
    r = rows[0]
    assert r["role"] == "coder" and r["agent_id"] == "a1" and r["model"] == "claude-sonnet-5-5"
    assert r["tags"] == {"пункт": "2", "круг": "1", "фаза": "реализация"}
    assert r["requests"] == 2
    assert (r["input"], r["cache_write"], r["cache_read"], r["output"], r["thinking"]) == (11, 100, 3000, 60, 5)
    assert r["tool_calls"] == {"Edit": 1, "Grep": 1, "Read": 1}
    assert r["resumed"] is False and r["usage_missing"] == 0


def test_resumed_agent_logs_only_new_part_and_keeps_tags(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [user("[orchestra пункт=1 круг=1]"), assistant("m1", usage=u())])
    stop_sub(project, t)
    stop_sub(project, t)  # повторный вызов без новых строк — ничего не пишет
    assert len(journal(project)) == 1
    write_transcript(t, [user("Исправь падение."), assistant("m2", usage=u(out=7))], mode="a")
    stop_sub(project, t)
    rows = journal(project)
    assert len(rows) == 2
    assert rows[1]["resumed"] is True and rows[1]["output"] == 7 and rows[1]["requests"] == 1
    assert rows[1]["tags"] == {"пункт": "1", "круг": "1"}


def test_new_tag_on_resume_wins(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [user("[orchestra пункт=1 круг=1]"), assistant("m1", usage=u())])
    stop_sub(project, t)
    write_transcript(t, [user("[orchestra пункт=1 круг=2]"), assistant("m2", usage=u())], mode="a")
    stop_sub(project, t)
    assert journal(project)[1]["tags"]["круг"] == "2"


def test_partial_last_line_is_left_for_next_time(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [assistant("m1", usage=u())])
    with open(t, "a", encoding="utf-8") as f:
        f.write(json.dumps(assistant("m2", usage=u()))[:40])  # строка ещё дописывается
    stop_sub(project, t)
    assert journal(project)[0]["requests"] == 1


def test_missing_usage_is_counted_not_zeroed(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [assistant("m1", usage=None), assistant("m2", usage=u())])
    stop_sub(project, t)
    r = journal(project)[0]
    assert r["requests"] == 2 and r["usage_missing"] == 1 and r["output"] == 50


def test_models_are_split(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [assistant("m1", model="claude-opus-5-5", usage=u()),
                         assistant("m2", model="claude-haiku-5-5", usage=u()),
                         assistant("m3", model="<synthetic>", usage=u())])
    stop_sub(project, t)
    assert sorted(r["model"] for r in journal(project)) == ["claude-haiku-5-5", "claude-opus-5-5"]


def test_main_session_is_teamlead_and_skips_sidechain(project, tmp_path):
    t = tmp_path / "main.jsonl"
    write_transcript(t, [assistant("m1", usage=u(out=1)), assistant("x", usage=u(out=999), sidechain=True)])
    hook(project, {"hook_event_name": "Stop", "session_id": "s", "transcript_path": str(t), "cwd": str(project)})
    rows = journal(project)
    assert len(rows) == 1 and rows[0]["role"] == "teamlead" and rows[0]["output"] == 1


def test_foreign_agent_keeps_its_type(project, tmp_path):
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [assistant("m1", usage=u())])
    stop_sub(project, t, agent_type="Explore")
    assert journal(project)[0]["role"] == "Explore"


def test_nothing_written_without_orchestra_dir(tmp_path):
    proj = tmp_path / "plain"
    proj.mkdir()
    t = tmp_path / "agent.jsonl"
    write_transcript(t, [assistant("m1", usage=u())])
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    payload = {"hook_event_name": "SubagentStop", "agent_type": "orchestra:coder",
               "agent_transcript_path": str(t), "cwd": str(proj)}
    proc = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=json.dumps(payload),
                          capture_output=True, encoding="utf-8", env=env)
    assert proc.returncode == 0
    assert not (proj / ".orchestra").exists()


def test_bad_input_does_not_fail(project):
    for raw in ("", "not json", "[]", json.dumps({"hook_event_name": "SubagentStop"}),
                json.dumps({"hook_event_name": "SubagentStop", "agent_transcript_path": "/no/such/file"})):
        proc = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=raw, capture_output=True, encoding="utf-8",
                              env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project)))
        assert proc.returncode == 0, raw


def test_parallel_subagents_do_not_lose_lines(project, tmp_path):
    paths = []
    for i in range(6):
        t = tmp_path / f"agent{i}.jsonl"
        write_transcript(t, [assistant(f"m{i}", usage=u())])
        paths.append(t)
    threads = [threading.Thread(target=stop_sub, args=(project, p, "orchestra:coder", f"a{i}")) for i, p in enumerate(paths)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    rows = journal(project)
    assert sorted(r["agent_id"] for r in rows) == [f"a{i}" for i in range(6)]
    state = json.loads((project / ".orchestra/reports/.usage-state.json").read_text(encoding="utf-8"))
    assert len(state) == 6


# ------------------------------------------------------------ сводка


def report(project, *args):
    proc = subprocess.run([sys.executable, str(SCRIPT), "report", "--root", str(project), *args],
                          capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def fill(project, tmp_path):
    for i, (atype, model, tag, out) in enumerate([
        ("orchestra:coder", "claude-sonnet-5-5", "[orchestra пункт=1 круг=1]", 100),
        ("orchestra:coder", "claude-sonnet-5-5", "[orchestra пункт=1 круг=2]", 50),
        ("orchestra:reviewer", "claude-opus-5-5", "[orchestra пункт=1]", 30),
        ("orchestra:tester", "claude-sonnet-5-5", "[orchestra пункт=2 круг=1]", 20),
    ]):
        t = tmp_path / f"a{i}.jsonl"
        write_transcript(t, [user(tag), assistant(f"m{i}", model=model, usage=u(out=out), blocks=("tool_use:Grep",))])
        stop_sub(project, t, atype, f"a{i}")


def test_report_by_role(project, tmp_path):
    fill(project, tmp_path)
    data = json.loads(report(project, "--json"))
    assert data["coder"]["runs"] == 2 and data["coder"]["output"] == 150
    assert data["ИТОГО"]["output"] == 200
    assert data["ИТОГО"]["explore_calls"] == 4
    assert data["coder"]["cache_hit"] == round(2000 / 2220, 3)
    assert data["coder"]["cost_usd"] is None  # цен нет — стоимость неизвестна


def test_report_by_item_and_round(project, tmp_path):
    fill(project, tmp_path)
    by_item = json.loads(report(project, "--json", "--by", "item"))
    assert by_item["пункт 1"]["runs"] == 3 and by_item["пункт 2"]["runs"] == 1
    by_round = json.loads(report(project, "--json", "--by", "round"))
    assert "пункт 1 / круг 2 / coder" in by_round


def test_report_cost_only_with_prices(project, tmp_path):
    fill(project, tmp_path)
    prices = {"claude-sonnet-5-5": {"input": 2, "cache_write": 2.5, "cache_read": 0.1, "output": 10}}
    (project / ".orchestra/config.json").write_text(json.dumps({"prices": prices}))
    data = json.loads(report(project, "--json"))
    # 3 записи sonnet: вход 10, запись 100, чтение 1000, выход 100/50/20
    expected = (3 * (10 * 2 + 100 * 2.5 + 1000 * 0.1) + 170 * 10) / 1_000_000
    assert data["coder"]["cost_usd"] is not None
    assert abs(data["coder"]["cost_usd"] + data["tester"]["cost_usd"] - expected) < 1e-4
    assert data["reviewer"]["cost_usd"] is None   # у opus цены нет
    assert data["ИТОГО"]["cost_usd"] is None


def test_report_markdown(project, tmp_path):
    fill(project, tmp_path)
    out = report(project)
    assert "| coder |" in out and "**ИТОГО**" in out


def test_report_empty(project):
    assert "пуст" in report(project)
