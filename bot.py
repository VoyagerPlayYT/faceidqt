"""
Face ID Protector — Telegram Bot
Деплой на Render.com (Free tier)
"""
import os, uuid, json, asyncio, logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

# ══════════════════════════════════════════════
#  КОНФИГ — вставь свой токен в переменную окружения на Render
# ══════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8669964430:AAHKJOjEBq5Lc9v8ItmW0dN4S-YvkV3ulwc")

# Хранилище устройств: { device_uuid: { "chat_id": 123, "confirmed": True } }
# В продакшене используй базу данных, для простоты — в памяти
devices = {}
# Ожидающие подтверждения: { device_uuid: { "chat_id": 123, "code": "1234" } }
pending = {}

# ══════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 *Face ID Protector Bot*\n\n"
        "Команды:\n"
        "/register `UUID` — привязать устройство\n"
        "/devices — мои устройства\n"
        "/help — помощь\n\n"
        "UUID устройства смотри в приложении → Настройки → UUID",
        parse_mode="Markdown"
    )

async def register_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Укажи UUID: /register ВАШ-UUID")
        return
    
    dev_uuid = ctx.args[0].strip()
    chat_id = update.effective_chat.id
    
    # Генерируем код подтверждения
    import random
    code = str(random.randint(100000, 999999))
    pending[dev_uuid] = {"chat_id": chat_id, "code": code, "time": datetime.now().timestamp()}
    
    await update.message.reply_text(
        f"📱 *Подтверждение устройства*\n\n"
        f"UUID: `{dev_uuid}`\n\n"
        f"Введи этот код в приложении на своём ПК:\n"
        f"*{code}*\n\n"
        f"Код действителен 5 минут.",
        parse_mode="Markdown"
    )

async def devices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my_devs = [uid for uid, d in devices.items() if d["chat_id"] == chat_id]
    
    if not my_devs:
        await update.message.reply_text("У вас нет привязанных устройств.\nИспользуйте /register UUID")
        return
    
    txt = "📱 *Ваши устройства:*\n\n"
    for d in my_devs:
        txt += f"• `{d}`\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

# ══════════════════════════════════════════════
#  API ENDPOINTS для C++ приложения
#  (через webhook или простой HTTP сервер)
# ══════════════════════════════════════════════

from aiohttp import web

app_http = web.Application()

async def api_verify_code(request):
    """C++ приложение проверяет код подтверждения"""
    data = await request.json()
    dev_uuid = data.get("uuid")
    code = data.get("code")
    
    if dev_uuid in pending:
        p = pending[dev_uuid]
        # Проверяем не истёк ли код (5 минут)
        if datetime.now().timestamp() - p["time"] > 300:
            del pending[dev_uuid]
            return web.json_response({"ok": False, "error": "expired"})
        
        if p["code"] == code:
            # Подтверждаем устройство
            devices[dev_uuid] = {"chat_id": p["chat_id"], "confirmed": True}
            del pending[dev_uuid]
            
            # Уведомляем пользователя
            await bot_app.bot.send_message(
                p["chat_id"],
                f"✅ *Устройство подтверждено!*\n\nUUID: `{dev_uuid}`\n\n"
                f"Теперь вы будете получать уведомления о попытках входа.",
                parse_mode="Markdown"
            )
            return web.json_response({"ok": True})
    
    return web.json_response({"ok": False, "error": "invalid"})

async def api_alert(request):
    """C++ приложение присылает уведомление о попытке взлома"""
    data = await request.json()
    dev_uuid = data.get("uuid")
    
    if dev_uuid not in devices:
        return web.json_response({"ok": False, "error": "device not found"})
    
    chat_id = devices[dev_uuid]["chat_id"]
    screenshot_b64 = data.get("screenshot")  # base64
    camera_b64 = data.get("camera")          # base64
    timestamp = data.get("time", datetime.now().strftime("%H:%M:%S %d.%m.%Y"))
    attempts = data.get("attempts", 1)
    
    # Отправляем текст
    await bot_app.bot.send_message(
        chat_id,
        f"🚨 *ПОПЫТКА НЕСАНКЦИОНИРОВАННОГО ВХОДА*\n\n"
        f"🕐 Время: {timestamp}\n"
        f"💻 Устройство: `{dev_uuid[:8]}...`\n"
        f"❌ Попыток: {attempts}\n\n"
        f"_Кто-то пытается получить доступ к вашему ПК!_",
        parse_mode="Markdown"
    )
    
    # Отправляем фото с камеры
    if camera_b64:
        import base64
        img_bytes = base64.b64decode(camera_b64)
        await bot_app.bot.send_photo(chat_id, photo=img_bytes, caption="📸 Фото с камеры")
    
    # Отправляем скриншот экрана
    if screenshot_b64:
        import base64
        img_bytes = base64.b64decode(screenshot_b64)
        await bot_app.bot.send_photo(chat_id, photo=img_bytes, caption="🖥️ Скриншот экрана")
    
    return web.json_response({"ok": True})

async def api_check_uuid(request):
    """C++ проверяет зарегистрировано ли устройство"""
    data = await request.json()
    dev_uuid = data.get("uuid")
    registered = dev_uuid in devices
    return web.json_response({"registered": registered})

app_http.router.add_post("/api/verify", api_verify_code)
app_http.router.add_post("/api/alert", api_alert)
app_http.router.add_post("/api/check", api_check_uuid)
app_http.router.add_get("/", lambda r: web.Response(text="Face ID Bot OK"))

# ══════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════
bot_app = None

async def main():
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("register", register_cmd))
    bot_app.add_handler(CommandHandler("devices", devices_cmd))
    
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    
    # Запускаем HTTP сервер
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app_http)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    print(f"Bot started, HTTP on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
