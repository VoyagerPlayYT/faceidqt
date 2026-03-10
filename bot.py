"""
Face ID Protector Bot v3
- Persistent storage (JSON файл)
- Web dashboard
- Realtime screen/camera stream
"""
import os, json, asyncio, logging, base64, random, time, urllib.parse
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8669964430:AAEnawN7vnTr341dHx00fTTZ-iiakSlXS14")
# ══════════════════════════════════════════════
#  ХРАНИЛИЩЕ (JSONBin.io — персистентное)
# ══════════════════════════════════════════════
import aiohttp as aiohttp_lib

JSONBIN_ID  = os.environ.get("JSONBIN_ID",  "")
JSONBIN_KEY = os.environ.get("JSONBIN_KEY", "")
JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

async def load_data_remote():
    if not JSONBIN_ID or not JSONBIN_KEY:
        return {"devices": {}, "pending": {}}
    try:
        async with aiohttp_lib.ClientSession() as s:
            async with s.get(JSONBIN_URL+"/latest",
                headers={"X-Master-Key": JSONBIN_KEY}) as r:
                if r.status == 200:
                    d = await r.json()
                    return d.get("record", {"devices":{},"pending":{}})
    except Exception as e:
        logging.error(f"Load error: {e}")
    return {"devices": {}, "pending": {}}

async def save_data():
    if not JSONBIN_ID or not JSONBIN_KEY:
        return
    try:
        async with aiohttp_lib.ClientSession() as s:
            await s.put(JSONBIN_URL,
                headers={"X-Master-Key": JSONBIN_KEY,
                         "Content-Type": "application/json"},
                json={"devices": devices, "pending": pending})
    except Exception as e:
        logging.error(f"Save error: {e}")

devices  = {}
pending  = {}
commands     = {}   # uuid -> {cmd, time}
file_results = {}   # uuid -> listdir result
stream_frames= {}   # uuid -> list of base64 frames

# ══════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════
def main_keyboard(uuid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Скриншот",       callback_data=f"screenshot|{uuid}"),
         InlineKeyboardButton("📷 Камера",         callback_data=f"camera|{uuid}"),
         InlineKeyboardButton("📊 Статус",         callback_data=f"status|{uuid}")],
        [InlineKeyboardButton("🔒 Заблокировать",  callback_data=f"lock|{uuid}"),
         InlineKeyboardButton("🔐 Face ID",        callback_data=f"faceid|{uuid}"),
         InlineKeyboardButton("🎥 Эфир",           callback_data=f"stream|{uuid}")],
        [InlineKeyboardButton("📱 Приложения",     callback_data=f"listapps|{uuid}"),
         InlineKeyboardButton("📁 Файлы",          callback_data=f"files|{uuid}")],
        [InlineKeyboardButton("🔄 Перезагрузить",  callback_data=f"reboot|{uuid}"),
         InlineKeyboardButton("⏻ Выключить",       callback_data=f"shutdown|{uuid}")],
        [InlineKeyboardButton("🌐 Веб-панель",     url=f"https://faceidqt.onrender.com/panel/{uuid}")],
    ])

# ══════════════════════════════════════════════
#  ФАЙЛОВЫЙ МЕНЕДЖЕР
# ══════════════════════════════════════════════
QUICK_FOLDERS = [
    ("🖥️ Рабочий стол", "DESKTOP"),
    ("📥 Загрузки",      "DOWNLOADS"),
    ("📄 Документы",     "DOCUMENTS"),
    ("🖼️ Изображения",   "PICTURES"),
    ("🎵 Музыка",        "MUSIC"),
    ("🎬 Видео",         "VIDEOS"),
    ("💾 Диск C:",       "C:"),
    ("💾 Диск D:",       "D:"),
]

