"""Вопросы интервью. Детерминированный список: интервью-бот ничего не «придумывает»."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    hint: str
    min_len: int = 10


QUESTIONS: tuple[Question, ...] = (
    Question(
        id="product",
        text="Что за продукт или услуга нужны? Опишите своими словами, как объяснили бы знакомому.",
        hint="Например: «телеграм-бот, который записывает клиентов на маникюр».",
        min_len=15,
    ),
    Question(
        id="audience",
        text="Для кого это? Кто будет этим пользоваться и кто принимает решение о покупке?",
        hint="Роль, примерное количество людей, их уровень техники.",
        min_len=10,
    ),
    Question(
        id="features",
        text="Какие 3 главные функции обязательны в первой версии? Нужны именно те, без которых запуск бессмыслен.",
        hint="Перечислите через точку с запятой, от самого важного.",
        min_len=20,
    ),
    Question(
        id="done",
        text="Как вы поймёте, что работа готова? Опишите проверяемый результат.",
        hint="Что вы будете делать сами, чтобы убедиться: «открываю бота, пишу /start, ...».",
        min_len=15,
    ),
    Question(
        id="deadline",
        text="К какой дате это нужно и есть ли жёсткие внешние события (запуск, презентация)?",
        hint="Дата или период, плюс что будет, если не успеть.",
        min_len=5,
    ),
    Question(
        id="budget",
        text="Какой бюджет и как удобно платить? Что входит в оплату помимо разработки?",
        hint="Диапазон в рублях, по этапам или целиком, нужна ли поддержка после сдачи.",
        min_len=5,
    ),
)

BY_ID = {q.id: q for q in QUESTIONS}


def next_question(answers: dict[str, str]) -> Question | None:
    """Первый вопрос, на который ещё нет ответа. None — интервью закончено."""
    for q in QUESTIONS:
        if q.id not in answers:
            return q
    return None


def is_complete(answers: dict[str, str]) -> bool:
    return next_question(answers) is None
