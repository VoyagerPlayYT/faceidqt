"""
Face ID Protector Bot v5 — ПОЛНАЯ ВЕРСИЯ
+ 2FA через Telegram
+ Диспетчер задач (процессы + kill)
+ Режим Паника
+ Поиск файлов
+ Аудио с микрофона
+ USB блокировка
+ Сон / Гибернация
+ Авто-скриншоты
+ История входов
+ Мониторинг CPU/RAM в веб-панели
+ WebSocket прямой эфир
"""

import os, json, asyncio, logging, base64, random, time, urllib.parse, string
from datetime import datetime
from aiohttp import web
import aiohttp as aiohttp_lib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "8669964430:AAGQI6ZlTv_MUlo50s3j9rplbM_Rfi6GfFo")
JSONBIN_ID  = os.environ.get("JSONBIN_ID",  "")
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

logger.info(f"Bot starting with token: {BOT_TOKEN[:20]}...")
logger.info(f"JSONBIN configured: {bool(JSONBIN_ID)}")

# ══════════════════════════════════════════════════
#  ХРАНИЛИЩЕ
# ══════════════════════════════════════════════════
async def load_data_remote():
    """Загружает данные с JsonBin"""
    if not JSONBIN_ID or not JSONBIN_KEY:
        logger.warning("JSONBIN not configured, using in-memory storage")
        return {"devices": {}, "pending": {}}
    try:
        async with aiohttp_lib.ClientSession() as s:
            async with s.get(JSONBIN_URL + "/latest",
                headers={"X-Master-Key": JSONBIN_KEY}, timeout=aiohttp_lib.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d = await r.json()
                    logger.info(f"Loaded data: {len(d.get('record', {}).get('devices', {}))} devices")
                    return d.get("record", {"devices": {}, "pending": {}})
    except Exception as e:
        logger.error(f"Load error: {e}")
    return {"devices": {}, "pending": {}}

async def save_data():
    """Сохраняет данные в JsonBin"""
    if not JSONBIN_ID or not JSONBIN_KEY:
        return
    try:
        async with aiohttp_lib.ClientSession() as s:
            await s.put(JSONBIN_URL,
                headers={"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"},
                json={"devices": devices, "pending": pending},
                timeout=aiohttp_lib.ClientTimeout(total=10))
        logger.debug("Data saved to JSONBIN")
    except Exception as e:
        logger.error(f"Save error: {e}")

# ══════════════════════════════════════════════════
#  ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# ══════════════════════════════════════════════════
devices       = {}      # uuid -> {chat_id, name, time}
used_tokens   = set()   # одноразовые токены (in-memory)
pending       = {}      # uuid -> {token, name, time, chat_id, verify_code}
commands      = {}      # uuid -> {cmd, time}
file_results  = {}      # uuid -> {entries, path}
last_images   = {}      # uuid -> {screenshot, camera}
ws_clients    = {}      # uuid -> set of websockets
tfa_codes     = {}      # uuid -> {code, chat_id, time}
search_results= {}      # uuid -> list
process_list  = {}      # uuid -> list
audio_results = {}      # uuid -> base64 audio
login_history = {}      # uuid -> list of events
usb_blocked   = {}      # uuid -> bool
autoscr_tasks = {}      # uuid -> asyncio.Task
sysmon_data   = {}      # uuid -> {cpu, ram, time}

# ══════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════
def main_keyboard(uuid):
    """Основная клавиатура управления"""
    usb_label = "🔌 USB разблокировать" if usb_blocked.get(uuid) else "🔌 USB заблокировать"
    autoscr_label = "⏹ Авто-скрин СТОП" if uuid in autoscr_tasks else "📷 Авто-скрин"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Скриншот",      callback_data=f"screenshot|{uuid}"),
         InlineKeyboardButton("📷 Камера",        callback_data=f"camera|{uuid}"),
         InlineKeyboardButton("📊 Статус",        callback_data=f"status|{uuid}")],
        [InlineKeyboardButton("🔒 Заблокировать", callback_data=f"lock|{uuid}"),
         InlineKeyboardButton("🔐 Face ID",       callback_data=f"faceid|{uuid}"),
         InlineKeyboardButton("🎥 Эфир",          callback_data=f"stream|{uuid}")],
        [InlineKeyboardButton("📱 Приложения",    callback_data=f"listapps|{uuid}"),
         InlineKeyboardButton("📁 Файлы",         callback_data=f"files|{uuid}"),
         InlineKeyboardButton("🔍 Поиск файлов",  callback_data=f"searchfiles|{uuid}")],
        [InlineKeyboardButton("💻 Процессы",      callback_data=f"processes|{uuid}"),
         InlineKeyboardButton("🎙 Аудио 15с",     callback_data=f"audio|{uuid}"),
         InlineKeyboardButton("🚨 ПАНИКА",        callback_data=f"panic|{uuid}")],
        [InlineKeyboardButton("😴 Сон",           callback_data=f"sleep|{uuid}"),
         InlineKeyboardButton("❄️ Гибернация",    callback_data=f"hibernate|{uuid}"),
         InlineKeyboardButton("🚀 FaceID",        callback_data=f"launch_faceid|{uuid}")],
        [InlineKeyboardButton(usb_label,           callback_data=f"usb_toggle|{uuid}"),
         InlineKeyboardButton(autoscr_label,       callback_data=f"autoscr|{uuid}")],
        [InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"reboot|{uuid}"),
         InlineKeyboardButton("⏻ Выключить",      callback_data=f"shutdown|{uuid}")],
        [InlineKeyboardButton("📋 История входов", callback_data=f"history|{uuid}")],
        [InlineKeyboardButton("🗑 Удалить устройство", callback_data=f"delete_confirm|{uuid}")],
        [InlineKeyboardButton("🌐 Веб-панель",    url=f"https://faceidqt.onrender.com/panel/{uuid}")],
    ])

def confirm_delete_keyboard(uuid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete_yes|{uuid}"),
         InlineKeyboardButton("❌ Отмена",      callback_data=f"select|{uuid}")]
    ])

def back_keyboard(uuid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=f"select|{uuid}")]])