def get_file_icon(name):
    ext = name.rsplit(".",1)[-1].lower() if "." in name else ""
    m = {"jpg":"🖼️","jpeg":"🖼️","png":"🖼️","gif":"🖼️","bmp":"🖼️","webp":"🖼️",
         "mp4":"🎬","avi":"🎬","mkv":"🎬","mov":"🎬","mp3":"🎵","wav":"🎵",
         "flac":"🎵","m4a":"🎵","pdf":"📕","doc":"📝","docx":"📝","txt":"📄",
         "xlsx":"📊","pptx":"📊","zip":"🗜️","rar":"🗜️","7z":"🗜️",
         "exe":"⚙️","msi":"⚙️","py":"🐍","cpp":"💻","js":"💻","html":"🌐"}
    return m.get(ext, "📄")

async def show_file_browser(query, uuid, path, edit=True):
    if path == "root":
        kb = []
        for label, key in QUICK_FOLDERS:
            safe = urllib.parse.quote(key, safe="")
            kb.append([InlineKeyboardButton(label, callback_data=f"browse:{safe}|{uuid}")])
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"select|{uuid}")])
        text = "📁 *Файловый менеджер*\nВыбери папку:"
        if edit:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    decoded = urllib.parse.unquote(path)
    commands[uuid] = {"cmd": f"listdir:{decoded}", "time": time.time()}

    for _ in range(16):
        await asyncio.sleep(0.5)
        if uuid in file_results:
            break

    result = file_results.pop(uuid, None)
    if not result:
        kb = [[InlineKeyboardButton("📁 Корень", callback_data=f"files|{uuid}"),
               InlineKeyboardButton("◀️ Назад",  callback_data=f"select|{uuid}")]]
        await query.edit_message_text("⏳ ПК не ответил.", reply_markup=InlineKeyboardMarkup(kb))
        return

    entries = result.get("entries", [])
    folders = [e for e in entries if e["type"]=="dir"]
    files   = [e for e in entries if e["type"]=="file"]
    kb = []

    # Кнопка назад
    if "\\" in decoded and decoded not in ("C:\\","D:\\","E:\\"):
        parent = decoded.rsplit("\\",1)[0]
        if parent.endswith(":"): parent += "\\"
        safe_p = urllib.parse.quote(parent, safe="")
        kb.append([InlineKeyboardButton("⬆️ ..", callback_data=f"browse:{safe_p}|{uuid}")])
    else:
        kb.append([InlineKeyboardButton("📁 Быстрые папки", callback_data=f"files|{uuid}")])

    for e in folders[:18]:
        full = decoded.rstrip("\\") + "\\" + e["name"]
        safe = urllib.parse.quote(full, safe="")
        kb.append([InlineKeyboardButton(f"📁 {e['name']}", callback_data=f"browse:{safe}|{uuid}")])

    for e in files[:15]:
        full = decoded.rstrip("\\") + "\\" + e["name"]
        safe = urllib.parse.quote(full, safe="")
        kb_val = e.get('size_kb', 0)
        sz = f" {kb_val}KB" if kb_val < 10240 else f" {kb_val//1024}MB"
        kb.append([InlineKeyboardButton(
            f"{get_file_icon(e['name'])} {e['name']}{sz}",
            callback_data=f"dlfile:{safe}|{uuid}"
        )])

    short = decoded[-40:] if len(decoded)>40 else decoded
    text = (f"📁 `{short}`\n"
            f"{'📂 '+str(len(folders))+' папок  ' if folders else ''}"
            f"{'📄 '+str(len(files))+' файлов' if files else ''}")
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ══════════════════════════════════════════════
#  БОТ КОМАНДЫ
# ══════════════════════════════════════════════
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 *Face ID Protector Bot*\n\n"
        "/register UUID — привязать устройство\n"
        "/devices — мои устройства\n"
        "/control — управление ПК\n"
        "/getfile UUID путь — скачать файл",
        parse_mode="Markdown"
    )

