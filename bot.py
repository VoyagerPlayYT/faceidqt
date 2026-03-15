"""
Face ID Protector Bot v6
Схема подключения:
1. В приложении нажимаешь Telegram -> видишь закодированный UUID
2. Отправляешь боту: /register ЗАКОДИРОВАННЫЙ_UUID
3. Бот присылает 6-значный код
4. Вводишь код в приложении
"""

import os, json, asyncio, logging, base64, random, time, urllib.parse, string
from datetime import datetime
from aiohttp import web
import aiohttp as aiohttp_lib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN   = os.environ.get("BOT_TOKEN", "8669964430:AAGQI6ZlTv_MUlo50s3j9rplbM_Rfi6GfFo")
JSONBIN_ID  = os.environ.get("JSONBIN_ID", "")
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

# Encoding alphabet
HEX_CHARS    = '0123456789abcdef'
CUSTOM_CHARS = 'KQWERTZNXCVBMPLJ'

def decode_uuid(encoded):
    """Decode custom alphabet back to real UUID"""
    result = ''
    for c in encoded:
        pos = CUSTOM_CHARS.find(c)
        if pos != -1:
            result += HEX_CHARS[pos]
    if len(result) == 32:
        return f"{result[0:8]}-{result[8:12]}-{result[12:16]}-{result[16:20]}-{result[20:32]}"
    return result

def encode_uuid(uuid):
    result = ''
    for c in uuid.replace('-','').lower():
        pos = HEX_CHARS.find(c)
        if pos != -1:
            result += CUSTOM_CHARS[pos]
    return result

# Storage
async def load_data():
    if not JSONBIN_ID or not JSONBIN_KEY:
        return {"devices": {}, "pending": {}}
    try:
        async with aiohttp_lib.ClientSession() as s:
            async with s.get(JSONBIN_URL+"/latest",
                headers={"X-Master-Key": JSONBIN_KEY},
                timeout=aiohttp_lib.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d.get("record", {"devices": {}, "pending": {}})
    except Exception as e:
        logger.error(f"Load error: {e}")
    return {"devices": {}, "pending": {}}

async def save_data():
    if not JSONBIN_ID or not JSONBIN_KEY:
        return
    try:
        async with aiohttp_lib.ClientSession() as s:
            await s.put(JSONBIN_URL,
                headers={"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"},
                json={"devices": devices, "pending": pending},
                timeout=aiohttp_lib.ClientTimeout(total=10))
    except Exception as e:
        logger.error(f"Save error: {e}")

# Global state
devices       = {}
pending       = {}  # uuid -> {code, chat_id, time}
commands      = {}
file_results  = {}
last_images   = {}
ws_clients    = {}
tfa_codes     = {}
login_history = {}
usb_blocked   = {}
autoscr_tasks = {}
sysmon_data   = {}

def main_keyboard(uuid):
    usb_label    = "🔓 USB разблокировать" if usb_blocked.get(uuid) else "🔒 USB заблокировать"
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
         InlineKeyboardButton("🔍 Поиск",         callback_data=f"searchfiles|{uuid}")],
        [InlineKeyboardButton("🎙 Аудио 15с",     callback_data=f"audio|{uuid}"),
         InlineKeyboardButton("🚨 ПАНИКА",        callback_data=f"panic|{uuid}"),
         InlineKeyboardButton("🚀 Запустить",     callback_data=f"launch_faceid|{uuid}")],
        [InlineKeyboardButton("😴 Сон",           callback_data=f"sleep|{uuid}"),
         InlineKeyboardButton("❄️ Гибернация",    callback_data=f"hibernate|{uuid}"),
         InlineKeyboardButton("🔄 Перезагрузить", callback_data=f"reboot|{uuid}")],
        [InlineKeyboardButton(usb_label,           callback_data=f"usb_toggle|{uuid}"),
         InlineKeyboardButton(autoscr_label,       callback_data=f"autoscr|{uuid}"),
         InlineKeyboardButton("⏻ Выключить",      callback_data=f"shutdown|{uuid}")],
        [InlineKeyboardButton("📋 История входов", callback_data=f"history|{uuid}"),
         InlineKeyboardButton("🗑 Удалить",        callback_data=f"delete_confirm|{uuid}")],
        [InlineKeyboardButton("🌐 Веб-панель", url=f"https://faceidqt.onrender.com/panel/{uuid}")],
    ])

def back_keyboard(uuid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data=f"back|{uuid}")]])

def confirm_delete_keyboard(uuid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"delete_yes|{uuid}"),
         InlineKeyboardButton("❌ Отмена",      callback_data=f"back|{uuid}")]
    ])

QUICK_FOLDERS = [
    ("🖥️ Рабочий стол", "DESKTOP"),
    ("📥 Загрузки",      "DOWNLOADS"),
    ("📄 Документы",     "DOCUMENTS"),
    ("🖼️ Изображения",  "PICTURES"),
    ("🎵 Музыка",        "MUSIC"),
    ("🎬 Видео",         "VIDEOS"),
    ("💾 Диск C:",       "C:"),
    ("💾 Диск D:",       "D:"),
]