# ══════════════════════════════════════════════════
#  УТИЛИТЫ
# ══════════════════════════════════════════════════
def generate_verify_code(length=16):
    """Генерирует 16-символьный код верификации"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.SystemRandom().choice(chars) for _ in range(length))

# ══════════════════════════════════════════════════
#  ФАЙЛОВЫЙ МЕНЕДЖЕР
# ══════════════════════════════════════════════════
QUICK_FOLDERS = [
    ("🖥️ Рабочий стол", "DESKTOP"),
    ("📥 Загрузки", "DOWNLOADS"),
    ("📄 Документы", "DOCUMENTS"),
    ("🖼️ Изображения", "PICTURES"),
    ("🎵 Музыка", "MUSIC"),
    ("🎬 Видео", "VIDEOS"),
    ("💾 Диск C:", "C:"),
    ("💾 Диск D:", "D:"),
]

def get_file_icon(name):
    """Возвращает иконку для файла"""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    m = {
        "jpg": "🖼️", "jpeg": "🖼️", "png": "🖼️", "gif": "🖼️",
        "mp4": "🎬", "avi": "🎬", "mkv": "🎬",
        "mp3": "🎵", "wav": "🎵", "flac": "🎵",
        "pdf": "📕", "doc": "📝", "docx": "📝",
        "txt": "📄", "xlsx": "📊", "zip": "🗜️",
        "rar": "🗜️", "7z": "🗜️",
        "exe": "⚙️", "msi": "⚙️",
        "py": "🐍", "cpp": "💻", "js": "💻", "html": "🌐"
    }
    return m.get(ext, "📄")

async def show_file_browser(query, uuid, path):
    """Показывает файловый браузер"""
    if path == "root":
        kb = [[InlineKeyboardButton(label, callback_data=f"browse:{urllib.parse.quote(key, safe='')}|{uuid}")]
              for label, key in QUICK_FOLDERS]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"select|{uuid}")])
        await query.edit_message_text("📁 *Файловый менеджер*\nВыбери папку:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    decoded = urllib.parse.unquote(path)
    commands[uuid] = {"cmd": f"listdir:{decoded}", "time": time.time()}
    for _ in range(20):
        await asyncio.sleep(0.5)
        if uuid in file_results:
            break

    result = file_results.pop(uuid, None)
    if not result:
        await query.edit_message_text("⏳ ПК не ответил.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📁 Корень", callback_data=f"files|{uuid}"),
                InlineKeyboardButton("◀️ Назад",  callback_data=f"select|{uuid}")]]))
        return

    entries = result.get("entries", [])
    folders = [e for e in entries if e["type"] == "dir"]
    files   = [e for e in entries if e["type"] == "file"]
    kb = []
    
    if "\\" in decoded and decoded not in ("C:\\", "D:\\", "E:\\"):
        parent = decoded.rsplit("\\", 1)[0]
        if parent.endswith(":"):
            parent += "\\"
        kb.append([InlineKeyboardButton("⬆️ ..", callback_data=f"browse:{urllib.parse.quote(parent, safe='')}|{uuid}")])
    else:
        kb.append([InlineKeyboardButton("📁 Быстрые папки", callback_data=f"files|{uuid}")])
    
    for e in folders[:18]:
        full = decoded.rstrip("\\") + "\\" + e["name"]
        kb.append([InlineKeyboardButton(f"📁 {e['name']}", callback_data=f"browse:{urllib.parse.quote(full, safe='')}|{uuid}")])
    
    for e in files[:15]:
        full = decoded.rstrip("\\") + "\\" + e["name"]
        sz = f" {e.get('size_kb', 0)}KB" if e.get('size_kb', 0) < 10240 else f" {e.get('size_kb', 0) // 1024}MB"
        kb.append([InlineKeyboardButton(f"{get_file_icon(e['name'])} {e['name']}{sz}",
            callback_data=f"dlfile:{urllib.parse.quote(full, safe='')}|{uuid}")])
    
    short = decoded[-40:] if len(decoded) > 40 else decoded
    await query.edit_message_text(f"📁 `{short}`\n📂 {len(folders)} папок  📄 {len(files)} файлов",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ══════════════════════════════════════════════════
#  АВТО-СКРИНШОТЫ
# ══════════════════════════════════════════════════
async def auto_screenshot_loop(uuid, interval_min=30):
    """Цикл автоматических скриншотов"""
    chat_id = devices[uuid]["chat_id"]
    try:
        while uuid in autoscr_tasks:
            await asyncio.sleep(interval_min * 60)
            if uuid not in autoscr_tasks or uuid not in devices:
                break
            commands[uuid] = {"cmd": "screenshot_silent", "time": time.time()}
    except asyncio.CancelledError:
        pass

# ══════════════════════════════════════════════════
#  БОТ КОМАНДЫ
# ══════════════════════════════════════════════════
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text(
        "🔐 *Face ID Protector Bot v5*\n\n"
        "/register UUID — привязать устройство\n"
        "/devices — мои устройства\n"
        "/control — управление ПК\n"
        "/getfile UUID путь — скачать файл\n"
        "/search UUID запрос — поиск файлов\n"
        "/history UUID — история входов",
        parse_mode="Markdown")

async def register_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /register"""
    await update.message.reply_text(
        "ℹ️ Используй `/connect ТОКЕН`\n\n"
        "Токен берётся из приложения FaceID Protector\n"
        "(кнопка ✈️ Telegram → одноразовый токен)",
        parse_mode="Markdown")

async def connect_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Привязка через одноразовый токен"""
    chat_id = update.effective_chat.id
    # If token provided as argument - process it
    if ctx.args:
        token = ctx.args[0].strip()
    else:
        # Ask user to just type the token
        ctx.user_data["waiting_token"] = True
        await update.message.reply_text(
            "📱 *Привязка устройства*\n\n"
            "1️⃣ Открой FaceID Protector\n"
            "2️⃣ Нажми ✈️ *Telegram*\n"
            "3️⃣ Скопируй токен (16 символов)\n"
            "4️⃣ Просто отправь токен сюда\n\n"
            "⏳ Жду токен...",
            parse_mode="Markdown")
        return
    token = token
    if len(token) != 16:
        await update.message.reply_text("❌ Токен должен быть ровно 16 символов.")
        return

    if token in used_tokens:
        await update.message.reply_text("❌ Этот токен уже использован.")
        return

    found_uuid = None
    for uuid, p in list(pending.items()):
        if p.get("token") == token:
            if time.time() - p["time"] > 300:
                del pending[uuid]
                await update.message.reply_text("⌛ Токен истёк. Нажми кнопку в приложении снова.")
                return
            found_uuid = uuid
            break

    if not found_uuid:
        await update.message.reply_text(
            "❌ Токен не найден.\n\n"
            "Убедись что:\n"
            "• Токен скопирован правильно\n"
            "• Прошло не более 5 минут\n"
            "• FaceID Protector запущен")
        return

    verify_code = generate_verify_code(16)
    pending[found_uuid]["verify_code"] = verify_code
    pending[found_uuid]["chat_id"] = chat_id
    used_tokens.add(token)
    await save_data()

    await update.message.reply_text(
        f"📱 *Устройство найдено!*\n\n"
        f"Введи этот код в приложении FaceID Protector:\n\n"
        f"🔑 `{verify_code}`\n\n"
        f"⏱ Код действует **5 минут**\n"
        f"🔒 Одноразовый — после ввода удалится",
        parse_mode="Markdown")

async def devices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /devices"""
    chat_id = update.effective_chat.id
    my = [(u, d) for u, d in devices.items() if int(d.get("chat_id", 0)) == int(chat_id)]
    if not my:
        await update.message.reply_text("Нет устройств.\n/register UUID")
        return
    txt = "📱 *Ваши устройства:*\n\n"
    for u, d in my:
        txt += f"• `{u[:8]}...` — {d.get('name', 'ПК')}\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def control_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /control"""
    chat_id = update.effective_chat.id
    logger.info(f"/control from chat_id={chat_id}, total_devices={len(devices)}")
    my = [(u, d) for u, d in devices.items() if int(d.get("chat_id", 0)) == int(chat_id)]
    logger.info(f"/control found {len(my)} devices for this chat: {[u[:8] for u,d in my]}")
    if not my:
        await update.message.reply_text("Нет устройств. Используй /connect ТОКЕН")
        return
    if len(my) == 1:
        uuid, d = my[0]
        await update.message.reply_text(
            f"🖥️ *{d.get('name', 'ПК')}*", parse_mode="Markdown",
            reply_markup=main_keyboard(uuid))
    else:
        kb = []
        for u, d in my:
            kb.append([InlineKeyboardButton(f"🖥️ {d.get('name','ПК')} ({u[:8]})", callback_data=f"select|{u}")])
        kb.append([InlineKeyboardButton("🗑 Удалить все старые", callback_data=f"deleteall|{chat_id}")])
        await update.message.reply_text(
            f"📱 Найдено {len(my)} устройств. Выбери:",
            reply_markup=InlineKeyboardMarkup(kb))

