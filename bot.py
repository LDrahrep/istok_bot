from __future__ import annotations

import logging
import re
import traceback

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config import Config, Buttons
from sheets import SheetManager
from handlers import (
    BotHandlers,
    R_NAME, R_CITY, R_HOTEL, R_PHONE, R_CAR, R_SEATS, R_REVIEW,
    S_TARGET, S_MODE, S_VALUE,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _eq(text: str) -> str:
    return f"^{re.escape(text)}$"


def build_app():
    config = Config()
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    sheets = SheetManager(config)
    h = BotHandlers(config, sheets)

    request = HTTPXRequest(connect_timeout=20.0, read_timeout=120.0,
                           write_timeout=20.0, pool_timeout=20.0)
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).request(request).build()

    async def on_error(update, context):
        # Больше не молчим: логируем, сообщаем пользователю, шлём трейсбек админу.
        logger.error("Handler error", exc_info=context.error)
        try:
            chat = getattr(update, "effective_chat", None)
            if chat:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text="⚠️ Что-то пошло не так. Если повторяется — сообщи администратору.")
        except Exception:
            pass
        if config.ADMIN_CHAT_ID:
            try:
                tb = "".join(traceback.format_exception(
                    type(context.error), context.error, context.error.__traceback__))
                await context.bot.send_message(chat_id=config.ADMIN_CHAT_ID,
                                               text=("🧾 Ошибка:\n" + tb)[-3500:])
            except Exception:
                pass

    app.add_error_handler(on_error)

    re_cancel = _eq(Buttons.CANCEL)
    cancel_msg = MessageHandler(filters.Regex(re_cancel), h.cancel)
    cancel_cb = CallbackQueryHandler(h.cancel, pattern=r"^cancel$")
    text = filters.TEXT & ~filters.COMMAND

    # ---------- Регистрация ----------
    reg = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(_eq(Buttons.I_AM_DRIVER)), h.reg_driver),
            MessageHandler(filters.Regex(_eq(Buttons.I_AM_PASSENGER)), h.reg_passenger),
            CallbackQueryHandler(h.edit_record_start, pattern=r"^editrec$"),
        ],
        states={
            R_NAME: [cancel_msg, MessageHandler(text, h.reg_name)],
            R_CITY: [
                CallbackQueryHandler(h.reg_city_pick, pattern=r"^(pick:\d+|page:\d+|my|cancel)$"),
                cancel_msg, MessageHandler(text, h.reg_city_filter),
            ],
            R_HOTEL: [
                CallbackQueryHandler(h.reg_hotel_pick, pattern=r"^(pick:\d+|page:\d+|skip|cancel)$"),
                cancel_msg, MessageHandler(text, h.reg_hotel_filter),
            ],
            R_PHONE: [
                MessageHandler(filters.CONTACT, h.reg_phone_contact),
                cancel_msg, MessageHandler(text, h.reg_phone_text),
            ],
            R_CAR: [cancel_msg, MessageHandler(text, h.reg_car)],
            R_SEATS: [CallbackQueryHandler(h.reg_seats_pick, pattern=r"^(seats:\d+|cancel)$")],
            R_REVIEW: [CallbackQueryHandler(h.review_action, pattern=r"^(save|edit:\w+|cancel)$")],
        },
        fallbacks=[CommandHandler("start", h.start), cancel_msg, cancel_cb],
        allow_reentry=True,
    )

    # ---------- Поиск ----------
    search = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(_eq(Buttons.SEARCH)), h.search_start)],
        states={
            S_TARGET: [CallbackQueryHandler(h.search_target, pattern=r"^(t:\w+|cancel)$")],
            S_MODE: [CallbackQueryHandler(h.search_mode, pattern=r"^(m:\w+|cancel)$")],
            S_VALUE: [
                CallbackQueryHandler(h.search_value_pick, pattern=r"^(pick:\d+|page:\d+|my|cancel)$"),
                cancel_msg, MessageHandler(text, h.search_value_filter),
            ],
        },
        fallbacks=[CommandHandler("start", h.start), cancel_msg, cancel_cb],
        allow_reentry=True,
    )

    app.add_handler(reg)
    app.add_handler(search)

    # ---------- Вне диалогов ----------
    app.add_handler(CommandHandler("start", h.start))
    app.add_handler(MessageHandler(filters.Regex(_eq(Buttons.MY_RECORD)), h.my_record))
    app.add_handler(CallbackQueryHandler(h.search_more, pattern=r"^more:\d+$"))
    app.add_handler(CallbackQueryHandler(h.record_action, pattern=r"^drop:\w+$"))
    app.add_handler(CallbackQueryHandler(h.record_confirm, pattern=r"^confirm:\w+$"))

    app.add_handler(MessageHandler(filters.ALL, h.unknown))
    return app


if __name__ == "__main__":
    application = build_app()
    application.run_polling(drop_pending_updates=True, allowed_updates=None)