def get_file_icon(name):
    ext = name.rsplit(".",1)[-1].lower() if "." in name else ""
    icons = {"jpg":"🖼️","jpeg":"🖼️","png":"🖼️","gif":"🖼️","mp4":"🎬","avi":"🎬",
             "mp3":"🎵","wav":"🎵","pdf":"📕","doc":"📝","docx":"📝","txt":"📄",
             "xlsx":"📊","zip":"🗜️","rar":"🗜️","exe":"⚙️","py":"🐍","cpp":"💻"}
    return icons.get(ext,"📄")

async def show_file_browser(query, uuid, path):
    if path == "root":
        kb = [[InlineKeyboardButton(label, callback_data=f"browse:{urllib.parse.quote(key,safe='')}|{uuid}")]
              for label,key in QUICK_FOLDERS]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"back|{uuid}")])
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
        await query.edit_message_text("⏳ ПК не ответил.", reply_markup=back_keyboard(uuid))
        return
    entries = result.get("entries",[])
    folders = [e for e in entries if e["type"]=="dir"]
    files   = [e for e in entries if e["type"]=="file"]
    kb = []
    if "\\" in decoded and decoded not in ("C:\\","D:\\"):
        parent = decoded.rsplit("\\",1)[0]
        if parent.endswith(":"): parent+="\\"
        kb.append([InlineKeyboardButton("⬆️ ..", callback_data=f"browse:{urllib.parse.quote(parent,safe='')}|{uuid}")])
    else:
        kb.append([InlineKeyboardButton("📁 Быстрые папки", callback_data=f"files|{uuid}")])
    for e in folders[:18]:
        full = decoded.rstrip("\\")+"\\"+e["name"]
        kb.append([InlineKeyboardButton(f"📁 {e['name']}", callback_data=f"browse:{urllib.parse.quote(full,safe='')}|{uuid}")])
    for e in files[:15]:
        full = decoded.rstrip("\\")+"\\"+e["name"]
        sz = f" {e.get('size_kb',0)}KB" if e.get('size_kb',0)<10240 else f" {e.get('size_kb',0)//1024}MB"
        kb.append([InlineKeyboardButton(f"{get_file_icon(e['name'])} {e['name']}{sz}",
            callback_data=f"dlfile:{urllib.parse.quote(full,safe='')}|{uuid}")])
    short = decoded[-40:] if len(decoded)>40 else decoded
    await query.edit_message_text(f"📁 `{short}`\n📂{len(folders)} папок  📄{len(files)} файлов",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def auto_screenshot_loop(uuid, interval_min=30):
    try:
        while uuid in autoscr_tasks:
            await asyncio.sleep(interval_min*60)
            if uuid not in autoscr_tasks or uuid not in devices: break
            commands[uuid] = {"cmd":"screenshot_silent","time":time.time()}
    except asyncio.CancelledError:
        pass

# ── BOT COMMANDS ──────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 *Face ID Protector Bot v6*\n\n"
        "📱 Как подключить устройство:\n"
        "1. Открой FaceID Protector\n"
        "2. Нажми ✈️ Telegram\n"
        "3. Скопируй команду `/register КОД`\n"
        "4. Отправь её сюда\n"
        "5. Получишь 6-значный код\n"
        "6. Введи код в приложении\n\n"
        "/devices — мои устройства\n"
        "/control — управление ПК\n"
        "/delete UUID — удалить устройство",
        parse_mode="Markdown")

async def register_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text(
            "❌ Использование: `/register КОД`\n\n"
            "КОД берётся из приложения FaceID Protector\n"
            "(кнопка ✈️ Telegram)",
            parse_mode="Markdown")
        return
    encoded = ctx.args[0].strip()
    # Decode to real UUID
    real_uuid = decode_uuid(encoded)
    if len(real_uuid.replace('-','')) != 32:
        await update.message.reply_text("❌ Неверный код устройства.")
        return
    # Generate 6-digit code
    code = str(random.randint(100000, 999999))
    pending[real_uuid] = {"code": code, "chat_id": int(chat_id), "time": time.time()}
    logger.info(f"Register: uuid={real_uuid[:8]}... code={code} chat={chat_id}")
    await update.message.reply_text(
        f"✅ *Устройство найдено!*\n\n"
        f"Введи этот код в приложении FaceID Protector:\n\n"
        f"🔑 `{code}`\n\n"
        f"⏱ Действует 5 минут",
        parse_mode="Markdown")