async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /delete UUID - удалить устройство"""
    chat_id = update.effective_chat.id
    if not ctx.args:
        my = [(u, d) for u, d in devices.items() if int(d.get("chat_id", 0)) == int(chat_id)]
        if not my:
            await update.message.reply_text("Нет устройств.")
            return
        txt = "Используй:
"
        for u, d in my:
            txt += f"`/delete {u}`
"
        await update.message.reply_text(txt, parse_mode="Markdown")
        return
    uuid = ctx.args[0].strip()
    if uuid in devices and int(devices[uuid].get("chat_id", 0)) == int(chat_id):
        del devices[uuid]
        await save_data()
        await update.message.reply_text(f"✅ Устройство `{uuid[:8]}...` удалено.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Устройство не найдено.")

async def getfile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /getfile"""
    if len(ctx.args) < 2:
        await update.message.reply_text("❌ /getfile UUID путь")
        return
    dev_uuid, filepath = ctx.args[0].strip(), " ".join(ctx.args[1:]).strip()
    chat_id = update.effective_chat.id
    if dev_uuid not in devices or devices[dev_uuid].get("chat_id") != chat_id:
        await update.message.reply_text("❌ Устройство не найдено.")
        return
    commands[dev_uuid] = {"cmd": f"sendfile:{filepath}", "time": time.time()}
    await update.message.reply_text(f"📁 Запрашиваю `{filepath}`", parse_mode="Markdown")

async def search_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /search"""
    if len(ctx.args) < 2:
        await update.message.reply_text("❌ /search UUID запрос")
        return
    dev_uuid, query_str = ctx.args[0].strip(), " ".join(ctx.args[1:]).strip()
    chat_id = update.effective_chat.id
    if dev_uuid not in devices or devices[dev_uuid].get("chat_id") != chat_id:
        await update.message.reply_text("❌ Устройство не найдено.")
        return
    commands[dev_uuid] = {"cmd": f"searchfiles:{query_str}", "time": time.time()}
    await update.message.reply_text(f"🔍 Ищу `{query_str}`...", parse_mode="Markdown")

async def history_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /history"""
    if not ctx.args:
        await update.message.reply_text("❌ /history UUID")
        return
    dev_uuid = ctx.args[0].strip()
    chat_id = update.effective_chat.id
    if dev_uuid not in devices or devices[dev_uuid].get("chat_id") != chat_id:
        await update.message.reply_text("❌ Устройство не найдено.")
        return
    hist = login_history.get(dev_uuid, [])
    if not hist:
        await update.message.reply_text("📋 История входов пуста.")
        return
    txt = "📋 *История входов:*\n\n"
    for h in hist[-10:]:
        icon = "✅" if h["success"] else "❌"
        txt += f"{icon} {h['time']} — {h.get('user', '?')}\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def file_upload_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка загруженных файлов"""
    chat_id = update.effective_chat.id
    my = [(u, d) for u, d in devices.items() if int(d.get("chat_id", 0)) == int(chat_id)]
    if not my:
        await update.message.reply_text("❌ Нет привязанных устройств.")
        return
    if len(my) > 1:
        await update.message.reply_text("❌ Укажи устройство через /control")
        return
    uuid = my[0][0]
    doc = update.message.document or (update.message.photo[-1] if update.message.photo else None)
    if not doc:
        await update.message.reply_text("❌ Файл не найден.")
        return
    fname = getattr(doc, 'file_name', 'upload.jpg') if hasattr(doc, 'file_name') else 'photo.jpg'
    msg = await update.message.reply_text(f"📤 Загружаю `{fname}` на ПК...", parse_mode="Markdown")
    file = await doc.get_file()
    data = await file.download_as_bytearray()
    b64 = base64.b64encode(data).decode()
    commands[uuid] = {"cmd": f"receivefile:{fname}:{b64}", "time": time.time()}

