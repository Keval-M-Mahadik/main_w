import os
import io
import csv
import json
import time
import threading
import traceback
import zipfile

from io import BytesIO
from urllib.parse import quote_plus

import requests

from PIL import Image
from flask import Flask, jsonify
from requests.adapters import HTTPAdapter


# ============================================================
# 1. FLASK APP + KEEP-ALIVE
# ============================================================
app = Flask(__name__)

_RUNTIME = {
    "total_users":  lambda: 0,
    "banned_users": lambda: 0,
    "maintenance":  lambda: False,
    "start_ts":     time.time(),
}


@app.route("/")
def home():
    return "Bot is running successfully!"


@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/health")
def health():
    try:
        return jsonify({
            "status":      "ok",
            "ts":          int(time.time()),
            "uptime_sec":  int(time.time() - _RUNTIME["start_ts"]()),
            "total_users": _RUNTIME["total_users"](),
            "banned":      _RUNTIME["banned_users"](),
            "maintenance": bool(_RUNTIME["maintenance"]()),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def run_flask():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


# ============================================================
# 2. CONFIGURATION
# ============================================================
def _env(name, default=""):
    return os.getenv(name, default).strip()


USER_BOT_TOKEN  = _env("USER_BOT_TOKEN",
                       "8771414496:AAEZYXZa3TXHPcJoEYxHmI105c60F8VSYxo")
ADMIN_BOT_TOKEN = _env("ADMIN_BOT_TOKEN_1",
                       "8916442795:AAETD7lL1snL27ab0RVVzpMPBWvnJ7_Xyn4")

JOIN_CHANNEL_URL     = _env("JOIN_CHANNEL_URL", "https://t.me/Cyber_Warriors_22")
JOIN_GROUP_URL       = _env("JOIN_GROUP_URL",   "https://t.me/Dark911_osint")
JOIN_CHANNEL_CHAT_ID = _env("CHANNEL_CHAT_ID", "-1004360588658")
JOIN_GROUP_CHAT_ID   = _env("GROUP_CHAT_ID",   "-1004344445959")

FORCE_JOIN_ENABLED = _env("FORCE_JOIN_ENABLED", "true").lower() in ("1", "true", "yes")

ADMIN_PASSWORD = _env("ADMIN_PASSWORD", "mahesh@321")
OWNER_ID       = _env("OWNER_ID", "6326027750")

SELF_URL           = _env("SELF_URL", "https://24-7-onilen.onrender.com")
SELF_PING_INTERVAL = int(_env("SELF_PING_INTERVAL", "600"))

SUPPORT_HANDLE = _env("SUPPORT_HANDLE", "@Tony_M_unlock")
SUPPORT_URL    = _env("SUPPORT_URL",    "https://t.me/Tony_")

ADMINS_FILE    = _env("ADMINS_FILE",    "admins.json")
PASSWORD_FILE  = _env("PASSWORD_FILE",  "admin_password.json")
BANNED_FILE    = _env("BANNED_FILE",    "banned.json")
NOTES_FILE     = _env("NOTES_FILE",     "user_notes.json")
ADMIN_LOG_FILE = _env("ADMIN_LOG_FILE", "admin_log.json")
LASTSEEN_FILE  = _env("LASTSEEN_FILE",  "last_seen.json")
LANGS_FILE     = _env("LANGS_FILE",     "user_langs.json")

IMAGES = {
    "welcome":     _env("WELCOME_IMG",     "images/welcome.png"),
    "osint":       _env("OSINT_IMG",       "images/osint.png"),
    "join":        _env("JOIN_IMG",        "images/join.png"),
    "maintenance": _env("MAINTENANCE_IMG", "images/maintenance.png"),
}
IMAGE_CACHE = {}


# ============================================================
# 3. GLOBAL STATE
# ============================================================
DYNAMIC_ADMINS   = set()
CURRENT_PASSWORD = ADMIN_PASSWORD
BANNED_USERS     = {}
USER_NOTES       = {}
ADMIN_LOG        = []
LAST_SEEN        = {}
MAINTENANCE_MODE = False
ADMIN_LOG_MAX    = 500

USER_STATE  = {}
USER_LANGS  = {}
ADMIN_STATE = {}

db_lock = threading.Lock()


# ============================================================
# 4. HTTP SESSIONS
# ============================================================
def _make_session(pool=20, maxsize=50):
    s = requests.Session()
    s.headers.update({"Connection": "keep-alive"})
    s.mount("https://", HTTPAdapter(pool_connections=pool, pool_maxsize=maxsize, max_retries=0))
    s.mount("http://",  HTTPAdapter(pool_connections=pool, pool_maxsize=maxsize, max_retries=0))
    return s


HTTP       = _make_session(20, 50)
ADMIN_HTTP = _make_session(10, 20)

TG_CONNECT, TG_READ   = 5, 35
API_CONNECT, API_READ = 3, 15

USER_TG_API   = f"https://api.telegram.org/bot{USER_BOT_TOKEN}"
ADMIN_TG_APIS = {1: f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}"}


# ============================================================
# 5. PERSISTENCE
# ============================================================
def _safe_load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"⚠️ load {path} failed: {e}")
        return default


def _safe_save(path, data):
    """Write JSON compactly (no whitespace) — smaller file, faster I/O."""
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"⚠️ save {path} failed: {e}")


# ============================================================
# 5a. AUTO-CREATE JSON FILES (only if missing)
# ============================================================
_JSON_DEFAULTS = {
    ADMINS_FILE:    [],
    PASSWORD_FILE:  None,
    BANNED_FILE:    {},
    NOTES_FILE:     {},
    ADMIN_LOG_FILE: [],
    LASTSEEN_FILE:  {},
    LANGS_FILE:     {},
}


def ensure_json_files():
    """Create every JSON file with a sensible default if it doesn't exist."""
    for path, default in _JSON_DEFAULTS.items():
        if os.path.exists(path):
            continue
        if path == PASSWORD_FILE:
            _safe_save(path, {"password": ADMIN_PASSWORD})
        else:
            _safe_save(path, default)
        print(f"🆕 Created {path}")


def load_password():
    global CURRENT_PASSWORD
    data = _safe_load(PASSWORD_FILE, {})
    if data.get("password"):
        CURRENT_PASSWORD = str(data["password"])
        print("🔐 Loaded persisted admin password.")


def save_password():
    _safe_save(PASSWORD_FILE, {"password": CURRENT_PASSWORD})


def load_admins():
    global DYNAMIC_ADMINS
    raw = _safe_load(ADMINS_FILE, [])
    DYNAMIC_ADMINS = {int(x) for x in raw if str(x).lstrip("-").isdigit()}
    if OWNER_ID.lstrip("-").isdigit():
        DYNAMIC_ADMINS.add(int(OWNER_ID))
        print(f"👑 OWNER_ID {OWNER_ID} auto-added as admin.")
    save_admins()


def save_admins():
    _safe_save(ADMINS_FILE, sorted(DYNAMIC_ADMINS))