async def devices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if int(d.get("chat_id",0))==int(chat_id)]
    if not my:
        await update.message.reply_text("Нет устройств.\nНажми ✈️ в приложении.")
        return
    txt = "📱 *Ваши устройства:*\n\n"
    for u,d in my:
        txt += f"• `{u[:8]}...` — {d.get('name','ПК')}\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def control_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if int(d.get("chat_id",0))==int(chat_id)]
    logger.info(f"/control chat={chat_id} found={len(my)}")
    if not my:
        await update.message.reply_text("Нет устройств. Нажми ✈️ в приложении.")
        return
    if len(my)==1:
        uuid,d = my[0]
        await update.message.reply_text(f"🖥️ *{d.get('name','ПК')}*",
            parse_mode="Markdown", reply_markup=main_keyboard(uuid))
    else:
        kb = [[InlineKeyboardButton(f"🖥️ {d.get('name','ПК')} ({u[:8]})",
            callback_data=f"back|{u}")] for u,d in my]
        await update.message.reply_text("Выбери устройство:", reply_markup=InlineKeyboardMarkup(kb))

async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not ctx.args:
        my = [(u,d) for u,d in devices.items() if int(d.get("chat_id",0))==int(chat_id)]
        if not my:
            await update.message.reply_text("Нет устройств.")
            return
        txt = "Устройства:\n"
        for u,d in my:
            txt += f"`/delete {u}`\n"
        await update.message.reply_text(txt, parse_mode="Markdown")
        return
    uuid = ctx.args[0].strip()
    if uuid in devices and int(devices[uuid].get("chat_id",0))==int(chat_id):
        del devices[uuid]
        await save_data()
        await update.message.reply_text("✅ Устройство удалено.")
    else:
        await update.message.reply_text("❌ Не найдено.")

async def getfile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args)<2:
        await update.message.reply_text("❌ /getfile UUID путь")
        return
    dev_uuid,filepath = ctx.args[0].strip()," ".join(ctx.args[1:])
    chat_id = update.effective_chat.id
    if dev_uuid not in devices or int(devices[dev_uuid].get("chat_id",0))!=int(chat_id):
        await update.message.reply_text("❌ Устройство не найдено.")
        return
    commands[dev_uuid] = {"cmd":f"sendfile:{filepath}","time":time.time()}
    await update.message.reply_text(f"📁 Запрашиваю `{filepath}`", parse_mode="Markdown")

async def history_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("❌ /history UUID")
        return
    dev_uuid = ctx.args[0].strip()
    chat_id = update.effective_chat.id
    if dev_uuid not in devices or int(devices[dev_uuid].get("chat_id",0))!=int(chat_id):
        await update.message.reply_text("❌ Устройство не найдено.")
        return
    hist = login_history.get(dev_uuid,[])
    if not hist:
        await update.message.reply_text("📋 История входов пуста.")
        return
    txt = "📋 *История входов:*\n\n"
    for h in hist[-10:]:
        txt += f"{'✅' if h['success'] else '❌'} {h['time']} — {h.get('user','?')}\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def file_upload_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if int(d.get("chat_id",0))==int(chat_id)]
    if not my:
        await update.message.reply_text("❌ Нет устройств.")
        return
    uuid = my[0][0]
    doc = update.message.document or (update.message.photo[-1] if update.message.photo else None)
    if not doc:
        return
    fname = getattr(doc,'file_name','upload.jpg') if hasattr(doc,'file_name') else 'photo.jpg'
    await update.message.reply_text(f"📤 Загружаю `{fname}` на ПК...", parse_mode="Markdown")
    file = await doc.get_file()
    data = await file.download_as_bytearray()
    b64 = base64.b64encode(data).decode()
    commands[uuid] = {"cmd":f"receivefile:{fname}:{b64}","time":time.time()}

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    # 2FA check
    for uuid,tfa in list(tfa_codes.items()):
        if int(tfa["chat_id"])==int(chat_id) and tfa["code"]==text:
            if time.time()-tfa["time"]<300:
                commands[uuid] = {"cmd":"tfa_ok","time":time.time()}
                del tfa_codes[uuid]
                await update.message.reply_text("✅ Код принят!", reply_markup=main_keyboard(uuid))
            else:
                del tfa_codes[uuid]
                await update.message.reply_text("⌛ Код устарел.")
            return
    # Search
    if "waiting_search" in ctx.user_data:
        uuid = ctx.user_data.pop("waiting_search")
        if uuid in devices:
            commands[uuid] = {"cmd":f"searchfiles:{text}","time":time.time()}
            await update.message.reply_text(f"🔍 Ищу `{text}`...", parse_mode="Markdown")