# ══════════════════════════════════════════════════
#  КНОПКИ
# ══════════════════════════════════════════════════
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query   = update.callback_query
    await query.answer()
    data    = query.data
    chat_id = query.from_user.id
    
    if "|" not in data:
        return
    
    cmd, uuid = data.split("|", 1)

    # Удаление
    if cmd == "delete_confirm":
        name = devices.get(uuid, {}).get("name", "ПК")
        await query.edit_message_text(
            f"🗑 *Удалить устройство?*\n\n`{uuid[:8]}...` — {name}",
            parse_mode="Markdown", reply_markup=confirm_delete_keyboard(uuid))
        return
    
    if cmd == "delete_yes":
        if uuid in devices and devices[uuid].get("chat_id") == chat_id:
            del devices[uuid]
            await save_data()
            await query.edit_message_text("✅ Устройство удалено.")
        else:
            await query.edit_message_text("❌ Нет доступа.")
        return

    if cmd == "deleteall":
        # uuid here is actually chat_id
        cid = int(uuid)
        to_delete = [u for u,d in devices.items() if int(d.get("chat_id",0))==cid]
        # Keep only the most recent one
        if len(to_delete) > 1:
            for u in to_delete[:-1]:
                del devices[u]
            await save_data()
            await query.edit_message_text(f"✅ Удалено {len(to_delete)-1} старых устройств.")
        return

    if cmd.startswith("select"):
        name = devices.get(uuid, {}).get("name", "ПК")
        await query.edit_message_text(f"🖥️ *{name}*", parse_mode="Markdown",
            reply_markup=main_keyboard(uuid))
        return

    if uuid not in devices or devices[uuid].get("chat_id") != chat_id:
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    # Файловый менеджер
    if cmd == "files":
        await show_file_browser(query, uuid, "root")
        return
    
    if cmd.startswith("browse:"):
        await show_file_browser(query, uuid, cmd[7:])
        return
    
    if cmd.startswith("dlfile:"):
        filepath = urllib.parse.unquote(cmd[7:])
        commands[uuid] = {"cmd": f"sendfile:{filepath}", "time": time.time()}
        name = filepath.rsplit("\\", 1)[-1]
        await query.edit_message_text(f"📥 Скачиваю `{name}`...", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=f"files|{uuid}")]]))
        return

    # Поиск файлов
    if cmd == "searchfiles":
        await query.edit_message_text(
            "🔍 *Поиск файлов*\n\nОтправь название файла:",
            parse_mode="Markdown", reply_markup=back_keyboard(uuid))
        ctx.user_data["waiting_search"] = uuid
        return

    # История входов
    if cmd == "history":
        hist = login_history.get(uuid, [])
        if not hist:
            await query.edit_message_text("📋 История входов пуста.", reply_markup=back_keyboard(uuid))
            return
        txt = "📋 *История входов:*\n\n"
        for h in hist[-10:]:
            icon = "✅" if h["success"] else "❌"
            txt += f"{icon} {h['time']} — {h.get('user', '?')}\n"
        await query.edit_message_text(txt, parse_mode="Markdown", reply_markup=back_keyboard(uuid))
        return

    # Процессы
    if cmd == "processes":
        commands[uuid] = {"cmd": "getprocesses", "time": time.time()}
        await query.edit_message_text("💻 Загружаю процессы...", reply_markup=back_keyboard(uuid))
        return

    # Аудио
    if cmd == "audio":
        commands[uuid] = {"cmd": "recordaudio:15", "time": time.time()}
        await query.edit_message_text("🎙 Запись 15 сек...", reply_markup=back_keyboard(uuid))
        return

    # ПАНИКА
    if cmd == "panic":
        commands[uuid] = {"cmd": "panic", "time": time.time()}
        await query.edit_message_text(
            "🚨 *РЕЖИМ ПАНИКИ АКТИВИРОВАН*\n\n🔒 Блокировка\n📸 Серия фото\n🔊 Сирена",
            parse_mode="Markdown", reply_markup=back_keyboard(uuid))
        return

    # USB
    if cmd == "usb_toggle":
        action = "usb_unblock" if usb_blocked.get(uuid) else "usb_block"
        usb_blocked[uuid] = not usb_blocked.get(uuid, False)
        commands[uuid] = {"cmd": action, "time": time.time()}
        label = "разблокированы" if not usb_blocked[uuid] else "заблокированы"
        await query.edit_message_text(f"🔌 USB {label}", reply_markup=back_keyboard(uuid))
        return

    # Сон / Гибернация
    if cmd in ("sleep", "hibernate"):
        commands[uuid] = {"cmd": cmd, "time": time.time()}
        label = "😴 ПК уходит в сон..." if cmd == "sleep" else "❄️ ПК уходит в гибернацию..."
        await query.edit_message_text(label, reply_markup=back_keyboard(uuid))
        return

    # Авто-скриншоты
    if cmd == "autoscr":
        if uuid in autoscr_tasks:
            autoscr_tasks[uuid].cancel()
            del autoscr_tasks[uuid]
            await query.edit_message_text("⏹ Авто-скриншоты остановлены.", reply_markup=back_keyboard(uuid))
        else:
            task = asyncio.create_task(auto_screenshot_loop(uuid, 30))
            autoscr_tasks[uuid] = task
            await query.edit_message_text("📷 Авто-скриншоты каждые 30 мин.", reply_markup=back_keyboard(uuid))
        return

    # Приложения
    if cmd == "listapps":
        commands[uuid] = {"cmd": "listapps", "time": time.time()}
        await query.edit_message_text("📱 Загружаю...", reply_markup=back_keyboard(uuid))
        return
    
    if cmd.startswith("launchapp:"):
        commands[uuid] = {"cmd": cmd, "time": time.time()}
        await query.answer("▶️ Запускаю...")
        return

    # FaceID запуск
    if cmd == "launch_faceid":
        commands[uuid] = {"cmd": "launch_faceid", "time": time.time()}
        await query.edit_message_text("🚀 Запускаю FaceID Protector...", reply_markup=back_keyboard(uuid))
        return

    # Остальные команды
    cmd_names = {
        "screenshot": "📸 Скриншот...",
        "camera": "📷 Камера...",
        "lock": "🔒 Блокирую...",
        "reboot": "🔄 Перезагружаю...",
        "shutdown": "⏻ Выключаю...",
        "stream": "🎥 Эфир...",
        "faceid": "🔐 Face ID...",
        "status": "📊 Статус...",
    }
    commands[uuid] = {"cmd": cmd, "time": time.time()}
    await query.edit_message_text(cmd_names.get(cmd, "Команда отправлена..."),
        reply_markup=back_keyboard(uuid))

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка текста"""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # 2FA проверка
    for uuid, tfa in list(tfa_codes.items()):
        if int(tfa["chat_id"]) == int(chat_id) and tfa["code"] == text:
            if time.time() - tfa["time"] < 300:
                commands[uuid] = {"cmd": "tfa_ok", "time": time.time()}
                del tfa_codes[uuid]
                await update.message.reply_text("✅ Код принят! Доступ разрешён.", reply_markup=main_keyboard(uuid))
                return
            else:
                del tfa_codes[uuid]
                await update.message.reply_text("⌛ Код устарел.")
                return

    # Ожидание токена подключения
    if ctx.user_data.get("waiting_token"):
        ctx.user_data.pop("waiting_token")
        token = text
        if len(token) != 16:
            await update.message.reply_text("❌ Токен должен быть 16 символов. Попробуй снова /connect")
            return
        if token in used_tokens:
            await update.message.reply_text("❌ Этот токен уже использован.")
            return
        found_uuid = None
        for uuid, p in list(pending.items()):
            if p.get("token") == token:
                if time.time() - p["time"] > 300:
                    del pending[uuid]
                    await update.message.reply_text("⌛ Токен истёк. Нажми кнопку в приложении снова.")
                    return
                found_uuid = uuid
                break
        if not found_uuid:
            await update.message.reply_text("❌ Токен не найден. Убедись что FaceID Protector запущен и токен свежий.")
            return
        verify_code = generate_verify_code(16)
        pending[found_uuid]["verify_code"] = verify_code
        pending[found_uuid]["chat_id"] = int(chat_id)
        used_tokens.add(token)
        await save_data()
        await update.message.reply_text(
            f"✅ *Устройство найдено!*\n\n"
            f"Введи этот код в приложении FaceID Protector:\n\n"
            f"🔑 `{verify_code}`\n\n"
            f"⏱ Действует 5 минут",
            parse_mode="Markdown")
        return

    # Поиск файлов
    if "waiting_search" in ctx.user_data:
        uuid = ctx.user_data.pop("waiting_search")
        if uuid in devices and int(devices[uuid].get("chat_id",0)) == int(chat_id):
            commands[uuid] = {"cmd": f"searchfiles:{text}", "time": time.time()}
            await update.message.reply_text(f"🔍 Ищу `{text}` на ПК...", parse_mode="Markdown")

# ══════════════════════════════════════════════════
#  HTTP API
# ══════════════════════════════════════════════════
async def api_verify(request):
    """API: Проверка кода подтверждения"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False, "error": "bad_json"})
    
    dev_uuid = data.get("uuid", "")
    token    = data.get("token", "")
    code     = data.get("code", "")

    if not dev_uuid or not token or not code:
        return web.json_response({"ok": False, "error": "missing_fields"})

    if dev_uuid not in pending:
        return web.json_response({"ok": False, "error": "not_found"})

    p = pending[dev_uuid]

    if p.get("token") != token:
        return web.json_response({"ok": False, "error": "wrong_token"})

    if time.time() - p["time"] > 300:
        del pending[dev_uuid]
        await save_data()
        return web.json_response({"ok": False, "error": "expired"})

    if p.get("verify_code", "") != code:
        return web.json_response({"ok": False, "error": "wrong_code"})

    chat_id = p["chat_id"]
    devices[dev_uuid] = {"chat_id": int(chat_id), "name": data.get("name", "ПК")}
    del pending[dev_uuid]
    used_tokens.add(token)
    await save_data()

    await bot_app.bot.send_message(chat_id,
        f"✅ *Устройство привязано!*\n"
        f"ID: `{dev_uuid[:8]}...`\n\n"
        f"🔒 Токен уничтожен\n/control",
        parse_mode="Markdown")
    
    logger.info(f"Device registered: {dev_uuid[:8]}...")
    return web.json_response({"ok": True})

