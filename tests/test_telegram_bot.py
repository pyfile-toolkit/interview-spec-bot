import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import store

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_bot_with_fake_updates(
    tmp_path: Path,
    updates: list[dict],
    log_path: Path,
    wait_for_document: bool = True,
    timeout: float = 25.0,
) -> list[dict]:
    """Поднимает стенд Bot API и бота, прогоняет очередь апдейтов, возвращает журнал стенда.

    Ждём появления sendDocument в журнале, а не фиксированную паузу: иначе тест либо
    моргает (медленная машина), либо проходит по ошибке (бот умер, а мы смотрим пустой лог).
    Дополнительно возвращаем хвост вывода бота — по нему видно, что именно упало.
    """
    host = "127.0.0.1"
    port = _free_port()
    api_base = f"http://{host}:{port}/bot"

    queue_path = tmp_path / "updates.json"
    queue_path.write_text(json.dumps(updates, ensure_ascii=False), encoding="utf-8")

    env = os.environ.copy()
    env["TELEGRAM_BASE_URL"] = api_base
    env["TELEGRAM_BOT_TOKEN"] = "test:test"
    env["INTERVIEW_DB"] = str(tmp_path / "interview.db")
    env["PYTHONUNBUFFERED"] = "1"

    fake_api = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "fake_tg_api.py"),
         "--host", host, "--port", str(port), "--queue", str(queue_path), "--log", str(log_path)],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)

    bot_out = ""
    bot_proc = None
    try:
        # Запускаем как МОДУЛЬ: при запуске файлом ломаются относительные импорты (from . import ...).
        bot_proc = subprocess.Popen(
            [sys.executable, "-m", "app.telegram_bot"],
            cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        need = any(bool(u.get("message")) for u in updates) if wait_for_document else False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if need and any(x.get("method") == "sendDocument" for x in _read_log(log_path)):
                break
            if bot_proc.poll() is not None:
                break
            time.sleep(0.2)
    finally:
        if bot_proc is not None:
            bot_proc.terminate()
            try:
                bot_out = bot_proc.communicate(timeout=5)[0] or ""
            except subprocess.TimeoutExpired:
                bot_proc.kill()
                bot_out = (bot_proc.communicate()[0] or "")
        fake_api.terminate()
        try:
            fake_api.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            fake_api.kill()

    lines = _read_log(log_path)
    if not lines and bot_out:
        pytest.fail(f"бот не сделал ни одного запроса к стенду; вывод бота:\n{bot_out[-2000:]}")
    return lines


def _find_send_document(log_lines: list[dict]) -> dict | None:
    for x in log_lines:
        if x.get("method") == "sendDocument":
            return x
    return None


def test_full_pass_until_pdf(tmp_path: Path):
    store.reset()

    updates = [
        {"update_id": 1, "message": {"message_id": 10, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "/start"}},
        {"update_id": 2, "message": {"message_id": 20, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Телеграм-бот для записи клиентов в барбершоп, с выбором мастера и времени"}},
        {"update_id": 3, "message": {"message_id": 30, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Клиенты барбершопа, 300 человек в месяц; решает владелец салона"}},
        {"update_id": 4, "message": {"message_id": 40, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Запись на слот; напоминание за 2 часа; выгрузка записей в Excel по дням"}},
        {"update_id": 5, "message": {"message_id": 50, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Открываю бота, жму /start, выбираю мастера и время — запись появляется у администратора"}},
        {"update_id": 6, "message": {"message_id": 60, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "До 20 октября, привязано к открытию второго салона"}},
        {"update_id": 7, "message": {"message_id": 70, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Оплата 45 000 рублей по этапам, поддержка 3 месяца после сдачи"}},
    ]

    log_path = tmp_path / "tg_api_log.jsonl"
    lines = _run_bot_with_fake_updates(tmp_path, updates, log_path)

    doc = _find_send_document(lines)
    assert doc is not None

    payload = doc["payload"]
    fname = payload.get("filename")
    assert fname

    import tempfile as _tmp

    pdf_path = Path(_tmp.gettempdir()) / fname
    assert pdf_path.exists()
    assert pdf_path.read_bytes()[:4] == b"%PDF"


def test_short_answer_triggers_retry(tmp_path: Path):
    store.reset()

    updates = [
        {"update_id": 1, "message": {"message_id": 10, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "/start"}},
        {"update_id": 2, "message": {"message_id": 20, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "коротко"}},
        {"update_id": 3, "message": {"message_id": 30, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Телеграм-бот для записи клиентов в барбершоп, с выбором мастера и времени"}},
        {"update_id": 4, "message": {"message_id": 40, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Клиенты барбершопа, 300 человек в месяц; решает владелец салона"}},
        {"update_id": 5, "message": {"message_id": 50, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Запись на слот; напоминание за 2 часа; выгрузка записей в Excel по дням"}},
        {"update_id": 6, "message": {"message_id": 60, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Открываю бота, жму /start, выбираю мастера и время — запись появляется у администратора"}},
        {"update_id": 7, "message": {"message_id": 70, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "До 20 октября, привязано к открытию второго салона"}},
        {"update_id": 8, "message": {"message_id": 80, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Оплата 45 000 рублей по этапам, поддержка 3 месяца после сдачи"}},
    ]

    log_path = tmp_path / "tg_api_log.jsonl"
    lines = _run_bot_with_fake_updates(tmp_path, updates, log_path)

    # Бот должен отправить текст про минимум символов
    assert any(
        x.get("method") == "sendMessage"
        and "нужно минимум" in x.get("payload", {}).get("text", "")
        for x in lines
    )

    # и PDF должен появиться только после правильного ответа
    assert any(x.get("method") == "sendDocument" for x in lines)


def test_restart_obnuletsa_intervju(tmp_path: Path):
    store.reset()

    updates = [
        {"update_id": 1, "message": {"message_id": 10, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "/start"}},
        {"update_id": 2, "message": {"message_id": 20, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Телеграм-бот"}},
        {"update_id": 3, "message": {"message_id": 30, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "/restart"}},
        {"update_id": 4, "message": {"message_id": 40, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Телеграм-бот для записи клиентов в барбершоп, с выбором мастера и времени"}},
        {"update_id": 5, "message": {"message_id": 50, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Клиенты барбершопа, 300 человек в месяц; решает владелец салона"}},
        {"update_id": 6, "message": {"message_id": 60, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Запись на слот; напоминание за 2 часа; выгрузка записей в Excel по дням"}},
        {"update_id": 7, "message": {"message_id": 70, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Открываю бота, жму /start, выбираю мастера и время — запись появляется у администратора"}},
        {"update_id": 8, "message": {"message_id": 80, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "До 20 октября, привязано к открытию второго салона"}},
        {"update_id": 9, "message": {"message_id": 90, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 7, "is_bot": False}, "text": "Оплата 45 000 рублей по этапам, поддержка 3 месяца после сдачи"}},
    ]

    log_path = tmp_path / "tg_api_log.jsonl"
    lines = _run_bot_with_fake_updates(tmp_path, updates, log_path)

    # после /restart бот снова задаёт первый вопрос (содержит подстроку про «Что за продукт…»)
    assert any(
        x.get("method") == "sendMessage" and "Что за продукт" in x.get("payload", {}).get("text", "")
        for x in lines
    )


def test_missing_token_returns_code_2(monkeypatch):
    env = os.environ.copy()
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env["TELEGRAM_BASE_URL"] = "http://127.0.0.1:9999/bot"

    proc = subprocess.run(
        [sys.executable, "-m", "app.telegram_bot"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    assert proc.returncode == 2
    assert "TELEGRAM_BOT_TOKEN" in proc.stdout
