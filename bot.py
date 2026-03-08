"""
Face ID Protector Bot v2
- Привязка устройства
- Управление ПК: блокировка, выключение, скриншот, камера
- Прямой эфир экрана (серия скриншотов)
- Уведомления о попытках входа
"""
import os, json, asyncio, logging, base64, random
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# devices[uuid] = {"chat_id": 123, "name": "PC"}
# pending[uuid] = {"chat_id": 123, "code": "123456", "time": 000}
# commands[uuid] = {"cmd": "screenshot"} — ожидающая команда для ПК
devices  = {}
pending  = {}
commands = {}  # команды ожидающие выполнения ПК

# ══════════════════════════════════════════════
#  КЛАВИАТУРА УПРАВЛЕНИЯ
# ══════════════════════════════════════════════
def main_keyboard(uuid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Скриншот экрана", callback_data=f"screenshot|{uuid}"),
         InlineKeyboardButton("📷 Фото с камеры",   callback_data=f"camera|{uuid}")],
        [InlineKeyboardButton("🔒 Заблокировать ПК", callback_data=f"lock|{uuid}"),
         InlineKeyboardButton("🔄 Перезагрузить",    callback_data=f"reboot|{uuid}")],
        [InlineKeyboardButton("⏻ Выключить ПК",      callback_data=f"shutdown|{uuid}"),
         InlineKeyboardButton("🎥 Эфир (5 кадров)",  callback_data=f"stream|{uuid}")],
        [InlineKeyboardButton("🔐 Запросить пароль", callback_data=f"askpass|{uuid}"),
         InlineKeyboardButton("📊 Статус",           callback_data=f"status|{uuid}")],
    ])

# ══════════════════════════════════════════════
#  БОТ КОМАНДЫ
# ══════════════════════════════════════════════
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 *Face ID Protector Bot*\n\n"
        "Команды:\n"
        "/register UUID — привязать устройство\n"
        "/devices — мои устройства\n"
        "/control — управление ПК\n\n"
        "UUID смотри в приложении → ✈️ Telegram",
        parse_mode="Markdown"
    )

async def register_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Укажи UUID:\n`/register ВАШ-UUID`", parse_mode="Markdown")
        return
    dev_uuid = ctx.args[0].strip()
    chat_id  = update.effective_chat.id
    code     = str(random.randint(100000, 999999))
    pending[dev_uuid] = {"chat_id": chat_id, "code": code, "time": datetime.now().timestamp()}
    await update.message.reply_text(
        f"📱 *Подтверждение устройства*\n\n"
        f"UUID: `{dev_uuid[:8]}...`\n\n"
        f"Введи этот код в приложении:\n"
        f"*{code}*\n\n"
        f"⏱ Код действителен 10 минут.",
        parse_mode="Markdown"
    )

async def devices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if d["chat_id"]==chat_id]
    if not my:
        await update.message.reply_text("Нет привязанных устройств.\n/register UUID")
        return
    txt = "📱 *Ваши устройства:*\n\n"
    for u,d in my:
        txt += f"• `{u[:8]}...` — {d.get('name','ПК')}\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def control_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if d["chat_id"]==chat_id]
    if not my:
        await update.message.reply_text("Нет устройств. /register UUID")
        return
    if len(my)==1:
        uuid = my[0][0]
        name = my[0][1].get("name","ПК")
        await update.message.reply_text(
            f"🖥️ *Управление: {name}*\nВыбери действие:",
            parse_mode="Markdown",
            reply_markup=main_keyboard(uuid)
        )
    else:
        # Выбор устройства
        kb = [[InlineKeyboardButton(d.get("name",u[:8]), callback_data=f"select|{u}")] for u,d in my]
        await update.message.reply_text("Выбери устройство:", reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data
    chat_id = query.from_user.id

    if data.startswith("select|"):
        uuid = data.split("|")[1]
        name = devices.get(uuid,{}).get("name","ПК")
        await query.edit_message_text(
            f"🖥️ *Управление: {name}*\nВыбери действие:",
            parse_mode="Markdown",
            reply_markup=main_keyboard(uuid)
        )
        return

    parts = data.split("|")
    if len(parts) != 2:
        return
    cmd, uuid = parts

    # Проверяем права
    if uuid not in devices or devices[uuid]["chat_id"] != chat_id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    # Ставим команду в очередь для ПК
    commands[uuid] = {"cmd": cmd, "time": datetime.now().timestamp()}

    cmd_names = {
        "screenshot": "📸 Запрос скриншота отправлен...",
        "camera":     "📷 Запрос фото с камеры...",
        "lock":       "🔒 Команда блокировки отправлена...",
        "reboot":     "🔄 Команда перезагрузки отправлена...",
        "shutdown":   "⏻ Команда выключения отправлена...",
        "stream":     "🎥 Запрос прямого эфира...",
        "askpass":    "🔐 Запрос пароля отправлен на ПК...",
        "status":     "📊 Запрос статуса...",
    }
    await query.edit_message_text(
        cmd_names.get(cmd, "Команда отправлена..."),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад", callback_data=f"select|{uuid}")
        ]])
    )

# ══════════════════════════════════════════════
#  HTTP API для C++ приложения
# ══════════════════════════════════════════════

