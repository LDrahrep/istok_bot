from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


class Buttons:
    # Главное меню
    I_AM_DRIVER = "🚗 Я водитель"
    I_AM_PASSENGER = "🧍 Я пассажир"
    MY_RECORD = "📋 Моя запись"
    SEARCH = "🔍 Найти"
    CANCEL = "↩️ Отмена"

    # Телефон
    SHARE_PHONE = "📱 Поделиться номером"

    # Поиск: кого
    FIND_DRIVER = "🚗 Водителя"
    FIND_PASSENGER = "🧍 Пассажира"
    # Поиск: признак
    BY_CITY = "🏙 По городу"
    BY_STATE = "🗺 По штату"
    BY_HOTEL = "🏨 По отелю"


@dataclass
class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
    GOOGLE_CREDENTIALS: str = os.getenv("GOOGLE_CREDENTIALS", "")

    PEOPLE_SHEET: str = os.getenv("PEOPLE_SHEET", "people")
    CITIES_SHEET: str = os.getenv("CITIES_SHEET", "cities")
    HOTELS_SHEET: str = os.getenv("HOTELS_SHEET", "hotels")

    PAGE_SIZE: int = int(os.getenv("PAGE_SIZE", "5"))

    ADMIN_USER_IDS: List[int] = None
    ADMIN_CHAT_ID: int = 0

    def __post_init__(self):
        raw_admins = os.getenv("ADMIN_USER_IDS", "").strip()
        self.ADMIN_USER_IDS = [
            int(x.strip()) for x in raw_admins.split(",") if x.strip().isdigit()
        ] if raw_admins else []

        raw_chat = os.getenv("ADMIN_CHAT_ID", "").strip()
        if raw_chat and raw_chat.lstrip("-").isdigit():
            self.ADMIN_CHAT_ID = int(raw_chat)
        else:
            self.ADMIN_CHAT_ID = self.ADMIN_USER_IDS[0] if self.ADMIN_USER_IDS else 0
