"""Тесты scripts/project_map.py. Запуск: python3 -m pytest dev/ -q"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins/orchestra/scripts/project_map.py"


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / ".orchestra").mkdir(parents=True)
    (p / ".orchestra/config.json").write_text(json.dumps({"tests_dir": "tests", "source_dirs": ["src"], "map_min_files": 0}))
    (p / "src/app/api").mkdir(parents=True)
    (p / "src/app/__init__.py").write_text("")
    (p / "src/app/api/__init__.py").write_text("")
    (p / "src/app/main.py").write_text('"""Точка входа FastAPI."""\nfrom fastapi import FastAPI\napp = FastAPI()\n')
    (p / "src/app/api/orders.py").write_text(
        '"""Эндпоинты заказов."""\n'
        "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
        '@router.get("/orders")\ndef list_orders():\n    return []\n\n'
        '@router.post("/orders")\nasync def create_order():\n    return {}\n\n'
        "def _private():\n    pass\n"
    )
    (p / "src/app/service.py").write_text(
        "class OrderService:\n    def create(self):\n        pass\n    def _hidden(self):\n        pass\n\n"
        "TRANSITIONS = {}\n__all__ = []\nT = 1\n\ndef helper():\n    pass\n"
    )
    (p / "src/app/broken.py").write_text("def f(:\n")
    (p / "Dockerfile").write_text("FROM python:3.12\n")
    (p / "README.md").write_text("readme")
    (p / "tests").mkdir()
    (p / "tests/conftest.py").write_text(
        "import pytest\n\n@pytest.fixture(scope='session')\ndef client():\n    \"\"\"HTTP-клиент приложения.\"\"\"\n    return 1\n"
    )
    (p / "tests/test_orders.py").write_text(
        "from app.api.orders import list_orders\n\n"
        "def test_list(client):\n    assert list_orders() == []\n\n"
        "class TestCreate:\n    def test_ok(self, tmp_path):\n        pass\n"
    )
    return p


def run(project, *args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(project)],
                          input=stdin, capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def read_all(project, index, sub):
    """Индекс и все файлы подробностей одной строкой — для проверок содержимого."""
    base = project / ".orchestra/map"
    parts = [(base / index).read_text(encoding="utf-8")]
    parts += [f.read_text(encoding="utf-8") for f in sorted((base / sub).glob("*.md"))] if (base / sub).is_dir() else []
    return "\n".join(parts)


def read_code_md(project):
    return read_all(project, "code.md", "code")


def read_tests_md(project):
    return read_all(project, "tests.md", "tests")


def test_build_code_map(project):
    res = run(project, "build")
    assert res["code_files"] == 7 and res["test_files"] == 2
    md = read_code_md(project)
    assert "# src/app/" in md and "# src/app/api/" in md
    assert "`orders.py` — Эндпоинты заказов." in md
    assert "`list_orders()`" in md and "_private" not in md
    assert "`OrderService`(create)" in md and "_hidden" not in md
    assert "`TRANSITIONS`" in md and "`__all__`" not in md and "`T`" not in md
    assert "`__init__.py`" not in md   # пустые пакетные файлы не засоряют карту
    assert "GET /orders → `list_orders` · `src/app/api/orders.py`" in md
    assert "POST /orders → `create_order`" in md
    assert "**Точки входа:** `src/app/main.py`" in md
    assert "`Dockerfile`" in md and "README" not in md
    assert "`broken.py` — **нет описания** · `f()`" in md   # не разобрался AST — запасной разбор регуляркой


def test_need_description_only_without_docstring(project):
    res = run(project, "build")
    assert sorted(res["need_description"]) == ["src/app/broken.py", "src/app/service.py"]
    assert "src/app/__init__.py" not in res["need_description"]   # пустой __init__
    assert "Dockerfile" not in res["need_description"]


def test_describe_and_staleness(project):
    run(project, "build")
    res = run(project, "describe", stdin=json.dumps({"./src/app/service.py": "Бизнес-логика заказов", "nope.py": "x"}))
    assert res["unknown"] == ["nope.py"]
    assert "src/app/service.py" not in res["need_description"]
    assert "`service.py` — Бизнес-логика заказов" in read_code_md(project)
    # файл изменился — описание устарело и снова нужно
    (project / "src/app/service.py").write_text("def helper():\n    return 2\n")
    res = run(project, "build")
    assert res["changed"] == ["src/app/service.py"]
    assert "src/app/service.py" in res["need_description"]
    assert "(устарело) Бизнес-логика заказов" in read_code_md(project)


def test_stale_description_not_required_when_docstring(project):
    run(project, "build")
    run(project, "describe", stdin=json.dumps({"src/app/api/orders.py": "Заказы: HTTP"}))
    (project / "src/app/api/orders.py").write_text('"""Эндпоинты заказов v2."""\n')
    res = run(project, "build")
    assert "src/app/api/orders.py" not in res["need_description"]


def test_added_and_removed(project):
    run(project, "build")
    (project / "src/app/new.py").write_text('"""Новый модуль."""\n')
    (project / "src/app/broken.py").unlink()
    res = run(project, "build")
    assert res["added"] == ["src/app/new.py"] and res["removed"] == ["src/app/broken.py"]


def test_first_build_does_not_report_everything_as_added(project):
    assert run(project, "build")["added"] == []


def test_tests_map(project):
    run(project, "build")
    md = read_tests_md(project)
    assert "тестов: 2" in md
    assert "`client` (tests/conftest.py, session) — HTTP-клиент приложения." in md
    assert "`tests/test_orders.py` — 2 тест(ов) · модули: `app.api.orders` · фикстуры: client, tmp_path" in md
    assert "test_list, TestCreate.test_ok" in md
    untested = md.split("**Модули без тестов**")[1]
    assert "`app.service`" in untested and "`app.main`" in untested and "`app.api.orders`" not in untested
    assert "python -m pytest tests" in md


def test_tests_map_has_no_code_symbols(project):
    """tests.md открыт тестеру — в нём не должно быть устройства реализации."""
    run(project, "build")
    md = read_tests_md(project)
    assert "OrderService" not in md and "GET /orders" not in md and "Эндпоинты заказов" not in md


def test_last_run_in_tests_map(project):
    (project / ".orchestra/reports").mkdir()
    (project / ".orchestra/reports/last-test-run.json").write_text(json.dumps({
        "status": "failed", "passed": 1, "failed": 1, "errors": 0, "skipped": 0, "finished_at": "2026-11-08T10:00:00",
        "failures": [{"test": "tests/test_orders.py::test_list", "message": "assert 1 == 2"}]}))
    run(project, "build")
    md = read_tests_md(project)
    assert "failed — 1 прошло, 1 упало" in md and "test_list` — assert 1 == 2" in md


def test_whole_project_when_no_source_dirs(tmp_path):
    p = tmp_path / "flat"
    (p / ".orchestra").mkdir(parents=True)
    (p / ".orchestra/config.json").write_text(json.dumps({"map_min_files": 0}))
    (p / "mybot").mkdir()
    (p / "mybot/handlers.py").write_text('"""Хендлеры бота."""\n')
    (p / ".venv/lib").mkdir(parents=True)
    (p / ".venv/lib/junk.py").write_text("")
    run(p, "build")
    md = read_code_md(p)
    assert "Хендлеры бота" in md and "junk" not in md


def test_need_command(project):
    assert sorted(run(project, "need")["need_description"]) == ["src/app/broken.py", "src/app/service.py"]


def test_requires_orchestra_dir(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "build", "--root", str(tmp_path)],
                          capture_output=True, encoding="utf-8")
    assert proc.returncode == 1 and "error" in json.loads(proc.stdout)


def test_nested_routes_fields_and_enums(project):
    (project / "src/app/models.py").write_text(
        '"""Модели."""\nfrom dataclasses import dataclass\nfrom enum import Enum\n\n'
        "class Status(str, Enum):\n    OPEN = 'open'\n    CLOSED = 'closed'\n\n"
        "@dataclass\nclass Ticket:\n    id: int\n    title: str\n    _secret: str = ''\n    def close(self):\n        pass\n"
    )
    (project / "src/app/api/users.py").write_text(
        '"""Эндпоинты пользователей."""\n\ndef register(router):\n'
        '    @router.post("/users")\n    def create_user(req):\n        return 1\n'
    )
    run(project, "build")
    md = read_code_md(project)
    assert "`Status`{OPEN, CLOSED}" in md
    assert "`Ticket`{id, title}(close)" in md and "_secret" not in md
    assert "POST /users → `create_user` · `src/app/api/users.py`" in md


def test_small_project_skips_map(tmp_path):
    p = tmp_path / "small"
    (p / ".orchestra").mkdir(parents=True)
    (p / "src").mkdir()
    for i in range(3):
        (p / f"src/m{i}.py").write_text('"""Модуль."""\n')
    res = run(p, "build")
    assert res["enabled"] is False and res["code_files"] == 3 and res["threshold"] == 150
    assert not (p / ".orchestra/map").exists()
    forced = subprocess.run([sys.executable, str(SCRIPT), "build", "--force", "--root", str(p)],
                            capture_output=True, encoding="utf-8")
    assert json.loads(forced.stdout)["enabled"] is True
    assert (p / ".orchestra/map/code.md").exists()


def test_two_level_layout(project):
    res = run(project, "build")
    base = project / ".orchestra/map"
    index = (base / "code.md").read_text(encoding="utf-8")
    # индекс: папка одной строкой со ссылкой на подробности, без символов файлов
    assert "- `src/app/api/` (1) · orders → `code/src.app.api.md`" in index
    assert "OrderService" not in index and "list_orders()" not in index
    assert (base / "code/src.app.api.md").exists() and (base / "code/src.app.md").exists()
    tindex = (base / "tests.md").read_text(encoding="utf-8")
    assert "`src/app/api/` — тестовых файлов: 1, тестов: 2 → `tests/src.app.api.md`" in tindex
    assert "test_list" not in tindex
    assert "- `tests/conftest.py`: client" in tindex
    assert res["map_files"] >= 5


def test_removed_dirs_drop_detail_files(project):
    run(project, "build")
    assert (project / ".orchestra/map/code/src.app.api.md").exists()
    for f in (project / "src/app/api").iterdir():
        f.unlink()
    (project / "src/app/api").rmdir()
    (project / "tests/test_orders.py").unlink()
    run(project, "build")
    assert not (project / ".orchestra/map/code/src.app.api.md").exists()
    assert not (project / ".orchestra/map/tests/src.app.api.md").exists()


def test_tests_attributed_to_rarest_import(tmp_path):
    """Тест, который строит сущности, относится к проверяемому сервису, а не к пакету сущностей."""
    p = tmp_path / "proj"
    (p / ".orchestra").mkdir(parents=True)
    (p / ".orchestra/config.json").write_text(json.dumps({"map_min_files": 0}))
    (p / "src/app/entities").mkdir(parents=True)
    (p / "src/app/services/pay").mkdir(parents=True)
    (p / "src/app/entities/user.py").write_text('"""Пользователь."""\n')
    (p / "src/app/services/orders.py").write_text('"""Заказы."""\n')
    (p / "src/app/services/pay/__init__.py").write_text('"""Оплата."""\nfrom .core import pay\n')
    (p / "src/app/services/pay/core.py").write_text('"""Ядро оплаты."""\ndef pay():\n    pass\n')
    (p / "tests").mkdir()
    for i in range(3):
        (p / f"tests/test_user{i}.py").write_text("from app.entities.user import User\n\ndef test_x():\n    pass\n")
    (p / "tests/test_orders.py").write_text(
        "from app.entities.user import User\nfrom app.services.orders import create\n\ndef test_o():\n    pass\n")
    (p / "tests/test_pay.py").write_text("from app.services.pay import pay\n\ndef test_p():\n    pass\n")
    run(p, "build")
    services = (p / ".orchestra/map/tests/src.app.services.md").read_text(encoding="utf-8")
    entities = (p / ".orchestra/map/tests/src.app.entities.md").read_text(encoding="utf-8")
    assert "test_orders.py" in services and "test_orders.py" not in entities
    pay = (p / ".orchestra/map/tests/src.app.services.pay.md").read_text(encoding="utf-8")
    assert "test_pay.py" in pay
    assert "Модули без тестов" not in pay   # пакет покрыт через реэкспорт в __init__.py


def test_telegram_handlers(project):
    (project / "src/app/bot.py").write_text(
        '"""Бот."""\nfrom aiogram import Router\nrouter = Router()\n\n'
        '@router.message(Command("start"))\nasync def start(m):\n    pass\n\n'
        '@router.callback_query(F.data == "ok")\nasync def ok(c):\n    pass\n'
    )
    run(project, "build")
    index = (project / ".orchestra/map/code.md").read_text(encoding="utf-8")
    assert "**Обработчики Telegram:** 2" in index
    detail = (project / ".orchestra/map/code/src.app.md").read_text(encoding="utf-8")
    assert "tg:message Command('start') → `start`" in detail
    assert "tg:callback_query F.data == 'ok' → `ok`" in detail
