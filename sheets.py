from __future__ import annotations

import time
import logging
from datetime import datetime, timezone
from typing import Optional

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

from models import Person, normalize_text

logger = logging.getLogger(__name__)

_RETRY_MAX = 3
_RETRY_BASE_WAIT = 10
_OP_CACHE_TTL = 3  # сек, инвалидируется после записи

PEOPLE_HEADERS = [
    "Name", "telegramID", "Username", "City", "State", "Hotel",
    "Car", "Seats", "Phone", "IsDriver", "IsPassenger", "UpdatedAt", "isActive",
]


class SheetManager:
    def __init__(self, config):
        self.config = config
        self.client = self._build_client()
        self._spreadsheet = None
        self._ws_cache: dict[str, object] = {}
        self._op_cache: dict[str, tuple[float, list]] = {}

    # ---- low-level ----
    def _build_client(self):
        import json
        info = json.loads(self.config.GOOGLE_CREDENTIALS)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return gspread.authorize(creds)

    def _retry(self, fn):
        retriable = {429, 500, 502, 503, 504}
        for attempt in range(_RETRY_MAX + 1):
            try:
                return fn()
            except APIError as e:
                if e.response.status_code in retriable and attempt < _RETRY_MAX:
                    time.sleep((attempt + 1) * _RETRY_BASE_WAIT)
                    self._spreadsheet = None
                    continue
                raise

    def _open(self):
        if self._spreadsheet is None:
            self._spreadsheet = self._retry(
                lambda: self.client.open_by_key(self.config.SPREADSHEET_ID)
            )
        return self._spreadsheet

    def _ws(self, name):
        if name not in self._ws_cache:
            self._ws_cache[name] = self._retry(lambda: self._open().worksheet(name))
        return self._ws_cache[name]

    def _values(self, name):
        now = time.time()
        cached = self._op_cache.get(name)
        if cached and now - cached[0] < _OP_CACHE_TTL:
            return cached[1]
        data = self._retry(lambda: self._ws(name).get_all_values())
        self._op_cache[name] = (now, data)
        return data

    def _invalidate(self, name):
        self._op_cache.pop(name, None)

    @staticmethod
    def _col_map(headers):
        return {h.strip(): i for i, h in enumerate(headers)}

    @staticmethod
    def _col_letter(idx):
        s = ""
        while True:
            s = chr(ord("A") + idx % 26) + s
            idx = idx // 26 - 1
            if idx < 0:
                return s

    @staticmethod
    def _row_dict(headers, row):
        return {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}

    # =====================================================================
    # Cities (City · State)
    # =====================================================================
    def cities(self) -> list[tuple[str, str]]:
        values = self._values(self.config.CITIES_SHEET)
        if not values or len(values) < 2:
            return []
        col = self._col_map(values[0])
        c_city, c_state = col.get("City"), col.get("State")
        if c_city is None:
            return []
        out, seen = [], set()
        for row in values[1:]:
            city = (row[c_city] if c_city < len(row) else "").strip()
            state = (row[c_state] if c_state is not None and c_state < len(row) else "").strip()
            if not city:
                continue
            key = (normalize_text(city), normalize_text(state))
            if key in seen:
                continue
            seen.add(key)
            out.append((city, state))
        return out

    def states(self) -> list[str]:
        out, seen = [], set()
        for _, state in self.cities():
            if state and normalize_text(state) not in seen:
                seen.add(normalize_text(state))
                out.append(state)
        return out

    def cities_in_state(self, state: str) -> list[tuple[str, str]]:
        n = normalize_text(state)
        return [(c, s) for c, s in self.cities() if normalize_text(s) == n]

    def state_of_city(self, city: str) -> str:
        n = normalize_text(city)
        for c, s in self.cities():
            if normalize_text(c) == n:
                return s
        return ""

    # =====================================================================
    # Hotels (Hotel · Address)
    # =====================================================================
    def hotels(self) -> list[tuple[str, str]]:
        values = self._values(self.config.HOTELS_SHEET)
        if not values or len(values) < 2:
            return []
        col = self._col_map(values[0])
        c_hotel, c_addr = col.get("Hotel"), col.get("Address")
        if c_hotel is None:
            return []
        out, seen = [], set()
        for row in values[1:]:
            hotel = (row[c_hotel] if c_hotel < len(row) else "").strip()
            addr = (row[c_addr] if c_addr is not None and c_addr < len(row) else "").strip()
            if not hotel:
                continue
            key = normalize_text(hotel)
            if key in seen:
                continue
            seen.add(key)
            out.append((hotel, addr))
        return out

    def search_hotels(self, query: str) -> list[tuple[str, str]]:
        """Фильтр отелей по подстроке в названии ИЛИ адресе (нормализовано)."""
        q = normalize_text(query)
        if not q:
            return self.hotels()
        return [(h, a) for h, a in self.hotels()
                if q in normalize_text(h) or q in normalize_text(a)]

    def address_of_hotel(self, hotel: str) -> str:
        n = normalize_text(hotel)
        for h, a in self.hotels():
            if normalize_text(h) == n:
                return a
        return ""

    # =====================================================================
    # People
    # =====================================================================
    def _people(self):
        values = self._values(self.config.PEOPLE_SHEET)
        headers = values[0] if values else PEOPLE_HEADERS
        return headers, (values[1:] if values and len(values) > 1 else [])

    def get_person(self, tg_id: int) -> Optional[Person]:
        headers, rows = self._people()
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            return None
        for row in rows:
            if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
                return Person.from_row(self._row_dict(headers, row))
        return None

    def all_people(self) -> list[Person]:
        headers, rows = self._people()
        out = []
        for row in rows:
            p = Person.from_row(self._row_dict(headers, row))
            if p and p.is_active:
                out.append(p)
        return out

    def upsert_person(self, tg_id: int, fields: dict):
        """Обновляет только управляемые колонки; ставит UpdatedAt и isActive."""
        name = self.config.PEOPLE_SHEET
        values = self._values(name)
        if not values:
            raise RuntimeError("people sheet empty (нет заголовков)")
        headers = values[0]
        col = self._col_map(headers)
        tg_col = col.get("telegramID")
        if tg_col is None:
            raise RuntimeError("telegramID column not found in people")
        ws = self._ws(name)

        payload = dict(fields)
        payload["UpdatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payload.setdefault("isActive", "TRUE")

        existing = None
        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
                existing = i
                break

        if existing:
            updates = []
            for key, val in payload.items():
                idx = col.get(key)
                if idx is None:
                    continue
                updates.append({"range": f"{self._col_letter(idx)}{existing}", "values": [[val]]})
            updates.append({"range": f"{self._col_letter(tg_col)}{existing}", "values": [[str(tg_id)]]})
            ws.batch_update(updates)
        else:
            row_out = [""] * len(headers)
            row_out[tg_col] = str(tg_id)
            for key, val in payload.items():
                if key in col:
                    row_out[col[key]] = val
            ws.append_row(row_out, value_input_option="USER_ENTERED")

        self._invalidate(name)

    def delete_person(self, tg_id: int) -> bool:
        name = self.config.PEOPLE_SHEET
        values = self._values(name)
        if not values or len(values) < 2:
            return False
        col = self._col_map(values[0])
        tg_col = col.get("telegramID")
        if tg_col is None:
            return False
        ws = self._ws(name)
        for i, row in enumerate(values[1:], start=2):
            if tg_col < len(row) and str(row[tg_col]).strip() == str(tg_id):
                ws.delete_rows(i)
                self._invalidate(name)
                return True
        return False

    def set_role(self, tg_id: int, role: str, on: bool) -> str:
        """Включить/выключить роль. Если после снятия обе роли выключены — удаляем строку.
        Возвращает 'updated' | 'deleted' | 'missing'."""
        p = self.get_person(tg_id)
        if not p:
            return "missing"
        is_driver = p.is_driver
        is_passenger = p.is_passenger
        fields = {}
        if role == "driver":
            is_driver = on
            fields["IsDriver"] = "TRUE" if on else "FALSE"
            if not on:
                fields["Car"] = ""
                fields["Seats"] = ""
        else:
            is_passenger = on
            fields["IsPassenger"] = "TRUE" if on else "FALSE"

        if not is_driver and not is_passenger:
            self.delete_person(tg_id)
            return "deleted"
        self.upsert_person(tg_id, fields)
        return "updated"

    # =====================================================================
    # Search
    # =====================================================================
    def search(self, target: str, mode: str, value: str) -> list[Person]:
        """target: 'driver'|'passenger'; mode: 'city'|'state'|'hotel'."""
        n = normalize_text(value)
        out = []
        for p in self.all_people():
            if target == "driver" and not p.is_driver:
                continue
            if target == "passenger" and not p.is_passenger:
                continue
            if mode == "city" and normalize_text(p.city) != n:
                continue
            if mode == "state" and normalize_text(p.state) != n:
                continue
            if mode == "hotel" and normalize_text(p.hotel) != n:
                continue
            out.append(p)
        # свежие сверху
        out.sort(key=lambda x: x.updated_at, reverse=True)
        return out