def load_banned():
    global BANNED_USERS
    raw = _safe_load(BANNED_FILE, {})
    BANNED_USERS = {int(k): v for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    print(f"🚫 Loaded {len(BANNED_USERS)} banned users.")


def save_banned():
    _safe_save(BANNED_FILE, {str(k): v for k, v in BANNED_USERS.items()})


def load_notes():
    global USER_NOTES
    raw = _safe_load(NOTES_FILE, {})
    USER_NOTES = {int(k): v for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    print(f"📝 Loaded notes for {len(USER_NOTES)} users.")


def save_notes():
    _safe_save(NOTES_FILE, {str(k): v for k, v in USER_NOTES.items()})


def load_log():
    global ADMIN_LOG
    raw = _safe_load(ADMIN_LOG_FILE, [])
    ADMIN_LOG = raw if isinstance(raw, list) else []


def save_log():
    _safe_save(ADMIN_LOG_FILE, ADMIN_LOG[-ADMIN_LOG_MAX:])


def load_lastseen():
    global LAST_SEEN
    raw = _safe_load(LASTSEEN_FILE, {})
    LAST_SEEN = {int(k): float(v) for k, v in raw.items() if str(k).lstrip("-").isdigit()}


def save_lastseen():
    _safe_save(LASTSEEN_FILE, {str(k): v for k, v in LAST_SEEN.items()})


def load_langs():
    global USER_LANGS
    raw = _safe_load(LANGS_FILE, {})
    USER_LANGS = {int(k): str(v) for k, v in raw.items() if str(k).lstrip("-").isdigit()}


def save_langs():
    with db_lock:
        _safe_save(LANGS_FILE, {str(k): v for k, v in USER_LANGS.items()})


def log_admin(by, action, target=""):
    entry = {"ts": int(time.time()), "by": int(by),
             "action": str(action), "target": str(target)}
    ADMIN_LOG.append(entry)
    if len(ADMIN_LOG) > ADMIN_LOG_MAX:
        del ADMIN_LOG[:-ADMIN_LOG_MAX]
    save_log()


# ============================================================
# 5b. OWNER JSON FILE MANAGER  (owner-only viewer + downloader)
# ============================================================
JSON_FILE_MAP = {
    "admins.json":         ADMINS_FILE,
    "admin_password.json": PASSWORD_FILE,
    "banned.json":         BANNED_FILE,
    "user_notes.json":     NOTES_FILE,
    "admin_log.json":      ADMIN_LOG_FILE,
    "last_seen.json":      LASTSEEN_FILE,
    "user_langs.json":     LANGS_FILE,
}


def owner_files_keyboard():
    rows = []
    for fname in JSON_FILE_MAP:
        rows.append([
            {"text": f"👁 View {fname}", "callback_data": f"owner:view:{fname}"},
            {"text": "📥",               "callback_data": f"owner:download:{fname}"},
        ])
    rows.append([{"text": "📦 Download All (ZIP)", "callback_data": "owner:download_all"}])
    rows.append([{"text": "🔙 Back",               "callback_data": "owner:back"}])
    return {"inline_keyboard": rows}


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _owner_send_file_content(bot_number, chat_id, fname):
    path = JSON_FILE_MAP.get(fname)
    if not path:
        admin_send_message(bot_number, chat_id, "❌ Unknown file.",
                           owner_files_keyboard())
        return
    if not os.path.exists(path):
        admin_send_message(bot_number, chat_id,
                           f"❌ File not found: <code>{fname}</code>",
                           owner_files_keyboard())
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        except Exception:
            pretty = raw
    except Exception as e:
        admin_send_message(bot_number, chat_id, f"❌ Read failed: {e}",
                           owner_files_keyboard())
        return

    header = f"📄 <b>{fname}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    body = _escape_html(pretty)
    chunk = 3500
    if len(body) <= chunk:
        admin_send_message(bot_number, chat_id,
                           header + f"<pre>{body}</pre>",
                           owner_files_keyboard())
        return
    admin_send_message(bot_number, chat_id, header + "<i>(truncated preview)</i>")
    for i in range(0, len(body), chunk):
        admin_send_message(bot_number, chat_id, f"<pre>{body[i:i + chunk]}</pre>")
    admin_send_message(bot_number, chat_id, "✅ End of preview.",
                       owner_files_keyboard())


def _owner_download_file(bot_number, chat_id, fname):
    path = JSON_FILE_MAP.get(fname)
    if not path or not os.path.exists(path):
        admin_send_message(bot_number, chat_id,
                           f"❌ File not found: <code>{fname}</code>",
                           owner_files_keyboard())
        return
    try:
        url = f"{ADMIN_TG_APIS[bot_number]}/sendDocument"
        with open(path, "rb") as f:
            ADMIN_HTTP.post(url,
                            data={"chat_id": chat_id,
                                  "caption": f"📥 <b>{fname}</b>",
                                  "parse_mode": "HTML"},
                            files={"document": (fname, f, "application/json")},
                            timeout=(10, 60))
    except Exception as e:
        admin_send_message(bot_number, chat_id, f"❌ Download failed: {e}",
                           owner_files_keyboard())


def _owner_download_all(bot_number, chat_id):
    try:
        tmp_path = "/tmp/all_data.zip"
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, path in JSON_FILE_MAP.items():
                if os.path.exists(path):
                    zf.write(path, arcname=fname)
        url = f"{ADMIN_TG_APIS[bot_number]}/sendDocument"
        with open(tmp_path, "rb") as f:
            ADMIN_HTTP.post(url,
                            data={"chat_id": chat_id,
                                  "caption": "📦 <b>All data files</b>",
                                  "parse_mode": "HTML"},
                            files={"document": ("all_data.zip", f, "application/zip")},
                            timeout=(10, 60))
    except Exception as e:
        admin_send_message(bot_number, chat_id, f"❌ ZIP failed: {e}",
                           owner_files_keyboard())


# ============================================================
# 6. ROLES / AUTH
# ============================================================
def is_owner(uid):  return OWNER_ID.lstrip("-").isdigit() and str(uid) == OWNER_ID
def is_admin(uid):  return uid in DYNAMIC_ADMINS
def is_banned(uid): return uid in BANNED_USERS


def get_role(uid):
    if is_owner(uid): return "owner"
    if is_admin(uid): return "admin"
    return "user"


def role_badge(uid):
    return {"owner": "👑 Owner", "admin": "🛡 Admin", "user": "👤 User"}[get_role(uid)]


def is_active(uid):
    return True


# ============================================================
# 7. IMAGE CACHE
# ============================================================
def cache_image(path):
    if not os.path.exists(path):
        print(f"⚠️ Image not found: {path}")
        return
    try:
        with Image.open(path) as img:
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            IMAGE_CACHE[path] = buf.getvalue()
            print(f"✅ Cached: {path} ({len(IMAGE_CACHE[path]) / 1024:.1f} KB)")
    except Exception as e:
        print(f"❌ Compress fail {path}: {e}")
        try:
            with open(path, "rb") as f:
                IMAGE_CACHE[path] = f.read()
        except Exception as raw_e:
            print(f"❌ Raw load fail {path}: {raw_e}")


for _p in IMAGES.values():
    cache_image(_p)


# ============================================================
# 8. i18n — ALL 32 languages with native names
# ============================================================
LANGUAGES = {
    "en": "🇬🇧 English",
    "hi": "🇮🇳 हिन्दी",
    "bn": "🇧🇩 বাংলা",
    "ur": "🇵🇰 اردو",
    "ar": "🇸🇦 العربية",
    "es": "🇪🇸 Español",
    "fr": "🇫🇷 Français",
    "de": "🇩🇪 Deutsch",
    "pt": "🇧🇷 Português",
    "ru": "🇷🇺 Русский",
    "zh": "🇨🇳 中文",
    "ja": "🇯🇵 日本語",
    "ko": "🇰🇷 한국어",
    "id": "🇮🇩 Indonesia",
    "tr": "🇹🇷 Türkçe",
    "fa": "🇮🇷 فارسی",
    "it": "🇮🇹 Italiano",
    "vi": "🇻🇳 Tiếng Việt",
    "th": "🇹🇭 ไทย",
    "ta": "🇮🇳 தமிழ்",
    "te": "🇮🇳 తెలుగు",
    "mr": "🇮🇳 मराठी",
    "gu": "🇮🇳 ગુજરાતી",
    "pa": "🇮🇳 ਪੰਜਾਬੀ",
    "ml": "🇮🇳 മലയാളം",
    "nl": "🇳🇱 Nederlands",
    "pl": "🇵🇱 Polski",
    "uk": "🇺🇦 Українська",
    "ro": "🇷🇴 Română",
    "sw": "🇰🇪 Kiswahili",
    "ms": "🇲🇾 Bahasa Melayu",
    "fil": "🇵🇭 Filipino",
}

_LANG_ALIASES = {
    "en": "en", "en-us": "en", "en-gb": "en",
    "hi": "hi", "hi-in": "hi",
    "bn": "bn", "bn-bd": "bn", "bn-in": "bn",
    "ur": "ur", "ur-pk": "ur",
    "ar": "ar", "ar-sa": "ar", "ar-eg": "ar",
    "es": "es", "es-es": "es", "es-mx": "es",
    "fr": "fr", "fr-fr": "fr",
    "de": "de", "de-de": "de",
    "pt": "pt", "pt-br": "pt", "pt-pt": "pt",
    "ru": "ru", "ru-ru": "ru",
    "zh": "zh", "zh-cn": "zh", "zh-hans": "zh", "zh-tw": "zh", "zh-hant": "zh",
    "ja": "ja", "ja-jp": "ja",
    "ko": "ko", "ko-kr": "ko",
    "id": "id", "id-id": "id", "in": "id",
    "tr": "tr", "tr-tr": "tr",
    "fa": "fa", "fa-ir": "fa",
    "it": "it", "it-it": "it",
    "vi": "vi", "vi-vn": "vi",
    "th": "th", "th-th": "th",
    "ta": "ta", "ta-in": "ta",
    "te": "te", "te-in": "te",
    "mr": "mr", "mr-in": "mr",
    "gu": "gu", "gu-in": "gu",
    "pa": "pa", "pa-in": "pa",
    "ml": "ml", "ml-in": "ml",
    "nl": "nl", "nl-nl": "nl",
    "pl": "pl", "pl-pl": "pl",
    "uk": "uk", "uk-ua": "uk",
    "ro": "ro", "ro-ro": "ro",
    "sw": "sw", "sw-ke": "sw",
    "ms": "ms", "ms-my": "ms",
    "fil": "fil", "tl": "fil",
}


TEXTS = {
    "en": {
        "welcome": ("✨ <b>W E L C O M E</b> ✨\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Free OSINT Bot</b>\n📓 All intelligence tools unlocked\n"
                    "⚡ Fast  •  🔒 Secure  •  🎯 Reliable\n\n"
                    "💬 <b>Support:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>M A I N   M E N U</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>Select a feature to begin:</b>\n\n"
                           "💬 <b>Support:</b> @Tony_M_unlock"),
        "support": "💬 <b>Support:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>Cancelled.</b>",
        "choose_language": "🌐 <b>Please choose your language:</b>",
        "language_set": "✅ Language updated successfully.",
        "send_cancel": "💡 <i>Send /cancel to cancel anytime.</i>",
        "searching": "🔎 <i>Searching...</i>",
        "no_result": "❌ No result or API error occurred.",
        "select_option": "❓ Please select an option from the menu.",
        "back": "🔙 Back",
        "cancel_btn": "❌ Cancel",
        "lang_btn": "🌐 Language",
        "support_btn": "💬 Support",
        "continue_btn": "✅ Continue",
        "join_required": ("🔒 <b>MEMBERSHIP REQUIRED</b>\n"
                          "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "You must join our <b>channel</b> and <b>group</b> "
                          "to use this bot.\n\n"
                          "After joining, tap <b>✅ Continue</b>.\n\n"
                          "💬 <b>Support:</b> @Tony_M_unlock"),
        "join_ok": "✅ Memberships verified. Welcome!",
        "join_fail": "❌ You must join both the channel and group first.",
        "maintenance": ("🛠 <b>U N D E R   M A I N T E N A N C E</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ The bot is temporarily <b>offline</b>\n"
                        "for scheduled maintenance.\n\n"
                        "🕒 Please try again in a little while.\n\n"
                        "💬 <b>Support:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>ACCESS BLOCKED</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Your account has been suspended."),
        "current_lang": "🌐 Current language: <b>{name}</b>",
    },
    "hi": {
        "welcome": ("✨ <b>स्वागत है</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>फ्री OSINT बॉट</b>\n📓 सभी इंटेलिजेंस टूल्स अनलॉक\n"
                    "⚡ तेज़  •  🔒 सुरक्षित  •  🎯 विश्वसनीय\n\n"
                    "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>मुख्य मेनू</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>शुरू करने के लिए एक सुविधा चुनें:</b>\n\n"
                           "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "support": "💬 <b>सपोर्ट:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>रद्द किया गया।</b>",
        "choose_language": "🌐 <b>कृपया अपनी भाषा चुनें:</b>",
        "language_set": "✅ भाषा सफलतापूर्वक अपडेट हो गई।",
        "send_cancel": "💡 <i>रद्द करने के लिए /cancel भेजें।</i>",
        "searching": "🔎 <i>खोज रहे हैं...</i>",
        "no_result": "❌ कोई परिणाम नहीं या API त्रुटि।",
        "select_option": "❓ कृपया मेनू से एक विकल्प चुनें।",
        "back": "🔙 वापस",
        "cancel_btn": "❌ रद्द करें",
        "lang_btn": "🌐 भाषा",
        "support_btn": "💬 सपोर्ट",
        "continue_btn": "✅ जारी रखें",
        "join_required": ("🔒 <b>सदस्यता आवश्यक</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "बॉट का उपयोग करने के लिए कृपया चैनल और ग्रुप जॉइन करें।\n\n"
                          "फिर <b>✅ जारी रखें</b> पर टैप करें।\n\n"
                          "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "join_ok": "✅ सदस्यता सत्यापित। स्वागत है!",
        "join_fail": "❌ पहले चैनल और ग्रुप दोनों जॉइन करें।",
        "maintenance": ("🛠 <b>रखरखाव जारी</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ बॉट अनुसूचित रखरखाव के लिए\n"
                        "अस्थायी रूप से <b>ऑफ़लाइन</b> है।\n\n"
                        "🕒 कृपया थोड़ी देर बाद पुनः प्रयास करें।\n\n"
                        "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>पहुंच अवरुद्ध</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "आपका खाता निलंबित कर दिया गया है।"),
        "current_lang": "🌐 वर्तमान भाषा: <b>{name}</b>",
    },
    "bn": {
        "welcome": ("✨ <b>স্বাগতম</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>ফ্রি OSINT বট</b>\n📓 সমস্ত ইন্টেলিজেন্স টুল আনলক\n"
                    "⚡ দ্রুত  •  🔒 নিরাপদ  •  🎯 নির্ভরযোগ্য\n\n"
                    "💬 <b>সাপোর্ট:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>প্রধান মেনু</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>শুরু করতে একটি ফিচার নির্বাচন করুন:</b>\n\n"
                           "💬 <b>সাপোর্ট:</b> @Tony_M_unlock"),
        "support": "💬 <b>সাপোর্ট:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>বাতিল হয়েছে।</b>",
        "choose_language": "🌐 <b>অনুগ্রহ করে আপনার ভাষা নির্বাচন করুন:</b>",
        "language_set": "✅ ভাষা সফলভাবে আপডেট হয়েছে।",
        "send_cancel": "💡 <i>বাতিল করতে /cancel পাঠান।</i>",
        "searching": "🔎 <i>খোঁজা হচ্ছে...</i>",
        "no_result": "❌ কোনো ফলাফল নেই বা API ত্রুটি।",
        "select_option": "❓ মেনু থেকে একটি অপশন নির্বাচন করুন।",
        "back": "🔙 ফিরে যান",
        "cancel_btn": "❌ বাতিল",
        "lang_btn": "🌐 ভাষা",
        "support_btn": "💬 সাপোর্ট",
        "continue_btn": "✅ চালিয়ে যান",
        "join_required": ("🔒 <b>সদস্যপদ প্রয়োজন</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "এই বট ব্যবহার করতে চ্যানেল ও গ্রুপ জয়েন করুন।\n\n"
                          "এরপর <b>✅ চালিয়ে যান</b> চাপুন।\n\n"
                          "💬 <b>সাপোর্ট:</b> @Tony_M_unlock"),
        "join_ok": "✅ সদস্যপদ যাচাই হয়েছে। স্বাগতম!",
        "join_fail": "❌ প্রথমে চ্যানেল ও গ্রুপ উভয়েই জয়েন করুন।",
        "maintenance": ("🛠 <b>রক্ষণাবেক্ষণ চলছে</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ বট নির্ধারিত রক্ষণাবেক্ষণের জন্য\n"
                        "সাময়িকভাবে <b>অফলাইন</b>।\n\n"
                        "🕒 অনুগ্রহ করে কিছুক্ষণ পরে আবার চেষ্টা করুন।\n\n"
                        "💬 <b>সাপোর্ট:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>প্রবেশ অবরুদ্ধ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "আপনার অ্যাকাউন্ট স্থগিত করা হয়েছে।"),
        "current_lang": "🌐 বর্তমান ভাষা: <b>{name}</b>",
    },
    "ur": {
        "welcome": ("✨ <b>خوش آمدید</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>مفت OSINT بوٹ</b>\n📓 تمام انٹیلیجنس ٹولز غیر مقفل\n"
                    "⚡ تیز  •  🔒 محفوظ  •  🎯 قابل اعتماد\n\n"
                    "💬 <b>سپورٹ:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>مرکزی مینو</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>شروع کرنے کے لیے ایک فیچر منتخب کریں:</b>\n\n"
                           "💬 <b>سپورٹ:</b> @Tony_M_unlock"),
        "support": "💬 <b>سپورٹ:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>منسوخ کر دیا گیا۔</b>",
        "choose_language": "🌐 <b>براہ کرم اپنی زبان منتخب کریں:</b>",
        "language_set": "✅ زبان کامیابی سے اپ ڈیٹ ہو گئی۔",
        "send_cancel": "💡 <i>منسوخ کرنے کے لیے /cancel بھیجیں۔</i>",
        "searching": "🔎 <i>تلاش جاری ہے...</i>",
        "no_result": "❌ کوئی نتیجہ نہیں یا API خرابی۔",
        "select_option": "❓ براہ کرم مینو سے ایک آپشن منتخب کریں۔",
        "back": "🔙 واپس",
        "cancel_btn": "❌ منسوخ",
        "lang_btn": "🌐 زبان",
        "support_btn": "💬 سپورٹ",
        "continue_btn": "✅ جاری رکھیں",
        "join_required": ("🔒 <b>رکنیت درکار ہے</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "اس بوٹ کو استعمال کرنے کے لیے چینل اور گروپ جوائن کریں۔\n\n"
                          "جوائن کے بعد <b>✅ جاری رکھیں</b> پر ٹیپ کریں۔\n\n"
                          "💬 <b>سپورٹ:</b> @Tony_M_unlock"),
        "join_ok": "✅ رکنیت کی تصدیق ہو گئی۔ خوش آمدید!",
        "join_fail": "❌ پہلے چینل اور گروپ دونوں جوائن کریں۔",
        "maintenance": ("🛠 <b>دیکھ بھال جاری ہے</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ بوٹ مقررہ دیکھ بھال کے لیے\n"
                        "عارضی طور پر <b>آف لائن</b> ہے۔\n\n"
                        "🕒 براہ کرم تھوڑی دیر بعد دوبارہ کوشش کریں۔\n\n"
                        "💬 <b>سپورٹ:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>رسائی بلاک</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "آپ کا اکاؤنٹ معطل کر دیا گیا ہے۔"),
        "current_lang": "🌐 موجودہ زبان: <b>{name}</b>",
    },
    "ar": {
        "welcome": ("✨ <b>مرحباً</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>بوت OSINT مجاني</b>\n📓 جميع أدوات الاستخبارات مفتوحة\n"
                    "⚡ سريع  •  🔒 آمن  •  🎯 موثوق\n\n"
                    "💬 <b>الدعم:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>القائمة الرئيسية</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>اختر ميزة للبدء:</b>\n\n💬 <b>الدعم:</b> @Tony_M_unlock"),
        "support": "💬 <b>الدعم:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>تم الإلغاء.</b>",
        "choose_language": "🌐 <b>الرجاء اختيار لغتك:</b>",
        "language_set": "✅ تم تحديث اللغة بنجاح.",
        "send_cancel": "💡 <i>أرسل /cancel للإلغاء.</i>",
        "searching": "🔎 <i>جاري البحث...</i>",
        "no_result": "❌ لا توجد نتيجة أو حدث خطأ في API.",
        "select_option": "❓ الرجاء اختيار خيار من القائمة.",
        "back": "🔙 رجوع",
        "cancel_btn": "❌ إلغاء",
        "lang_btn": "🌐 اللغة",
        "support_btn": "💬 الدعم",
        "continue_btn": "✅ متابعة",
        "join_required": ("🔒 <b>العضوية مطلوبة</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "يجب الانضمام إلى القناة والمجموعة لاستخدام هذا البوت.\n\n"
                          "بعد الانضمام اضغط <b>✅ متابعة</b>.\n\n"
                          "💬 <b>الدعم:</b> @Tony_M_unlock"),
        "join_ok": "✅ تم التحقق من العضوية. مرحباً!",
        "join_fail": "❌ يجب الانضمام إلى القناة والمجموعة أولاً.",
        "maintenance": ("🛠 <b>تحت الصيانة</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ البوت <b>غير متصل</b> مؤقتاً\n"
                        "لأعمال صيانة مجدولة.\n\n"
                        "🕒 يرجى المحاولة مرة أخرى بعد قليل.\n\n"
                        "💬 <b>الدعم:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>تم حظر الوصول</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "تم تعليق حسابك."),
        "current_lang": "🌐 اللغة الحالية: <b>{name}</b>",
    },
    "es": {
        "welcome": ("✨ <b>B I E N V E N I D O</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Bot OSINT Gratuito</b>\n📓 Todas las herramientas desbloqueadas\n"
                    "⚡ Rápido  •  🔒 Seguro  •  🎯 Confiable\n\n"
                    "💬 <b>Soporte:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>M E N Ú   P R I N C I P A L</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>Selecciona una función:</b>\n\n💬 <b>Soporte:</b> @Tony_M_unlock"),
        "support": "💬 <b>Soporte:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>Cancelado.</b>",
        "choose_language": "🌐 <b>Por favor elige tu idioma:</b>",
        "language_set": "✅ Idioma actualizado correctamente.",
        "send_cancel": "💡 <i>Envía /cancel para cancelar.</i>",
        "searching": "🔎 <i>Buscando...</i>",
        "no_result": "❌ No hay resultados o error en la API.",
        "select_option": "❓ Selecciona una opción del menú.",
        "back": "🔙 Atrás",
        "cancel_btn": "❌ Cancelar",
        "lang_btn": "🌐 Idioma",
        "support_btn": "💬 Soporte",
        "continue_btn": "✅ Continuar",
        "join_required": ("🔒 <b>SE REQUIERE MEMBRESÍA</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "Debes unirte a nuestro canal y grupo para usar este bot.\n\n"
                          "Después pulsa <b>✅ Continuar</b>.\n\n"
                          "💬 <b>Soporte:</b> @Tony_M_unlock"),
        "join_ok": "✅ Membresías verificadas. ¡Bienvenido!",
        "join_fail": "❌ Primero debes unirte al canal y al grupo.",
        "maintenance": ("🛠 <b>E N   M A N T E N I M I E N T O</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ El bot está <b>fuera de línea</b> temporalmente\n"
                        "por mantenimiento programado.\n\n"
                        "🕒 Inténtalo de nuevo en un momento.\n\n"
                        "💬 <b>Soporte:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>ACCESO BLOQUEADO</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Tu cuenta ha sido suspendida."),
        "current_lang": "🌐 Idioma actual: <b>{name}</b>",
    },
    "fr": {
        "welcome": ("✨ <b>B I E N V E N U E</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Bot OSINT Gratuit</b>\n📓 Tous les outils débloqués\n"
                    "⚡ Rapide  •  🔒 Sécurisé  •  🎯 Fiable\n\n"
                    "💬 <b>Support:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>M E N U   P R I N C I P A L</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>Sélectionnez une fonctionnalité:</b>\n\n💬 <b>Support:</b> @Tony_M_unlock"),
        "support": "💬 <b>Support:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>Annulé.</b>",
        "choose_language": "🌐 <b>Veuillez choisir votre langue:</b>",
        "language_set": "✅ Langue mise à jour avec succès.",
        "send_cancel": "💡 <i>Envoyez /cancel pour annuler.</i>",
        "searching": "🔎 <i>Recherche...</i>",
        "no_result": "❌ Aucun résultat ou erreur API.",
        "select_option": "❓ Veuillez sélectionner une option.",
        "back": "🔙 Retour",
        "cancel_btn": "❌ Annuler",
        "lang_btn": "🌐 Langue",
        "support_btn": "💬 Support",
        "continue_btn": "✅ Continuer",
        "join_required": ("🔒 <b>ADHÉSION REQUISE</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "Rejoignez notre chaîne et groupe pour utiliser ce bot.\n\n"
                          "Puis appuyez sur <b>✅ Continuer</b>.\n\n"
                          "💬 <b>Support:</b> @Tony_M_unlock"),
        "join_ok": "✅ Adhésions vérifiées. Bienvenue!",
        "join_fail": "❌ Rejoignez d'abord la chaîne et le groupe.",
        "maintenance": ("🛠 <b>E N   M A I N T E N A N C E</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ Le bot est temporairement <b>hors ligne</b>\n"
                        "pour maintenance programmée.\n\n"
                        "🕒 Réessayez dans quelques instants.\n\n"
                        "💬 <b>Support:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>ACCÈS BLOQUÉ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Votre compte a été suspendu."),
        "current_lang": "🌐 Langue actuelle: <b>{name}</b>",
    },
    "ru": {
        "welcome": ("✨ <b>Д О Б Р О   П О Ж А Л О В А Т Ь</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Бесплатный OSINT-бот</b>\n📓 Все инструменты разблокированы\n"
                    "⚡ Быстро  •  🔒 Безопасно  •  🎯 Надёжно\n\n"
                    "💬 <b>Поддержка:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>Г Л А В Н О Е   М Е Н Ю</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>Выберите функцию:</b>\n\n💬 <b>Поддержка:</b> @Tony_M_unlock"),
        "support": "💬 <b>Поддержка:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>Отменено.</b>",
        "choose_language": "🌐 <b>Выберите язык:</b>",
        "language_set": "✅ Язык успешно обновлён.",
        "send_cancel": "💡 <i>Отправьте /cancel чтобы отменить.</i>",
        "searching": "🔎 <i>Поиск...</i>",
        "no_result": "❌ Нет результатов или ошибка API.",
        "select_option": "❓ Выберите пункт из меню.",
        "back": "🔙 Назад",
        "cancel_btn": "❌ Отмена",
        "lang_btn": "🌐 Язык",
        "support_btn": "💬 Поддержка",
        "continue_btn": "✅ Продолжить",
        "join_required": ("🔒 <b>ТРЕБУЕТСЯ ПОДПИСКА</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "Присоединитесь к каналу и группе чтобы использовать бота.\n\n"
                          "После нажмите <b>✅ Продолжить</b>.\n\n"
                          "💬 <b>Поддержка:</b> @Tony_M_unlock"),
        "join_ok": "✅ Подписки подтверждены. Добро пожаловать!",
        "join_fail": "❌ Сначала присоединитесь к каналу и группе.",
        "maintenance": ("🛠 <b>Т Е Х Н И Ч Е С К О Е   О Б С Л У Ж И В А Н И Е</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ Бот временно <b>недоступен</b>\n"
                        "по причине планового обслуживания.\n\n"
                        "🕒 Попробуйте ещё раз через несколько минут.\n\n"
                        "💬 <b>Поддержка:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>ДОСТУП ЗАБЛОКИРОВАН</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Ваш аккаунт заблокирован."),
        "current_lang": "🌐 Текущий язык: <b>{name}</b>",
    },
    "pt": {
        "welcome": ("✨ <b>B E M - V I N D O</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Bot OSINT Gratuito</b>\n📓 Todas as ferramentas desbloqueadas\n"
                    "⚡ Rápido  •  🔒 Seguro  •  🎯 Confiável\n\n"
                    "💬 <b>Suporte:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>M E N U   P R I N C I P A L</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>Selecione um recurso:</b>\n\n💬 <b>Suporte:</b> @Tony_M_unlock"),
        "support": "💬 <b>Suporte:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>Cancelado.</b>",
        "choose_language": "🌐 <b>Escolha seu idioma:</b>",
        "language_set": "✅ Idioma atualizado com sucesso.",
        "send_cancel": "💡 <i>Envie /cancel para cancelar.</i>",
        "searching": "🔎 <i>Pesquisando...</i>",
        "no_result": "❌ Nenhum resultado ou erro na API.",
        "select_option": "❓ Selecione uma opção do menu.",
        "back": "🔙 Voltar",
        "cancel_btn": "❌ Cancelar",
        "lang_btn": "🌐 Idioma",
        "support_btn": "💬 Suporte",
        "continue_btn": "✅ Continuar",
        "join_required": ("🔒 <b>MEMBRESIA NECESSÁRIA</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "Entre no nosso canal e grupo para usar este bot.\n\n"
                          "Depois toque em <b>✅ Continuar</b>.\n\n"
                          "💬 <b>Suporte:</b> @Tony_M_unlock"),
        "join_ok": "✅ Membresias verificadas. Bem-vindo!",
        "join_fail": "❌ Entre no canal e no grupo primeiro.",
        "maintenance": ("🛠 <b>E M   M A N U T E N Ç Ã O</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ O bot está temporariamente <b>offline</b>\n"
                        "para manutenção programada.\n\n"
                        "🕒 Tente novamente em alguns instantes.\n\n"
                        "💬 <b>Suporte:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>ACESSO BLOQUEADO</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Sua conta foi suspensa."),
        "current_lang": "🌐 Idioma atual: <b>{name}</b>",
    },
    "id": {
        "welcome": ("✨ <b>S E L A M A T   D A T A N G</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Bot OSINT Gratis</b>\n📓 Semua alat intelijen terbuka\n"
                    "⚡ Cepat  •  🔒 Aman  •  🎯 Andal\n\n"
                    "💬 <b>Dukungan:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>M E N U   U T A M A</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👮 <b>Pilih fitur untuk memulai:</b>\n\n💬 <b>Dukungan:</b> @Tony_M_unlock"),
        "support": "💬 <b>Dukungan:</b> @Tony_M_unlock",
        "cancelled": "❌ <b>Dibatalkan.</b>",
        "choose_language": "🌐 <b>Silakan pilih bahasa Anda:</b>",
        "language_set": "✅ Bahasa berhasil diperbarui.",
        "send_cancel": "💡 <i>Kirim /cancel untuk membatalkan.</i>",
        "searching": "🔎 <i>Mencari...</i>",
        "no_result": "❌ Tidak ada hasil atau kesalahan API.",
        "select_option": "❓ Silakan pilih opsi dari menu.",
        "back": "🔙 Kembali",
        "cancel_btn": "❌ Batal",
        "lang_btn": "🌐 Bahasa",
        "support_btn": "💬 Dukungan",
        "continue_btn": "✅ Lanjutkan",
        "join_required": ("🔒 <b>KEANGGOTAAN DIPERLUKAN</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "Gabung ke saluran dan grup kami untuk menggunakan bot ini.\n\n"
                          "Setelah bergabung, ketuk <b>✅ Lanjutkan</b>.\n\n"
                          "💬 <b>Dukungan:</b> @Tony_M_unlock"),
        "join_ok": "✅ Keanggotaan diverifikasi. Selamat datang!",
        "join_fail": "❌ Anda harus bergabung dengan saluran dan grup dulu.",
        "maintenance": ("🛠 <b>D A L A M   P E M E L I H A R A A N</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "⚠️ Bot sementara <b>offline</b>\n"
                        "untuk pemeliharaan terjadwal.\n\n"
                        "🕒 Silakan coba lagi beberapa saat lagi.\n\n"
                        "💬 <b>Dukungan:</b> @Tony_M_unlock"),
        "banned_msg": ("🚫 <b>AKSES DIBLOKIR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Akun Anda telah ditangguhkan."),
        "current_lang": "🌐 Bahasa saat ini: <b>{name}</b>",
    },
}


def _register_lang(code, data):
    """Merge compact translation data with English fallback."""
    base = dict(TEXTS["en"])
    base.update(data)
    TEXTS[code] = base


_register_lang("de", {
    "welcome": "✨ <b>W I L L K O M M E N</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Kostenloser OSINT-Bot</b>\n📓 Alle Tools freigeschaltet\n⚡ Schnell  •  🔒 Sicher  •  🎯 Zuverlässig\n\n💬 <b>Support:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>H A U P T M E N Ü</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Wählen Sie eine Funktion:</b>",
    "cancelled": "❌ <b>Abgebrochen.</b>",
    "choose_language": "🌐 <b>Bitte wählen Sie Ihre Sprache:</b>",
    "language_set": "✅ Sprache erfolgreich aktualisiert.",
    "send_cancel": "💡 <i>Senden Sie /cancel zum Abbrechen.</i>",
    "searching": "🔎 <i>Suche läuft...</i>",
    "no_result": "❌ Kein Ergebnis oder API-Fehler.",
    "select_option": "❓ Bitte wählen Sie eine Option.",
    "back": "🔙 Zurück",
    "cancel_btn": "❌ Abbrechen",
    "lang_btn": "🌐 Sprache",
    "support_btn": "💬 Support",
    "continue_btn": "✅ Weiter",
    "maintenance": "🛠 <b>W A R T U N G</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Der Bot ist vorübergehend <b>offline</b> wegen geplanter Wartung.\n\n🕒 Bitte versuchen Sie es in Kürze erneut.\n\n💬 <b>Support:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ZUGANG BLOCKIERT</b>\n\nIhr Konto wurde gesperrt.",
    "current_lang": "🌐 Aktuelle Sprache: <b>{name}</b>",
})
_register_lang("zh", {
    "welcome": "✨ <b>欢 迎</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>免费 OSINT 机器人</b>\n📓 所有工具已解锁\n⚡ 快速  •  🔒 安全  •  🎯 可靠\n\n💬 <b>支持:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>主 菜 单</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>请选择功能:</b>",
    "cancelled": "❌ <b>已取消。</b>",
    "choose_language": "🌐 <b>请选择您的语言:</b>",
    "language_set": "✅ 语言更新成功。",
    "send_cancel": "💡 <i>发送 /cancel 取消。</i>",
    "searching": "🔎 <i>搜索中...</i>",
    "no_result": "❌ 没有结果或 API 错误。",
    "select_option": "❓ 请从菜单中选择一个选项。",
    "back": "🔙 返回",
    "cancel_btn": "❌ 取消",
    "lang_btn": "🌐 语言",
    "support_btn": "💬 支持",
    "continue_btn": "✅ 继续",
    "maintenance": "🛠 <b>维 护 中</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ 机器人因计划维护暂时<b>离线</b>。\n\n🕒 请稍后重试。\n\n💬 <b>支持:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>访问被阻止</b>\n\n您的账户已被暂停。",
    "current_lang": "🌐 当前语言: <b>{name}</b>",
})
_register_lang("ja", {
    "welcome": "✨ <b>よ う こ そ</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>無料 OSINT Bot</b>\n📓 すべてのツールが解除されました\n⚡ 高速  •  🔒 安全  •  🎯 信頼性\n\n💬 <b>サポート:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>メ イ ン メ ニ ュ ー</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>機能を選択してください:</b>",
    "cancelled": "❌ <b>キャンセルされました。</b>",
    "choose_language": "🌐 <b>言語を選択してください:</b>",
    "language_set": "✅ 言語が正常に更新されました。",
    "send_cancel": "💡 <i>/cancel を送信してキャンセル。</i>",
    "searching": "🔎 <i>検索中...</i>",
    "no_result": "❌ 結果なし、または API エラー。",
    "select_option": "❓ メニューから選択してください。",
    "back": "🔙 戻る",
    "cancel_btn": "❌ キャンセル",
    "lang_btn": "🌐 言語",
    "support_btn": "💬 サポート",
    "continue_btn": "✅ 続行",
    "maintenance": "🛠 <b>メ ン テ ナ ン ス 中</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Bot は定期メンテナンスのため一時的に<b>オフライン</b>です。\n\n🕒 しばらくしてからもう一度お試しください。\n\n💬 <b>サポート:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>アクセスがブロックされました</b>\n\nアカウントが停止されました。",
    "current_lang": "🌐 現在の言語: <b>{name}</b>",
})
_register_lang("ko", {
    "welcome": "✨ <b>환 영 합 니 다</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>무료 OSINT 봇</b>\n📓 모든 도구 잠금 해제\n⚡ 빠름  •  🔒 안전  •  🎯 신뢰성\n\n💬 <b>지원:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>메 인 메 뉴</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>기능을 선택하세요:</b>",
    "cancelled": "❌ <b>취소되었습니다.</b>",
    "choose_language": "🌐 <b>언어를 선택하세요:</b>",
    "language_set": "✅ 언어가 업데이트되었습니다.",
    "send_cancel": "💡 <i>/cancel 을 보내 취소하세요.</i>",
    "searching": "🔎 <i>검색 중...</i>",
    "no_result": "❌ 결과 없음 또는 API 오류.",
    "select_option": "❓ 메뉴에서 옵션을 선택하세요.",
    "back": "🔙 뒤로",
    "cancel_btn": "❌ 취소",
    "lang_btn": "🌐 언어",
    "support_btn": "💬 지원",
    "continue_btn": "✅ 계속",
    "maintenance": "🛠 <b>점 검 중</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ 봇이 예정된 점검으로 인해\n일시적으로 <b>오프라인</b>입니다.\n\n🕒 잠시 후 다시 시도해주세요.\n\n💬 <b>지원:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>접근 차단됨</b>\n\n계정이 정지되었습니다.",
    "current_lang": "🌐 현재 언어: <b>{name}</b>",
})
_register_lang("tr", {
    "welcome": "✨ <b>H O Ş   G E L D İ N İ Z</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Ücretsiz OSINT Botu</b>\n📓 Tüm araçlar açık\n⚡ Hızlı  •  🔒 Güvenli  •  🎯 Güvenilir\n\n💬 <b>Destek:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>A N A   M E N Ü</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Bir özellik seçin:</b>",
    "cancelled": "❌ <b>İptal edildi.</b>",
    "choose_language": "🌐 <b>Lütfen dilinizi seçin:</b>",
    "language_set": "✅ Dil başarıyla güncellendi.",
    "send_cancel": "💡 <i>İptal için /cancel gönderin.</i>",
    "searching": "🔎 <i>Aranıyor...</i>",
    "no_result": "❌ Sonuç yok veya API hatası.",
    "select_option": "❓ Lütfen menüden bir seçenek seçin.",
    "back": "🔙 Geri",
    "cancel_btn": "❌ İptal",
    "lang_btn": "🌐 Dil",
    "support_btn": "💬 Destek",
    "continue_btn": "✅ Devam",
    "maintenance": "🛠 <b>B A K I M D A</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Bot planlı bakım nedeniyle\ngeçici olarak <b>çevrimdışı</b>.\n\n🕒 Lütfen kısa süre sonra tekrar deneyin.\n\n💬 <b>Destek:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ERİŞİM ENGELLENDİ</b>\n\nHesabınız askıya alındı.",
    "current_lang": "🌐 Geçerli dil: <b>{name}</b>",
})
_register_lang("fa", {
    "welcome": "✨ <b>خ و ش   آ م د ی د</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>ربات OSINT رایگان</b>\n📓 همه ابزارها فعال شد\n⚡ سریع  •  🔒 امن  •  🎯 قابل اعتماد\n\n💬 <b>پشتیبانی:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>م ن و ی   ا ص ل ی</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>یک ویژگی انتخاب کنید:</b>",
    "cancelled": "❌ <b>لغو شد.</b>",
    "choose_language": "🌐 <b>لطفا زبان خود را انتخاب کنید:</b>",
    "language_set": "✅ زبان با موفقیت بهروز شد.",
    "send_cancel": "💡 <i>برای لغو /cancel بفرستید.</i>",
    "searching": "🔎 <i>در حال جستجو...</i>",
    "no_result": "❌ نتیجهای یافت نشد یا خطای API.",
    "select_option": "❓ لطفا یک گزینه انتخاب کنید.",
    "back": "🔙 بازگشت",
    "cancel_btn": "❌ لغو",
    "lang_btn": "🌐 زبان",
    "support_btn": "💬 پشتیبانی",
    "continue_btn": "✅ ادامه",
    "maintenance": "🛠 <b>د ر   ح ا ل   ت ع م ی ر</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ ربات به دلیل تعمیرات برنامهریزی شده\nموقتاً <b>آفلاین</b> است.\n\n🕒 لطفاً کمی بعد دوباره تلاش کنید.\n\n💬 <b>پشتیبانی:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>دسترسی مسدود شد</b>\n\nحساب شما تعلیق شده است.",
    "current_lang": "🌐 زبان فعلی: <b>{name}</b>",
})
_register_lang("it", {
    "welcome": "✨ <b>B E N V E N U T O</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Bot OSINT Gratuito</b>\n📓 Tutti gli strumenti sbloccati\n⚡ Veloce  •  🔒 Sicuro  •  🎯 Affidabile\n\n💬 <b>Supporto:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>M E N U   P R I N C I P A L E</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Seleziona una funzione:</b>",
    "cancelled": "❌ <b>Annullato.</b>",
    "choose_language": "🌐 <b>Seleziona la tua lingua:</b>",
    "language_set": "✅ Lingua aggiornata.",
    "send_cancel": "💡 <i>Invia /cancel per annullare.</i>",
    "searching": "🔎 <i>Ricerca...</i>",
    "no_result": "❌ Nessun risultato o errore API.",
    "select_option": "❓ Seleziona un'opzione dal menu.",
    "back": "🔙 Indietro",
    "cancel_btn": "❌ Annulla",
    "lang_btn": "🌐 Lingua",
    "support_btn": "💬 Supporto",
    "continue_btn": "✅ Continua",
    "maintenance": "🛠 <b>I N   M A N U T E N Z I O N E</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Il bot è temporaneamente <b>offline</b>\nper manutenzione programmata.\n\n🕒 Riprova tra qualche istante.\n\n💬 <b>Supporto:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ACCESSO BLOCCATO</b>\n\nIl tuo account è stato sospeso.",
    "current_lang": "🌐 Lingua attuale: <b>{name}</b>",
})
_register_lang("vi", {
    "welcome": "✨ <b>C H À O   M Ừ N G</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Bot OSINT Miễn phí</b>\n📓 Tất cả công cụ đã mở khóa\n⚡ Nhanh  •  🔒 An toàn  •  🎯 Đáng tin\n\n💬 <b>Hỗ trợ:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>M E N U   C H Í N H</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Chọn một tính năng:</b>",
    "cancelled": "❌ <b>Đã hủy.</b>",
    "choose_language": "🌐 <b>Vui lòng chọn ngôn ngữ:</b>",
    "language_set": "✅ Đã cập nhật ngôn ngữ.",
    "send_cancel": "💡 <i>Gửi /cancel để hủy.</i>",
    "searching": "🔎 <i>Đang tìm...</i>",
    "no_result": "❌ Không có kết quả hoặc lỗi API.",
    "select_option": "❓ Vui lòng chọn từ menu.",
    "back": "🔙 Quay lại",
    "cancel_btn": "❌ Hủy",
    "lang_btn": "🌐 Ngôn ngữ",
    "support_btn": "💬 Hỗ trợ",
    "continue_btn": "✅ Tiếp tục",
    "maintenance": "🛠 <b>Đ A N G   B Ả O   T R Ì</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Bot tạm thời <b>offline</b>\nđể bảo trì theo lịch.\n\n🕒 Vui lòng thử lại sau ít phút.\n\n💬 <b>Hỗ trợ:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>TRUY CẬP BỊ CHẶN</b>\n\nTài khoản của bạn đã bị đình chỉ.",
    "current_lang": "🌐 Ngôn ngữ hiện tại: <b>{name}</b>",
})
_register_lang("th", {
    "welcome": "✨ <b>ย ิ น ดี ต ้ อ น ร ั บ</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>บอท OSINT ฟรี</b>\n📓 ปลดล็อกเครื่องมือทั้งหมด\n⚡ เร็ว  •  🔒 ปลอดภัย  •  🎯 เชื่อถือได้\n\n💬 <b>สนับสนุน:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>เ ม น ู ห ล ั ก</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>เลือกคุณสมบัติ:</b>",
    "cancelled": "❌ <b>ยกเลิกแล้ว</b>",
    "choose_language": "🌐 <b>เลือกภาษาของคุณ:</b>",
    "language_set": "✅ อัปเดตภาษาเรียบร้อย",
    "send_cancel": "💡 <i>ส่ง /cancel เพื่อยกเลิก</i>",
    "searching": "🔎 <i>กำลังค้นหา...</i>",
    "no_result": "❌ ไม่พบผลลัพธ์หรือเกิดข้อผิดพลาด",
    "select_option": "❓ เลือกตัวเลือกจากเมนู",
    "back": "🔙 กลับ",
    "cancel_btn": "❌ ยกเลิก",
    "lang_btn": "🌐 ภาษา",
    "support_btn": "💬 สนับสนุน",
    "continue_btn": "✅ ดำเนินการต่อ",
    "maintenance": "🛠 <b>อ ย ู ่ ร ะ ห ว ่ า ง ก า ร บ ำ ร ุ ง ร ั ก ษ า</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ บอท <b>ออฟไลน์</b> ชั่วคราว\nเนื่องจากการบำรุงรักษาตามกำหนด\n\n🕒 โปรดลองใหม่ภายหลัง\n\n💬 <b>สนับสนุน:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>การเข้าถึงถูกบล็อก</b>\n\nบัญชีของคุณถูกระงับ",
    "current_lang": "🌐 ภาษาปัจจุบัน: <b>{name}</b>",
})
_register_lang("ta", {
    "welcome": "✨ <b>வ ர வ ே ற ் ப ு</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>இலவச OSINT போட்</b>\n📓 அனைத்து கருவிகளும் திறக்கப்பட்டன\n⚡ வேகம்  •  🔒 பாதுகாப்பு  •  🎯 நம்பகத்தன்மை\n\n💬 <b>ஆதரவு:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>ம ு க ் க ி ய   ம ெ ன ு</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>ஒரு அம்சத்தை தேர்ந்தெடுக்கவும்:</b>",
    "cancelled": "❌ <b>ரத்து செய்யப்பட்டது.</b>",
    "choose_language": "🌐 <b>உங்கள் மொழியை தேர்ந்தெடுக்கவும்:</b>",
    "language_set": "✅ மொழி புதுப்பிக்கப்பட்டது.",
    "send_cancel": "💡 <i>ரத்து செய்ய /cancel அனுப்பவும்.</i>",
    "searching": "🔎 <i>தேடுகிறது...</i>",
    "no_result": "❌ முடிவு இல்லை அல்லது API பிழை.",
    "select_option": "❓ மெனுவிலிருந்து ஒரு விருப்பத்தை தேர்ந்தெடுக்கவும்.",
    "back": "🔙 பின்செல்",
    "cancel_btn": "❌ ரத்து",
    "lang_btn": "🌐 மொழி",
    "support_btn": "💬 ஆதரவு",
    "continue_btn": "✅ தொடரவும்",
    "maintenance": "🛠 <b>ப ர ா ம ர ி ப ் ப ு   ந ட ை ப ெ ற ு க ி ற த ு</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ திட்டமிடப்பட்ட பராமரிப்பு காரணமாக\nபோட் தற்காலிகமாக <b>ஆஃப்லைன்</b> உள்ளது.\n\n🕒 சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்.\n\n💬 <b>ஆதரவு:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>அணுகல் தடுக்கப்பட்டது</b>\n\nஉங்கள் கணக்கு இடைநிறுத்தப்பட்டது.",
    "current_lang": "🌐 தற்போதைய மொழி: <b>{name}</b>",
})
_register_lang("te", {
    "welcome": "✨ <b>స ్ వ ా గ త ం</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>ఉచిత OSINT బాట్</b>\n📓 అన్ని సాధనాలు అన్లాక్\n⚡ వేగం  •  🔒 సురక్షితం  •  🎯 విశ్వసనీయం\n\n💬 <b>మద్దతు:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>ప ్ ర ధ ా న   మ ె న ూ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>ఒక ఫీచర్ ఎంచుకోండి:</b>",
    "cancelled": "❌ <b>రద్దు చేయబడింది.</b>",
    "choose_language": "🌐 <b>మీ భాషను ఎంచుకోండి:</b>",
    "language_set": "✅ భాష నవీకరించబడింది.",
    "send_cancel": "💡 <i>రద్దు కోసం /cancel పంపండి.</i>",
    "searching": "🔎 <i>వెతుకుతోంది...</i>",
    "no_result": "❌ ఫలితం లేదు లేదా API లోపం.",
    "select_option": "❓ మెనూ నుండి ఎంపిక చేయండి.",
    "back": "🔙 వెనుకకు",
    "cancel_btn": "❌ రద్దు",
    "lang_btn": "🌐 భాష",
    "support_btn": "💬 మద్దతు",
    "continue_btn": "✅ కొనసాగించు",
    "maintenance": "🛠 <b>న ి ర ్ వ హ ణ   ల ో   ఉ ం ద ి</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ షెడ్యూల్ నిర్వహణ కారణంగా\nబాట్ తాత్కాలికంగా <b>ఆఫ్లైన్</b>.\n\n🕒 కొద్దిసేపటి తర్వాత మళ్లీ ప్రయత్నించండి.\n\n💬 <b>మద్దతు:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>యాక్సెస్ బ్లాక్ చేయబడింది</b>\n\nమీ ఖాతా సస్పెండ్ చేయబడింది.",
    "current_lang": "🌐 ప్రస్తుత భాష: <b>{name}</b>",
})
_register_lang("mr", {
    "welcome": "✨ <b>स ् व ा ग त</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>मोफत OSINT बॉट</b>\n📓 सर्व साधने अनलॉक\n⚡ वेगवान  •  🔒 सुरक्षित  •  🎯 विश्वसनीय\n\n💬 <b>सपोर्ट:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>म ु ख ् य   म े न ू</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>वैशिष्ट्य निवडा:</b>",
    "cancelled": "❌ <b>रद्द केले.</b>",
    "choose_language": "🌐 <b>तुमची भाषा निवडा:</b>",
    "language_set": "✅ भाषा अपडेट झाली.",
    "send_cancel": "💡 <i>रद्द करण्यासाठी /cancel पाठवा.</i>",
    "searching": "🔎 <i>शोधत आहोत...</i>",
    "no_result": "❌ निकाल नाही किंवा API त्रुटी.",
    "select_option": "❓ मेनूमधून पर्याय निवडा.",
    "back": "🔙 मागे",
    "cancel_btn": "❌ रद्द करा",
    "lang_btn": "🌐 भाषा",
    "support_btn": "💬 सपोर्ट",
    "continue_btn": "✅ सुरू ठेवा",
    "maintenance": "🛠 <b>द े ख भ ा ल   स ु र ू</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ नियोजित देखभालीमुळे\nबॉट तात्पुरता <b>ऑफलाइन</b> आहे.\n\n🕒 कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.\n\n💬 <b>सपोर्ट:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>प्रवेश अवरोधित</b>\n\nतुमचे खाते निलंबित केले आहे.",
    "current_lang": "🌐 सध्याची भाषा: <b>{name}</b>",
})
_register_lang("gu", {
    "welcome": "✨ <b>સ ્ વ ા ગ ત</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>મફત OSINT બોટ</b>\n📓 બધા સાધનો અનલૉક\n⚡ ઝડપી  •  🔒 સુરક્ષિત  •  🎯 વિશ્વસનીય\n\n💬 <b>સપોર્ટ:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>મ ુ ખ ્ ય   મ ે ન ૂ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>એક સુવિધા પસંદ કરો:</b>",
    "cancelled": "❌ <b>રદ કર્યું.</b>",
    "choose_language": "🌐 <b>તમારી ભાષા પસંદ કરો:</b>",
    "language_set": "✅ ભાષા અપડેટ થઈ.",
    "send_cancel": "💡 <i>રદ કરવા /cancel મોકલો.</i>",
    "searching": "🔎 <i>શોધી રહ્યું છે...</i>",
    "no_result": "❌ કોઈ પરિણામ નથી અથવા API ભૂલ.",
    "select_option": "❓ મેનૂમાંથી વિકલ્પ પસંદ કરો.",
    "back": "🔙 પાછળ",
    "cancel_btn": "❌ રદ કરો",
    "lang_btn": "🌐 ભાષા",
    "support_btn": "💬 સપોર્ટ",
    "continue_btn": "✅ ચાલુ રાખો",
    "maintenance": "🛠 <b>જ ા ળ વ ણ ી   ચ ા લ ુ   છ ે</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ નિર્ધારિત જાળવણીને કારણે\nબોટ અસ્થાયી રૂપે <b>ઑફલાઇન</b> છે.\n\n🕒 થોડા સમય પછી ફરી પ્રયાસ કરો.\n\n💬 <b>સપોર્ટ:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>એક્સેસ બ્લોક</b>\n\nતમારું એકાઉન્ટ સસ્પેન્ડ કર્યું છે.",
    "current_lang": "🌐 વર્તમાન ભાષા: <b>{name}</b>",
})
_register_lang("pa", {
    "welcome": "✨ <b>ਜ ੀ   ਆ ਇ ਆ ਂ   ਨ ੂ ਂ</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>ਮੁਫ਼ਤ OSINT ਬੋਟ</b>\n📓 ਸਾਰੇ ਸਾਧਨ ਅਨਲੌਕ\n⚡ ਤੇਜ਼  •  🔒 ਸੁਰੱਖਿਅਤ  •  🎯 ਭਰੋਸੇਯੋਗ\n\n💬 <b>ਸਪੋਰਟ:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>ਮ ੁ ਖ   ਮ ੀ ਨ ੂ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>ਇੱਕ ਵਿਸ਼ੇਸ਼ਤਾ ਚੁਣੋ:</b>",
    "cancelled": "❌ <b>ਰੱਦ ਕੀਤਾ।</b>",
    "choose_language": "🌐 <b>ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ:</b>",
    "language_set": "✅ ਭਾਸ਼ਾ ਅੱਪਡੇਟ ਹੋ ਗਈ।",
    "send_cancel": "💡 <i>ਰੱਦ ਕਰਨ ਲਈ /cancel ਭੇਜੋ।</i>",
    "searching": "🔎 <i>ਖੋਜ ਰਿਹਾ ਹੈ...</i>",
    "no_result": "❌ ਕੋਈ ਨਤੀਜਾ ਨਹੀਂ ਜਾਂ API ਗਲਤੀ।",
    "select_option": "❓ ਮੀਨੂ ਵਿੱਚੋਂ ਚੁਣੋ।",
    "back": "🔙 ਵਾਪਸ",
    "cancel_btn": "❌ ਰੱਦ ਕਰੋ",
    "lang_btn": "🌐 ਭਾਸ਼ਾ",
    "support_btn": "💬 ਸਪੋਰਟ",
    "continue_btn": "✅ ਜਾਰੀ ਰੱਖੋ",
    "maintenance": "🛠 <b>ਰ ੱ ਖ - ਰ ਖ ਾ ਵ   ਜ ਾ ਰ ੀ   ਹ ੈ</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ ਤਹਿ ਕੀਤੀ ਸੰਭਾਲ ਕਾਰਨ\nਬੋਟ ਅਸਥਾਈ ਤੌਰ ਤੇ <b>ਔਫਲਾਈਨ</b> ਹੈ।\n\n🕒 ਕਿਰਪਾ ਕਰਕੇ ਥੋੜ੍ਹੀ ਦੇਰ ਬਾਅਦ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।\n\n💬 <b>ਸਪੋਰਟ:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ਪਹੁੰਚ ਬਲੌਕ</b>\n\nਤੁਹਾਡਾ ਖਾਤਾ ਮੁਅੱਤਲ ਕੀਤਾ ਗਿਆ ਹੈ।",
    "current_lang": "🌐 ਮੌਜੂਦਾ ਭਾਸ਼ਾ: <b>{name}</b>",
})
_register_lang("ml", {
    "welcome": "✨ <b>സ ് വ ാ ഗ ത ം</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>സൗജന്യ OSINT ബോട്ട്</b>\n📓 എല്ലാ ഉപകരണങ്ങളും അൺലോക്ക്\n⚡ വേഗം  •  🔒 സുരക്ഷിതം  •  🎯 വിശ്വസനീയം\n\n💬 <b>പിന്തുണ:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>പ ് ര ധ ా ന   മ െ ന ു</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>ഒരു സവിശേഷത തിരഞ്ഞെടുക്കുക:</b>",
    "cancelled": "❌ <b>റദ്ദാക്കി.</b>",
    "choose_language": "🌐 <b>നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക:</b>",
    "language_set": "✅ ഭാഷ അപ്ഡേറ്റ് ചെയ്തു.",
    "send_cancel": "💡 <i>റദ്ദാക്കാൻ /cancel അയയ്ക്കുക.</i>",
    "searching": "🔎 <i>തിരയുന്നു...</i>",
    "no_result": "❌ ഫലമില്ല അല്ലെങ്കിൽ API പിശക്.",
    "select_option": "❓ മെനുവിൽ നിന്ന് തിരഞ്ഞെടുക്കുക.",
    "back": "🔙 പിന്നോട്ട്",
    "cancel_btn": "❌ റദ്ദാക്കുക",
    "lang_btn": "🌐 ഭാഷ",
    "support_btn": "💬 പിന്തുണ",
    "continue_btn": "✅ തുടരുക",
    "maintenance": "🛠 <b>അ റ ് റ ് ക ു ട ് ത ൽ   ന ട ക ് ക ു ന ് ന ു</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ ഷെഡ്യൂൾ ചെയ്ത അറ്റകുറ്റപ്പണി കാരണം\nബോട്ട് താൽക്കാലികമായി <b>ഓഫ്ലൈൻ</b> ആണ്.\n\n🕒 കുറച്ചു കഴിഞ്ഞ് വീണ്ടും ശ്രമിക്കുക.\n\n💬 <b>പിന്തുണ:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ആക്സസ് ബ്ലോക്ക്</b>\n\nനിങ്ങളുടെ അക്കൗണ്ട് സസ്പെൻഡ് ചെയ്തു.",
    "current_lang": "🌐 നിലവിലെ ഭാഷ: <b>{name}</b>",
})
_register_lang("nl", {
    "welcome": "✨ <b>W E L K O M</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Gratis OSINT Bot</b>\n📓 Alle tools ontgrendeld\n⚡ Snel  •  🔒 Veilig  •  🎯 Betrouwbaar\n\n💬 <b>Ondersteuning:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>H O O F D M E N U</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Selecteer een functie:</b>",
    "cancelled": "❌ <b>Geannuleerd.</b>",
    "choose_language": "🌐 <b>Kies uw taal:</b>",
    "language_set": "✅ Taal succesvol bijgewerkt.",
    "send_cancel": "💡 <i>Stuur /cancel om te annuleren.</i>",
    "searching": "🔎 <i>Zoeken...</i>",
    "no_result": "❌ Geen resultaat of API-fout.",
    "select_option": "❓ Selecteer een optie.",
    "back": "🔙 Terug",
    "cancel_btn": "❌ Annuleren",
    "lang_btn": "🌐 Taal",
    "support_btn": "💬 Ondersteuning",
    "continue_btn": "✅ Doorgaan",
    "maintenance": "🛠 <b>O N D E R H O U D</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ De bot is tijdelijk <b>offline</b>\nvanwege gepland onderhoud.\n\n🕒 Probeer het over een moment opnieuw.\n\n💬 <b>Ondersteuning:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>TOEGANG GEBLOKKEERD</b>\n\nUw account is opgeschort.",
    "current_lang": "🌐 Huidige taal: <b>{name}</b>",
})
_register_lang("pl", {
    "welcome": "✨ <b>W I T A M Y</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Darmowy Bot OSINT</b>\n📓 Wszystkie narzędzia odblokowane\n⚡ Szybko  •  🔒 Bezpiecznie  •  🎯 Niezawodnie\n\n💬 <b>Wsparcie:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>M E N U   G Ł Ó W N E</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Wybierz funkcję:</b>",
    "cancelled": "❌ <b>Anulowano.</b>",
    "choose_language": "🌐 <b>Wybierz swój język:</b>",
    "language_set": "✅ Język zaktualizowany.",
    "send_cancel": "💡 <i>Wyślij /cancel aby anulować.</i>",
    "searching": "🔎 <i>Szukam...</i>",
    "no_result": "❌ Brak wyników lub błąd API.",
    "select_option": "❓ Wybierz opcję z menu.",
    "back": "🔙 Wstecz",
    "cancel_btn": "❌ Anuluj",
    "lang_btn": "🌐 Język",
    "support_btn": "💬 Wsparcie",
    "continue_btn": "✅ Kontynuuj",
    "maintenance": "🛠 <b>K O N S E R W A C J A</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Bot jest tymczasowo <b>offline</b>\nz powodu zaplanowanej konserwacji.\n\n🕒 Spróbuj ponownie za chwilę.\n\n💬 <b>Wsparcie:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>DOSTĘP ZABLOKOWANY</b>\n\nTwoje konto zostało zawieszone.",
    "current_lang": "🌐 Aktualny język: <b>{name}</b>",
})
_register_lang("uk", {
    "welcome": "✨ <b>Л А С К А В О   П Р О С И М О</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Безкоштовний OSINT-бот</b>\n📓 Усі інструменти розблоковано\n⚡ Швидко  •  🔒 Безпечно  •  🎯 Надійно\n\n💬 <b>Підтримка:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>Г О Л О В Н Е   М Е Н Ю</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Оберіть функцію:</b>",
    "cancelled": "❌ <b>Скасовано.</b>",
    "choose_language": "🌐 <b>Оберіть мову:</b>",
    "language_set": "✅ Мову оновлено.",
    "send_cancel": "💡 <i>Надішліть /cancel щоб скасувати.</i>",
    "searching": "🔎 <i>Пошук...</i>",
    "no_result": "❌ Немає результатів або помилка API.",
    "select_option": "❓ Оберіть пункт з меню.",
    "back": "🔙 Назад",
    "cancel_btn": "❌ Скасувати",
    "lang_btn": "🌐 Мова",
    "support_btn": "💬 Підтримка",
    "continue_btn": "✅ Продовжити",
    "maintenance": "🛠 <b>О Б С Л У Г О В У В А Н Н Я</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Бот тимчасово <b>недоступний</b>\nчерез планове обслуговування.\n\n🕒 Спробуйте пізніше.\n\n💬 <b>Підтримка:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ДОСТУП ЗАБЛОКОВАНО</b>\n\nВаш акаунт призупинено.",
    "current_lang": "🌐 Поточна мова: <b>{name}</b>",
})
_register_lang("ro", {
    "welcome": "✨ <b>B I N E   A Ț I   V E N I T</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Bot OSINT Gratuit</b>\n📓 Toate instrumentele deblocate\n⚡ Rapid  •  🔒 Sigur  •  🎯 Fiabil\n\n💬 <b>Suport:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>M E N I U   P R I N C I P A L</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Selectați o funcție:</b>",
    "cancelled": "❌ <b>Anulat.</b>",
    "choose_language": "🌐 <b>Alegeți limba:</b>",
    "language_set": "✅ Limba a fost actualizată.",
    "send_cancel": "💡 <i>Trimiteți /cancel pentru a anula.</i>",
    "searching": "🔎 <i>Căutare...</i>",
    "no_result": "❌ Niciun rezultat sau eroare API.",
    "select_option": "❓ Selectați o opțiune.",
    "back": "🔙 Înapoi",
    "cancel_btn": "❌ Anulează",
    "lang_btn": "🌐 Limbă",
    "support_btn": "💬 Suport",
    "continue_btn": "✅ Continuă",
    "maintenance": "🛠 <b>Î N   M E N T E N A N Ț Ă</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Botul este temporar <b>offline</b>\npentru mentenanță programată.\n\n🕒 Încercați din nou în curând.\n\n💬 <b>Suport:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>ACCES BLOCAT</b>\n\nContul dvs. a fost suspendat.",
    "current_lang": "🌐 Limba curentă: <b>{name}</b>",
})
_register_lang("sw", {
    "welcome": "✨ <b>K A R I B U</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Bot ya OSINT ya Bure</b>\n📓 Zana zote zimefunguliwa\n⚡ Haraka  •  🔒 Salama  •  🎯 Kuaminika\n\n💬 <b>Msaada:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>M E N Y U   K U U</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Chagua kipengele:</b>",
    "cancelled": "❌ <b>Imefutwa.</b>",
    "choose_language": "🌐 <b>Chagua lugha yako:</b>",
    "language_set": "✅ Lugha imesasishwa.",
    "send_cancel": "💡 <i>Tuma /cancel kufuta.</i>",
    "searching": "🔎 <i>Inatafuta...</i>",
    "no_result": "❌ Hakuna matokeo au hitilafu ya API.",
    "select_option": "❓ Chagua chaguo kutoka kwenye menyu.",
    "back": "🔙 Nyuma",
    "cancel_btn": "❌ Ghairi",
    "lang_btn": "🌐 Lugha",
    "support_btn": "💬 Msaada",
    "continue_btn": "✅ Endelea",
    "maintenance": "🛠 <b>M A T E N G E N E Z O</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Bot haipatikani kwa muda\nkwa matengenezo yaliyopangwa.\n\n🕒 Jaribu tena baadaye kidogo.\n\n💬 <b>Msaada:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>Ufikiaji Umezuiwa</b>\n\nAkaunti yako imesimamishwa.",
    "current_lang": "🌐 Lugha ya sasa: <b>{name}</b>",
})
_register_lang("ms", {
    "welcome": "✨ <b>S E L A M A T   D A T A N G</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Bot OSINT Percuma</b>\n📓 Semua alat dibuka\n⚡ Pantas  •  🔒 Selamat  •  🎯 Boleh dipercayai\n\n💬 <b>Sokongan:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>M E N U   U T A M A</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Pilih ciri:</b>",
    "cancelled": "❌ <b>Dibatalkan.</b>",
    "choose_language": "🌐 <b>Pilih bahasa anda:</b>",
    "language_set": "✅ Bahasa dikemas kini.",
    "send_cancel": "💡 <i>Hantar /cancel untuk batal.</i>",
    "searching": "🔎 <i>Mencari...</i>",
    "no_result": "❌ Tiada hasil atau ralat API.",
    "select_option": "❓ Pilih pilihan dari menu.",
    "back": "🔙 Kembali",
    "cancel_btn": "❌ Batal",
    "lang_btn": "🌐 Bahasa",
    "support_btn": "💬 Sokongan",
    "continue_btn": "✅ Teruskan",
    "maintenance": "🛠 <b>P E N Y E L E N G G A R A A N</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Bot tidak tersedia sementara\nuntuk penyelenggaraan berjadual.\n\n🕒 Cuba lagi sebentar lagi.\n\n💬 <b>Sokongan:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>AKSES DIBLOK</b>\n\nAkaun anda telah digantung.",
    "current_lang": "🌐 Bahasa semasa: <b>{name}</b>",
})
_register_lang("fil", {
    "welcome": "✨ <b>M A L I G A Y A N G   P A G D A T I N G</b> ✨\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🤖 <b>Libreng OSINT Bot</b>\n📓 Naka-unlock lahat ng tool\n⚡ Mabilis  •  🔒 Ligtas  •  🎯 Maaasahan\n\n💬 <b>Suporta:</b> @Tony_M_unlock",
    "select_feature": "🛠 <b>P A N G U N A H I N G   M E N U</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n👮 <b>Pumili ng feature:</b>",
    "cancelled": "❌ <b>Kinansela.</b>",
    "choose_language": "🌐 <b>Pumili ng wika:</b>",
    "language_set": "✅ Na-update ang wika.",
    "send_cancel": "💡 <i>Ipadala ang /cancel para kanselahin.</i>",
    "searching": "🔎 <i>Naghahanap...</i>",
    "no_result": "❌ Walang resulta o error sa API.",
    "select_option": "❓ Pumili ng opsyon sa menu.",
    "back": "🔙 Bumalik",
    "cancel_btn": "❌ Kanselahin",
    "lang_btn": "🌐 Wika",
    "support_btn": "💬 Suporta",
    "continue_btn": "✅ Magpatuloy",
    "maintenance": "🛠 <b>P A G P A P A N A T I L I</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n⚠️ Pansamantalang <b>offline</b> ang bot\npara sa naka-iskedyul na pagpapanatili.\n\n🕒 Subukan muli mamaya.\n\n💬 <b>Suporta:</b> @Tony_M_unlock",
    "banned_msg": "🚫 <b>NAKA-BLOCK ANG ACCESS</b>\n\nSinuspinde ang iyong account.",
    "current_lang": "🌐 Kasalukuyang wika: <b>{name}</b>",
})


def t(lang, key, **kwargs):
    """Safe translation lookup — falls back to English, never crashes."""
    pack = TEXTS.get(lang) or TEXTS["en"]
    template = pack.get(key) or TEXTS["en"].get(key) or key
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def detect_lang_from_tg(tg_code):
    if not tg_code:
        return "en"
    return _LANG_ALIASES.get(tg_code.lower(), "en")


def normalize_text(s):
    if not isinstance(s, str):
        return ""
    for ch in ("\ufe0f", "\u200b", "\u200c", "\u200d", "\u00a0", "\u2060"):
        s = s.replace(ch, "")
    return s.strip().lower()


def is_button(text, key, lang):
    target = normalize_text(text)
    if not target:
        return False
    for code in LANGUAGES:
        if normalize_text(TEXTS[code].get(key, "")) == target:
            return True
    return False


def get_lang(cid):
    return USER_LANGS.get(cid, "en")


def set_lang(cid, lang):
    if lang in LANGUAGES:
        USER_LANGS[cid] = lang
        save_langs()


def lang_label(code):
    return LANGUAGES.get(code, LANGUAGES["en"])


# ============================================================
# 9. OSINT FEATURE REGISTRY
# ============================================================
API_CONFIG = {
    "🪪 Aadhaar Info": {
        "url": "https://travelers-creature-sarah-rogers.trycloudflare.com/search?q=",
        "prompt": "🪪 Send a 12-digit Aadhaar number.",
        "command": "/adhr",
    },
    "📞 Number Info": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "📞 Send a 10-digit Indian number (without +91).\nExample: 9712073901",
        "command": "/num",
    },
    "📭 PIN Code": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "📭 Send a PIN code.",
        "command": "/pin",
    },
    "🚘 Vehicle Info": {
        "url": "https://parivahan-x.paskhinpf9.workers.dev/?vehicle=",
        "prompt": "🚘 Send a vehicle number (lowercase).",
        "command": "/vech",
    },
    "🤖 TG Username": {
        "url": "https://anon-tg-info.vercel.app/telegram?key=temp1750&username=",
        "prompt": "🤖 Send a Telegram username.",
        "command": "/tgid",
    },
    "🆔 PAN Info": {
        "url": "https://paninfo.noob73613.workers.dev/pan?pan=",
        "prompt": "🆔 Send a PAN number.",
        "command": "/pan",
    },
    "📱 TG Chat ID": {
        "url": "https://anon-tg-info.vercel.app/tgReg_beta?userid=",
        "prompt": "📱 Send a Telegram Chat ID.",
        "command": "/tgid2",
    },
    "💳 IFSC Info": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "💳 Send an IFSC code.",
        "command": "/ifsc",
    },
    "🏦 UPI Info": {
        "url": "https://upi-id-to-info-by-abhigyan.onrender.com/upi/",
        "prompt": "🏦 Send a UPI ID.",
        "command": "/upi",
    },
    "📧 Email Info": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "📧 Send an email address.",
        "command": "/email",
    },
    "🌐 IP Info": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "🌐 Send an IP address.",
        "command": "/ip",
    },
}

COMMAND_FEATURE_MAP = {v["command"]: k for k, v in API_CONFIG.items() if v.get("command")}


# ============================================================
# 10. TELEGRAM HELPERS
# ============================================================
def send_message(chat_id, text, keyboard=None, parse_mode="HTML"):
    url = f"{USER_TG_API}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if keyboard is not None:
        data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
    try:
        r = HTTP.post(url, data=data, timeout=(TG_CONNECT, TG_READ))
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 400 and "parse" in e.response.text.lower():
            data.pop("parse_mode", None)
            try:
                r = HTTP.post(url, data=data, timeout=(TG_CONNECT, TG_READ))
                r.raise_for_status()
                return r.json()
            except Exception as inner:
                print("Plain retry fail:", inner)
        print("sendMessage err:", e)
        return None
    except Exception as e:
        print("sendMessage err:", e)
        return None


def send_photo(chat_id, photo, caption=None, keyboard=None, parse_mode="HTML"):
    url = f"{USER_TG_API}/sendPhoto"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
    if keyboard is not None:
        data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)

    files = None
    if isinstance(photo, str) and photo in IMAGE_CACHE:
        files = {"photo": (os.path.basename(photo).replace(".png", ".jpg"),
                           IMAGE_CACHE[photo], "image/jpeg")}
    elif isinstance(photo, str) and os.path.exists(photo):
        try:
            with open(photo, "rb") as f:
                mime = "image/png" if photo.lower().endswith(".png") else "image/jpeg"
                files = {"photo": (os.path.basename(photo), f.read(), mime)}
        except Exception as e:
            print(f"read img err {photo}: {e}")
    elif isinstance(photo, str):
        data["photo"] = photo

    if files is None and isinstance(photo, str) and photo.lower().endswith((".png", ".jpg", ".jpeg")):
        return send_message(chat_id, caption or "⚠️", keyboard, parse_mode)

    try:
        r = HTTP.post(url, data=data, files=files, timeout=(TG_CONNECT, 60))
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        if caption:
            send_message(chat_id, caption, keyboard, parse_mode)
        return None
    except requests.exceptions.HTTPError as e:
        print(f"sendPhoto HTTP {e.response.status_code}")
        if caption:
            send_message(chat_id, caption, keyboard, parse_mode)
        return None
    except Exception as e:
        print("sendPhoto err:", e)
        if caption:
            send_message(chat_id, caption, keyboard, parse_mode)
        return None


def get_updates(offset=None):
    url = f"{USER_TG_API}/getUpdates"
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        r = HTTP.get(url, params=params, timeout=(TG_CONNECT, TG_READ))
        if r.status_code == 409:
            return {"ok": False, "conflict": True}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("getUpdates err:", e)
        return None


def answer_callback(cb_id, text=None):
    if not cb_id:
        return
    data = {"callback_query_id": cb_id}
    if text:
        data["text"] = text[:200]
    try:
        HTTP.post(f"{USER_TG_API}/answerCallbackQuery", data=data, timeout=(5, 10))
    except Exception as e:
        print("answerCB err:", e)


def delete_message(chat_id, message_id):
    try:
        HTTP.post(f"{USER_TG_API}/deleteMessage",
                  data={"chat_id": chat_id, "message_id": message_id},
                  timeout=(5, 10))
    except Exception as e:
        print("delMsg err:", e)


def get_chat_member(chat_id, user_id):
    if not chat_id or not user_id:
        return None
    try:
        r = HTTP.get(f"{USER_TG_API}/getChatMember",
                     params={"chat_id": chat_id, "user_id": user_id},
                     timeout=(5, 10))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("getChatMember err:", e)
        return None


def leave_chat(chat_id):
    """Force the user bot to leave a group / supergroup / channel."""
    try:
        HTTP.post(f"{USER_TG_API}/leaveChat",
                  data={"chat_id": chat_id},
                  timeout=(5, 10))
        print(f"👋 User bot left chat {chat_id}")
    except Exception as e:
        print(f"leaveChat err: {e}")


def leave_admin_chat(chat_id):
    """Force the admin bot to leave a group / supergroup / channel."""
    try:
        ADMIN_HTTP.post(f"{ADMIN_TG_APIS[1]}/leaveChat",
                        data={"chat_id": chat_id},
                        timeout=(5, 10))
        print(f"👋 Admin bot left chat {chat_id}")
    except Exception as e:
        print(f"admin leaveChat err: {e}")


# ============================================================
# 11. FORCE-JOIN MEMBERSHIP CHECK
# ============================================================
_BOT_ID_CACHE = {"id": None}


def _is_member_status(resp):
    return bool(resp and resp.get("ok") and resp.get("result", {}).get("status")
                in ("creator", "administrator", "member"))


def _bot_id():
    if _BOT_ID_CACHE["id"]:
        return _BOT_ID_CACHE["id"]
    try:
        r = HTTP.get(f"{USER_TG_API}/getMe", timeout=(5, 10))
        r.raise_for_status()
        _BOT_ID_CACHE["id"] = r.json().get("result", {}).get("id")
    except Exception:
        pass
    return _BOT_ID_CACHE["id"]


def _bot_in_communities():
    if not FORCE_JOIN_ENABLED:
        return True
    bid = _bot_id()
    if not bid:
        return False
    if JOIN_GROUP_CHAT_ID and not _is_member_status(get_chat_member(JOIN_GROUP_CHAT_ID, bid)):
        return False
    if JOIN_CHANNEL_CHAT_ID and not _is_member_status(get_chat_member(JOIN_CHANNEL_CHAT_ID, bid)):
        return False
    return True


def check_user_joined(user_id):
    if not FORCE_JOIN_ENABLED:
        return True, "disabled"
    if not _bot_in_communities():
        return False, "bot_not_member"
    if JOIN_GROUP_CHAT_ID:
        if not _is_member_status(get_chat_member(JOIN_GROUP_CHAT_ID, user_id)):
            return False, "group"
    if JOIN_CHANNEL_CHAT_ID:
        if not _is_member_status(get_chat_member(JOIN_CHANNEL_CHAT_ID, user_id)):
            return False, "channel"
    return True, "ok"


# ============================================================
# 12. USER KEYBOARDS
# ============================================================
def main_keyboard(lang="en"):
    buttons = list(API_CONFIG.keys())
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if len(keyboard[-1]) == 1:
        keyboard[-1].append(t(lang, "cancel_btn"))
    else:
        keyboard.append([t(lang, "cancel_btn")])
    keyboard.append([t(lang, "lang_btn"), t(lang, "support_btn")])
    return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": False}


def language_keyboard(page=0):
    codes = list(LANGUAGES.keys())
    per_page = 20
    total = len(codes)
    start = page * per_page
    chunk = codes[start:start + per_page]

    rows = []
    for i in range(0, len(chunk), 2):
        row = []
        for c in chunk[i:i + 2]:
            row.append({"text": LANGUAGES[c], "callback_data": f"lang:{c}"})
        rows.append(row)

    nav = []
    if page > 0:
        nav.append({"text": "⬅️ Prev", "callback_data": f"langpage:{page - 1}"})
    if start + per_page < total:
        nav.append({"text": "Next ➡️", "callback_data": f"langpage:{page + 1}"})
    if nav:
        rows.append(nav)

    rows.append([{"text": "💬 Contact Support (@Tony_M_unlock)", "url": SUPPORT_URL}])
    return {"inline_keyboard": rows}


def force_join_keyboard(lang="en"):
    return {"inline_keyboard": [
        [{"text": "📢  Join Channel", "url": JOIN_CHANNEL_URL}],
        [{"text": "💬  Join Group",   "url": JOIN_GROUP_URL}],
        [{"text": t(lang, "continue_btn"), "callback_data": "user:continue"}],
        [{"text": "💬 Contact Support (@Tony_M_unlock)", "url": SUPPORT_URL}],
    ]}


# ============================================================
# 13. ADMIN & OWNER KEYBOARDS
# ============================================================
def build_admin_main_keyboard(is_owner=False):
    kb = [
        [{"text": "👥  Users",     "callback_data": "admin:list"},
         {"text": "📊  Stats",     "callback_data": "admin:stats"}],
        [{"text": "🚫  Banned",    "callback_data": "admin:banned"},
         {"text": "🟢  Online",    "callback_data": "admin:online"}],
        [{"text": "📢  Broadcast", "callback_data": "admin:broadcast"},
         {"text": "📜  Logs",      "callback_data": "admin:logs"}],
        [{"text": "🆔  Who Am I",  "callback_data": "admin:whoami"}],
    ]
    if is_owner:
        kb.append([{"text": "👑  Owner Panel", "callback_data": "admin:owner_panel"}])
    return {"inline_keyboard": kb}


def owner_panel_keyboard():
    return {"inline_keyboard": [
        [{"text": "🛡  Admin Management", "callback_data": "owner:admins"},
         {"text": "⚙️  System Settings",  "callback_data": "owner:system"}],
        [{"text": "🗂  Data Files (JSON)", "callback_data": "owner:files"}],
        [{"text": "🔙  Back to Admin",    "callback_data": "admin:back"}],
    ]}


def owner_admins_keyboard():
    return {"inline_keyboard": [
        [{"text": "➕  Add Admin",    "callback_data": "owner:add_admin"}],
        [{"text": "➖  Remove Admin", "callback_data": "owner:remove_admin"}],
        [{"text": "📋  List Admins",  "callback_data": "owner:list_admins"}],
        [{"text": "🔙  Back",         "callback_data": "owner:back"}],
    ]}


def owner_system_keyboard():
    return {"inline_keyboard": [
        [{"text": "🛠  Maintenance ON",  "callback_data": "owner:maintenance_on"},
         {"text": "✅  Maintenance OFF", "callback_data": "owner:maintenance_off"}],
        [{"text": "🔑  Change Password", "callback_data": "owner:change_password"}],
        [{"text": "🔙  Back",            "callback_data": "owner:back"}],
    ]}


# ============================================================
# 14. API CALL HELPER
# ============================================================
def call_api(url, query):
    try:
        r = HTTP.get(url + quote_plus(query), timeout=(API_CONNECT, API_READ))
        r.raise_for_status()
        try:
            return {"ok": True, "data": r.json()}
        except ValueError:
            return {"ok": True, "data": r.text[:3900]}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _html_escape(s):
    return (str(s).replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))


def _format_api_data(data, depth=0):
    if depth > 4:
        return f"<code>{_html_escape(str(data))[:200]}</code>"
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            key_str = _html_escape(str(k).replace("_", " ").title())
            if isinstance(v, (dict, list)) and v:
                lines.append(f"<b>{key_str}</b>\n{_format_api_data(v, depth + 1)}")
            else:
                lines.append(f"<b>{key_str}:</b> <code>{_html_escape(str(v))}</code>")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for i, item in enumerate(data[:20]):
            if isinstance(item, (dict, list)):
                lines.append(f"<b>▪ Item {i + 1}</b>\n{_format_api_data(item, depth + 1)}")
            else:
                lines.append(f"• <code>{_html_escape(str(item))}</code>")
        if len(data) > 20:
            lines.append(f"<i>…and {len(data) - 20} more items</i>")
        return "\n".join(lines)
    return f"<code>{_html_escape(str(data))}</code>"


def _send_long(chat_id, text, keyboard=None):
    max_len = 3900
    if len(text) <= max_len:
        send_message(chat_id, text, keyboard)
        return
    for i in range(0, len(text), max_len):
        send_message(chat_id, text[i:i + max_len])
    if keyboard is not None:
        send_message(chat_id, "✅ Finished.", keyboard)


# ============================================================
# 15. MAINTENANCE HELPER
# ============================================================
def _send_maintenance(chat_id, lang):
    img = IMAGES.get("maintenance")
    if img and (img in IMAGE_CACHE or os.path.exists(img)):
        send_photo(chat_id, img, caption=t(lang, "maintenance"))
    else:
        send_message(chat_id, t(lang, "maintenance"))


# ============================================================
# 16. USER CALLBACK HANDLER
# ============================================================
def process_callback(cb):
    data = cb.get("data", "") or ""
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    msg_id = msg.get("message_id")
    cb_id = cb.get("id")
    if chat_id is None:
        return
    # ── HARD BLOCK: only private chats ──
    if chat.get("type") != "private":
        return
    lang = get_lang(chat_id)

    if is_banned(chat_id):
        answer_callback(cb_id, "🚫 Banned")
        return

    if MAINTENANCE_MODE and get_role(chat_id) == "user":
        answer_callback(cb_id)
        _send_maintenance(chat_id, lang)
        return

    if data.startswith("langpage:"):
        try:
            page = int(data.split(":", 1)[1])
        except Exception:
            page = 0
        answer_callback(cb_id)
        if msg_id:
            delete_message(chat_id, msg_id)
        send_message(chat_id, t(lang, "choose_language"), language_keyboard(page))
        return

    if data.startswith("lang:"):
        code = data.split(":", 1)[1]
        if code in LANGUAGES:
            set_lang(chat_id, code)
            answer_callback(cb_id, t(code, "language_set"))
            if msg_id:
                delete_message(chat_id, msg_id)

            if FORCE_JOIN_ENABLED and get_role(chat_id) == "user":
                ok, reason = check_user_joined(chat_id)
                if not ok:
                    if reason == "bot_not_member":
                        _ask_bot_setup(chat_id)
                    else:
                        _ask_for_join(chat_id, code)
                    return

            _send_feature_menu(chat_id, code)
        return

    if data == "user:continue":
        ok, reason = check_user_joined(chat_id)
        if not ok:
            if reason == "bot_not_member":
                answer_callback(cb_id, "Bot setup incomplete")
                _ask_bot_setup(chat_id)
                return
            answer_callback(cb_id, t(lang, "join_fail"))
            send_photo(chat_id, IMAGES["join"],
                       caption=t(lang, "join_required"),
                       keyboard=force_join_keyboard(lang))
            return

        answer_callback(cb_id, t(lang, "join_ok"))
        if msg_id:
            delete_message(chat_id, msg_id)
        _send_feature_menu(chat_id, lang)
        return

    if data == "user:cancel":
        answer_callback(cb_id)
        USER_STATE.pop(chat_id, None)
        if msg_id:
            delete_message(chat_id, msg_id)
        _send_feature_menu(chat_id, lang)
        return

    if data == "user:lang":
        answer_callback(cb_id)
        current = lang_label(lang)
        send_message(chat_id,
                     t(lang, "current_lang", name=current) + "\n\n" + t(lang, "choose_language"),
                     language_keyboard(0))
        return

    if data == "user:support":
        answer_callback(cb_id)
        send_message(chat_id, t(lang, "support"))
        return


# ============================================================
# 17. USER MESSAGE HANDLER
# ============================================================
def _send_feature_menu(chat_id, lang):
    send_photo(chat_id, IMAGES["osint"],
               caption=t(lang, "select_feature"),
               keyboard=main_keyboard(lang))


def _ask_for_join(chat_id, lang):
    send_photo(chat_id, IMAGES["join"],
               caption=t(lang, "join_required"),
               keyboard=force_join_keyboard(lang))


def _ask_bot_setup(chat_id):
    send_message(
        chat_id,
        "⚠️ <b>Bot Setup Required</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "The bot is <b>not yet a member</b> of the channel / group.\n\n"
        "👉 Please add the bot to both, then send /start again.\n\n"
        "💬 <b>Support:</b> @Tony_M_unlock",
    )


def process_update(update):
    # ── Auto-leave if user bot was added to a group / channel ──
    if "my_chat_member" in update:
        mcm = update["my_chat_member"]
        chat = mcm.get("chat") or {}
        if chat.get("type") != "private":
            new_status = (mcm.get("new_chat_member") or {}).get("status")
            if new_status in ("member", "administrator"):
                cid = chat.get("id")
                try:
                    send_message(
                        cid,
                        "⚠️ This bot only works in <b>private chats</b>.\n"
                        "Leaving this chat now — please DM me instead. 💬",
                    )
                except Exception:
                    pass
                leave_chat(cid)
        return

    if "callback_query" in update:
        process_callback(update["callback_query"])
        return
    if "message" not in update:
        return
    msg = update["message"]
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    # ── HARD BLOCK: only private chats ──
    if chat.get("type") != "private":
        return

    user_id = msg.get("from", {}).get("id", chat_id)

    LAST_SEEN[chat_id] = time.time()

    text = msg.get("text", "")
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return
    lang = get_lang(chat_id)

    if is_banned(chat_id):
        if text == "/start":
            send_message(chat_id, t(lang, "banned_msg"))
        return

    if MAINTENANCE_MODE and get_role(chat_id) == "user":
        _send_maintenance(chat_id, lang)
        return

    if text == "/start":
        USER_STATE.pop(chat_id, None)

        if chat_id not in USER_LANGS:
            tg_code = (msg.get("from") or {}).get("language_code", "en")
            detected = detect_lang_from_tg(tg_code)
            USER_LANGS[chat_id] = detected
            save_langs()

            caption = (t(detected, "welcome") + "\n\n"
                       "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       + t(detected, "choose_language"))
            send_photo(chat_id, IMAGES["welcome"], caption=caption,
                       keyboard=language_keyboard(0))
            return

        lang = get_lang(chat_id)
        if FORCE_JOIN_ENABLED and get_role(chat_id) == "user":
            ok, reason = check_user_joined(user_id)
            if not ok:
                if reason == "bot_not_member":
                    _ask_bot_setup(chat_id)
                else:
                    _ask_for_join(chat_id, lang)
                return

        _send_feature_menu(chat_id, lang)
        return

    if text in ("/support", "/help_support") or is_button(text, "support_btn", lang):
        send_message(chat_id, t(lang, "support"))
        return

    if text in ("/lang", "/language") or is_button(text, "lang_btn", lang):
        current = lang_label(lang)
        send_message(chat_id,
                     t(lang, "current_lang", name=current) + "\n\n" + t(lang, "choose_language"),
                     language_keyboard(0))
        return

    if FORCE_JOIN_ENABLED and get_role(chat_id) == "user":
        ok, reason = check_user_joined(user_id)
        if not ok:
            USER_STATE.pop(chat_id, None)
            if reason == "bot_not_member":
                _ask_bot_setup(chat_id)
            else:
                _ask_for_join(chat_id, lang)
            return

    if text == "/cancel" or is_button(text, "cancel_btn", lang):
        USER_STATE.pop(chat_id, None)
        _send_feature_menu(chat_id, lang)
        return

    state = USER_STATE.get(chat_id, {})
    if state.get("flow") == "api_query":
        api_name = state.get("api_name")
        cfg = API_CONFIG.get(api_name)
        if not cfg:
            USER_STATE.pop(chat_id, None)
            send_message(chat_id, t(lang, "select_option"), main_keyboard(lang))
            return
        send_message(chat_id, t(lang, "searching"))
        res = call_api(cfg["url"], text)
        if res["ok"]:
            body = res["data"]
            formatted = _format_api_data(body)
            _send_long(chat_id, formatted, main_keyboard(lang))
        else:
            send_message(chat_id, t(lang, "no_result"), main_keyboard(lang))
        USER_STATE.pop(chat_id, None)
        return

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        feature = COMMAND_FEATURE_MAP.get(cmd)
        if feature:
            query = parts[1].strip() if len(parts) > 1 else ""
            cfg = API_CONFIG[feature]
            if not query:
                send_message(chat_id, f"{cfg['prompt']}\n\n{t(lang, 'send_cancel')}",
                             main_keyboard(lang))
                return
            send_message(chat_id, t(lang, "searching"))
            res = call_api(cfg["url"], query)
            if res["ok"]:
                body = res["data"]
                formatted = _format_api_data(body)
                _send_long(chat_id, formatted, main_keyboard(lang))
            else:
                send_message(chat_id, t(lang, "no_result"), main_keyboard(lang))
            return

    api_name = next((name for name in API_CONFIG if name.strip() == text.strip()), None)
    if api_name is not None:
        cfg = API_CONFIG[api_name]
        USER_STATE[chat_id] = {"flow": "api_query", "api_name": api_name}
        send_message(chat_id, cfg["prompt"] + "\n\n" + t(lang, "send_cancel"),
                     main_keyboard(lang))
        return

    send_message(chat_id, t(lang, "select_option"), main_keyboard(lang))


# ============================================================
# 18. ADMIN BOT HELPERS
# ============================================================
def admin_send_message(bot_number, chat_id, text, keyboard=None):
    api = ADMIN_TG_APIS.get(bot_number)
    if not api:
        return None
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
    try:
        r = ADMIN_HTTP.post(f"{api}/sendMessage", data=data, timeout=(5, 35))
        return r.json()
    except Exception as e:
        print(f"adminSend err: {e}")
        return None


def admin_get_updates(bot_number, offset=None):
    api = ADMIN_TG_APIS.get(bot_number)
    if not api:
        return None
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        r = ADMIN_HTTP.get(f"{api}/getUpdates", params=params, timeout=(5, 35))
        if r.status_code == 409:
            return {"ok": False, "conflict": True}
        if r.status_code != 200:
            print(f"admin getUpdates HTTP {r.status_code}: {r.text[:200]}")
            return None
        return r.json()
    except Exception as e:
        print(f"admin getUpdates err: {e}")
        return None


def admin_answer_callback(bot_number, cb_id, text=None):
    api = ADMIN_TG_APIS.get(bot_number)
    if not api or not cb_id:
        return
    data = {"callback_query_id": cb_id}
    if text:
        data["text"] = text[:200]
    try:
        ADMIN_HTTP.post(f"{api}/answerCallbackQuery", data=data, timeout=(5, 10))
    except Exception as e:
        print(f"answerCB err: {e}")


# ============================================================
# 19. ADMIN CALLBACK ROUTER
# ============================================================
def _fmt_user_line(uid):
    lang = USER_LANGS.get(uid, "en")
    ban = "🚫" if uid in BANNED_USERS else ""
    seen = LAST_SEEN.get(uid)
    seen_str = time.strftime("%m-%d %H:%M", time.localtime(seen)) if seen else "—"
    return f"🆔{ban} <code>{uid}</code> · {lang} · seen {seen_str}"


def _send_admin_panel(bot_number, chat_id, title=None):
    admin_send_message(
        bot_number, chat_id,
        title or ("🛠 <b>A D M I N   P A N E L</b>\n"
                  "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                  "👮 Choose an action below:"),
        build_admin_main_keyboard(is_owner(chat_id)),
    )


def process_admin_callback(bot_number, cb):
    global MAINTENANCE_MODE, CURRENT_PASSWORD
    data = cb.get("data", "") or ""
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    cb_id = cb.get("id")
    if chat_id is None:
        return
    if not is_admin(chat_id):
        admin_answer_callback(bot_number, cb_id, "⛔ Unauthorized")
        return

    if data == "admin:back":
        admin_answer_callback(bot_number, cb_id)
        _send_admin_panel(bot_number, chat_id)
        return

    if data == "admin:owner_panel":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(
            bot_number, chat_id,
            "👑 <b>O W N E R   P A N E L</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Sensitive controls for the bot owner.",
            owner_panel_keyboard(),
        )
        return

    if data == "owner:admins":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(
            bot_number, chat_id,
            "🛡 <b>A D M I N   M A N A G E M E N T</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            owner_admins_keyboard(),
        )
        return

    if data == "owner:list_admins":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        lines = ["👑 <b>ADMIN LIST</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for aid in sorted(DYNAMIC_ADMINS):
            lines.append(f"• <code>{aid}</code> — {role_badge(aid)}")
        admin_send_message(bot_number, chat_id, "\n".join(lines), owner_admins_keyboard())
        return

    if data == "owner:add_admin":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_add_admin"
        admin_send_message(
            bot_number, chat_id,
            "➕ <b>ADD ADMIN</b>\n\nSend the Telegram User ID to promote.\n\n/cancel to abort.",
            owner_admins_keyboard(),
        )
        return

    if data == "owner:remove_admin":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_remove_admin"
        admin_send_message(
            bot_number, chat_id,
            "➖ <b>REMOVE ADMIN</b>\n\nSend the Telegram User ID to demote.\n\n/cancel to abort.",
            owner_admins_keyboard(),
        )
        return

    if data == "owner:system":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(
            bot_number, chat_id,
            "⚙️ <b>S Y S T E M   S E T T I N G S</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>",
            owner_system_keyboard(),
        )
        return

    if data == "owner:maintenance_on":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        MAINTENANCE_MODE = True
        log_admin(chat_id, "maintenance ON")
        admin_answer_callback(bot_number, cb_id, "🛠 ON")
        admin_send_message(bot_number, chat_id, "🛠 Maintenance mode <b>ON</b>.",
                           owner_system_keyboard())
        return

    if data == "owner:maintenance_off":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        MAINTENANCE_MODE = False
        log_admin(chat_id, "maintenance OFF")
        admin_answer_callback(bot_number, cb_id, "✅ OFF")
        admin_send_message(bot_number, chat_id, "✅ Maintenance mode <b>OFF</b>.",
                           owner_system_keyboard())
        return

    if data == "owner:change_password":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_new_password"
        admin_send_message(
            bot_number, chat_id,
            "🔑 <b>CHANGE PASSWORD</b>\n\nSend the new admin password.\n/cancel to abort.",
            owner_system_keyboard(),
        )
        return

    # ══════════ OWNER FILE MANAGER CALLBACKS ══════════
    if data == "owner:files":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(
            bot_number, chat_id,
            "🗂 <b>D A T A   F I L E S</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👁 Tap a file to preview\n"
            "📥 Tap the icon to download\n"
            "📦 Or download all as ZIP\n\n"
            "⚠️ <i>Owner-only. Contains sensitive data.</i>",
            owner_files_keyboard(),
        )
        return

    if data.startswith("owner:view:"):
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        fname = data.split(":", 2)[2]
        _owner_send_file_content(bot_number, chat_id, fname)
        return

    if data.startswith("owner:download:"):
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        fname = data.split(":", 2)[2]
        _owner_download_file(bot_number, chat_id, fname)
        return

    if data == "owner:download_all":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        _owner_download_all(bot_number, chat_id)
        return
    # ═══════════════════════════════════════════════════════

    if data == "owner:back":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(
            bot_number, chat_id,
            "👑 <b>O W N E R   P A N E L</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Sensitive controls for the bot owner.",
            owner_panel_keyboard(),
        )
        return

    if data == "admin:list":
        admin_answer_callback(bot_number, cb_id)
        if not LAST_SEEN:
            admin_send_message(bot_number, chat_id, "📭 No users yet.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        lines = ["👥 <b>USERS</b> (recent first)\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        items = sorted(LAST_SEEN.items(), key=lambda x: x[1], reverse=True)[:40]
        for uid, _ in items:
            lines.append(_fmt_user_line(uid))
        if len(LAST_SEEN) > 40:
            lines.append(f"\n…and {len(LAST_SEEN) - 40} more (use /export).")
        admin_send_message(bot_number, chat_id, "\n".join(lines),
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:banned":
        admin_answer_callback(bot_number, cb_id)
        if not BANNED_USERS:
            admin_send_message(bot_number, chat_id, "✅ No banned users.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        lines = ["🚫 <b>BANNED USERS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for uid, info in sorted(BANNED_USERS.items()):
            reason = info.get("reason", "—")
            lines.append(f"• <code>{uid}</code> — {reason}")
        lines.append("\nUnban: <code>/unban USER_ID</code>")
        admin_send_message(bot_number, chat_id, "\n".join(lines),
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:online":
        admin_answer_callback(bot_number, cb_id)
        now = time.time()
        recent = [(u, ts) for u, ts in LAST_SEEN.items() if now - ts < 86400]
        recent.sort(key=lambda x: x[1], reverse=True)
        if not recent:
            admin_send_message(bot_number, chat_id, "💤 No activity in last 24h.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        lines = ["🟢 <b>ACTIVE (last 24h)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for uid, ts in recent[:40]:
            ago = int(now - ts)
            rel = f"{ago // 60}m ago" if ago < 3600 else f"{ago // 3600}h ago"
            lines.append(f"• <code>{uid}</code> — {rel}")
        admin_send_message(bot_number, chat_id, "\n".join(lines),
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:logs":
        admin_answer_callback(bot_number, cb_id)
        if not ADMIN_LOG:
            admin_send_message(bot_number, chat_id, "📭 No admin logs yet.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        lines = ["📜 <b>ADMIN LOG</b> (latest 40)\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for e in ADMIN_LOG[-40:][::-1]:
            ts = time.strftime("%m-%d %H:%M", time.localtime(e["ts"]))
            lines.append(f"<code>{ts}</code> · <code>{e['by']}</code> → "
                         f"{e['action']} <code>{e.get('target','')}</code>")
        admin_send_message(bot_number, chat_id, "\n".join(lines),
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:stats":
        admin_answer_callback(bot_number, cb_id)
        now = time.time()
        total = len(LAST_SEEN)
        seen24 = sum(1 for ts in LAST_SEEN.values() if now - ts < 86400)
        admin_send_message(bot_number, chat_id,
                           f"📊 <b>BOT STATISTICS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                           f"👥 Total users: <b>{total}</b>\n"
                           f"🟢 Seen (24h): <b>{seen24}</b>\n"
                           f"🚫 Banned: <b>{len(BANNED_USERS)}</b>\n"
                           f"🛡 Admins: <b>{len(DYNAMIC_ADMINS)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>\n"
                           f"🆓 Mode: <b>FREE</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━━━━━━",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:whoami":
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(bot_number, chat_id,
                           f"🆔 <code>{chat_id}</code>\n"
                           f"🏷 Role: <b>{role_badge(chat_id)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>\n"
                           f"🆓 Mode: <b>FREE</b>",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:broadcast":
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_broadcast"
        admin_send_message(bot_number, chat_id,
                           "📢 <b>BROADCAST</b>\n\nSend the message to broadcast "
                           "to all users. /cancel to abort.",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    admin_answer_callback(bot_number, cb_id, "Unknown action")


# ============================================================
# 20. ADMIN COMMAND HANDLER
# ============================================================
def process_admin_command(bot_number, chat_id, text, message):
    global CURRENT_PASSWORD, MAINTENANCE_MODE
    args = text.split()
    cmd = args[0].lower() if args else ""

    if cmd in ("/start", "/help"):
        admin_send_message(bot_number, chat_id,
                           "🛠 <b>A D M I N   P A N E L</b>\n"
                           "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "<b>Moderation</b>\n"
                           "/ban ID [reason] · /unban ID · /banned\n"
                           "/msg ID TEXT\n\n"
                           "<b>Notes</b>\n"
                           "/note ID TEXT · /notes ID · /delnote ID N\n\n"
                           "<b>Reports</b>\n"
                           "/list · /stats · /online · /logs · /export\n\n"
                           "<b>Owner only</b>\n"
                           "/owner — Open Owner Panel\n"
                           "/files — View / download all JSON data\n"
                           "/add_admin ID · /remove_admin ID\n"
                           "/setpassword NEW · /maintenance on|off\n\n"
                           "/whoami · /logout",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/owner":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        admin_send_message(bot_number, chat_id,
                           "👑 <b>O W N E R   P A N E L</b>\n"
                           "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "Sensitive controls for the bot owner.",
                           owner_panel_keyboard())
        return

    if cmd == "/files":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        admin_send_message(
            bot_number, chat_id,
            "🗂 <b>D A T A   F I L E S</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👁 Preview any JSON below,\n"
            "📥 or download the raw file.",
            owner_files_keyboard(),
        )
        return

    if cmd == "/whoami":
        admin_send_message(bot_number, chat_id,
                           f"🆔 <code>{chat_id}</code>\n"
                           f"🏷 Role: <b>{role_badge(chat_id)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>\n"
                           f"🆓 Mode: <b>FREE</b>",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/logout":
        if is_owner(chat_id):
            admin_send_message(bot_number, chat_id,
                               "❌ Owner can't logout.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        DYNAMIC_ADMINS.discard(chat_id); save_admins()
        log_admin(chat_id, "logout")
        admin_send_message(bot_number, chat_id,
                           "👋 Logged out. Send /login &lt;password&gt; to log back in.")
        return

    if cmd == "/setpassword":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        if len(args) < 2:
            admin_send_message(bot_number, chat_id,
                               "Usage: <code>/setpassword NEW_PASSWORD</code>",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        CURRENT_PASSWORD = args[1]; save_password()
        log_admin(chat_id, "setpassword")
        admin_send_message(bot_number, chat_id, "✅ <b>Password updated.</b>",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/maintenance":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            admin_send_message(bot_number, chat_id,
                               "Usage: <code>/maintenance on|off</code>",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        MAINTENANCE_MODE = (args[1].lower() == "on")
        log_admin(chat_id, f"maintenance {args[1].lower()}")
        admin_send_message(bot_number, chat_id,
                           f"🛠 Maintenance <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>.",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/add_admin":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /add_admin ID",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        DYNAMIC_ADMINS.add(target); save_admins()
        log_admin(chat_id, "add_admin", target)
        admin_send_message(bot_number, chat_id,
                           f"✅ <code>{target}</code> is now Admin.",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/remove_admin":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /remove_admin ID",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        if is_owner(target):
            admin_send_message(bot_number, chat_id, "❌ Cannot remove owner.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        DYNAMIC_ADMINS.discard(target); save_admins()
        log_admin(chat_id, "remove_admin", target)
        admin_send_message(bot_number, chat_id,
                           f"✅ <code>{target}</code> removed.",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    passthrough = {
        "/list": "admin:list", "/stats": "admin:stats",
        "/online": "admin:online", "/logs": "admin:logs",
        "/banned": "admin:banned", "/broadcast": "admin:broadcast",
    }
    if cmd in passthrough:
        process_admin_callback(bot_number,
                               {"data": passthrough[cmd], "message": message})
        return

    if cmd == "/export":
        try:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["user_id", "lang", "banned", "last_seen", "notes"])
            for uid in sorted(LAST_SEEN.keys()):
                lang = USER_LANGS.get(uid, "en")
                banned = "yes" if uid in BANNED_USERS else "no"
                seen = LAST_SEEN.get(uid)
                seen_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(seen)) if seen else ""
                notes = " | ".join(n["text"] for n in USER_NOTES.get(uid, []))
                w.writerow([uid, lang, banned, seen_str, notes])
            content = buf.getvalue()
            tmp_path = "/tmp/export_users.csv"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            url = f"{ADMIN_TG_APIS[bot_number]}/sendDocument"
            with open(tmp_path, "rb") as f:
                ADMIN_HTTP.post(url,
                                data={"chat_id": chat_id,
                                      "caption": f"📁 Export · {len(LAST_SEEN)} users"},
                                files={"document": ("users.csv", f, "text/csv")},
                                timeout=(10, 60))
            log_admin(chat_id, "export")
        except Exception as e:
            admin_send_message(bot_number, chat_id, f"❌ Export failed: {e}",
                               build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd in ("/check", "/role"):
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, f"Usage: {cmd} USER_ID",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        lang = USER_LANGS.get(target, "en")
        ban_info = BANNED_USERS.get(target)
        seen = LAST_SEEN.get(target)
        ban_line = (f"🚫 Banned — {ban_info.get('reason','—')}"
                    if ban_info else "🟢 Not banned")
        seen_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(seen)) if seen else "—"
        notes = USER_NOTES.get(target, [])
        note_block = ""
        if notes:
            note_block = "\n\n📝 <b>NOTES</b>\n" + "\n".join(
                f"{i + 1}. {n['text']}" for i, n in enumerate(notes[-5:]))
        admin_send_message(bot_number, chat_id,
                           f"👤 <b>USER INFO</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                           f"🆔 <code>{target}</code>\n"
                           f"🏷 Role: <b>{role_badge(target)}</b>\n"
                           f"🌐 Lang: {lang}\n"
                           f"🆓 Access: <b>FREE</b>\n"
                           f"{ban_line}\n"
                           f"👀 Last seen: {seen_str}"
                           f"{note_block}\n━━━━━━━━━━━━━━━━━━━━━━━━━",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/ban":
        if len(args) < 2:
            admin_send_message(bot_number, chat_id, "Usage: /ban ID [reason]",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        if is_admin(target):
            admin_send_message(bot_number, chat_id, "❌ Cannot ban an admin.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        reason = " ".join(args[2:]) or "—"
        BANNED_USERS[target] = {"reason": reason, "ts": int(time.time()), "by": chat_id}
        save_banned()
        log_admin(chat_id, "ban", f"{target} ({reason})")
        send_message(target, t(get_lang(target), "banned_msg"))
        admin_send_message(bot_number, chat_id,
                           f"🚫 <code>{target}</code> banned. Reason: {reason}",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/unban":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /unban ID",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        if target in BANNED_USERS:
            del BANNED_USERS[target]; save_banned()
            log_admin(chat_id, "unban", target)
            admin_send_message(bot_number, chat_id,
                               f"✅ <code>{target}</code> unbanned.",
                               build_admin_main_keyboard(is_owner(chat_id)))
        else:
            admin_send_message(bot_number, chat_id, "❌ Not banned.",
                               build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/msg":
        if len(args) < 3:
            admin_send_message(bot_number, chat_id, "Usage: /msg ID TEXT",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        body = " ".join(args[2:])
        res = send_message(target, f"📩 <b>Message from admin</b>\n\n{body}")
        if res and res.get("ok"):
            log_admin(chat_id, "msg", target)
            admin_send_message(bot_number, chat_id, "✅ Sent.",
                               build_admin_main_keyboard(is_owner(chat_id)))
        else:
            admin_send_message(bot_number, chat_id,
                               "❌ Failed (user may have blocked bot).",
                               build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/note":
        if len(args) < 3:
            admin_send_message(bot_number, chat_id, "Usage: /note ID TEXT",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        USER_NOTES.setdefault(target, []).append(
            {"text": " ".join(args[2:]), "ts": int(time.time()), "by": chat_id})
        save_notes()
        log_admin(chat_id, "note", target)
        admin_send_message(bot_number, chat_id, "📝 Note added.",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/notes":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /notes ID",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        notes = USER_NOTES.get(target, [])
        if not notes:
            admin_send_message(bot_number, chat_id, "📭 No notes.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        lines = [f"📝 <b>NOTES — <code>{target}</code></b>\n━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, n in enumerate(notes):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(n["ts"]))
            lines.append(f"{i + 1}. {n['text']}\n   <i>{ts} · by {n['by']}</i>")
        admin_send_message(bot_number, chat_id, "\n".join(lines),
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/delnote":
        if len(args) != 3:
            admin_send_message(bot_number, chat_id, "Usage: /delnote ID N",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        try:
            target = int(args[1]); idx = int(args[2]) - 1
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid.",
                               build_admin_main_keyboard(is_owner(chat_id))); return
        notes = USER_NOTES.get(target, [])
        if 0 <= idx < len(notes):
            notes.pop(idx); save_notes()
            log_admin(chat_id, "delnote", f"{target} #{idx + 1}")
            admin_send_message(bot_number, chat_id, "🗑 Deleted.",
                               build_admin_main_keyboard(is_owner(chat_id)))
        else:
            admin_send_message(bot_number, chat_id, "❌ Note index out of range.",
                               build_admin_main_keyboard(is_owner(chat_id)))
        return

    admin_send_message(bot_number, chat_id,
                       "❓ Unknown command. Use /help.",
                       build_admin_main_keyboard(is_owner(chat_id)))


# ============================================================
# 21. ADMIN UPDATE ROUTER
# ============================================================
def process_admin_update(bot_number, update):
    global CURRENT_PASSWORD

    # ── Auto-leave if admin bot added to a group / channel ──
    if "my_chat_member" in update:
        mcm = update["my_chat_member"]
        chat = mcm.get("chat") or {}
        if chat.get("type") != "private":
            new_status = (mcm.get("new_chat_member") or {}).get("status")
            if new_status in ("member", "administrator"):
                leave_admin_chat(chat.get("id"))
        return

    # ── HARD BLOCK: only private chats ──
    msg_for_type = (update.get("message") or
                    (update.get("callback_query") or {}).get("message") or {})
    chat_type = (msg_for_type.get("chat") or {}).get("type")
    if chat_type and chat_type != "private":
        return

    if "callback_query" in update:
        process_admin_callback(bot_number, update["callback_query"])
        return
    if "message" not in update:
        return
    message = update["message"]
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return
    text_raw = message.get("text", "")
    text = text_raw.strip() if isinstance(text_raw, str) else ""

    if not is_admin(chat_id):
        if text in ("/start", "/help", "/whoami", "/id"):
            admin_send_message(bot_number, chat_id,
                               f"🔐 <b>Admin Bot</b>\n\n"
                               f"🆔 Your Chat ID: <code>{chat_id}</code>\n\n"
                               f"Send <code>/login YOUR_ADMIN_PASSWORD</code>\n\n"
                               f"💡 Owner: set OWNER_ID=<code>{chat_id}</code> in env "
                               f"to auto-login.")
            return
        if text.startswith("/login"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                admin_send_message(bot_number, chat_id,
                                   "Usage: <code>/login YOUR_PASSWORD</code>")
                return
            supplied = parts[1].strip()
            if supplied == CURRENT_PASSWORD or supplied in (ADMIN_BOT_TOKEN, USER_BOT_TOKEN):
                DYNAMIC_ADMINS.add(chat_id); save_admins()
                log_admin(chat_id, "login")
                admin_send_message(bot_number, chat_id,
                                   f"✅ <b>Login OK.</b>\n🏷 Role: <b>{role_badge(chat_id)}</b>",
                                   build_admin_main_keyboard(is_owner(chat_id)))
            else:
                admin_send_message(bot_number, chat_id, "❌ Incorrect password.")
            return
        if text and text == CURRENT_PASSWORD:
            DYNAMIC_ADMINS.add(chat_id); save_admins()
            log_admin(chat_id, "login (bare)")
            admin_send_message(bot_number, chat_id,
                               f"✅ <b>Login OK.</b>\n🏷 Role: <b>{role_badge(chat_id)}</b>",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        admin_send_message(bot_number, chat_id,
                           f"⛔ Not authorized.\n🆔 Your ID: <code>{chat_id}</code>\n\n"
                           f"Send <code>/login YOUR_PASSWORD</code>.")
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_new_password":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.",
                               owner_system_keyboard())
            return
        if not is_owner(chat_id):
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        CURRENT_PASSWORD = text
        save_password()
        ADMIN_STATE.pop(chat_id, None)
        log_admin(chat_id, "setpassword (via panel)")
        admin_send_message(bot_number, chat_id,
                           "✅ <b>Password updated successfully.</b>",
                           owner_system_keyboard())
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_add_admin":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.",
                               owner_admins_keyboard())
            return
        if not is_owner(chat_id):
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            new_admin = int(text)
            DYNAMIC_ADMINS.add(new_admin); save_admins()
            ADMIN_STATE.pop(chat_id, None)
            log_admin(chat_id, "add_admin", new_admin)
            admin_send_message(bot_number, chat_id,
                               f"✅ <code>{new_admin}</code> is now Admin.",
                               owner_admins_keyboard())
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               owner_admins_keyboard())
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_remove_admin":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.",
                               owner_admins_keyboard())
            return
        if not is_owner(chat_id):
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "⛔ Owner only.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            target = int(text)
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               owner_admins_keyboard()); return
        if is_owner(target):
            admin_send_message(bot_number, chat_id, "❌ Cannot remove owner.",
                               owner_admins_keyboard()); return
        if target in DYNAMIC_ADMINS:
            DYNAMIC_ADMINS.discard(target); save_admins()
            ADMIN_STATE.pop(chat_id, None)
            log_admin(chat_id, "remove_admin", target)
            admin_send_message(bot_number, chat_id,
                               f"✅ <code>{target}</code> removed.",
                               owner_admins_keyboard())
        else:
            admin_send_message(bot_number, chat_id, "❌ Not an admin.",
                               owner_admins_keyboard())
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_broadcast":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        ADMIN_STATE.pop(chat_id, None)
        admin_send_message(bot_number, chat_id, "⏳ Broadcasting…")
        success = failed = 0
        for uid in list(LAST_SEEN.keys()):
            if is_banned(uid):
                continue
            try:
                res = send_message(uid, f"📢 <b>ANNOUNCEMENT</b>\n\n{text}")
                if res and res.get("ok"):
                    success += 1
                else:
                    failed += 1
                time.sleep(0.05)
            except Exception:
                failed += 1
        log_admin(chat_id, "broadcast", f"ok={success} fail={failed}")
        admin_send_message(bot_number, chat_id,
                           f"✅ Broadcast done.\nSent: {success} · Failed: {failed}",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    process_admin_command(bot_number, chat_id, text, message)


def admin_bot_loop(bot_number):
    print(f"🚀 Admin bot {bot_number} polling started.")
    offset, backoff = None, 1
    while True:
        try:
            result = admin_get_updates(bot_number, offset)
            if result and result.get("ok"):
                backoff = 1
                for upd in result.get("result", []):
                    offset = upd.get("update_id", 0) + 1
                    try:
                        process_admin_update(bot_number, upd)
                    except Exception as ue:
                        print(f"admin update err: {ue}")
                        traceback.print_exc()
            elif result and result.get("conflict"):
                time.sleep(5)
            else:
                time.sleep(min(backoff, 10))
                backoff = min(backoff * 2, 30)
        except Exception as e:
            print(f"admin loop err: {e}")
            time.sleep(min(backoff, 10))
            backoff = min(backoff * 2, 30)


# ============================================================
# 22. USER BOT POLLING
# ============================================================
def clear_webhook(api_base, label):
    try:
        r = HTTP.get(f"{api_base}/deleteWebhook",
                     params={"drop_pending_updates": "true"},
                     timeout=(5, 10))
        j = r.json()
        if j.get("ok"):
            print(f"🧹 {label}: webhook cleared.")
        else:
            print(f"⚠️ {label}: deleteWebhook → {j}")
    except Exception as e:
        print(f"⚠️ {label}: deleteWebhook failed — {e}")


def user_bot_loop():
    print("🚀 User bot polling started.")
    offset, backoff = None, 1
    while True:
        try:
            result = get_updates(offset)
            if result and result.get("ok"):
                backoff = 1
                for upd in result.get("result", []):
                    offset = upd.get("update_id", 0) + 1
                    try:
                        process_update(upd)
                    except Exception as ue:
                        print("update err:", ue)
                        traceback.print_exc()
            elif result and result.get("conflict"):
                time.sleep(5)
            else:
                time.sleep(min(backoff, 10))
                backoff = min(backoff * 2, 30)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print("user loop err:", e)
            time.sleep(min(backoff, 10))
            backoff = min(backoff * 2, 30)


# ============================================================
# 23. SELF-PING
# ============================================================
def self_ping_loop():
    if not SELF_URL or not SELF_URL.startswith("http"):
        print("ℹ️ SELF_URL not set — skipping keep-alive.")
        return
    target = SELF_URL.rstrip("/") + "/health"
    print(f"🔄 Self-ping → {target} every {SELF_PING_INTERVAL}s")
    time.sleep(30)
    while True:
        try:
            r = HTTP.get(target, timeout=(5, 15))
            print(f"💓 {r.status_code} @ {time.strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"⚠️ self-ping fail: {e}")
        time.sleep(SELF_PING_INTERVAL)


# ============================================================
# 24. BOOTSTRAP
# ============================================================
def _hook_health_runtime():
    _RUNTIME["total_users"]  = lambda: len(LAST_SEEN)
    _RUNTIME["banned_users"] = lambda: len(BANNED_USERS)
    _RUNTIME["maintenance"]  = lambda: MAINTENANCE_MODE


def main():
    if not USER_BOT_TOKEN or "YOUR_USER_BOT_TOKEN" in USER_BOT_TOKEN:
        print("ERROR: USER_BOT_TOKEN missing.")
        return

    # 🆕 Ensure all JSON files exist before loading them
    ensure_json_files()

    load_password()
    load_admins()
    load_banned()
    load_notes()
    load_log()
    load_lastseen()
    load_langs()
    _hook_health_runtime()

    threading.Thread(target=run_flask, daemon=True).start()

    try:
        r = HTTP.get(f"{USER_TG_API}/getMe", timeout=(5, 10))
        info = r.json()
        if not info.get("ok"):
            print("ERROR: Invalid USER token.")
            return
        print("✅ USER bot:", info["result"]["username"])
    except Exception as e:
        print("USER bot connect error:", e)
        return

    try:
        r = ADMIN_HTTP.get(f"{ADMIN_TG_APIS[1]}/getMe", timeout=(5, 10))
        info = r.json()
        if info.get("ok"):
            print(f"✅ ADMIN bot: {info['result']['username']}")
        else:
            print(f"⚠️ ADMIN bot check: {info}")
    except Exception as e:
        print("⚠️ ADMIN bot verify fail:", e)

    clear_webhook(USER_TG_API, "USER bot")
    clear_webhook(ADMIN_TG_APIS[1], "ADMIN bot")

    if FORCE_JOIN_ENABLED:
        print("🔒 Force-join is ENABLED")
        if not _bot_in_communities():
            print("⚠️ Bot is NOT a member of group and/or channel. "
                  "Add it as admin/member to both.")

    threading.Thread(target=admin_bot_loop, args=(1,), daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()

    print("=" * 60)
    print("FREE bot started · owner:", OWNER_ID or "(unset)")
    print("Support:", SUPPORT_HANDLE)
    print("=" * 60)

    user_bot_loop()


if __name__ == "__main__":
    main()