async def api_alert(request):
    """API: Алерт о попытке входа"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid = data.get("uuid", "")
    if dev_uuid not in devices:
        return web.json_response({"ok": False, "error": "device_not_found"})
    
    chat_id = devices[dev_uuid]["chat_id"]
    ts = data.get("time", datetime.now().strftime("%H:%M:%S %d.%m.%Y"))
    attempts = data.get("attempts", 1)

    # История входов
    if dev_uuid not in login_history:
        login_history[dev_uuid] = []
    login_history[dev_uuid].append({
        "time": ts, "success": False, "user": "Unknown", "attempts": attempts
    })
    if len(login_history[dev_uuid]) > 100:
        login_history[dev_uuid] = login_history[dev_uuid][-100:]

    await bot_app.bot.send_message(chat_id,
        f"🚨 *ПОПЫТКА ВХОДА*\n\n🕐 {ts}\n❌ Попыток: {attempts}",
        parse_mode="Markdown")
    
    if data.get("camera"):
        await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["camera"]),
            caption="📸 Фото злоумышленника")
    
    if data.get("screenshot"):
        await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["screenshot"]),
            caption="🖥️ Скриншот экрана")

    # 2FA
    if attempts >= 2:
        code = str(random.randint(100000, 999999))
        tfa_codes[dev_uuid] = {"code": code, "chat_id": chat_id, "time": time.time()}
        await bot_app.bot.send_message(chat_id,
            f"🔐 *2FA КОД ДЛЯ ВХОДА*\n\n`{code}`\n\n"
            f"Введи этот код на ПК для разблокировки.\nДействует 5 минут.",
            parse_mode="Markdown")
        commands[dev_uuid] = {"cmd": f"tfa_send:{code}", "time": time.time()}

    logger.warning(f"Failed login attempt for {dev_uuid[:8]}... - {attempts} attempts")
    return web.json_response({"ok": True})

async def api_login_success(request):
    """API: Успешный вход"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid = data.get("uuid", "")
    if dev_uuid not in devices:
        return web.json_response({"ok": False})
    
    ts = datetime.now().strftime("%H:%M:%S %d.%m.%Y")
    if dev_uuid not in login_history:
        login_history[dev_uuid] = []
    login_history[dev_uuid].append({
        "time": ts, "success": True, "user": data.get("user", "?")
    })
    
    logger.info(f"Successful login for {dev_uuid[:8]}...")
    return web.json_response({"ok": True})

async def api_poll(request):
    """API: Получение команд"""
    try:
        data = await request.json()
    except:
        return web.json_response({"cmd": None})
    
    dev_uuid = data.get("uuid", "")
    logger.info(f"POLL from {dev_uuid[:8] if dev_uuid else '?'}, has_cmd={dev_uuid in commands}")
    if dev_uuid not in commands:
        return web.json_response({"cmd": None})
    
    cmd = commands.pop(dev_uuid)
    if time.time() - cmd["time"] > 30:
        return web.json_response({"cmd": None})
    
    logger.debug(f"Sent command to {dev_uuid[:8]}...: {cmd['cmd'][:50]}")
    return web.json_response({"cmd": cmd["cmd"]})