async def register_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text(
            "📱 *Привязка устройства*\n\n"
            "1️⃣ Открой приложение FaceID Protector\n"
            "2️⃣ Нажми кнопку ✈️ *Telegram*\n"
            "3️⃣ Скопируй UUID который покажет приложение\n"
            "4️⃣ Отправь сюда:\n\n"
            "`/register ВАШ-UUID`\n\n"
            "Например:\n"
            "`/register 7b23db80-b356-4a86-b32b-84a40e643706`",
            parse_mode="Markdown"
        )
        return
    dev_uuid = ctx.args[0].strip()
    code     = str(random.randint(100000, 999999))
    pending[dev_uuid] = {"chat_id": chat_id, "code": code, "time": time.time()}
    await save_data()
    await update.message.reply_text(
        f"📱 *Подтверждение устройства*\n\n"
        f"UUID: `{dev_uuid[:8]}...`\n\n"
        f"Введи этот код в приложении FaceID:\n\n"
        f"🔑 *{code}*\n\n"
        f"⏱ Код действителен 10 минут.",
        parse_mode="Markdown"
    )

async def devices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if d.get("chat_id")==chat_id]
    if not my:
        await update.message.reply_text("Нет привязанных устройств.\n/register UUID")
        return
    txt = "📱 *Ваши устройства:*\n\n"
    for u,d in my:
        txt += f"• `{u[:8]}...` — {d.get('name','ПК')}\n"
    await update.message.reply_text(txt, parse_mode="Markdown")

async def control_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    my = [(u,d) for u,d in devices.items() if d.get("chat_id")==chat_id]
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
        kb = [[InlineKeyboardButton(d.get("name",u[:8]), callback_data=f"select|{u}")] for u,d in my]
        await update.message.reply_text("Выбери устройство:", reply_markup=InlineKeyboardMarkup(kb))

async def getfile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await update.message.reply_text("❌ Формат: /getfile UUID путь")
        return
    dev_uuid = ctx.args[0].strip()
    filepath = " ".join(ctx.args[1:]).strip()
    chat_id  = update.effective_chat.id
    if dev_uuid not in devices or devices[dev_uuid].get("chat_id") != chat_id:
        await update.message.reply_text("❌ Устройство не найдено.")
        return
    commands[dev_uuid] = {"cmd": f"sendfile:{filepath}", "time": time.time()}
    await update.message.reply_text(f"📁 Запрашиваю `{filepath}`", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  КНОПКИ
# ══════════════════════════════════════════════
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    chat_id = query.from_user.id

    if "|" not in data:
        return
    cmd, uuid = data.split("|", 1)

    if cmd.startswith("select"):
        name = devices.get(uuid,{}).get("name","ПК")
        await query.edit_message_text(
            f"🖥️ *Управление: {name}*\nВыбери действие:",
            parse_mode="Markdown", reply_markup=main_keyboard(uuid))
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
        name = filepath.rsplit("\\",1)[-1]
        await query.edit_message_text(
            f"📥 Скачиваю `{name}`...", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data=f"files|{uuid}")
            ]]))
        return

    # Приложения
    if cmd == "listapps":
        commands[uuid] = {"cmd": "listapps", "time": time.time()}
        await query.edit_message_text("📱 Загружаю список...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data=f"select|{uuid}")
            ]]))
        return
    if cmd.startswith("launchapp:"):
        commands[uuid] = {"cmd": cmd, "time": time.time()}
        await query.answer("▶️ Запускаю...")
        return

    # Остальные команды
    cmd_names = {
        "screenshot":"📸 Запрос скриншота...",
        "camera":    "📷 Запрос с камеры...",
        "lock":      "🔒 Блокирую ПК...",
        "reboot":    "🔄 Перезагружаю...",
        "shutdown":  "⏻ Выключаю...",
        "stream":    "🎥 Запрашиваю эфир...",
        "faceid":    "🔐 Запрос Face ID на ПК...",
        "status":    "📊 Запрос статуса...",
    }
    commands[uuid] = {"cmd": cmd, "time": time.time()}
    await query.edit_message_text(
        cmd_names.get(cmd, "Команда отправлена..."),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Назад", callback_data=f"select|{uuid}")
        ]]))

