import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _wait_port(host: str, port: int, timeout_s: float = 5.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Не удалось дождаться порта {host}:{port}")


def _read_log_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    # Важно: никаких обращений к настоящему api.telegram.org
    host = "127.0.0.1"
    port = _free_port()
    api_base = f"http://{host}:{port}/bot"

    with tempfile.TemporaryDirectory(prefix="interview-spec-bot-e2e-") as td:
        td_path = Path(td)
        queue_path = td_path / "updates.json"
        log_path = td_path / "tg_api_log.jsonl"

        # Для диалога нам достаточно message.update: text+chat+user.
        user_id = 12345
        chat_id = 999
        def upd(text: str, update_id: int) -> dict:
            return {
                "update_id": update_id,
                "message": {
                    "message_id": update_id * 10,
                    "date": int(time.time()),
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
                    "text": text,
                },
            }

        updates = [
            upd("/start", 1),
            upd("коротко", 2),  # меньше минимума -> бот должен переспросить
            upd(
                "Телеграм-бот для записи клиентов в барбершоп, с выбором мастера и времени",
                3,
            ),
            upd("Клиенты барбершопа, 300 человек в месяц; решает владелец салона", 4),
            upd(
                "Запись на слот; напоминание за 2 часа; выгрузка записей в Excel по дням", 5
            ),
            upd(
                "Открываю бота, жму /start, выбираю мастера и время — запись появляется у администратора",
                6,
            ),
            upd("До 20 октября, привязано к открытию второго салона", 7),
            upd("Оплата 45 000 рублей по этапам, поддержка 3 месяца после сдачи", 8),
        ]

        queue_path.write_text(json.dumps(updates, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        env["TELEGRAM_BASE_URL"] = api_base
        env["TELEGRAM_BOT_TOKEN"] = "test:test"
        # Чтобы SQLite не мусорил репозиторий
        env["INTERVIEW_DB"] = str(td_path / "interview.db")

        # Запускаем стенд
        fake_api = subprocess.Popen(
            [
                sys.executable,
                str(REPO / "tools" / "fake_tg_api.py"),
                "--host",
                host,
                "--port",
                str(port),
                "--queue",
                str(queue_path),
                "--log",
                str(log_path),
            ],
            cwd=str(REPO),
            env=env,
        )

        try:
            _wait_port(host, port)

            # Запускаем контроллером модуля: при запуске файлом относительные импорты не работают.
            bot_proc = subprocess.Popen(
                [sys.executable, "-m", "app.telegram_bot"],
                cwd=str(REPO),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Ждём появления sendDocument в журнале, а не фиксированную паузу: так стенд
            # медленный не даёт ложный FAIL, а упавший бот — не даёт ложный PASS.
            bot_out = ""
            deadline = time.time() + 30
            try:
                while time.time() < deadline:
                    if any(x.get("method") == "sendDocument" for x in _read_log_lines(log_path)):
                        break
                    if bot_proc.poll() is not None:
                        break
                    time.sleep(0.2)
                bot_proc.terminate()
                try:
                    bot_out = bot_proc.communicate(timeout=5)[0] or ""
                except subprocess.TimeoutExpired:
                    bot_proc.kill()
                    bot_out = bot_proc.communicate()[0] or ""
            except Exception:
                bot_proc.kill()
                raise

            lines = _read_log_lines(log_path)
            send_doc = [x for x in lines if x.get("method") == "sendDocument"]

            # Должен быть хотя бы один sendDocument
            if not send_doc:
                # печатаем последние строки для диагностики
                tail = "\n".join([json.dumps(x, ensure_ascii=False) for x in lines[-10:]])
                print("FAIL: sendDocument не найдено. Последние записи лога:\n" + tail)
                if bot_out:
                    print("Вывод бота (хвост):\n" + bot_out[-1500:])
                return 1

            # Проверяем, что PDF реально читается из файла (бот пишет в /tmp)
            # В payload filename должно быть spec-<...>.pdf
            payload = send_doc[-1].get("payload", {})
            filename = payload.get("filename") or payload.get("document")
            if isinstance(filename, dict):
                filename = filename.get("file_name")
            # Мы передаём InputFile, поэтому payload содержит filename; проверим типично.
            if not filename:
                print("FAIL: В sendDocument нет filename в логе")
                return 1

            pdf_path = Path(tempfile.gettempdir()) / filename
            if not pdf_path.exists():
                print(f"FAIL: PDF не найден по пути {pdf_path}")
                return 1

            head = pdf_path.read_bytes()[:4]
            if head != b"%PDF":
                print(f"FAIL: Файл {pdf_path} не похож на PDF, первые байты: {head!r}")
                return 1

            # Проверка переспроса на короткий ответ: должен быть sendMessage с текстом про минимум
            short_ask = False
            for x in lines:
                if x.get("method") == "sendMessage":
                    txt = x.get("payload", {}).get("text", "")
                    if "нужно минимум" in txt and "символов" in txt:
                        short_ask = True
                        break

            if not short_ask:
                print("FAIL: не увидели переспрос при слишком коротком ответе")
                return 1

            print("PASS")
            return 0

        finally:
            fake_api.terminate()
            try:
                fake_api.wait(timeout=2)
            except subprocess.TimeoutExpired:
                fake_api.kill()


if __name__ == "__main__":
    raise SystemExit(main())