# ── BUTTONS ───────────────────────────────────────

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id
    if "|" not in data:
        return
    cmd, uuid = data.split("|",1)

    if cmd == "back":
        name = devices.get(uuid,{}).get("name","ПК")
        try:
            await query.edit_message_text(f"🖥️ *{name}*", parse_mode="Markdown",
                reply_markup=main_keyboard(uuid))
        except:
            pass
        return

    if cmd == "delete_confirm":
        name = devices.get(uuid,{}).get("name","ПК")
        await query.edit_message_text(f"🗑 Удалить `{uuid[:8]}...` — {name}?",
            parse_mode="Markdown", reply_markup=confirm_delete_keyboard(uuid))
        return

    if cmd == "delete_yes":
        if uuid in devices and int(devices[uuid].get("chat_id",0))==int(chat_id):
            del devices[uuid]
            await save_data()
            await query.edit_message_text("✅ Удалено.")
        return

    if uuid not in devices or int(devices[uuid].get("chat_id",0))!=int(chat_id):
        await query.answer("❌ Нет доступа", show_alert=True)
        return

    if cmd=="files":
        await show_file_browser(query,uuid,"root"); return
    if cmd.startswith("browse:"):
        await show_file_browser(query,uuid,cmd[7:]); return
    if cmd.startswith("dlfile:"):
        fp = urllib.parse.unquote(cmd[7:])
        commands[uuid] = {"cmd":f"sendfile:{fp}","time":time.time()}
        await query.edit_message_text(f"📥 Скачиваю...", reply_markup=back_keyboard(uuid)); return
    if cmd=="searchfiles":
        ctx.user_data["waiting_search"] = uuid
        await query.edit_message_text("🔍 Отправь название файла:", reply_markup=back_keyboard(uuid)); return
    if cmd=="history":
        hist=login_history.get(uuid,[])
        txt="📋 *История:*\n\n" if hist else "📋 История пуста."
        for h in hist[-10:]:
            txt+=f"{'✅' if h['success'] else '❌'} {h['time']}\n"
        await query.edit_message_text(txt,parse_mode="Markdown",reply_markup=back_keyboard(uuid)); return
    if cmd=="audio":
        commands[uuid]={"cmd":"recordaudio:15","time":time.time()}
        await query.edit_message_text("🎙 Запись 15 сек...",reply_markup=back_keyboard(uuid)); return
    if cmd=="panic":
        commands[uuid]={"cmd":"panic","time":time.time()}
        await query.edit_message_text("🚨 *ПАНИКА АКТИВИРОВАНА*",parse_mode="Markdown",reply_markup=back_keyboard(uuid)); return
    if cmd=="usb_toggle":
        action="usb_unblock" if usb_blocked.get(uuid) else "usb_block"
        usb_blocked[uuid]=not usb_blocked.get(uuid,False)
        commands[uuid]={"cmd":action,"time":time.time()}
        await query.edit_message_text(f"🔌 USB {'разблокированы' if not usb_blocked[uuid] else 'заблокированы'}",reply_markup=back_keyboard(uuid)); return
    if cmd in ("sleep","hibernate"):
        commands[uuid]={"cmd":cmd,"time":time.time()}
        await query.edit_message_text("😴 Выполняю..." if cmd=="sleep" else "❄️ Выполняю...",reply_markup=back_keyboard(uuid)); return
    if cmd=="autoscr":
        if uuid in autoscr_tasks:
            autoscr_tasks[uuid].cancel(); del autoscr_tasks[uuid]
            await query.edit_message_text("⏹ Авто-скрин остановлен.",reply_markup=back_keyboard(uuid))
        else:
            autoscr_tasks[uuid]=asyncio.create_task(auto_screenshot_loop(uuid,30))
            await query.edit_message_text("📷 Авто-скрин каждые 30 мин.",reply_markup=back_keyboard(uuid))
        return
    if cmd=="listapps":
        commands[uuid]={"cmd":"listapps","time":time.time()}
        await query.edit_message_text("📱 Загружаю...",reply_markup=back_keyboard(uuid)); return
    if cmd.startswith("launchapp:"):
        commands[uuid]={"cmd":cmd,"time":time.time()}
        await query.answer("▶️ Запускаю..."); return
    if cmd=="launch_faceid":
        commands[uuid]={"cmd":"launch_faceid","time":time.time()}
        await query.edit_message_text("🚀 Запускаю...",reply_markup=back_keyboard(uuid)); return

    labels={"screenshot":"📸 Скриншот...","camera":"📷 Камера...","lock":"🔒 Блокирую...",
            "reboot":"🔄 Перезагружаю...","shutdown":"⏻ Выключаю...","stream":"🎥 Эфир...",
            "faceid":"🔐 Face ID...","status":"📊 Статус..."}
    commands[uuid]={"cmd":cmd,"time":time.time()}
    await query.edit_message_text(labels.get(cmd,"Выполняю..."),reply_markup=back_keyboard(uuid))

# ── HTTP API ──────────────────────────────────────