# ══════════════════════════════════════════════
#  HTTP API
# ══════════════════════════════════════════════
async def api_verify(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False,"error":"bad_json"})
    dev_uuid = data.get("uuid","")
    code     = data.get("code","")
    logging.info(f"Verify: uuid={dev_uuid[:8]}, pending_keys={list(pending.keys())[:3]}")
    if dev_uuid not in pending:
        return web.json_response({"ok":False,"error":"not_found"})
    p = pending[dev_uuid]
    if time.time() - p["time"] > 600:
        del pending[dev_uuid]; await save_data()
        return web.json_response({"ok":False,"error":"expired"})
    if p["code"] != code:
        return web.json_response({"ok":False,"error":"wrong_code"})
    devices[dev_uuid] = {"chat_id": p["chat_id"], "name": data.get("name","ПК")}
    del pending[dev_uuid]
    await save_data()
    await bot_app.bot.send_message(
        p["chat_id"],
        f"✅ *Устройство привязано!*\nUUID: `{dev_uuid[:8]}...`\n\nИспользуй /control",
        parse_mode="Markdown"
    )
    return web.json_response({"ok":True})

async def api_alert(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices:
        return web.json_response({"ok":False,"error":"device_not_found"})
    chat_id = devices[dev_uuid]["chat_id"]
    ts      = data.get("time", datetime.now().strftime("%H:%M:%S %d.%m.%Y"))
    await bot_app.bot.send_message(chat_id,
        f"🚨 *ПОПЫТКА ВХОДА*\n\n🕐 {ts}\n❌ Попыток: {data.get('attempts',1)}",
        parse_mode="Markdown")
    if data.get("camera"):
        await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["camera"]),
            caption="📸 Фото злоумышленника")
    if data.get("screenshot"):
        await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["screenshot"]),
            caption="🖥️ Скриншот экрана")
    return web.json_response({"ok":True})

async def api_poll(request):
    try: data = await request.json()
    except: return web.json_response({"cmd":None})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in commands:
        return web.json_response({"cmd":None})
    cmd = commands.pop(dev_uuid)
    if time.time() - cmd["time"] > 30:
        return web.json_response({"cmd":None})
    return web.json_response({"cmd": cmd["cmd"]})

