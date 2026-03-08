"""
Face ID Protector — Telegram Bot
"""
import os, json, asyncio, logging, base64, random
from datetime import datetime
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# devices[uuid] = {"chat_id": 123}
# pending[uuid] = {"chat_id": 123, "code": "123456", "time": 000}
devices = {}
pending = {}

bot_app = None

# ══════════════ BOT HANDLERS ══════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 *Face ID Protector Bot*\n\n"
        "Команды:\n"
        "/register UUID — привязать устройство\n"
        "/devices — мои устройства\n\n"
        "UUID устройства смотри в приложении → Настройки",
        parse_mode="Markdown"
    )

async def register_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ Укажи UUID:\n/register ВАШ-UUID")
        return
    dev_uuid = ctx.args[0].strip()
    chat_id = update.effective_chat.id
    code = str(random.randint(100000, 999999))
    pending[dev_uuid] = {"chat_id": chat_id, "code": code, "time": datetime.now().timestamp()}
    await update.message.reply_text(
        f"📱 *Подтверждение устройства*\n\n"
        f"UUID: `{dev_uuid}`\n\n"
        f"Введи этот код в приложении на ПК:\n"
        f"*{code}*\n\n"
        f"Код действителен 5 минут.",
        parse_mode="Markdown"
    )

async def devices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [u for u, d in devices.items() if d["chat_id"] == chat_id]
    if not my:
        await update.message.reply_text("Нет привязанных устройств.\n/register UUID")
        return
    txt = "📱 *Ваши устройства:*\n\n" + "\n".join(f"• `{d}`" for d in my)
    await update.message.reply_text(txt, parse_mode="Markdown")

# ══════════════ HTTP API ══════════════

async def api_verify(request):
    data = await request.json()
    dev_uuid = data.get("uuid", "")
    code = data.get("code", "")
    if dev_uuid not in pending:
        return web.json_response({"ok": False, "error": "not_found"})
    p = pending[dev_uuid]
    if datetime.now().timestamp() - p["time"] > 300:
        del pending[dev_uuid]
        return web.json_response({"ok": False, "error": "expired"})
    if p["code"] != code:
        return web.json_response({"ok": False, "error": "wrong_code"})
    devices[dev_uuid] = {"chat_id": p["chat_id"]}
    del pending[dev_uuid]
    await bot_app.bot.send_message(
        p["chat_id"],
        f"✅ *Устройство подтверждено!*\n`{dev_uuid}`\n\nТеперь получаете уведомления о попытках входа.",
        parse_mode="Markdown"
    )
    return web.json_response({"ok": True})

async def api_alert(request):
    data = await request.json()
    dev_uuid = data.get("uuid", "")
    if dev_uuid not in devices:
        return web.json_response({"ok": False, "error": "device_not_found"})
    chat_id = devices[dev_uuid]["chat_id"]
    ts = data.get("time", datetime.now().strftime("%H:%M:%S %d.%m.%Y"))
    attempts = data.get("attempts", 1)
    await bot_app.bot.send_message(
        chat_id,
        f"🚨 *ПОПЫТКА НЕСАНКЦИОНИРОВАННОГО ВХОДА*\n\n"
        f"🕐 {ts}\n"
        f"💻 Устройство: `{dev_uuid[:8]}...`\n"
        f"❌ Попыток: {attempts}",
        parse_mode="Markdown"
    )
    camera_b64 = data.get("camera")
    if camera_b64:
        img = base64.b64decode(camera_b64)
        await bot_app.bot.send_photo(chat_id, photo=img, caption="📸 Фото злоумышленника")
    screenshot_b64 = data.get("screenshot")
    if screenshot_b64:
        img = base64.b64decode(screenshot_b64)
        await bot_app.bot.send_photo(chat_id, photo=img, caption="🖥️ Скриншот экрана")
    return web.json_response({"ok": True})

async def api_check(request):
    data = await request.json()
    dev_uuid = data.get("uuid", "")
    return web.json_response({"registered": dev_uuid in devices})

async def healthcheck(request):
    return web.Response(text="Face ID Bot OK")

# ══════════════ MAIN ══════════════

async def main():
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("register", register_cmd))
    bot_app.add_handler(CommandHandler("devices", devices_cmd))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)

    http = web.Application()
    http.router.add_get("/", healthcheck)
    http.router.add_post("/api/verify", api_verify)
    http.router.add_post("/api/alert", api_alert)
    http.router.add_post("/api/check", api_check)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(http)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"✅ Bot running, HTTP port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
