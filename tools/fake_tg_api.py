import http.server
import json
import os
import re
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeTgState:
    token: str = "test:test"
    queue_file: str | None = None
    log_file: str = "tg_api_log.jsonl"
    incoming: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _read_queue(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Ожидаем список update-объектов (без top-level update_id тоже ок)
    return data


def _parse_multipart(raw: bytes, ctype: str) -> dict[str, Any]:
    """Очень простой разбор multipart/form-data: текстовые поля + имена файлов.

    Нужен только для журнала: sendDocument уходит как multipart, и без этого
    в логе стояло бы `{}` — то есть проверять было бы нечего.
    """
    boundary = ""
    for part in ctype.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part.split("=", 1)[1].strip('"')
    out: dict[str, Any] = {}
    if not boundary:
        return out
    for chunk in raw.split(b"--" + boundary.encode()):
        if b"\r\n\r\n" not in chunk:
            continue
        head, _, body = chunk.partition(b"\r\n\r\n")
        head_txt = head.decode("utf-8", "replace")
        name = None
        for token in head_txt.split(";"):
            token = token.strip()
            if token.startswith("name="):
                name = token.split("=", 1)[1].strip('"')
        if not name:
            continue
        if "filename=" in head_txt:
            out[name] = head_txt.split("filename=", 1)[1].split(";")[0].strip('"').strip()
            out.setdefault("__files__", []).append(name)
            out[name + "_bytes"] = len(body.rstrip(b"\r\n"))
        else:
            out[name] = body.rstrip(b"\r\n").decode("utf-8", "replace")
    return out


def _parse_multipart(raw: bytes, boundary_raw: str) -> dict[str, Any]:
    """Простой разбор multipart/form-data: текстовые поля + имена файлов.

    Нужен только для журнала: sendDocument уходит как multipart, и без этого
    в логе стояло бы `{}` — проверять было бы нечего.
    """
    boundary = b"--" + boundary_raw.strip('"').encode("utf-8")
    out: dict[str, Any] = {}
    for part in raw.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        head_txt = head.decode("utf-8", "replace")
        name_match = re.search(r'name="([^"]+)"', head_txt)
        if not name_match:
            continue
        name = name_match.group(1)
        fname = re.search(r'filename="([^"]*)"', head_txt)
        if fname is not None:
            out[name] = fname.group(1)
            out[name + "_bytes"] = len(body.rstrip(b"\r\n"))
            out.setdefault("__files__", []).append(name)
            if name == "document" and "filename" not in out:
                out["filename"] = fname.group(1)
        else:
            out[name] = body.rstrip(b"\r\n").decode("utf-8", "replace")
    return out


def _parse_form(data: bytes) -> dict[str, Any]:
    """application/x-www-form-urlencoded (именно так python-telegram-bot шлёт обычные методы)."""
    from urllib.parse import parse_qs

    parsed = parse_qs(data.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def _normalize_update(update: dict[str, Any]) -> dict[str, Any]:
    """Доводит апдейт до вида, который реально шлёт Telegram.

    1) Для команд добавляет разметку `entities: [{type: bot_command}]`: на неё смотрит
       `CommandHandler`, иначе `/start` улетает в текстовый обработчик.
    2) Дополняет обязательные поля отправителя (`is_bot`, `first_name`): без них
       python-telegram-bot просто не разберёт `from` и апдейт потеряется.
    """
    msg = update.get("message")
    if not isinstance(msg, dict):
        return update

    msg = dict(msg)
    sender = msg.get("from")
    if isinstance(sender, dict):
        sender = dict(sender)
        sender.setdefault("is_bot", False)
        sender.setdefault("first_name", "tester")
        msg["from"] = sender

    text = msg.get("text") or ""
    if text.startswith("/") and not msg.get("entities"):
        command = text.split()[0] if text.split() else text
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]

    update = dict(update)
    update["message"] = msg
    return update


def run_server(host: str, port: int, state: FakeTgState) -> None:
    state.incoming = _read_queue(state.queue_file)

    if not state.log_file:
        raise ValueError("log_file не задан")

    # очищаем лог
    os.makedirs(os.path.dirname(os.path.abspath(state.log_file)), exist_ok=True)
    with open(state.log_file, "w", encoding="utf-8") as f:
        pass

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _collect(self, payload: dict[str, Any]) -> None:
            rec = {"ts": time.time(), **payload}
            with open(state.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            if not raw and (self.headers.get("Transfer-Encoding", "").lower() == "chunked"):
                # httpx может присылать chunked-тело: тогда Content-Length нет,
                # и без этого разбора payload выглядел бы пустым ({}) в журнале.
                chunks = []
                while True:
                    line = self.rfile.readline().strip()
                    size = int(line.split(b";")[0] or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.readline()
                raw = b"".join(chunks)
            ctype = (self.headers.get("Content-Type") or "").lower()
            try:
                if "application/x-www-form-urlencoded" in ctype:
                    data = _parse_form(raw)
                elif "multipart/form-data" in ctype:
                    boundary = ""
                    for token in ctype.split(";"):
                        token = token.strip()
                        if token.startswith("boundary="):
                            boundary = token.split("=", 1)[1]
                    data = _parse_multipart(raw, boundary)
                elif "application/json" in ctype:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                else:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                data = {}

            if not self.path.startswith("/"):
                self._send_json(404, {"ok": False, "description": "bad path"})
                return

            parts = self.path.strip("/").split("/")
            # /bot<TOKEN>/<method>
            if len(parts) < 2:
                self._send_json(404, {"ok": False})
                return

            bot_part = parts[0]
            method = parts[1]

            if not bot_part.startswith("bot"):
                self._send_json(404, {"ok": False})
                return

            if method == "getMe":
                # Полноценный User: PTB строит объект из ответа и требует обязательные поля.
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "id": 1,
                            "is_bot": True,
                            "first_name": "fake",
                            "username": "fake_test_bot",
                            "can_join_groups": True,
                            "can_read_all_group_messages": False,
                            "supports_inline_queries": False,
                        },
                    },
                )
                return

            if method == "getUpdates":
                # Забираем до 100 обновлений из очереди.
                with state.lock:
                    updates = state.incoming[:100]
                    state.incoming = state.incoming[100:]

                # telegram ожидает каждый update с update_id
                normalized = []
                for i, u in enumerate(updates):
                    uu = _normalize_update(dict(u))
                    if "update_id" not in uu:
                        uu["update_id"] = int(time.time()) + i
                    normalized.append(uu)

                # Пишем в журнал, ЧТО именно отдали: без этого не видно, почему бот
                # ответил не то (например, пришёл пустой текст или всё одним пакетом).
                self._collect(
                    {
                        "method": method,
                        "payload": {
                            "offset": data.get("offset"),
                            "count": len(normalized),
                            "texts": [
                                (u.get("message") or {}).get("text") for u in normalized
                            ],
                        },
                    }
                )
                self._send_json(200, {"ok": True, "result": normalized})
                return

            if method == "sendMessage":
                self._collect({"method": method, "payload": data})
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "message_id": 1000,
                            "date": int(time.time()),
                            "chat": {"id": data.get("chat_id", 0), "type": "private"},
                            "text": data.get("text", ""),
                        },
                    },
                )
                return

            if method == "sendDocument":
                self._collect({"method": method, "payload": data})
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "message_id": 1001,
                            "date": int(time.time()),
                            "chat": {"id": data.get("chat_id", 0), "type": "private"},
                            "caption": data.get("caption", ""),
                            "document": {
                                "file_id": "fakefile",
                                "file_unique_id": "fakefile-1",
                                "file_name": data.get("filename") or data.get("document") or "file.pdf",
                                "file_size": 1,
                            },
                        },
                    },
                )
                return

            if method in {"deleteWebhook", "setMyCommands", "getWebhookInfo"}:
                self._collect({"method": method, "payload": data})
                self._send_json(200, {"ok": True, "result": True})
                return

            # unknown
            self._send_json(404, {"ok": False, "description": f"unknown method {method}"})

        def do_GET(self) -> None:  # noqa: N802
            # Для setMyCommands/прочего обычно POST, но на всякий.
            self._send_json(404, {"ok": False})

    with _Server((host, port), Handler) as httpd:
        httpd.serve_forever()


class _Server(socketserver.ThreadingTCPServer):
    """Потоковый сервер: бот может опрашивать getUpdates и одновременно грузить документ."""

    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--queue", help="json файл со списком update-объектов")
    ap.add_argument("--log", default="tg_api_log.jsonl")
    args = ap.parse_args()

    st = FakeTgState(queue_file=args.queue, log_file=args.log)
    run_server(args.host, args.port, st)
