from __future__ import annotations

import logging

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes, ConversationHandler

from config import Buttons
from models import clean_name, parse_us_phone, parse_seats, format_phone_display

logger = logging.getLogger(__name__)

# Состояния диалогов
(
    R_NAME, R_CITY, R_HOTEL, R_PHONE, R_CAR, R_SEATS, R_REVIEW,
    S_TARGET, S_MODE, S_VALUE,
) = range(10)

# Порядок обязательных/опрашиваемых полей
REQUIRED = ["name", "city", "hotel", "phone"]
DRIVER_EXTRA = ["car", "seats"]


class BotHandlers:
    def __init__(self, config, sheets):
        self.config = config
        self.sheets = sheets
        self.page_size = getattr(config, "PAGE_SIZE", 5)

    # ==================================================================
    # Общие утилиты
    # ==================================================================
    def kb_main(self):
        return ReplyKeyboardMarkup(
            [[Buttons.I_AM_DRIVER, Buttons.I_AM_PASSENGER],
             [Buttons.MY_RECORD, Buttons.SEARCH]],
            resize_keyboard=True,
        )

    def cancel_kb(self):
        # Только «Отмена» — чтобы кнопки меню не попали в текстовый ввод.
        return ReplyKeyboardMarkup([[Buttons.CANCEL]], resize_keyboard=True)

    async def _send(self, context, chat_id, text, reply_markup=None):
        return await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    def _selection_kb(self, items, page, labeler, extras=None):
        """Инлайн-клавиатура со страницей элементов (callback pick:<idx>) + навигация."""
        size = self.page_size
        start = page * size
        chunk = items[start:start + size]
        rows = [[InlineKeyboardButton(labeler(it), callback_data=f"pick:{start + i}")]
                for i, it in enumerate(chunk)]
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Назад", callback_data=f"page:{page - 1}"))
        if start + size < len(items):
            nav.append(InlineKeyboardButton("▶️ Ещё", callback_data=f"page:{page + 1}"))
        if nav:
            rows.append(nav)
        for row in (extras or []):
            rows.append(row)
        rows.append([InlineKeyboardButton("↩️ Отмена", callback_data="cancel")])
        return InlineKeyboardMarkup(rows)

    # ==================================================================
    # Start / Cancel / Unknown
    # ==================================================================
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await self._send(
            context, update.effective_chat.id,
            "Привет! Я помогаю найти попутчиков 🚗\n\n"
            "• «🚗 Я водитель» / «🧍 Я пассажир» — завести или обновить свою запись.\n"
            "• «🔍 Найти» — водителя или пассажира по городу, штату или отелю.\n\n"
            "Выбери кнопкой ниже:",
            self.kb_main(),
        )
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        chat_id = update.effective_chat.id
        if update.callback_query:
            await update.callback_query.answer()
        await self._send(context, chat_id, "Ок, отменил 👍", self.kb_main())
        return ConversationHandler.END

    async def unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            await self._send(context, update.effective_chat.id,
                             "Не понял 🤔 Пользуйся кнопками ниже или /start", self.kb_main())

    # ==================================================================
    # РЕГИСТРАЦИЯ (две роли, общее спрашиваем один раз, экран проверки)
    # ==================================================================
    async def reg_driver(self, update, context):
        return await self._reg_start(update, context, "driver")

    async def reg_passenger(self, update, context):
        return await self._reg_start(update, context, "passenger")

    async def _reg_start(self, update, context, role):
        context.user_data.clear()
        context.user_data["reg_role"] = role
        # префилл из существующей записи
        p = self.sheets.get_person(update.effective_user.id)
        if p:
            context.user_data.update(
                name=p.name, city=p.city, state=p.state, hotel=p.hotel,
                car=p.car, seats=p.seats, phone=p.phone,
                had_driver=p.is_driver, had_passenger=p.is_passenger,
                hotel_asked=True,  # уже есть запись — отель не переспрашиваем
            )
        return await self._advance(context, update.effective_chat.id)

    def _next_field(self, ud):
        role = ud["reg_role"]
        for f in REQUIRED:
            if f == "hotel":
                if not ud.get("hotel_asked"):
                    return "hotel"
                continue
            if not ud.get(f):
                return f
        if role == "driver":
            for f in DRIVER_EXTRA:
                if not ud.get(f):
                    return f
        return None

    async def _advance(self, context, chat_id):
        ud = context.user_data
        field = self._next_field(ud)
        if field is None:
            return await self._show_review(context, chat_id)
        return await self._ask_field(context, chat_id, field)

    async def _after_field(self, context, chat_id):
        """После ввода поля: если правим из экрана проверки — вернуться туда."""
        if context.user_data.pop("editing", None):
            return await self._show_review(context, chat_id)
        return await self._advance(context, chat_id)

    async def _ask_field(self, context, chat_id, field):
        ud = context.user_data
        if field == "name":
            await self._send(context, chat_id, "Как тебя зовут? Имя и фамилия.\nПример: Ivan Ivanov",
                             self.cancel_kb())
            return R_NAME
        if field == "city":
            ud["sel_kind"] = "city"
            ud["sel_items"] = self.sheets.cities()
            ud["sel_page"] = 0
            extras = []
            if ud.get("city"):
                extras.append([InlineKeyboardButton(f"📍 Мой: {ud['city']}", callback_data="my")])
            await self._send(context, chat_id, "В каком городе ты живёшь? Выбери кнопкой (или напиши для поиска):",
                             self._selection_kb(ud["sel_items"], 0, self._city_label, extras))
            return R_CITY
        if field == "hotel":
            ud["sel_kind"] = "hotel"
            ud["sel_items"] = self.sheets.hotels()
            ud["sel_page"] = 0
            extras = [[InlineKeyboardButton("🚫 Без отеля", callback_data="skip")]]
            await self._send(context, chat_id,
                             "В каком отеле ты живёшь? Выбери кнопкой, или напиши часть названия/адреса для поиска.\n"
                             "Если твоего отеля нет — «🚫 Без отеля».",
                             self._selection_kb(ud["sel_items"], 0, self._hotel_label, extras))
            return R_HOTEL
        if field == "phone":
            await self._send(context, chat_id,
                             "Твой номер телефона (US). Нажми «📱 Поделиться номером» или напиши вручную.\n"
                             "Пример: 415 555 0123",
                             ReplyKeyboardMarkup(
                                 [[KeyboardButton(Buttons.SHARE_PHONE, request_contact=True)],
                                  [Buttons.CANCEL]],
                                 resize_keyboard=True, one_time_keyboard=True))
            return R_PHONE
        if field == "car":
            await self._send(context, chat_id, "Марка/модель машины?\nПример: Toyota Camry", self.cancel_kb())
            return R_CAR
        if field == "seats":
            rows = [[InlineKeyboardButton(str(n), callback_data=f"seats:{n}") for n in (1, 2, 3, 4)],
                    [InlineKeyboardButton(str(n), callback_data=f"seats:{n}") for n in (5, 6, 7, 8)],
                    [InlineKeyboardButton("↩️ Отмена", callback_data="cancel")]]
            await self._send(context, chat_id, "Сколько мест для пассажиров? Выбери:",
                             InlineKeyboardMarkup(rows))
            return R_SEATS
        return await self._show_review(context, chat_id)

    @staticmethod
    def _city_label(it):
        c, s = it
        return f"{c}, {s}" if s else c

    @staticmethod
    def _hotel_label(it):
        h, a = it
        return h if not a else f"{h} — {a}"[:60]

    # ----- обработчики полей -----
    async def reg_name(self, update, context):
        name = clean_name(update.message.text)
        if not name:
            await self._send(context, update.effective_chat.id, "Пустое имя. Напиши имя и фамилию.")
            return R_NAME
        context.user_data["name"] = name
        return await self._after_field(context, update.effective_chat.id)

    async def reg_city_pick(self, update, context):
        q = update.callback_query
        await q.answer()
        ud = context.user_data
        data = q.data
        if data == "cancel":
            return await self.cancel(update, context)
        if data == "my":
            # оставляем текущий город
            return await self._after_field(context, q.message.chat_id)
        if data.startswith("page:"):
            ud["sel_page"] = int(data.split(":")[1])
            await q.edit_message_reply_markup(self._selection_kb(ud["sel_items"], ud["sel_page"], self._city_label))
            return R_CITY
        if data.startswith("pick:"):
            it = ud["sel_items"][int(data.split(":")[1])]
            ud["city"], ud["state"] = it[0], it[1]
            return await self._after_field(context, q.message.chat_id)
        return R_CITY

    async def reg_city_filter(self, update, context):
        ud = context.user_data
        term = update.message.text.strip()
        n = term.casefold()
        found = [it for it in self.sheets.cities() if n in self._city_label(it).casefold()]
        if found:
            ud["sel_items"] = found
            header = f"Города по запросу «{term}»:"
        else:
            ud["sel_items"] = self.sheets.cities()
            header = f"По запросу «{term}» ничего не нашёл. Вот весь список:"
        ud["sel_page"] = 0
        await self._send(context, update.effective_chat.id, header,
                         self._selection_kb(ud["sel_items"], 0, self._city_label))
        return R_CITY

    async def reg_hotel_pick(self, update, context):
        q = update.callback_query
        await q.answer()
        ud = context.user_data
        data = q.data
        if data == "cancel":
            return await self.cancel(update, context)
        if data == "skip":
            ud["hotel"] = ""
            ud["hotel_asked"] = True
            return await self._after_field(context, q.message.chat_id)
        if data.startswith("page:"):
            ud["sel_page"] = int(data.split(":")[1])
            await q.edit_message_reply_markup(self._selection_kb(
                ud["sel_items"], ud["sel_page"], self._hotel_label,
                [[InlineKeyboardButton("🚫 Без отеля", callback_data="skip")]]))
            return R_HOTEL
        if data.startswith("pick:"):
            it = ud["sel_items"][int(data.split(":")[1])]
            ud["hotel"] = it[0]
            ud["hotel_asked"] = True
            return await self._after_field(context, q.message.chat_id)
        return R_HOTEL

    async def reg_hotel_filter(self, update, context):
        ud = context.user_data
        term = update.message.text.strip()
        found = self.sheets.search_hotels(term)
        ud["sel_items"] = found
        ud["sel_page"] = 0
        extras = [[InlineKeyboardButton("🚫 Без отеля / нет в списке", callback_data="skip")]]
        if not found:
            await self._send(context, update.effective_chat.id,
                             f"По «{term}» ничего не нашёл. Попробуй другое слово из названия/адреса, "
                             "или нажми «🚫 Без отеля».",
                             InlineKeyboardMarkup(extras + [[InlineKeyboardButton("↩️ Отмена", callback_data="cancel")]]))
            return R_HOTEL
        await self._send(context, update.effective_chat.id, f"Отели по запросу «{term}»:",
                         self._selection_kb(found, 0, self._hotel_label, extras))
        return R_HOTEL

    async def reg_phone_text(self, update, context):
        r = parse_us_phone(update.message.text)
        if not r["ok"]:
            await self._send(context, update.effective_chat.id, "❌ " + r["error"])
            return R_PHONE
        context.user_data["phone"] = r["e164"]
        await self._send(context, update.effective_chat.id, f"✅ Телефон: {r['display']}", ReplyKeyboardRemove())
        return await self._after_field(context, update.effective_chat.id)

    async def reg_phone_contact(self, update, context):
        raw = update.message.contact.phone_number
        r = parse_us_phone(raw)
        if not r["ok"]:
            await self._send(context, update.effective_chat.id,
                             "Номер из контакта не похож на US. Напиши вручную. Пример: 415 555 0123")
            return R_PHONE
        context.user_data["phone"] = r["e164"]
        await self._send(context, update.effective_chat.id, f"✅ Телефон: {r['display']}", ReplyKeyboardRemove())
        return await self._after_field(context, update.effective_chat.id)

    async def reg_car(self, update, context):
        context.user_data["car"] = clean_name(update.message.text)
        return await self._after_field(context, update.effective_chat.id)

    async def reg_seats_pick(self, update, context):
        q = update.callback_query
        await q.answer()
        if q.data == "cancel":
            return await self.cancel(update, context)
        if q.data.startswith("seats:"):
            context.user_data["seats"] = str(parse_seats(q.data.split(":")[1]) or "")
            return await self._after_field(context, q.message.chat_id)
        return R_SEATS

    # ----- экран проверки -----
    async def _show_review(self, context, chat_id):
        ud = context.user_data
        role = ud["reg_role"]
        lines = ["Проверь данные:", "",
                 f"👤 Имя: {ud.get('name','—')}",
                 f"🏙 Город: {ud.get('city','—')}{', ' + ud['state'] if ud.get('state') else ''}",
                 f"🏨 Отель: {ud.get('hotel') or '— (без отеля)'}",
                 f"📞 Телефон: {format_phone_display(ud.get('phone',''))}"]
        if role == "driver":
            lines.append(f"🚗 Машина: {ud.get('car','—')}")
            lines.append(f"💺 Мест: {ud.get('seats','—')}")
        role_word = "водителя" if role == "driver" else "пассажира"
        rows = [
            [InlineKeyboardButton("✅ Сохранить", callback_data="save")],
            [InlineKeyboardButton("✏️ Имя", callback_data="edit:name"),
             InlineKeyboardButton("✏️ Город", callback_data="edit:city")],
            [InlineKeyboardButton("✏️ Отель", callback_data="edit:hotel"),
             InlineKeyboardButton("✏️ Телефон", callback_data="edit:phone")],
        ]
        if role == "driver":
            rows.append([InlineKeyboardButton("✏️ Машина", callback_data="edit:car"),
                         InlineKeyboardButton("✏️ Места", callback_data="edit:seats")])
        rows.append([InlineKeyboardButton("↩️ Отмена", callback_data="cancel")])
        await self._send(context, chat_id, "\n".join(lines) + f"\n\nСохранить как {role_word}?",
                         InlineKeyboardMarkup(rows))
        return R_REVIEW

    async def review_action(self, update, context):
        q = update.callback_query
        await q.answer()
        data = q.data
        chat_id = q.message.chat_id
        if data == "cancel":
            return await self.cancel(update, context)
        if data.startswith("edit:"):
            field = data.split(":")[1]
            context.user_data["editing"] = True
            if field == "hotel":
                context.user_data["hotel_asked"] = False
            return await self._ask_field(context, chat_id, field)
        if data == "save":
            return await self._save(context, q.from_user, chat_id)
        return R_REVIEW

    async def _save(self, context, user, chat_id):
        ud = context.user_data
        role = ud["reg_role"]
        fields = {
            "Name": ud.get("name", ""),
            "Username": user.username or "",
            "City": ud.get("city", ""),
            "State": ud.get("state", ""),
            "Hotel": ud.get("hotel", ""),
            "Phone": ud.get("phone", ""),
            "IsDriver": "TRUE" if (role == "driver" or ud.get("had_driver")) else "FALSE",
            "IsPassenger": "TRUE" if (role == "passenger" or ud.get("had_passenger")) else "FALSE",
        }
        if role == "driver":
            fields["Car"] = ud.get("car", "")
            fields["Seats"] = ud.get("seats", "")
        try:
            self.sheets.upsert_person(user.id, fields)
        except Exception as e:
            logger.error("save failed: %s", e)
            await self._send(context, chat_id, "❌ Ошибка сохранения. Попробуй позже.", self.kb_main())
            return ConversationHandler.END
        word = "водителей" if role == "driver" else "пассажиров"
        await self._send(context, chat_id, f"✅ Готово! Ты в списке {word}.\n"
                         "Нажми «🔍 Найти», чтобы искать попутчиков.", self.kb_main())
        context.user_data.clear()
        return ConversationHandler.END

    # ==================================================================
    # ПОИСК
    # ==================================================================
    async def search_start(self, update, context):
        context.user_data.clear()
        rows = [[InlineKeyboardButton(Buttons.FIND_DRIVER, callback_data="t:driver"),
                 InlineKeyboardButton(Buttons.FIND_PASSENGER, callback_data="t:passenger")],
                [InlineKeyboardButton("↩️ Отмена", callback_data="cancel")]]
        await self._send(context, update.effective_chat.id, "Кого ищешь?", InlineKeyboardMarkup(rows))
        return S_TARGET

    async def search_target(self, update, context):
        q = update.callback_query
        await q.answer()
        if q.data == "cancel":
            return await self.cancel(update, context)
        context.user_data["target"] = q.data.split(":")[1]
        rows = [[InlineKeyboardButton(Buttons.BY_CITY, callback_data="m:city"),
                 InlineKeyboardButton(Buttons.BY_STATE, callback_data="m:state")],
                [InlineKeyboardButton(Buttons.BY_HOTEL, callback_data="m:hotel")],
                [InlineKeyboardButton("↩️ Отмена", callback_data="cancel")]]
        await q.edit_message_text("По какому признаку искать?", reply_markup=InlineKeyboardMarkup(rows))
        return S_MODE

    async def search_mode(self, update, context):
        q = update.callback_query
        await q.answer()
        if q.data == "cancel":
            return await self.cancel(update, context)
        mode = q.data.split(":")[1]
        ud = context.user_data
        ud["mode"] = mode
        me = self.sheets.get_person(q.from_user.id)
        if mode == "city":
            ud["sel_items"] = self.sheets.cities()
            extras = []
            if me and me.city:
                extras.append([InlineKeyboardButton(f"📍 Мой: {me.city}", callback_data="my")])
            await self._send(context, q.message.chat_id, "В каком городе искать? (или напиши для поиска)",
                             self._selection_kb(ud["sel_items"], 0, self._city_label, extras))
        elif mode == "state":
            ud["sel_items"] = [(s,) for s in self.sheets.states()]
            extras = []
            if me and me.state:
                extras.append([InlineKeyboardButton(f"📍 Мой штат: {me.state}", callback_data="my")])
            await self._send(context, q.message.chat_id, "В каком штате искать?",
                             self._selection_kb(ud["sel_items"], 0, lambda it: it[0], extras))
        else:
            ud["sel_items"] = self.sheets.hotels()
            await self._send(context, q.message.chat_id, "Какой отель? (или напиши часть названия/адреса)",
                             self._selection_kb(ud["sel_items"], 0, self._hotel_label))
        ud["sel_page"] = 0
        return S_VALUE

    async def search_value_pick(self, update, context):
        q = update.callback_query
        await q.answer()
        ud = context.user_data
        data = q.data
        mode = ud["mode"]
        if data == "cancel":
            return await self.cancel(update, context)
        labeler = self._city_label if mode == "city" else (self._hotel_label if mode == "hotel" else (lambda it: it[0]))
        if data.startswith("page:"):
            ud["sel_page"] = int(data.split(":")[1])
            await q.edit_message_reply_markup(self._selection_kb(ud["sel_items"], ud["sel_page"], labeler))
            return S_VALUE
        if data == "my":
            me = self.sheets.get_person(q.from_user.id)
            value = (me.city if mode == "city" else me.state) if me else ""
            return await self._run_search(context, q.from_user.id, q.message.chat_id, value)
        if data.startswith("pick:"):
            it = ud["sel_items"][int(data.split(":")[1])]
            value = it[0]  # city name / state / hotel name
            return await self._run_search(context, q.from_user.id, q.message.chat_id, value)
        return S_VALUE

    async def search_value_filter(self, update, context):
        ud = context.user_data
        mode = ud["mode"]
        term = update.message.text.strip()
        n = term.casefold()
        if mode == "hotel":
            found = self.sheets.search_hotels(term)
            full = self.sheets.hotels()
            labeler = self._hotel_label
        elif mode == "city":
            found = [it for it in self.sheets.cities() if n in self._city_label(it).casefold()]
            full = self.sheets.cities()
            labeler = self._city_label
        else:
            found = [(s,) for s in self.sheets.states() if n in s.casefold()]
            full = [(s,) for s in self.sheets.states()]
            labeler = lambda it: it[0]
        if found:
            ud["sel_items"] = found
            header = f"По запросу «{term}»:"
        else:
            ud["sel_items"] = full
            header = f"По запросу «{term}» ничего не нашёл. Вот весь список:"
        ud["sel_page"] = 0
        await self._send(context, update.effective_chat.id, header,
                         self._selection_kb(ud["sel_items"], 0, labeler))
        return S_VALUE

    async def _run_search(self, context, user_id, chat_id, value):
        ud = context.user_data
        target, mode = ud["target"], ud["mode"]
        if not value:
            await self._send(context, chat_id, "Пустой запрос. Начни поиск заново «🔍 Найти».", self.kb_main())
            return ConversationHandler.END
        results = [p for p in self.sheets.search(target, mode, value) if p.tg_id != user_id]
        # сохраняем контекст для «показать ещё»
        context.user_data.clear()
        context.user_data["search_ctx"] = {"target": target, "mode": mode, "value": value, "ids_exclude": [user_id]}
        await self._render_results(context, chat_id, results, 0)
        return ConversationHandler.END

    def _person_line(self, p, target):
        parts = [f"👤 {p.name}"]
        if target == "driver":
            car = p.car or "—"
            parts.append("🚗 " + car + (f", 💺 {p.seats}" if p.seats else ""))
        loc = p.city + (f", {p.state}" if p.state else "")
        if p.hotel:
            loc += f" · 🏨 {p.hotel}"
        parts.append("📍 " + loc)
        contact = []
        if p.username:
            contact.append(f"t.me/{p.username}")
        if p.phone:
            contact.append("📞 " + format_phone_display(p.phone))
        parts.append(" · ".join(contact) if contact else "—")
        return "\n".join(parts)

    async def _render_results(self, context, chat_id, results, offset):
        target = context.user_data.get("search_ctx", {}).get("target", "driver")
        who = "водителей" if target == "driver" else "пассажиров"
        if not results:
            await self._send(context, chat_id, f"Никого не нашёл. Попробуй другой признак или локацию.", self.kb_main())
            return
        size = self.page_size
        chunk = results[offset:offset + size]
        text = f"Найдено {who}: {len(results)} (показаны {offset + 1}–{offset + len(chunk)})\n\n"
        text += "\n\n".join(self._person_line(p, target) for p in chunk)
        markup = None
        if offset + size < len(results):
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"▶️ Показать ещё ({len(results) - offset - size})",
                                       callback_data=f"more:{offset + size}")]])
        await self._send(context, chat_id, text, markup)

    async def search_more(self, update, context):
        q = update.callback_query
        await q.answer()
        ctx = context.user_data.get("search_ctx")
        if not ctx:
            await q.edit_message_reply_markup(None)
            return
        offset = int(q.data.split(":")[1])
        results = [p for p in self.sheets.search(ctx["target"], ctx["mode"], ctx["value"])
                   if p.tg_id not in ctx.get("ids_exclude", [])]
        await q.edit_message_reply_markup(None)  # убираем старую кнопку
        await self._render_results(context, q.message.chat_id, results, offset)

    # ==================================================================
    # МОЯ ЗАПИСЬ + удаление
    # ==================================================================
    async def my_record(self, update, context):
        p = self.sheets.get_person(update.effective_user.id)
        chat_id = update.effective_chat.id
        if not p:
            await self._send(context, chat_id, "У тебя пока нет записи. Нажми «🚗 Я водитель» или «🧍 Я пассажир».",
                             self.kb_main())
            return
        roles = []
        if p.is_driver:
            roles.append("водитель")
        if p.is_passenger:
            roles.append("пассажир")
        lines = [f"📋 Твоя запись ({' + '.join(roles) or '—'}):", "",
                 f"👤 Имя: {p.name}",
                 f"🏙 Город: {p.city}{', ' + p.state if p.state else ''}",
                 f"🏨 Отель: {p.hotel or '— (без отеля)'}",
                 f"📞 Телефон: {format_phone_display(p.phone)}"]
        if p.is_driver:
            lines += [f"🚗 Машина: {p.car or '—'}", f"💺 Мест: {p.seats or '—'}"]
        rows = []
        if p.is_driver:
            rows.append([InlineKeyboardButton("🛑 Перестать быть водителем", callback_data="drop:driver")])
        if p.is_passenger:
            rows.append([InlineKeyboardButton("🛑 Перестать быть пассажиром", callback_data="drop:passenger")])
        rows.append([InlineKeyboardButton("🗑 Удалить полностью", callback_data="drop:all")])
        await self._send(context, chat_id, "\n".join(lines), InlineKeyboardMarkup(rows))

    async def record_action(self, update, context):
        q = update.callback_query
        await q.answer()
        action = q.data.split(":")[1]  # driver | passenger | all
        titles = {"driver": "перестать быть водителем", "passenger": "перестать быть пассажиром",
                  "all": "удалить запись полностью"}
        rows = [[InlineKeyboardButton("✅ Да", callback_data=f"confirm:{action}"),
                 InlineKeyboardButton("❌ Нет", callback_data="confirm:no")]]
        await q.edit_message_text(f"Точно {titles[action]}?", reply_markup=InlineKeyboardMarkup(rows))

    async def record_confirm(self, update, context):
        q = update.callback_query
        await q.answer()
        action = q.data.split(":")[1]
        chat_id = q.message.chat_id
        if action == "no":
            await q.edit_message_text("Ок, ничего не меняю.")
            await self._send(context, chat_id, "Меню:", self.kb_main())
            return
        try:
            if action == "all":
                self.sheets.delete_person(q.from_user.id)
                msg = "🗑 Запись удалена полностью."
            else:
                res = self.sheets.set_role(q.from_user.id, action, False)
                msg = "🗑 Запись удалена (ролей не осталось)." if res == "deleted" else "✅ Роль снята."
        except Exception as e:
            logger.error("record action failed: %s", e)
            await q.edit_message_text("❌ Ошибка. Попробуй позже.")
            await self._send(context, chat_id, "Меню:", self.kb_main())
            return
        await q.edit_message_text(msg)
        await self._send(context, chat_id, "Меню:", self.kb_main())