async def api_verify(request):
    """Проверка кода привязки"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False, "error": "bad_json"})
    
    dev_uuid = data.get("uuid","")
    code     = data.get("code","")
    
    logging.info(f"Verify attempt: uuid={dev_uuid[:8]}, code={code}, pending={list(pending.keys())[:3]}")
    
    if dev_uuid not in pending:
        return web.json_response({"ok": False, "error": "not_found"})
    
    p = pending[dev_uuid]
    if datetime.now().timestamp() - p["time"] > 600:  # 10 минут
        del pending[dev_uuid]
        return web.json_response({"ok": False, "error": "expired"})
    
    if p["code"] != code:
        return web.json_response({"ok": False, "error": "wrong_code"})
    
    devices[dev_uuid] = {"chat_id": p["chat_id"], "name": data.get("name","ПК")}
    del pending[dev_uuid]
    
    await bot_app.bot.send_message(
        p["chat_id"],
        f"✅ *Устройство привязано!*\n\n"
        f"UUID: `{dev_uuid[:8]}...`\n\n"
        f"Теперь используй /control для управления ПК!",
        parse_mode="Markdown"
    )
    return web.json_response({"ok": True})

async def api_alert(request):
    """Уведомление о попытке входа"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices:
        return web.json_response({"ok": False, "error": "device_not_found"})
    
    chat_id  = devices[dev_uuid]["chat_id"]
    ts       = data.get("time", datetime.now().strftime("%H:%M:%S %d.%m.%Y"))
    attempts = data.get("attempts", 1)
    
    await bot_app.bot.send_message(
        chat_id,
        f"🚨 *ПОПЫТКА НЕСАНКЦИОНИРОВАННОГО ВХОДА*\n\n"
        f"🕐 {ts}\n"
        f"❌ Попыток: {attempts}\n"
        f"💻 {devices[dev_uuid].get('name','ПК')}",
        parse_mode="Markdown"
    )
    
    if data.get("camera"):
        img = base64.b64decode(data["camera"])
        await bot_app.bot.send_photo(chat_id, photo=img, caption="📸 Фото злоумышленника")
    
    if data.get("screenshot"):
        img = base64.b64decode(data["screenshot"])
        await bot_app.bot.send_photo(chat_id, photo=img, caption="🖥️ Скриншот экрана")
    
    return web.json_response({"ok": True})

async def api_poll(request):
    """ПК опрашивает — есть ли команда для него"""
    try:
        data = await request.json()
    except:
        return web.json_response({"cmd": None})
    
    dev_uuid = data.get("uuid","")
    if dev_uuid not in commands:
        return web.json_response({"cmd": None})
    
    cmd = commands.pop(dev_uuid)
    # Проверяем не устарела ли команда (30 сек)
    if datetime.now().timestamp() - cmd["time"] > 30:
        return web.json_response({"cmd": None})
    
    return web.json_response({"cmd": cmd["cmd"]})

async def api_result(request):
    """ПК отправляет результат команды (скриншот, камера и т.д.)"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices:
        return web.json_response({"ok": False})
    
    chat_id = devices[dev_uuid]["chat_id"]
    cmd     = data.get("cmd","")
    
    if cmd in ("screenshot","stream_frame"):
        if data.get("image"):
            img = base64.b64decode(data["image"])
            caption = "📸 Скриншот" if cmd=="screenshot" else "🎥 Кадр прямого эфира"
            await bot_app.bot.send_photo(chat_id, photo=img, caption=caption)
    
    elif cmd == "camera":
        if data.get("image"):
            img = base64.b64decode(data["image"])
            await bot_app.bot.send_photo(chat_id, photo=img, caption="📷 Фото с камеры")
    
    elif cmd == "status":
        status = data.get("status", {})
        await bot_app.bot.send_message(
            chat_id,
            f"📊 *Статус ПК*\n\n"
            f"🖥️ {status.get('hostname','?')}\n"
            f"👤 Пользователь: {status.get('user','?')}\n"
            f"🔒 Заблокирован: {'Да' if status.get('locked') else 'Нет'}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="Markdown",
            reply_markup=main_keyboard(dev_uuid)
        )
    
    elif cmd == "locked":
        await bot_app.bot.send_message(chat_id, "🔒 ПК заблокирован",
            reply_markup=main_keyboard(dev_uuid))
    
    return web.json_response({"ok": True})

async def api_check(request):
    try:
        data = await request.json()
    except:
        return web.json_response({"registered": False})
    dev_uuid = data.get("uuid","")
    return web.json_response({"registered": dev_uuid in devices})

async def healthcheck(request):
    return web.Response(text=f"Face ID Bot OK | devices:{len(devices)} | pending:{len(pending)}")

# ══════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════
bot_app = None

async def main():
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start",   start))
    bot_app.add_handler(CommandHandler("register",register_cmd))
    bot_app.add_handler(CommandHandler("devices", devices_cmd))
    bot_app.add_handler(CommandHandler("control", control_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)

    http = web.Application()
    http.router.add_get("/",            healthcheck)
    http.router.add_post("/api/verify", api_verify)
    http.router.add_post("/api/alert",  api_alert)
    http.router.add_post("/api/poll",   api_poll)
    http.router.add_post("/api/result", api_result)
    http.router.add_post("/api/check",  api_check)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(http)
    await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",port).start()
    print(f"✅ Bot running on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