async def api_result(request):
    """API: Получение результатов"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid = data.get("uuid", "")
    if dev_uuid not in devices:
        return web.json_response({"ok": False})
    
    chat_id = devices[dev_uuid]["chat_id"]
    cmd = data.get("cmd", "")

    logger.debug(f"Result from {dev_uuid[:8]}...: {cmd}")

    if cmd in ("screenshot", "screenshot_silent", "stream_frame"):
        if data.get("image"):
            if dev_uuid not in last_images:
                last_images[dev_uuid] = {}
            last_images[dev_uuid]["screenshot"] = data["image"]
            # WebSocket рассылка
            if dev_uuid in ws_clients:
                dead = set()
                for ws in ws_clients[dev_uuid]:
                    try:
                        await ws.send_str(json.dumps({"type": "frame", "image": data["image"]}))
                    except:
                        dead.add(ws)
                ws_clients[dev_uuid] -= dead
            if cmd == "screenshot":
                await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["image"]),
                    caption="📸 Скриншот", reply_markup=main_keyboard(dev_uuid))
            elif cmd == "screenshot_silent":
                await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["image"]),
                    caption=f"🤖 Авто-скриншот {datetime.now().strftime('%H:%M')}")

    elif cmd == "camera":
        if data.get("image"):
            if dev_uuid not in last_images:
                last_images[dev_uuid] = {}
            last_images[dev_uuid]["camera"] = data["image"]
            if dev_uuid in ws_clients:
                dead = set()
                for ws in ws_clients[dev_uuid]:
                    try:
                        await ws.send_str(json.dumps({"type": "camera", "image": data["image"]}))
                    except:
                        dead.add(ws)
                ws_clients[dev_uuid] -= dead
            await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["image"]),
                caption="📷 Камера", reply_markup=main_keyboard(dev_uuid))

    elif cmd == "locked":
        await bot_app.bot.send_message(chat_id, "🔒 ПК заблокирован", reply_markup=main_keyboard(dev_uuid))

    elif cmd == "listdir":
        file_results[dev_uuid] = data

    elif cmd == "file":
        if data.get("image"):
            fname = data.get("filename", "file.bin")
            await bot_app.bot.send_document(chat_id,
                document=base64.b64decode(data["image"]), filename=fname, caption=f"📁 {fname}")

    elif cmd == "file_error":
        errs = {
            "no_path": "Путь не указан",
            "not_found": "Файл не найден",
            "too_large": "Файл >50MB"
        }
        await bot_app.bot.send_message(chat_id,
            f"❌ {errs.get(data.get('error', ''), data.get('error', '?'))}")

    elif cmd == "apps_list":
        apps = data.get("apps", [])
        if not apps:
            await bot_app.bot.send_message(chat_id, "📱 Нет приложений.")
            return web.json_response({"ok": True})
        kb = [[InlineKeyboardButton(f"▶️ {a['name']}", callback_data=f"launchapp:{a['idx']}|{dev_uuid}")] for a in apps]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"select|{dev_uuid}")])
        await bot_app.bot.send_message(chat_id, "📱 *Приложения:*",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif cmd == "app_launched":
        await bot_app.bot.send_message(chat_id, f"▶️ Запущено: *{data.get('name', '?')}*",
            parse_mode="Markdown", reply_markup=main_keyboard(dev_uuid))

    elif cmd == "faceid_launched":
        await bot_app.bot.send_message(chat_id, "🚀 FaceID Protector запущен!", reply_markup=main_keyboard(dev_uuid))

    elif cmd == "processes_list":
        procs = data.get("processes", [])
        process_list[dev_uuid] = procs
        if not procs:
            await bot_app.bot.send_message(chat_id, "💻 Нет процессов.")
            return web.json_response({"ok": True})
        kb = []
        for p in procs[:20]:
            kb.append([InlineKeyboardButton(
                f"💀 {p['name']} (PID:{p['pid']})",
                callback_data=f"killproc:{p['pid']}|{dev_uuid}")])
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"select|{dev_uuid}")])
        await bot_app.bot.send_message(chat_id,
            f"💻 *Процессы ({len(procs)}):*",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif cmd == "process_killed":
        await bot_app.bot.send_message(chat_id,
            f"💀 Процесс {data.get('pid', '?')} завершён.", reply_markup=main_keyboard(dev_uuid))

    elif cmd == "audio":
        if data.get("audio"):
            await bot_app.bot.send_audio(chat_id,
                audio=base64.b64decode(data["audio"]),
                filename="mic_record.wav", caption="🎙 Запись микрофона",
                reply_markup=main_keyboard(dev_uuid))

    elif cmd == "search_results":
        results = data.get("results", [])
        if not results:
            await bot_app.bot.send_message(chat_id, "🔍 Ничего не найдено.", reply_markup=main_keyboard(dev_uuid))
            return web.json_response({"ok": True})
        txt = f"🔍 *Найдено {len(results)} файлов:*\n\n"
        kb = []
        for r in results[:15]:
            txt += f"📄 `{r['path'][-50:]}`\n"
            safe = urllib.parse.quote(r['path'], safe='')
            kb.append([InlineKeyboardButton(f"📥 {r['name']}", callback_data=f"dlfile:{safe}|{dev_uuid}")])
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"select|{dev_uuid}")])
        await bot_app.bot.send_message(chat_id, txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    elif cmd == "panic_done":
        await bot_app.bot.send_message(chat_id, "🚨 ПАНИКА выполнена!\n🔒 Заблокировано\n📸 Фото сделаны",
            reply_markup=main_keyboard(dev_uuid))
        if data.get("photos"):
            for ph in data["photos"]:
                await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(ph), caption="📸 Паника")

    elif cmd == "sysmon":
        s = data.get("sysmon", {})
        sysmon_data[dev_uuid] = {**s, "time": time.time()}
        # Рассылаем в WebSocket
        if dev_uuid in ws_clients:
            dead = set()
            for ws in ws_clients[dev_uuid]:
                try:
                    await ws.send_str(json.dumps({"type": "sysmon", "data": s}))
                except:
                    dead.add(ws)
            ws_clients[dev_uuid] -= dead

    elif cmd == "status":
        s = data.get("status", {})
        await bot_app.bot.send_message(chat_id,
            f"📊 *Статус ПК*\n\n🖥️ {s.get('hostname', '?')}\n👤 {s.get('user', '?')}\n"
            f"🔒 {'Заблокирован' if s.get('locked') else 'Разблокирован'}\n"
            f"💾 CPU: {s.get('cpu', '?')}%  RAM: {s.get('ram', '?')}%\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="Markdown", reply_markup=main_keyboard(dev_uuid))

    elif cmd == "file_received":
        await bot_app.bot.send_message(chat_id,
            f"✅ Файл `{data.get('filename', '?')}` сохранён на ПК", parse_mode="Markdown")

    return web.json_response({"ok": True})

async def api_check(request):
    """API: Проверка регистрации"""
    try:
        data = await request.json()
    except:
        return web.json_response({"registered": False})
    return web.json_response({"registered": data.get("uuid", "") in devices})

async def api_connect_token(request):
    """API: Регистрация одноразового токена"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid = data.get("uuid", "")
    token    = data.get("token", "")
    name     = data.get("name", "ПК")
    
    if not dev_uuid or not token or len(dev_uuid) < 32 or len(token) != 16:
        return web.json_response({"ok": False, "error": "invalid"})
    
    pending[dev_uuid] = {"token": token, "name": name, "time": time.time()}
    await save_data()
    
    logger.info(f"Token registered for device: {dev_uuid[:8]}...")
    return web.json_response({"ok": True})

async def api_webcmd(request):
    """API: Команда из веб-панели"""
    try:
        data = await request.json()
    except:
        return web.json_response({"ok": False})
    
    dev_uuid, cmd = data.get("uuid", ""), data.get("cmd", "")
    if dev_uuid not in devices:
        return web.json_response({"ok": False, "error": "not_found"})
    
    commands[dev_uuid] = {"cmd": cmd, "time": time.time()}
    return web.json_response({"ok": True})

async def api_webresult(request):
    """API: Получение изображения для веб-панели"""
    uuid  = request.match_info.get("uuid", "")
    itype = request.match_info.get("type", "screenshot")
    imgs  = last_images.get(uuid, {})
    if itype in imgs:
        img = imgs.pop(itype)
        return web.json_response({"image": img})
    return web.json_response({"image": None})

async def api_sysmon(request):
    """API: Данные мониторинга"""
    uuid = request.match_info.get("uuid", "")
    return web.json_response(sysmon_data.get(uuid, {}))

# ══════════════════════════════════════════════════
#  WEBSOCKET
# ══════════════════════════════════════════════════
async def ws_stream(request):
    """WebSocket для прямого эфира"""
    uuid = request.match_info.get("uuid", "")
    if uuid not in devices:
        return web.Response(status=403)
    
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    if uuid not in ws_clients:
        ws_clients[uuid] = set()
    ws_clients[uuid].add(ws)
    
    logger.info(f"WebSocket connected for {uuid[:8]}... - {len(ws_clients[uuid])} clients")
    
    try:
        async for msg in ws:
            pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if uuid in ws_clients:
            ws_clients[uuid].discard(ws)
            logger.info(f"WebSocket disconnected for {uuid[:8]}... - {len(ws_clients[uuid])} clients left")
    
    return ws