async def api_verify(request):
    """App sends uuid+code to verify 6-digit code"""
    try: data = await request.json()
    except: return web.json_response({"ok":False,"error":"bad_json"})
    dev_uuid = data.get("uuid","")
    code     = data.get("code","")
    if not dev_uuid or not code:
        return web.json_response({"ok":False,"error":"missing"})
    if dev_uuid not in pending:
        return web.json_response({"ok":False,"error":"not_found"})
    p = pending[dev_uuid]
    if time.time()-p["time"]>300:
        del pending[dev_uuid]
        return web.json_response({"ok":False,"error":"expired"})
    if p["code"]!=code:
        return web.json_response({"ok":False,"error":"wrong_code"})
    chat_id = p["chat_id"]
    devices[dev_uuid] = {"chat_id":int(chat_id),"name":data.get("name","ПК")}
    del pending[dev_uuid]
    await save_data()
    try:
        await bot_app.bot.send_message(chat_id,
            f"✅ *Устройство привязано!*\n`{dev_uuid[:8]}...`\n\n/control",
            parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Notify error: {e}")
    logger.info(f"Device verified: {dev_uuid[:8]}")
    return web.json_response({"ok":True})

async def api_alert(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices:
        return web.json_response({"ok":False})
    chat_id = devices[dev_uuid]["chat_id"]
    ts = data.get("time", datetime.now().strftime("%H:%M:%S %d.%m.%Y"))
    attempts = data.get("attempts",1)
    if dev_uuid not in login_history: login_history[dev_uuid]=[]
    login_history[dev_uuid].append({"time":ts,"success":False,"user":"Unknown","attempts":attempts})
    await bot_app.bot.send_message(chat_id,
        f"🚨 *ПОПЫТКА ВХОДА*\n🕐 {ts}\n❌ Попыток: {attempts}",
        parse_mode="Markdown")
    if data.get("camera"):
        await bot_app.bot.send_photo(chat_id,photo=base64.b64decode(data["camera"]),caption="📸 Фото")
    if data.get("screenshot"):
        await bot_app.bot.send_photo(chat_id,photo=base64.b64decode(data["screenshot"]),caption="🖥️ Скриншот")
    if attempts>=2:
        code=str(random.randint(100000,999999))
        tfa_codes[dev_uuid]={"code":code,"chat_id":chat_id,"time":time.time()}
        await bot_app.bot.send_message(chat_id,
            f"🔐 *2FA КОД*\n\n`{code}`\n\nВведи на ПК. Действует 5 мин.",
            parse_mode="Markdown")
        commands[dev_uuid]={"cmd":f"tfa_send:{code}","time":time.time()}
    return web.json_response({"ok":True})

async def api_poll(request):
    try: data = await request.json()
    except: return web.json_response({"cmd":None})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in commands:
        return web.json_response({"cmd":None})
    cmd = commands.pop(dev_uuid)
    if time.time()-cmd["time"]>30:
        return web.json_response({"cmd":None})
    logger.info(f"CMD -> {dev_uuid[:8]}: {cmd['cmd'][:40]}")
    return web.json_response({"cmd":cmd["cmd"]})

async def api_result(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices:
        return web.json_response({"ok":False})
    chat_id = devices[dev_uuid]["chat_id"]
    cmd = data.get("cmd","")
    logger.info(f"RESULT {dev_uuid[:8]}: {cmd}")

    if cmd in ("screenshot","screenshot_silent","stream_frame"):
        if data.get("image"):
            if dev_uuid not in last_images: last_images[dev_uuid]={}
            last_images[dev_uuid]["screenshot"]=data["image"]
            if dev_uuid in ws_clients:
                dead=set()
                for ws in ws_clients[dev_uuid]:
                    try: await ws.send_str(json.dumps({"type":"frame","image":data["image"]}))
                    except: dead.add(ws)
                ws_clients[dev_uuid]-=dead
            if cmd=="screenshot":
                await bot_app.bot.send_photo(chat_id,photo=base64.b64decode(data["image"]),
                    caption="📸 Скриншот",reply_markup=main_keyboard(dev_uuid))
            elif cmd=="screenshot_silent":
                await bot_app.bot.send_photo(chat_id,photo=base64.b64decode(data["image"]),
                    caption=f"🤖 Авто-скрин {datetime.now().strftime('%H:%M')}")

    elif cmd=="camera":
        if data.get("image"):
            if dev_uuid not in last_images: last_images[dev_uuid]={}
            last_images[dev_uuid]["camera"]=data["image"]
            if dev_uuid in ws_clients:
                dead=set()
                for ws in ws_clients[dev_uuid]:
                    try: await ws.send_str(json.dumps({"type":"camera","image":data["image"]}))
                    except: dead.add(ws)
                ws_clients[dev_uuid]-=dead
            await bot_app.bot.send_photo(chat_id,photo=base64.b64decode(data["image"]),
                caption="📷 Камера",reply_markup=main_keyboard(dev_uuid))

    elif cmd=="locked":
        await bot_app.bot.send_message(chat_id,"🔒 ПК заблокирован",reply_markup=main_keyboard(dev_uuid))

    elif cmd=="listdir":
        file_results[dev_uuid]=data

    elif cmd=="file":
        if data.get("image"):
            fname=data.get("filename","file.bin")
            await bot_app.bot.send_document(chat_id,
                document=base64.b64decode(data["image"]),filename=fname,caption=f"📁 {fname}")

    elif cmd=="file_error":
        errs={"no_path":"Путь не указан","not_found":"Файл не найден","too_large":"Файл >50MB"}
        await bot_app.bot.send_message(chat_id,f"❌ {errs.get(data.get('error',''),data.get('error','?'))}")

    elif cmd=="apps_list":
        apps=data.get("apps",[])
        if not apps:
            await bot_app.bot.send_message(chat_id,"📱 Нет приложений.")
            return web.json_response({"ok":True})
        kb=[[InlineKeyboardButton(f"▶️ {a['name']}",callback_data=f"launchapp:{a['idx']}|{dev_uuid}")] for a in apps]
        kb.append([InlineKeyboardButton("◀️ Назад",callback_data=f"back|{dev_uuid}")])
        await bot_app.bot.send_message(chat_id,"📱 *Приложения:*",
            parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

    elif cmd=="app_launched":
        await bot_app.bot.send_message(chat_id,f"▶️ *{data.get('name','?')}* запущено",
            parse_mode="Markdown",reply_markup=main_keyboard(dev_uuid))

    elif cmd=="faceid_launched":
        await bot_app.bot.send_message(chat_id,"🚀 FaceID запущен!",reply_markup=main_keyboard(dev_uuid))

    elif cmd=="panic_done":
        await bot_app.bot.send_message(chat_id,"🚨 ПАНИКА выполнена!",reply_markup=main_keyboard(dev_uuid))
        if data.get("photos"):
            for ph in data["photos"]:
                await bot_app.bot.send_photo(chat_id,photo=base64.b64decode(ph),caption="📸 Паника")

    elif cmd=="audio":
        if data.get("audio"):
            await bot_app.bot.send_audio(chat_id,
                audio=base64.b64decode(data["audio"]),
                filename="mic.wav",caption="🎙 Микрофон",reply_markup=main_keyboard(dev_uuid))

    elif cmd=="search_results":
        results=data.get("results",[])
        if not results:
            await bot_app.bot.send_message(chat_id,"🔍 Ничего не найдено.",reply_markup=main_keyboard(dev_uuid))
            return web.json_response({"ok":True})
        txt=f"🔍 *Найдено {len(results)}:*\n\n"
        kb=[]
        for r in results[:15]:
            txt+=f"📄 `{r['path'][-50:]}`\n"
            kb.append([InlineKeyboardButton(f"📥 {r['name']}",
                callback_data=f"dlfile:{urllib.parse.quote(r['path'],safe='')}|{dev_uuid}")])
        kb.append([InlineKeyboardButton("◀️ Назад",callback_data=f"back|{dev_uuid}")])
        await bot_app.bot.send_message(chat_id,txt,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

    elif cmd=="status":
        s=data.get("status",{})
        await bot_app.bot.send_message(chat_id,
            f"📊 *Статус ПК*\n🖥️ {s.get('hostname','?')}\n👤 {s.get('user','?')}\n"
            f"🔒 {'Заблокирован' if s.get('locked') else 'Разблокирован'}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="Markdown",reply_markup=main_keyboard(dev_uuid))

    elif cmd=="file_received":
        await bot_app.bot.send_message(chat_id,
            f"✅ Файл `{data.get('filename','?')}` сохранён",parse_mode="Markdown")

    elif cmd=="sysmon":
        s=data.get("sysmon",{})
        sysmon_data[dev_uuid]={**s,"time":time.time()}
        if dev_uuid in ws_clients:
            dead=set()
            for ws in ws_clients[dev_uuid]:
                try: await ws.send_str(json.dumps({"type":"sysmon","data":s}))
                except: dead.add(ws)
            ws_clients[dev_uuid]-=dead

    return web.json_response({"ok":True})

async def api_connect_token(request):
    """Legacy endpoint - not used in new scheme"""
    return web.json_response({"ok":True})

async def api_check(request):
    try: data = await request.json()
    except: return web.json_response({"registered":False})
    return web.json_response({"registered":data.get("uuid","") in devices})

async def api_login_success(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices: return web.json_response({"ok":False})
    ts=datetime.now().strftime("%H:%M:%S %d.%m.%Y")
    if dev_uuid not in login_history: login_history[dev_uuid]=[]
    login_history[dev_uuid].append({"time":ts,"success":True,"user":data.get("user","?")})
    return web.json_response({"ok":True})

async def api_webcmd(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid,cmd=data.get("uuid",""),data.get("cmd","")
    if dev_uuid not in devices: return web.json_response({"ok":False})
    commands[dev_uuid]={"cmd":cmd,"time":time.time()}
    return web.json_response({"ok":True})

async def api_webresult(request):
    uuid=request.match_info.get("uuid","")
    itype=request.match_info.get("type","screenshot")
    imgs=last_images.get(uuid,{})
    if itype in imgs:
        img=imgs.pop(itype)
        return web.json_response({"image":img})
    return web.json_response({"image":None})

async def api_sysmon(request):
    uuid=request.match_info.get("uuid","")
    return web.json_response(sysmon_data.get(uuid,{}))

async def ws_stream(request):
    uuid=request.match_info.get("uuid","")
    if uuid not in devices: return web.Response(status=403)
    ws=web.WebSocketResponse()
    await ws.prepare(request)
    if uuid not in ws_clients: ws_clients[uuid]=set()
    ws_clients[uuid].add(ws)
    logger.info(f"WS connected {uuid[:8]}")
    try:
        async for msg in ws: pass
    except: pass
    finally:
        if uuid in ws_clients: ws_clients[uuid].discard(ws)
    return ws

async def web_panel(request):
    uuid=request.match_info.get("uuid","")
    if uuid not in devices: return web.Response(text="Not found",status=404)
    proto="wss" if request.secure else "ws"
    host=request.host
    html=f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>FaceID — {uuid[:8]}</title>
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
.card h2{{font-size:11px;letter-spacing:2px;color:#1a4a70;margin-bottom:14px;text-transform:uppercase}}
.btn{{background:linear-gradient(135deg,#1565c0,#0d47a1);border:none;color:white;padding:10px 16px;border-radius:10px;cursor:pointer;font-size:13px;font-weight:600;width:100%;margin-bottom:8px;transition:all .2s}}
.btn:hover{{opacity:.85;transform:translateY(-1px)}}
.btn.red{{background:linear-gradient(135deg,#c62828,#8e0000)}}
.btn.yellow{{background:linear-gradient(135deg,#f57f17,#e65100)}}
.btn.green{{background:linear-gradient(135deg,#2e7d32,#1b5e20)}}
.btn.purple{{background:linear-gradient(135deg,#6a1b9a,#4a148c)}}
.btn-row{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
#screen,#cam{{width:100%;border-radius:10px;background:#000;min-height:240px;display:flex;align-items:center;justify-content:center;color:#1a3a50;font-size:13px;overflow:hidden}}
#screen img,#cam img{{width:100%;border-radius:10px;display:block}}
.log{{background:#030508;border-radius:8px;padding:12px;font-size:11px;color:#2a5878;height:120px;overflow-y:auto;font-family:monospace}}
.meter{{background:#0d1a2a;border-radius:8px;height:20px;margin:6px 0;overflow:hidden}}
.meter-bar{{height:100%;border-radius:8px;transition:width .5s;display:flex;align-items:center;padding-left:8px;font-size:11px;font-weight:600}}
.cpu{{background:linear-gradient(90deg,#1565c0,#42a5f5)}}
.ram{{background:linear-gradient(90deg,#6a1b9a,#ce93d8)}}
.search-box{{width:100%;background:#0d1a2a;border:1px solid #1a3a50;border-radius:8px;padding:10px;color:#c8d8e8;font-size:13px;margin-bottom:8px}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.dot{{width:8px;height:8px;border-radius:50%;background:#27ae60;display:inline-block;margin-right:6px;animation:pulse 2s infinite}}
</style></head><body>
<div class="header">
  <div><h1>🔐 FACE ID PROTECTOR</h1>
  <div style="font-size:11px;color:#1a4a70;margin-top:2px"><span class="dot"></span>{uuid[:8]}...</div></div>
  <div class="badge" id="badge">ONLINE</div>
</div>
<div class="grid">
  <div class="card" style="grid-column:1/-1">
    <h2>🖥️ Прямой эфир</h2>
    <div id="screen" style="min-height:300px"><span>Нажмите "Начать эфир"</span></div>
    <div class="btn-row" style="margin-top:12px">
      <button class="btn" onclick="takeScreenshot()">📸 Скриншот</button>
      <button class="btn purple" id="streamBtn" onclick="toggleStream()">🎥 Начать эфир</button>
    </div>
  </div>
  <div class="card">
    <h2>📷 Камера + Мониторинг</h2>
    <div id="cam" style="min-height:160px"><span>Камера</span></div>
    <button class="btn" onclick="send('camera')" style="margin-top:10px">📷 Снимок</button>
    <div style="font-size:11px;color:#1a4a70;margin:12px 0 4px">CPU</div>
    <div class="meter"><div class="meter-bar cpu" id="cpu" style="width:0%">0%</div></div>
    <div style="font-size:11px;color:#1a4a70;margin:4px 0">RAM</div>
    <div class="meter"><div class="meter-bar ram" id="ram" style="width:0%">0%</div></div>
    <button class="btn" onclick="send('status')" style="margin-top:8px">🔄 Обновить</button>
  </div>
  <div class="card">
    <h2>⚙️ Управление</h2>
    <button class="btn green" onclick="send('launch_faceid')">🚀 Запустить FaceID</button>
    <button class="btn" onclick="send('lock')">🔒 Заблокировать</button>
    <button class="btn" onclick="send('faceid')">🔐 Face ID</button>
    <button class="btn red" onclick="if(confirm('ПАНИКА?'))send('panic')">🚨 ПАНИКА</button>
    <div class="btn-row">
      <button class="btn yellow" onclick="if(confirm('Перезагрузить?'))send('reboot')">🔄 Перезагрузить</button>
      <button class="btn red" onclick="if(confirm('Выключить?'))send('shutdown')">⏻ Выключить</button>
    </div>
    <div class="btn-row" style="margin-top:8px">
      <button class="btn" onclick="send('sleep')">😴 Сон</button>
      <button class="btn" onclick="send('hibernate')">❄️ Гибернация</button>
    </div>
    <button class="btn purple" onclick="send('recordaudio:15')">🎙 Аудио 15с</button>
  </div>
  <div class="card">
    <h2>🔍 Поиск файлов</h2>
    <input class="search-box" id="q" type="text" placeholder="Название файла...">
    <button class="btn" onclick="doSearch()">🔍 Найти</button>
    <div id="sr" style="font-size:12px;color:#4a8ab0;margin-top:8px;max-height:150px;overflow-y:auto"></div>
  </div>
  <div class="card" style="grid-column:1/-1">
    <h2>📋 Лог</h2>
    <div class="log" id="log">Готов...<br></div>
  </div>
</div>
<script>
const UUID='{uuid}',WS_URL='{proto}://{host}/ws/{uuid}';
let ws=null,streaming=false,si=null,frames=0,fps=0,ft=Date.now();
function log(m){{const e=document.getElementById('log'),t=new Date().toLocaleTimeString();e.innerHTML+=`[${{t}}] ${{m}}<br>`;e.scrollTop=e.scrollHeight;}}
async function send(cmd){{
  const r=await fetch('/api/webcmd',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{uuid:UUID,cmd}})}});
  const d=await r.json();
  log(d.ok?`→ ${{cmd}}`:`✗ ${{cmd}}`);
}}
function takeScreenshot(){{send('screenshot');setTimeout(()=>poll('screenshot'),3000);}}
async function poll(t){{
  const r=await fetch(`/api/webresult/${{UUID}}/${{t}}`);
  const d=await r.json();
  if(d.image){{document.getElementById(t==='camera'?'cam':'screen').innerHTML=`<img src="data:image/jpeg;base64,${{d.image}}"/>`;log(`✓ ${{t}}`);}}
  else setTimeout(()=>poll(t),1000);
}}
function toggleStream(){{
  streaming=!streaming;
  const btn=document.getElementById('streamBtn'),badge=document.getElementById('badge');
  if(streaming){{
    ws=new WebSocket(WS_URL);
    ws.onopen=()=>log('🔗 WS подключен');
    ws.onerror=()=>log('✗ WS ошибка');
    ws.onmessage=(e)=>{{
      const d=JSON.parse(e.data);
      if(d.type==='frame'&&d.image){{
        document.getElementById('screen').innerHTML=`<img src="data:image/jpeg;base64,${{d.image}}"/>`;
        frames++;const now=Date.now();if(now-ft>=1000){{fps=frames;frames=0;ft=now;}}
      }}else if(d.type==='camera'&&d.image){{
        document.getElementById('cam').innerHTML=`<img src="data:image/jpeg;base64,${{d.image}}"/>`;
      }}else if(d.type==='sysmon'){{
        if(d.data.cpu!=null){{document.getElementById('cpu').style.width=d.data.cpu+'%';document.getElementById('cpu').textContent=Math.round(d.data.cpu)+'%';}}
        if(d.data.ram!=null){{document.getElementById('ram').style.width=d.data.ram+'%';document.getElementById('ram').textContent=Math.round(d.data.ram)+'%';}}
      }}
    }};
    ws.onclose=()=>{{log('WS отключен');if(streaming)setTimeout(()=>toggleStream(),0);}};
    si=setInterval(()=>send('screenshot'),500);
    btn.textContent='⏹ Стоп';btn.className='btn red';
    badge.textContent='● LIVE';badge.className='badge live';
    log('🎥 Эфир запущен');
  }}else{{
    if(ws)ws.close();ws=null;clearInterval(si);
    btn.textContent='🎥 Начать эфир';btn.className='btn purple';
    badge.textContent='ONLINE';badge.className='badge';
    log('⏹ Эфир остановлен');
  }}
}}
function doSearch(){{
  const q=document.getElementById('q').value.trim();
  if(!q)return;
  send(`searchfiles:${{q}}`);
  document.getElementById('sr').innerHTML='🔍 Ищу...';
}}
setInterval(()=>send('status'),30000);
log('Панель загружена');
</script></body></html>"""
    return web.Response(text=html,content_type='text/html')

async def healthcheck(request):
    return web.Response(text=f"FaceID v6 | devices:{len(devices)} | pending:{len(pending)}")

bot_app = None

async def main():
    global bot_app, devices, pending
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start",    start))
    bot_app.add_handler(CommandHandler("register", register_cmd))
    bot_app.add_handler(CommandHandler("devices",  devices_cmd))
    bot_app.add_handler(CommandHandler("control",  control_cmd))
    bot_app.add_handler(CommandHandler("delete",   delete_cmd))
    bot_app.add_handler(CommandHandler("getfile",  getfile_cmd))
    bot_app.add_handler(CommandHandler("history",  history_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.Document.ALL|filters.PHOTO, file_upload_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND, text_handler))

    loaded = await load_data()
    devices = loaded.get("devices",{})
    pending = loaded.get("pending",{})
    logger.info(f"Loaded {len(devices)} devices")

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot started")

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

    port = int(os.environ.get("PORT",8080))
    runner = web.AppRunner(http)
    await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",port).start()
    logger.info(f"HTTP on port {port}")
    print(f"FaceID Bot v6 running on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
