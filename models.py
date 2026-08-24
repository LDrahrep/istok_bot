from __future__ import annotations

import re
from dataclasses import dataclass


def normalize_text(s: str) -> str:
    """Регистронезависимая нормализация: убираем невидимые символы и лишние
    пробелы. Для матчинга городов/штатов/отелей."""
    s = (s or "").replace(" ", " ").replace("​", "").replace("﻿", "")
    return " ".join(s.split()).casefold()


def clean_name(s: str) -> str:
    """Аккуратное имя: без невидимых символов, одиночные пробелы по краям срезаны."""
    s = (s or "").replace(" ", " ").replace("​", "").replace("﻿", "")
    return " ".join(s.split())


# --------------------------------------------------------------------------
# Парсер телефона (US). Прощающий: срезает всё кроме цифр, понимает +1 / 1.
# Возвращает dict: {"ok": True, "e164": "+1XXXXXXXXXX", "display": "+1 (XXX) XXX-XXXX"}
# или {"ok": False, "error": "<понятная подсказка>"}.
# --------------------------------------------------------------------------

def parse_us_phone(raw: str) -> dict:
    digits = re.sub(r"\D", "", raw or "")

    # ведущая 1 (код страны) — отбрасываем
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]

    if len(digits) != 10:
        n = len(digits)
        if n == 0:
            return {"ok": False, "error": "Не вижу цифр номера. Введи номер США, пример: 415 555 0123"}
        return {
            "ok": False,
            "error": f"Нужно 10 цифр номера (США), а ты ввёл {n}. Пример: 415 555 0123",
        }

    area, exch, sub = digits[0:3], digits[3:6], digits[6:10]
    if area[0] in "01" or exch[0] in "01":
        return {
            "ok": False,
            "error": "Код города и номер не могут начинаться с 0 или 1. Проверь номер. Пример: 415 555 0123",
        }

    return {
        "ok": True,
        "e164": "+1" + digits,
        "display": f"+1 ({area}) {exch}-{sub}",
    }


def format_phone_display(stored: str) -> str:
    """Показ хранимого +1XXXXXXXXXX как +1 (XXX) XXX-XXXX. Иначе — как есть."""
    r = parse_us_phone(stored or "")
    return r["display"] if r.get("ok") else (stored or "")


# --------------------------------------------------------------------------
# Парсер числа мест: вытаскивает число, проверяет диапазон 1..8.
# --------------------------------------------------------------------------

def parse_seats(raw: str) -> int | None:
    m = re.search(r"\d+", raw or "")
    if not m:
        return None
    v = int(m.group())
    return v if 1 <= v <= 8 else None


TRUE_SET = {"true", "1", "yes", "да", "✓", "x"}


def as_bool(raw) -> bool:
    return str(raw or "").strip().casefold() in TRUE_SET


@dataclass
class Person:
    tg_id: int
    name: str = ""
    username: str = ""
    city: str = ""
    state: str = ""
    hotel: str = ""
    car: str = ""
    seats: str = ""
    phone: str = ""
    is_driver: bool = False
    is_passenger: bool = False
    updated_at: str = ""
    is_active: bool = True

    @staticmethod
    def from_row(row: dict) -> "Person | None":
        tg_raw = str(row.get("telegramID") or "").strip()
        if not tg_raw.isdigit():
            return None
        return Person(
            tg_id=int(tg_raw),
            name=(row.get("Name") or "").strip(),
            username=(row.get("Username") or "").strip(),
            city=(row.get("City") or "").strip(),
            state=(row.get("State") or "").strip(),
            hotel=(row.get("Hotel") or "").strip(),
            car=(row.get("Car") or "").strip(),
            seats=(row.get("Seats") or "").strip(),
            phone=(row.get("Phone") or "").strip(),
            is_driver=as_bool(row.get("IsDriver")),
            is_passenger=as_bool(row.get("IsPassenger")),
            updated_at=(row.get("UpdatedAt") or "").strip(),
            is_active=str(row.get("isActive") or "TRUE").strip().casefold() != "false",
        )