# ══════════════════════════════════════════════════
#  ВЕБ-ПАНЕЛЬ
# ══════════════════════════════════════════════════
async def web_panel(request):
    """HTML веб-панель"""
    uuid = request.match_info.get("uuid", "")
    if uuid not in devices:
        return web.Response(text="Device not found", status=404)
    
    # Определяем протокол корректно
    proto = "wss" if request.secure else "ws"
    host = request.host
    
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>FaceID Protector — {uuid[:8]}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',sans-serif}}
body{{background:#050810;color:#c8d8e8;min-height:100vh}}
.header{{background:rgba(10,16,30,.95);padding:16px 24px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #0d1a2a}}
.header h1{{font-size:18px;letter-spacing:3px;color:#e8f4ff}}
.badge{{background:#1565c0;padding:3px 10px;border-radius:20px;font-size:11px}}
.badge.live{{background:#c62828;animation:pulse 1s infinite}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:20px;max-width:1400px;margin:0 auto}}
@media(max-width:768px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:rgba(10,16,30,.8);border:1px solid #0d1a2a;border-radius:16px;padding:20px}}
.card h2{{font-size:12px;letter-spacing:2px;color:#1a4a70;margin-bottom:16px}}
.btn{{background:linear-gradient(135deg,#1565c0,#0d47a1);border:none;color:white;padding:10px 16px;border-radius:10px;cursor:pointer;font-size:13px;font-weight:600;width:100%;margin-bottom:8px;transition:all .2s}}
.btn:hover{{background:linear-gradient(135deg,#1e88e5,#1565c0);transform:translateY(-1px)}}
.btn.red{{background:linear-gradient(135deg,#c62828,#8e0000)}}
.btn.yellow{{background:linear-gradient(135deg,#f57f17,#e65100)}}
.btn.green{{background:linear-gradient(135deg,#2e7d32,#1b5e20)}}
.btn.purple{{background:linear-gradient(135deg,#6a1b9a,#4a148c)}}
.btn.orange{{background:linear-gradient(135deg,#e65100,#bf360c)}}
.btn-row{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.btn-row3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}}
#screen,#camera-view{{width:100%;border-radius:10px;background:#000;min-height:240px;display:flex;align-items:center;justify-content:center;color:#1a3a50;font-size:13px;overflow:hidden;position:relative}}
#screen img,#camera-view img{{width:100%;border-radius:10px;display:block}}
.fps{{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.7);padding:2px 8px;border-radius:4px;font-size:11px;color:#27ae60}}
.live-dot{{display:inline-block;width:8px;height:8px;background:#e53935;border-radius:50%;margin-right:4px;animation:pulse 1s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.status-dot{{width:8px;height:8px;border-radius:50%;background:#27ae60;display:inline-block;margin-right:6px;animation:pulse 2s infinite}}
.log{{background:#030508;border-radius:8px;padding:12px;font-size:11px;color:#2a5878;height:130px;overflow-y:auto;font-family:monospace}}
.meter{{background:#0d1a2a;border-radius:8px;height:22px;margin:6px 0;overflow:hidden;position:relative}}
.meter-bar{{height:100%;border-radius:8px;transition:width .5s;display:flex;align-items:center;padding-left:8px;font-size:11px;font-weight:600}}
.meter-cpu{{background:linear-gradient(90deg,#1565c0,#42a5f5)}}
.meter-ram{{background:linear-gradient(90deg,#6a1b9a,#ce93d8)}}
#stream-info{{font-size:11px;color:#9b59b6;margin-top:6px;text-align:center}}
.search-box{{width:100%;background:#0d1a2a;border:1px solid #1a3a50;border-radius:8px;padding:10px;color:#c8d8e8;font-size:13px;margin-bottom:8px}}
.search-box::placeholder{{color:#1a4a70}}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🔐 FACE ID PROTECTOR</h1>
    <div style="font-size:11px;color:#1a4a70;margin-top:2px;">
      <span class="status-dot"></span>{uuid[:8]}...
    </div>
  </div>
  <div class="badge" id="liveBadge">ONLINE</div>
</div>
<div class="grid">

  <!-- Экран -->
  <div class="card" style="grid-column:1/-1">
    <h2>🖥️ ПРЯМОЙ ЭФИР</h2>
    <div id="screen" style="min-height:320px;"><span>Нажмите "Начать эфир"</span></div>
    <div id="stream-info"></div>
    <div class="btn-row" style="margin-top:12px">
      <button class="btn" onclick="takeScreenshot()">📸 Скриншот</button>
      <button class="btn purple" id="streamBtn" onclick="toggleStream()">🎥 Начать эфир</button>
    </div>
  </div>

  <!-- Камера + Мониторинг -->
  <div class="card">
    <h2>📷 КАМЕРА</h2>
    <div id="camera-view" style="min-height:180px;"><span>Нажмите для снимка</span></div>
    <button class="btn" onclick="sendCmd('camera')" style="margin-top:12px">📷 Сфотографировать</button>
    <h2 style="margin-top:20px">📊 МОНИТОРИНГ</h2>
    <div style="font-size:11px;color:#1a4a70;margin-bottom:4px">CPU</div>
    <div class="meter"><div class="meter-bar meter-cpu" id="cpuBar" style="width:0%">0%</div></div>
    <div style="font-size:11px;color:#1a4a70;margin-bottom:4px">RAM</div>
    <div class="meter"><div class="meter-bar meter-ram" id="ramBar" style="width:0%">0%</div></div>
    <button class="btn" onclick="sendCmd('status')" style="margin-top:8px">🔄 Обновить</button>
  </div>

  <!-- Управление -->
  <div class="card">
    <h2>⚙️ УПРАВЛЕНИЕ</h2>
    <button class="btn green" onclick="sendCmd('launch_faceid')">🚀 Запустить FaceID Protector</button>
    <button class="btn" onclick="sendCmd('lock')">🔒 Заблокировать ПК</button>
    <button class="btn" onclick="sendCmd('faceid')">🔐 Запросить Face ID</button>
    <button class="btn orange" onclick="if(confirm('Режим ПАНИКИ?'))sendCmd('panic')">🚨 ПАНИКА</button>
    <div class="btn-row">
      <button class="btn yellow" onclick="if(confirm('Перезагрузить?'))sendCmd('reboot')">🔄 Перезагрузить</button>
      <button class="btn red" onclick="if(confirm('Выключить?'))sendCmd('shutdown')">⏻ Выключить</button>
    </div>
    <div class="btn-row" style="margin-top:8px">
      <button class="btn" onclick="sendCmd('sleep')">😴 Сон</button>
      <button class="btn" onclick="sendCmd('hibernate')">❄️ Гибернация</button>
    </div>
    <button class="btn" id="usbBtn" onclick="toggleUsb()">🔌 USB заблокировать</button>
    <button class="btn purple" onclick="sendCmd('recordaudio:15')">🎙 Запись аудио 15с</button>
  </div>

  <!-- Поиск файлов -->
  <div class="card">
    <h2>🔍 ПОИСК ФАЙЛОВ</h2>
    <input class="search-box" id="searchInput" type="text" placeholder="Введите название файла...">
    <button class="btn" onclick="doSearch()">🔍 Найти</button>
    <div id="searchResults" style="font-size:12px;color:#4a8ab0;margin-top:8px;max-height:150px;overflow-y:auto"></div>
  </div>

  <!-- Процессы -->
  <div class="card">
    <h2>💻 ПРОЦЕССЫ</h2>
    <button class="btn" onclick="getProcesses()">🔄 Обновить список</button>
    <div id="processList" style="max-height:200px;overflow-y:auto;margin-top:8px;font-size:12px"></div>
  </div>

  <!-- Лог -->
  <div class="card" style="grid-column:1/-1">
    <h2>📋 ЛОГ</h2>
    <div class="log" id="log">Готов...<br></div>
  </div>
</div>

<script>
const UUID = '{uuid}';
const PROTO = '{proto}';
const HOST = '{host}';
const WS_URL = PROTO + '://' + HOST + '/ws/' + UUID;
let ws=null, streamActive=false, streamInterval=null;
let frameCount=0, fps=0, lastFpsTime=Date.now();
let usbBlocked=false;

function log(msg){{
  const el=document.getElementById('log');
  const t=new Date().toLocaleTimeString();
  el.innerHTML+=`[${{t}}] ${{msg}}<br>`;
  el.scrollTop=el.scrollHeight;
}}

log('🌐 WebSocket URL: ' + WS_URL);

async function sendCmd(cmd){{
  try{{
    const r=await fetch('/api/webcmd',{{method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{uuid:UUID,cmd}})}});
    const d=await r.json();
    log(d.ok?`→ ${{cmd}}`:`✗ ${{cmd}}`);
  }}catch(e){{log('✗ '+e)}}
}}

function takeScreenshot(){{
  sendCmd('screenshot');
  setTimeout(()=>pollImg('screenshot'),3000);
}}

async function pollImg(type){{
  const r=await fetch(`/api/webresult/${{UUID}}/${{type}}`);
  const d=await r.json();
  if(d.image){{
    const el=document.getElementById(type==='camera'?'camera-view':'screen');
    el.innerHTML=`<img src="data:image/jpeg;base64,${{d.image}}"/>`;
    log(`✓ ${{type}} получен`);
  }}else setTimeout(()=>pollImg(type),1000);
}}

function toggleStream(){{
  streamActive=!streamActive;
  const btn=document.getElementById('streamBtn');
  const badge=document.getElementById('liveBadge');
  const info=document.getElementById('stream-info');
  if(streamActive){{
    connectWS();
    btn.textContent='⏹ Остановить';btn.className='btn red';
    badge.textContent='● LIVE';badge.className='badge live';
    info.innerHTML='<span class="live-dot"></span>Прямой эфир';
    streamInterval=setInterval(()=>sendCmd('screenshot'),500);
    log('🎥 Эфир запущен');
  }}else{{
    if(ws){{ws.close();ws=null;}}
    clearInterval(streamInterval);
    btn.textContent='🎥 Начать эфир';btn.className='btn purple';
    badge.textContent='ONLINE';badge.className='badge';
    info.innerHTML='';
    log('⏹ Эфир остановлен');
  }}
}}

function connectWS(){{
  try{{
    ws=new WebSocket(WS_URL);
    ws.onopen=()=>{{
      log('🔗 WebSocket подключен: '+WS_URL);
    }};
    ws.onerror=(e)=>{{
      log('✗ WebSocket ошибка: '+e.type);
      log('💡 Проверь что сервер работает');
    }};
    ws.onmessage=(e)=>{{
      const d=JSON.parse(e.data);
      if(d.type==='frame'&&d.image){{
        document.getElementById('screen').innerHTML=
          `<img src="data:image/jpeg;base64,${{d.image}}"/><div class="fps">${{fps}} fps</div>`;
        frameCount++;
        const now=Date.now();
        if(now-lastFpsTime>=1000){{fps=frameCount;frameCount=0;lastFpsTime=now;}}
      }}else if(d.type==='camera'&&d.image){{
        document.getElementById('camera-view').innerHTML=
          `<img src="data:image/jpeg;base64,${{d.image}}"/>`;
      }}else if(d.type==='sysmon'){{
        updateSysmon(d.data);
      }}
    }};
    ws.onclose=()=>{{
      log('🔌 WebSocket отключен');
      if(streamActive)setTimeout(connectWS,2000);
    }};
  }}catch(e){{
    log('✗ WebSocket ошибка: '+e.message);
  }}
}}

function updateSysmon(data){{
  if(data.cpu!==undefined){{
    const cpu=Math.round(data.cpu);
    document.getElementById('cpuBar').style.width=cpu+'%';
    document.getElementById('cpuBar').textContent=cpu+'%';
  }}
  if(data.ram!==undefined){{
    const ram=Math.round(data.ram);
    document.getElementById('ramBar').style.width=ram+'%';
    document.getElementById('ramBar').textContent=ram+'%';
  }}
}}

async function doSearch(){{
  const q=document.getElementById('searchInput').value.trim();
  if(!q)return;
  sendCmd(`searchfiles:${{q}}`);
  document.getElementById('searchResults').innerHTML='🔍 Ищу...';
  log(`🔍 Поиск: ${{q}}`);
}}

async function getProcesses(){{
  sendCmd('getprocesses');
  document.getElementById('processList').innerHTML='💻 Загружаю...';
}}

function toggleUsb(){{
  usbBlocked=!usbBlocked;
  sendCmd(usbBlocked?'usb_block':'usb_unblock');
  document.getElementById('usbBtn').textContent=
    usbBlocked?'🔌 USB разблокировать':'🔌 USB заблокировать';
}}

# Авто-обновление мониторинга
setInterval(()=>sendCmd('status'),15000);
log('🌐 Панель загружена');
</script>
</body>
</html>"""
    
    return web.Response(text=html, content_type='text/html')

async def healthcheck(request):
    """Health check endpoint"""
    return web.Response(text=f"FaceID Bot v5 | devices:{len(devices)} | pending:{len(pending)}")

# ══════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════
bot_app = None

async def main():
    global bot_app
    
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start",    start))
    bot_app.add_handler(CommandHandler("register", register_cmd))
    bot_app.add_handler(CommandHandler("connect",  connect_cmd))
    bot_app.add_handler(CommandHandler("devices",  devices_cmd))
    bot_app.add_handler(CommandHandler("control",  control_cmd))
    bot_app.add_handler(CommandHandler("getfile",  getfile_cmd))
    bot_app.add_handler(CommandHandler("search",   search_cmd))
    bot_app.add_handler(CommandHandler("history",  history_cmd))
    bot_app.add_handler(CommandHandler("delete",   delete_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, file_upload_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    global devices, pending
    loaded = await load_data_remote()
    devices = loaded.get("devices", {})
    pending = loaded.get("pending", {})
    
    logger.info("Bot initialization started")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    
    logger.info("Bot started successfully, listening for updates")

    # HTTP сервер
    http = web.Application()
    http.router.add_get("/",                            healthcheck)
    http.router.add_get("/panel/{uuid}",                web_panel)
    http.router.add_get("/ws/{uuid}",                   ws_stream)
    http.router.add_post("/api/verify",                 api_verify)
    http.router.add_post("/api/alert",                  api_alert)
    http.router.add_post("/api/poll",                   api_poll)
    http.router.add_post("/api/result",                 api_result)
    http.router.add_post("/api/check",                  api_check)
    http.router.add_post("/api/connect_token",          api_connect_token)
    http.router.add_post("/api/webcmd",                 api_webcmd)
    http.router.add_post("/api/login_success",          api_login_success)
    http.router.add_get("/api/webresult/{uuid}/{type}", api_webresult)
    http.router.add_get("/api/sysmon/{uuid}",           api_sysmon)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(http)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    
    logger.info(f"HTTP server running on port {port}")
    print(f"✅ FaceID Bot v5 running on port {port}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
