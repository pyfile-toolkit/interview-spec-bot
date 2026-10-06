"""Генерация PDF с техническим заданием из ответов интервью.

Кириллица: fpdf2 не умеет встроенные шрифты для русского, поэтому берём TTF.
Шрифт ищется по списку путей, первым делом — в самой папке приложения (fonts/).
"""

import os
import re
from datetime import datetime, timezone

from fpdf import FPDF

from .questions import QUESTIONS

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_CANDIDATES = [
    os.path.join(HERE, "fonts", "DejaVuSans.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]

NEXT = {"new_x": "LMARGIN", "new_y": "NEXT"}


def font_path() -> str:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError(
        "Не найден шрифт с кириллицей. Положите DejaVuSans.ttf в app/fonts/ "
        "(или установите пакет fonts-dejavu)."
    )


def _clarify_points(answers: dict[str, str]) -> list[str]:
    """Детерминированные подсказки: то, что в ответах заказчика осталось размытым."""
    out: list[str] = []
    budget = answers.get("budget", "")
    if not re.search(r"\d", budget):
        out.append("Бюджет: в ответе нет суммы — зафиксировать диапазон и порядок оплаты (этапы или целиком).")
    deadline = answers.get("deadline", "")
    if not re.search(
        r"\d|\b(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|недел|месяц)",
        deadline,
        re.I,
    ):
        out.append("Срок: не названа дата — согласовать календарную дату готовности первой версии.")
    features = answers.get("features", "")
    parts = [p for p in re.split(r"[;\n]+", features) if p.strip()]
    if len(parts) < 3:
        out.append(f"Первая версия: перечислено функций — {len(parts)}; добить список до трёх обязательных.")
    done = answers.get("done", "")
    if not re.search(r"(открыва|нажима|пиш|вижу|получа|проверя|смотр|скачива)", done, re.I):
        out.append("Критерий готовности: описать проверяемое действие («открываю … и вижу …»), иначе приёмка спорна.")
    audience = answers.get("audience", "")
    if not re.search(r"\d", audience):
        out.append("Пользователи: не указано количество — уточнить, чтобы выбрать хостинг и нагрузку.")
    if not out:
        out.append("Размытых мест не осталось: согласовать состав первой версии письменно и стартовать.")
    return out[:4]


def build_pdf(answers: dict[str, str], session_id: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    face = font_path()
    pdf.add_font("dejavu", "", face)
    pdf.add_font("dejavu", "B", face)
    pdf.set_margins(18, 18, 18)

    pdf.set_font("dejavu", "B", 16)
    product = (answers.get("product") or "проект").strip().split("\n")[0]
    pdf.multi_cell(0, 9, f"Техническое задание: {product[:70]}", **NEXT)
    pdf.ln(2)

    pdf.set_font("dejavu", "", 10)
    pdf.set_text_color(90)
    pdf.multi_cell(0, 6, "Составлено интервью-ботом, интервью → ТЗ (демо)", **NEXT)
    pdf.multi_cell(
        0,
        6,
        f"Дата: {datetime.now(timezone.utc).strftime('%d.%m.%Y')} · сессия {session_id[:8]}",
        **NEXT,
    )
    pdf.set_text_color(0)
    pdf.ln(4)

    for q in QUESTIONS:
        answer = (answers.get(q.id) or "").strip()
        pdf.set_font("dejavu", "B", 11)
        pdf.multi_cell(0, 7, q.text, **NEXT)
        pdf.set_font("dejavu", "", 11)
        pdf.multi_cell(0, 7, answer or "—", **NEXT)
        pdf.ln(2)

    pdf.ln(2)
    pdf.set_font("dejavu", "B", 13)
    pdf.multi_cell(0, 8, "Что уточнить у заказчика", **NEXT)
    pdf.set_font("dejavu", "", 11)
    for point in _clarify_points(answers):
        pdf.multi_cell(0, 7, "• " + point, **NEXT)

    return bytes(pdf.output())
