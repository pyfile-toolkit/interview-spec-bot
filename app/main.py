"""Интервью → ТЗ. FastAPI-приложение: чат-интервью, PDF-ТЗ и Excel-выгрузка ответов.

Запуск: uvicorn app.main:app --reload
"""

import io
from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from openpyxl import Workbook
from pydantic import BaseModel

from . import store
from .pdf import build_pdf
from .questions import BY_ID, QUESTIONS, is_complete, next_question

app = FastAPI(title="Интервью → ТЗ", version="1.0.0")


class AnswerIn(BaseModel):
    session_id: str
    answer: str


def _state(sid: str) -> dict:
    session = store.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    answers = session["answers"]
    q = next_question(answers)
    return {
        "session_id": sid,
        "answers_count": len(answers),
        "total_questions": len(QUESTIONS),
        "complete": q is None,
        "question": None if q is None else {"id": q.id, "text": q.text, "hint": q.hint, "min_len": q.min_len},
        "pdf_url": f"/api/spec/{sid}.pdf" if q is None else None,
        "xlsx_url": f"/api/spec/{sid}.xlsx" if q is None else None,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.post("/api/session")
def create_session() -> dict:
    return _state(store.create_session())


@app.get("/api/session/{session_id}")
def session_state(session_id: str) -> dict:
    return _state(session_id)


@app.post("/api/answer")
def answer(payload: AnswerIn) -> dict:
    state = _state(payload.session_id)  # 404 для неизвестной сессии
    if state["complete"]:
        raise HTTPException(status_code=409, detail="Интервью уже закончено")
    text = payload.answer.strip()
    q = BY_ID[state["question"]["id"]]
    if not text:
        raise HTTPException(status_code=422, detail="Пустой ответ: напишите хотя бы одно предложение")
    if len(text) < q.min_len:
        raise HTTPException(status_code=422, detail=f"Слишком короткий ответ: нужно минимум {q.min_len} символов")
    store.save_answer(payload.session_id, q.id, text)
    state = _state(payload.session_id)
    if state["complete"]:
        store.mark_finished(payload.session_id)
    return state


def _answers_or_error(session_id: str) -> dict:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    if not is_complete(session["answers"]):
        raise HTTPException(status_code=409, detail="ТЗ появится после последнего вопроса интервью")
    return session["answers"]


@app.get("/api/spec/{session_id}.pdf")
def spec_pdf(session_id: str) -> Response:
    answers = _answers_or_error(session_id)
    body = build_pdf(answers, session_id)
    return Response(
        content=body,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="spec-{session_id[:8]}.pdf"'},
    )


@app.get("/api/spec/{session_id}.xlsx")
def spec_xlsx(session_id: str) -> Response:
    answers = _answers_or_error(session_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Ответы"
    ws.append(["Вопрос", "Ответ"])
    for q in QUESTIONS:
        ws.append([q.text, answers.get(q.id, "")])
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 80
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="answers-{session_id[:8]}.xlsx"'},
    )


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Интервью → ТЗ</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f5f6f8; color: #14161a; }
  main { max-width: 720px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #5b6472; margin-bottom: 16px; }
  #log { background: #fff; border: 1px solid #e2e5ea; border-radius: 12px; padding: 16px; min-height: 220px; }
  .q { font-weight: 600; margin: 12px 0 2px; }
  .a { background: #eef2ff; border-radius: 8px; padding: 8px 10px; margin: 4px 0 12px; white-space: pre-wrap; }
  .hint { color: #78808d; font-size: 14px; }
  form { display: flex; gap: 8px; margin-top: 12px; }
  input { flex: 1; padding: 12px; border: 1px solid #cfd4dc; border-radius: 10px; font: inherit; }
  button { padding: 12px 18px; border: 0; border-radius: 10px; background: #2f5cff; color: #fff; font: inherit; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  #result a { display: inline-block; margin-right: 12px; margin-top: 12px; }
  .err { color: #b3261e; }
</style>
</head>
<body>
<main>
  <h1>Интервью → ТЗ</h1>
  <div class="sub">Шесть вопросов. На выходе — PDF с техническим заданием и Excel с ответами.</div>
  <div id="log"></div>
  <form id="f">
    <input id="i" autocomplete="off" placeholder="Ваш ответ…">
    <button id="b" type="submit">Ответить</button>
  </form>
  <div id="result"></div>
</main>
<script>
const log = document.getElementById('log');
const f = document.getElementById('f');
const i = document.getElementById('i');
const b = document.getElementById('b');
const result = document.getElementById('result');
let sid = null, current = null;

function add(cls, text) {
  const d = document.createElement('div');
  d.className = cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

function render(st) {
  if (st.question) {
    current = st.question;
    add('q', st.question.text);
    add('hint', 'Подсказка: ' + st.question.hint);
    i.placeholder = 'Минимум ' + st.question.min_len + ' символов…';
  }
  if (st.complete) {
    current = null;
    f.style.display = 'none';
    add('q', 'Интервью закончено.');
    result.innerHTML = '<a href="' + st.pdf_url + '">Скачать ТЗ (PDF)</a><a href="' + st.xlsx_url + '">Выгрузка ответов (Excel)</a>';
  }
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || ('Ошибка ' + r.status));
  return data;
}

(async () => {
  try {
    const st = await api('/api/session', {});
    sid = st.session_id;
    render(st);
  } catch (e) { add('err', 'Не удалось начать интервью: ' + e.message); }
})();

f.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const text = i.value.trim();
  if (!text || !sid) return;
  b.disabled = true;
  try {
    add('a', text);
    i.value = '';
    const st = await api('/api/answer', { session_id: sid, answer: text });
    render(st);
  } catch (e) {
    add('err', e.message);
  } finally {
    b.disabled = false;
    i.focus();
  }
});
</script>
</body>
</html>
"""