async def api_result(request):
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid = data.get("uuid","")
    if dev_uuid not in devices:
        return web.json_response({"ok":False})
    chat_id = devices[dev_uuid]["chat_id"]
    cmd     = data.get("cmd","")

    if cmd in ("screenshot","stream_frame"):
        if data.get("image"):
            # Сохраняем для веб-панели
            if dev_uuid not in last_images: last_images[dev_uuid]={}
            last_images[dev_uuid]["screenshot"] = data["image"]
            cap = "📸 Скриншот" if cmd=="screenshot" else "🎥 Прямой эфир"
            await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["image"]), caption=cap)
    elif cmd == "camera":
        if data.get("image"):
            if dev_uuid not in last_images: last_images[dev_uuid]={}
            last_images[dev_uuid]["camera"] = data["image"]
            await bot_app.bot.send_photo(chat_id, photo=base64.b64decode(data["image"]),
                caption="📷 Фото с камеры")
    elif cmd == "locked":
        await bot_app.bot.send_message(chat_id, "🔒 ПК заблокирован",
            reply_markup=main_keyboard(dev_uuid))
    elif cmd == "listdir":
        file_results[dev_uuid] = data
    elif cmd == "file":
        if data.get("image"):
            fname = data.get("filename","file.bin")
            await bot_app.bot.send_document(chat_id,
                document=base64.b64decode(data["image"]),
                filename=fname, caption=f"📁 {fname}")
    elif cmd == "file_error":
        errs = {"no_path":"Путь не указан","not_found":"Файл не найден","too_large":"Файл >50MB"}
        await bot_app.bot.send_message(chat_id, f"❌ {errs.get(data.get('error',''),data.get('error','?'))}")
    elif cmd == "apps_list":
        apps = data.get("apps",[])
        if not apps:
            await bot_app.bot.send_message(chat_id, "📱 Нет приложений.")
            return web.json_response({"ok":True})
        kb = [[InlineKeyboardButton(f"▶️ {a['name']}", callback_data=f"launchapp:{a['idx']}|{dev_uuid}")] for a in apps]
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"select|{dev_uuid}")])
        await bot_app.bot.send_message(chat_id, "📱 *Приложения:*",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    elif cmd == "app_launched":
        await bot_app.bot.send_message(chat_id, f"▶️ Запущено: *{data.get('name','?')}*",
            parse_mode="Markdown", reply_markup=main_keyboard(dev_uuid))
    elif cmd == "status":
        s = data.get("status",{})
        await bot_app.bot.send_message(chat_id,
            f"📊 *Статус ПК*\n\n🖥️ {s.get('hostname','?')}\n👤 {s.get('user','?')}\n"
            f"🔒 {'Заблокирован' if s.get('locked') else 'Разблокирован'}\n"
            f"🕐 {datetime.now().strftime('%H:%M:%S')}",
            parse_mode="Markdown", reply_markup=main_keyboard(dev_uuid))

    return web.json_response({"ok":True})

async def api_check(request):
    try: data = await request.json()
    except: return web.json_response({"registered":False})
    return web.json_response({"registered": data.get("uuid","") in devices})

# ══════════════════════════════════════════════
#  ВЕБ-ПАНЕЛЬ
# ══════════════════════════════════════════════
async def web_panel(request):
    uuid = request.match_info.get("uuid","")
    if uuid not in devices:
        return web.Response(text="Device not found", status=404)
    
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Face ID Protector — {uuid[:8]}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; font-family:'Segoe UI',sans-serif; }}
  body {{ background:#050810; color:#c8d8e8; min-height:100vh; }}
  .header {{ background:rgba(10,16,30,0.95); padding:16px 24px; display:flex;
             align-items:center; gap:12px; border-bottom:1px solid #0d1a2a; }}
  .header h1 {{ font-size:18px; letter-spacing:3px; color:#e8f4ff; }}
  .badge {{ background:#1565c0; padding:3px 10px; border-radius:20px; font-size:11px; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; padding:20px; max-width:1200px; margin:0 auto; }}
  @media(max-width:768px){{ .grid{{grid-template-columns:1fr;}} }}
  .card {{ background:rgba(10,16,30,0.8); border:1px solid #0d1a2a; border-radius:16px; padding:20px; }}
  .card h2 {{ font-size:12px; letter-spacing:2px; color:#1a4a70; margin-bottom:16px; }}
  .btn {{ background:linear-gradient(135deg,#1565c0,#0d47a1); border:none; color:white;
           padding:12px 20px; border-radius:10px; cursor:pointer; font-size:13px;
           font-weight:600; letter-spacing:1px; width:100%; margin-bottom:8px;
           transition:all .2s; }}
  .btn:hover {{ background:linear-gradient(135deg,#1e88e5,#1565c0); transform:translateY(-1px); }}
  .btn.red {{ background:linear-gradient(135deg,#c62828,#8e0000); }}
  .btn.red:hover {{ background:#e53935; }}
  .btn.yellow {{ background:linear-gradient(135deg,#f57f17,#e65100); }}
  .btn-row {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
  #screen {{ width:100%; border-radius:10px; background:#000; min-height:200px;
             display:flex; align-items:center; justify-content:center; color:#1a3a50;
             font-size:13px; }}
  #screen img {{ width:100%; border-radius:10px; display:block; }}
  #camera {{ width:100%; border-radius:10px; background:#000; min-height:160px;
             display:flex; align-items:center; justify-content:center; color:#1a3a50; }}
  #camera img {{ width:100%; border-radius:10px; display:block; }}
  .status-dot {{ width:8px; height:8px; border-radius:50%; background:#27ae60;
                 display:inline-block; margin-right:6px; animation:pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
  .log {{ background:#030508; border-radius:8px; padding:12px; font-size:11px;
          color:#2a5878; height:120px; overflow-y:auto; font-family:monospace; }}
  .stream-btn {{ background:linear-gradient(135deg,#6a1b9a,#4a148c); }}
  #stream-status {{ font-size:11px; color:#9b59b6; margin-top:6px; text-align:center; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🔐 FACE ID PROTECTOR</h1>
    <div style="font-size:11px;color:#1a4a70;margin-top:2px;">
      <span class="status-dot"></span>Устройство: {uuid[:8]}...
    </div>
  </div>
  <div class="badge">ONLINE</div>
</div>

<div class="grid">
  <!-- Экран -->
  <div class="card" style="grid-column:1/-1">
    <h2>🖥️ ЭКРАН ПК</h2>
    <div id="screen">Нажмите "Скриншот" для обновления</div>
    <div style="margin-top:12px;" class="btn-row">
      <button class="btn" onclick="sendCmd('screenshot')">📸 Скриншот</button>
      <button class="btn stream-btn" id="streamBtn" onclick="toggleStream()">🎥 Начать эфир</button>
    </div>
    <div id="stream-status"></div>
  </div>

  <!-- Камера -->
  <div class="card">
    <h2>📷 КАМЕРА</h2>
    <div id="camera">Нажмите для снимка</div>
    <button class="btn" onclick="sendCmd('camera')" style="margin-top:12px;">📷 Сфотографировать</button>
  </div>

  <!-- Управление -->
  <div class="card">
    <h2>⚙️ УПРАВЛЕНИЕ</h2>
    <button class="btn" onclick="sendCmd('lock')">🔒 Заблокировать</button>
    <button class="btn" onclick="sendCmd('faceid')">🔐 Запросить Face ID</button>
    <div class="btn-row">
      <button class="btn yellow" onclick="sendCmd('reboot')">🔄 Перезагрузить</button>
      <button class="btn red" onclick="confirmShutdown()">⏻ Выключить</button>
    </div>
  </div>

  <!-- Статус -->
  <div class="card">
    <h2>📊 СТАТУС</h2>
    <div id="status-info" style="font-size:13px;color:#4a8ab0;margin-bottom:12px;">
      Нажмите для обновления
    </div>
    <button class="btn" onclick="sendCmd('status')">📊 Обновить статус</button>
  </div>

  <!-- Лог -->
  <div class="card" style="grid-column:1/-1">
    <h2>📋 ЛОГ ДЕЙСТВИЙ</h2>
    <div class="log" id="log">Готов к работе...<br></div>
  </div>
</div>

<script>
const UUID = '{uuid}';
let streamActive = false;
let streamInterval = null;

function log(msg) {{
  const el = document.getElementById('log');
  const t = new Date().toLocaleTimeString();
  el.innerHTML += `[${{t}}] ${{msg}}<br>`;
  el.scrollTop = el.scrollHeight;
}}

async function sendCmd(cmd) {{
  log('→ Отправляю: ' + cmd);
  try {{
    const r = await fetch('/api/webcmd', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{uuid: UUID, cmd: cmd}})
    }});
    const d = await r.json();
    if(d.ok) {{
      log('✓ Команда отправлена');
      if(cmd === 'screenshot' || cmd === 'camera') {{
        setTimeout(() => pollResult(cmd), 3000);
      }}
    }}
  }} catch(e) {{ log('✗ Ошибка: ' + e); }}
}}

async function pollResult(type) {{
  try {{
    const r = await fetch(`/api/webresult/${{UUID}}/${{type}}`);
    const d = await r.json();
    if(d.image) {{
      const img = `<img src="data:image/jpeg;base64,${{d.image}}" />`;
      document.getElementById(type==='camera'?'camera':'screen').innerHTML = img;
      log('✓ Получено изображение');
    }} else {{
      setTimeout(() => pollResult(type), 1000);
    }}
  }} catch(e) {{}}
}}

function toggleStream() {{
  streamActive = !streamActive;
  const btn = document.getElementById('streamBtn');
  const status = document.getElementById('stream-status');
  if(streamActive) {{
    btn.textContent = '⏹ Остановить эфир';
    btn.style.background = 'linear-gradient(135deg,#c62828,#8e0000)';
    status.textContent = '● LIVE';
    streamInterval = setInterval(() => {{
      sendCmd('stream_one');
    }}, 2000);
    log('🎥 Прямой эфир запущен');
  }} else {{
    btn.textContent = '🎥 Начать эфир';
    btn.style.background = 'linear-gradient(135deg,#6a1b9a,#4a148c)';
    status.textContent = '';
    clearInterval(streamInterval);
    log('⏹ Эфир остановлен');
  }}
}}

function confirmShutdown() {{
  if(confirm('Выключить ПК?')) sendCmd('shutdown');
}}

// Авто-обновление статуса каждые 30 сек
setInterval(() => sendCmd('status'), 30000);
log('🌐 Веб-панель загружена');
</script>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html')

async def api_webcmd(request):
    """Веб-панель отправляет команду"""
    try: data = await request.json()
    except: return web.json_response({"ok":False})
    dev_uuid = data.get("uuid","")
    cmd      = data.get("cmd","")
    if dev_uuid not in devices:
        return web.json_response({"ok":False,"error":"not_found"})
    if cmd == "stream_one":
        cmd = "screenshot"
    commands[dev_uuid] = {"cmd": cmd, "time": time.time()}
    return web.json_response({"ok":True})

# Временное хранилище последних изображений для веб
last_images = {}  # uuid -> {screenshot: b64, camera: b64}

async def api_webresult(request):
    """Веб-панель запрашивает последнее изображение"""
    uuid  = request.match_info.get("uuid","")
    itype = request.match_info.get("type","screenshot")
    imgs  = last_images.get(uuid,{})
    if itype in imgs:
        img = imgs.pop(itype)
        return web.json_response({"image": img})
    return web.json_response({"image": None})

async def healthcheck(request):
    return web.Response(text=f"Face ID Bot OK | devices:{len(devices)}")

# ══════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════
bot_app = None

async def main():
    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start",    start))
    bot_app.add_handler(CommandHandler("register", register_cmd))
    bot_app.add_handler(CommandHandler("devices",  devices_cmd))
    bot_app.add_handler(CommandHandler("control",  control_cmd))
    bot_app.add_handler(CommandHandler("getfile",  getfile_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))

    # Загружаем данные из JSONBin
    global devices, pending
    loaded = await load_data_remote()
    devices = loaded.get("devices", {})
    pending = loaded.get("pending", {})
    logging.info(f"Loaded {len(devices)} devices from storage")

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)

    http = web.Application()
    http.router.add_get("/",                         healthcheck)
    http.router.add_get("/panel/{uuid}",             web_panel)
    http.router.add_post("/api/verify",              api_verify)
    http.router.add_post("/api/alert",               api_alert)
    http.router.add_post("/api/poll",                api_poll)
    http.router.add_post("/api/result",              api_result)
    http.router.add_post("/api/check",               api_check)
    http.router.add_post("/api/webcmd",              api_webcmd)
    http.router.add_get("/api/webresult/{uuid}/{type}", api_webresult)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(http)
    await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",port).start()
    print(f"✅ Bot + Web running on port {port}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
