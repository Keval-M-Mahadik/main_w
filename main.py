import os
import io
import csv
import json
import time
import threading
import traceback

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
    "welcome": _env("WELCOME_IMG", "images/welcome.png"),
    "osint":   _env("OSINT_IMG",   "images/osint.png"),
    "join":    _env("JOIN_IMG",    "images/join.png"),
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
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"⚠️ save {path} failed: {e}")


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
    """FREE MODE — every user has full access. No expiry."""
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
# 8. i18n
# ============================================================
LANGUAGES = {
    "en": "🇬🇧 English", "hi": "🇮🇳 हिन्दी", "bn": "🇧🇩 বাংলা", "ur": "🇵🇰 اردو",
    "ar": "🇸🇦 العربية", "es": "🇪🇸 Español", "fr": "🇫🇷 Français", "de": "🇩🇪 Deutsch",
    "pt": "🇵🇹 Português", "ru": "🇷🇺 Русский", "zh": "🇨🇳 中文", "ja": "🇯🇵 日本語",
    "ko": "🇰🇷 한국어", "id": "🇮🇩 Indonesia", "tr": "🇹🇷 Türkçe", "fa": "🇮🇷 فارسی",
    "it": "🇮🇹 Italiano", "vi": "🇻🇳 Tiếng Việt", "th": "🇹🇭 ไทย", "ta": "🇮🇳 தமிழ்",
    "te": "🇮🇳 తెలుగు", "mr": "🇮🇳 मराठी", "gu": "🇮🇳 ગુજરાતી", "pa": "🇮🇳 ਪੰਜਾਬੀ",
    "ml": "🇮🇳 മലയാളം", "nl": "🇳🇱 Nederlands", "pl": "🇵🇱 Polski", "uk": "🇺🇦 Українська",
    "ro": "🇷🇴 Română", "sw": "🇰🇪 Kiswahili", "ms": "🇲🇾 Bahasa Melayu", "fil": "🇵🇭 Filipino",
}

TEXTS = {
    "en": {
        "welcome": ("✨ <b>W E L C O M E</b> ✨\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>Free OSINT Bot</b>\n🔓 All intelligence tools unlocked\n"
                    "⚡ Fast  •  🔒 Secure  •  🎯 Reliable\n\n"
                    "💬 <b>Support:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>M A I N   M E N U</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👇 <b>Select a feature to begin:</b>\n\n"
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
        "join_prompt": ("📢 <b>JOIN OUR COMMUNITY</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "Please join our official channel and group to receive "
                        "updates, support and tips.\n\n"
                        "Then tap <b>Continue</b>.\n\n"
                        "💬 <b>Support:</b> @Tony_M_unlock"),
        "join_required": ("🔒 <b>MEMBERSHIP REQUIRED</b>\n"
                          "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "You must join our <b>channel</b> and <b>group</b> "
                          "to use this bot.\n\n"
                          "After joining, tap <b>✅ Continue</b>.\n\n"
                          "💬 <b>Support:</b> @Tony_M_unlock"),
        "join_ok": "✅ Memberships verified. Welcome!",
        "join_fail": "❌ You must join both the channel and group first.",
        "maintenance": ("🛠 <b>UNDER MAINTENANCE</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "The bot is temporarily unavailable.\nPlease try again later."),
        "banned_msg": ("🚫 <b>ACCESS BLOCKED</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "Your account has been suspended."),
        "free_badge": "🆓 <b>Free Access</b> — all tools unlocked",
    },
    "hi": {
        "welcome": ("✨ <b>स्वागत है</b> ✨\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "🤖 <b>फ्री OSINT बॉट</b>\n🔓 सभी इंटेलिजेंस टूल्स अनलॉक\n"
                    "⚡ तेज़  •  🔒 सुरक्षित  •  🎯 विश्वसनीय\n\n"
                    "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "select_feature": ("🛠 <b>मुख्य मेनू</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👇 <b>शुरू करने के लिए एक सुविधा चुनें:</b>\n\n"
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
        "join_prompt": ("📢 <b>हमारे कम्युनिटी से जुड़ें</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "कृपया हमारा आधिकारिक चैनल और ग्रुप जॉइन करें।\n\n"
                        "फिर <b>जारी रखें</b> पर टैप करें।\n\n"
                        "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "join_required": ("🔒 <b>सदस्यता आवश्यक</b>\n"
                          "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                          "बॉट का उपयोग करने के लिए कृपया चैनल और ग्रुप जॉइन करें।\n\n"
                          "फिर <b>✅ जारी रखें</b> पर टैप करें।\n\n"
                          "💬 <b>सपोर्ट:</b> @Tony_M_unlock"),
        "join_ok": "✅ सदस्यता सत्यापित। स्वागत है!",
        "join_fail": "❌ पहले चैनल और ग्रुप दोनों जॉइन करें।",
        "maintenance": ("🛠 <b>रखरखाव जारी</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "बॉट अस्थायी रूप से अनुपलब्ध है।\nकृपया बाद में प्रयास करें।"),
        "banned_msg": ("🚫 <b>पहुंच अवरुद्ध</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       "आपका खाता निलंबित कर दिया गया है।"),
        "free_badge": "🆓 <b>फ्री एक्सेस</b> — सभी टूल्स अनलॉक",
    },
}

for _code in LANGUAGES:
    TEXTS.setdefault(_code, TEXTS["en"])


def t(lang, key, **kwargs):
    lang = lang if lang in TEXTS else "en"
    template = TEXTS[lang].get(key) or TEXTS["en"].get(key) or key
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def normalize_text(s):
    if not isinstance(s, str):
        return ""
    for ch in ("\ufe0f", "\u200b", "\u200c", "\u200d", "\u00a0", "\u2060"):
        s = s.replace(ch, "")
    return s.strip().lower()


def is_button(text, key, lang):
    candidates = {normalize_text(t(lang, key)),
                  normalize_text(TEXTS["en"].get(key, ""))}
    return normalize_text(text) in candidates


def get_lang(cid):  return USER_LANGS.get(cid, "en")


def set_lang(cid, lang):
    USER_LANGS[cid] = lang
    save_langs()


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
    """Main OSINT menu — includes a Support button."""
    buttons = list(API_CONFIG.keys())
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if len(keyboard[-1]) == 1:
        keyboard[-1].append(t(lang, "cancel_btn"))
    else:
        keyboard.append([t(lang, "cancel_btn")])
    keyboard.append([t(lang, "lang_btn"), t(lang, "support_btn")])
    return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": False}


def language_keyboard():
    items = list(LANGUAGES.items())
    rows = []
    for i in range(0, len(items), 2):
        rows.append([{"text": name, "callback_data": f"lang:{code}"}
                     for code, name in items[i:i + 2]])
    rows.append([{"text": "💬 Contact Support (@Tony_M_unlock)", "url": SUPPORT_URL}])
    return {"inline_keyboard": rows}


def join_keyboard(lang="en"):
    return {"inline_keyboard": [
        [{"text": "📢  Join Channel", "url": JOIN_CHANNEL_URL}],
        [{"text": "💬  Join Group",   "url": JOIN_GROUP_URL}],
        [{"text": t(lang, "continue_btn"), "callback_data": "user:continue"}],
        [{"text": "💬 Contact Support (@Tony_M_unlock)", "url": SUPPORT_URL}],
    ]}


def force_join_keyboard(lang="en"):
    return {"inline_keyboard": [
        [{"text": "📢  Join Channel", "url": JOIN_CHANNEL_URL}],
        [{"text": "💬  Join Group",   "url": JOIN_GROUP_URL}],
        [{"text": t(lang, "continue_btn"), "callback_data": "user:continue"}],
        [{"text": "💬 Contact Support (@Tony_M_unlock)", "url": SUPPORT_URL}],
    ]}


# ============================================================
# 13. ADMIN KEYBOARDS
# ============================================================
def admin_main_keyboard():
    return {"inline_keyboard": [
        [{"text": "👥  Users",         "callback_data": "admin:list"},
         {"text": "📊  Stats",         "callback_data": "admin:stats"}],
        [{"text": "🚫  Banned",        "callback_data": "admin:banned"},
         {"text": "🟢  Online",        "callback_data": "admin:online"}],
        [{"text": "👑  Admins",        "callback_data": "admin:admins"},
         {"text": "📜  Logs",          "callback_data": "admin:logs"}],
        [{"text": "📢  Broadcast",     "callback_data": "admin:broadcast"},
         {"text": "🛠  Maintenance",   "callback_data": "admin:maintenance"}],
        [{"text": "🆔  Who Am I",      "callback_data": "admin:whoami"}],
    ]}


def admin_admins_keyboard():
    return {"inline_keyboard": [
        [{"text": "➕  Add Admin",    "callback_data": "admin:add_admin"}],
        [{"text": "➖  Remove Admin", "callback_data": "admin:remove_admin"}],
        [{"text": "📋  List Admins",  "callback_data": "admin:list_admins"}],
        [{"text": "🔙  Back",         "callback_data": "admin:back"}],
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
# 15. USER CALLBACK HANDLER
# ============================================================
def process_callback(cb):
    data = cb.get("data", "") or ""
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    msg_id = msg.get("message_id")
    cb_id = cb.get("id")
    if chat_id is None:
        return
    lang = get_lang(chat_id)

    if is_banned(chat_id):
        answer_callback(cb_id, "🚫 Banned")
        return
    if MAINTENANCE_MODE and get_role(chat_id) == "user":
        answer_callback(cb_id, "🛠 Maintenance")
        return

    # --- language picker ---
    if data.startswith("lang:"):
        code = data.split(":", 1)[1]
        if code in LANGUAGES:
            set_lang(chat_id, code)
            answer_callback(cb_id, t(code, "language_set"))
            if msg_id:
                delete_message(chat_id, msg_id)

            # Force-join gate AFTER language pick
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

    # --- continue (after force-join prompt) ---
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
        send_message(chat_id, t(lang, "choose_language"), language_keyboard())
        return

    if data == "user:support":
        answer_callback(cb_id)
        send_message(chat_id, t(lang, "support"))
        return


# ============================================================
# 16. USER MESSAGE HANDLER
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
    if "callback_query" in update:
        process_callback(update["callback_query"])
        return
    if "message" not in update:
        return
    msg = update["message"]
    chat_id = msg.get("chat", {}).get("id")
    user_id = msg.get("from", {}).get("id", chat_id)
    if chat_id is None:
        return

    LAST_SEEN[chat_id] = time.time()

    text = msg.get("text", "")
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return
    lang = get_lang(chat_id)

    # ---- Banned / Maintenance ----
    if is_banned(chat_id):
        if text == "/start":
            send_message(chat_id, t(lang, "banned_msg"))
        return
    if MAINTENANCE_MODE and get_role(chat_id) == "user":
        if text == "/start":
            send_message(chat_id, t(lang, "maintenance"))
        return

    # ---- /start ----
    if text == "/start":
        USER_STATE.pop(chat_id, None)

        # NEW USER → language picker first (join gate runs after pick)
        if chat_id not in USER_LANGS:
            caption = (t(lang, "welcome") + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                       + t(lang, "choose_language"))
            send_photo(chat_id, IMAGES["welcome"], caption=caption,
                       keyboard=language_keyboard())
            return

        # RETURNING USER → join gate first
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

    # ---- Support ----
    if text in ("/support", "/help_support") or is_button(text, "support_btn", lang):
        send_message(chat_id, t(lang, "support"))
        return

    # ---- Language change (allowed anytime) ----
    if text in ("/lang", "/language") or is_button(text, "lang_btn", lang):
        send_message(chat_id, t(lang, "choose_language"), language_keyboard())
        return

    # ---- Force-join gate for every other message ----
    if FORCE_JOIN_ENABLED and get_role(chat_id) == "user":
        ok, reason = check_user_joined(user_id)
        if not ok:
            USER_STATE.pop(chat_id, None)
            if reason == "bot_not_member":
                _ask_bot_setup(chat_id)
            else:
                _ask_for_join(chat_id, lang)
            return

    # ---- Cancel ----
    if text == "/cancel" or is_button(text, "cancel_btn", lang):
        USER_STATE.pop(chat_id, None)
        _send_feature_menu(chat_id, lang)
        return

    # ---- OSINT API query input ----
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
            formatted = (json.dumps(body, indent=2, ensure_ascii=False)
                         if isinstance(body, (dict, list)) else str(body))
            _send_long(chat_id, f"<pre>{_html_escape(formatted)}</pre>", main_keyboard(lang))
        else:
            send_message(chat_id, t(lang, "no_result"), main_keyboard(lang))
        USER_STATE.pop(chat_id, None)
        return

    # ---- Command shortcuts (/num 9876543210) ----
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
                formatted = (json.dumps(body, indent=2, ensure_ascii=False)
                             if isinstance(body, (dict, list)) else str(body))
                _send_long(chat_id, f"<pre>{_html_escape(formatted)}</pre>", main_keyboard(lang))
            else:
                send_message(chat_id, t(lang, "no_result"), main_keyboard(lang))
            return

    # ---- Feature button match ----
    api_name = next((name for name in API_CONFIG if name.strip() == text.strip()), None)
    if api_name is not None:
        cfg = API_CONFIG[api_name]
        USER_STATE[chat_id] = {"flow": "api_query", "api_name": api_name}
        send_message(chat_id, cfg["prompt"] + "\n\n" + t(lang, "send_cancel"),
                     main_keyboard(lang))
        return

    send_message(chat_id, t(lang, "select_option"), main_keyboard(lang))


# ============================================================
# 17. ADMIN BOT HELPERS
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
# 18. ADMIN CALLBACK ROUTER
# ============================================================
def _fmt_user_line(uid):
    lang = USER_LANGS.get(uid, "en")
    ban = "🚫" if uid in BANNED_USERS else ""
    seen = LAST_SEEN.get(uid)
    seen_str = time.strftime("%m-%d %H:%M", time.localtime(seen)) if seen else "—"
    return f"🆓{ban} <code>{uid}</code> · {lang} · seen {seen_str}"


def process_admin_callback(bot_number, cb):
    global MAINTENANCE_MODE
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
        admin_send_message(bot_number, chat_id,
                           "🛠 <b>A D M I N   P A N E L</b>\n"
                           "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                           "👇 Choose an action below:",
                           admin_main_keyboard())
        return

    if data == "admin:list":
        admin_answer_callback(bot_number, cb_id)
        if not LAST_SEEN:
            admin_send_message(bot_number, chat_id, "📭 No users yet.", admin_main_keyboard())
            return
        lines = ["👥 <b>USERS</b> (recent first)\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        items = sorted(LAST_SEEN.items(), key=lambda x: x[1], reverse=True)[:40]
        for uid, _ in items:
            lines.append(_fmt_user_line(uid))
        if len(LAST_SEEN) > 40:
            lines.append(f"\n…and {len(LAST_SEEN) - 40} more (use /export).")
        admin_send_message(bot_number, chat_id, "\n".join(lines), admin_main_keyboard())
        return

    if data == "admin:banned":
        admin_answer_callback(bot_number, cb_id)
        if not BANNED_USERS:
            admin_send_message(bot_number, chat_id, "✅ No banned users.", admin_main_keyboard())
            return
        lines = ["🚫 <b>BANNED USERS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for uid, info in sorted(BANNED_USERS.items()):
            reason = info.get("reason", "—")
            lines.append(f"• <code>{uid}</code> — {reason}")
        lines.append("\nUnban: <code>/unban USER_ID</code>")
        admin_send_message(bot_number, chat_id, "\n".join(lines), admin_main_keyboard())
        return

    if data == "admin:online":
        admin_answer_callback(bot_number, cb_id)
        now = time.time()
        recent = [(u, ts) for u, ts in LAST_SEEN.items() if now - ts < 86400]
        recent.sort(key=lambda x: x[1], reverse=True)
        if not recent:
            admin_send_message(bot_number, chat_id, "💤 No activity in last 24h.",
                               admin_main_keyboard())
            return
        lines = ["🟢 <b>ACTIVE (last 24h)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for uid, ts in recent[:40]:
            ago = int(now - ts)
            rel = f"{ago // 60}m ago" if ago < 3600 else f"{ago // 3600}h ago"
            lines.append(f"• <code>{uid}</code> — {rel}")
        admin_send_message(bot_number, chat_id, "\n".join(lines), admin_main_keyboard())
        return

    if data == "admin:logs":
        admin_answer_callback(bot_number, cb_id)
        if not ADMIN_LOG:
            admin_send_message(bot_number, chat_id, "📭 No admin logs yet.",
                               admin_main_keyboard())
            return
        lines = ["📜 <b>ADMIN LOG</b> (latest 40)\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for e in ADMIN_LOG[-40:][::-1]:
            ts = time.strftime("%m-%d %H:%M", time.localtime(e["ts"]))
            lines.append(f"<code>{ts}</code> · <code>{e['by']}</code> → "
                         f"{e['action']} <code>{e.get('target','')}</code>")
        admin_send_message(bot_number, chat_id, "\n".join(lines), admin_main_keyboard())
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
                           admin_main_keyboard())
        return

    if data == "admin:admins":
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(bot_number, chat_id,
                           "👑 <b>A D M I N   M A N A G E M E N T</b>\n"
                           "━━━━━━━━━━━━━━━━━━━━━━━━━",
                           admin_admins_keyboard())
        return

    if data == "admin:list_admins":
        admin_answer_callback(bot_number, cb_id)
        lines = ["👑 <b>ADMIN LIST</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for aid in sorted(DYNAMIC_ADMINS):
            lines.append(f"• <code>{aid}</code> — {role_badge(aid)}")
        admin_send_message(bot_number, chat_id, "\n".join(lines), admin_admins_keyboard())
        return

    if data == "admin:maintenance":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        kb = {"inline_keyboard": [
            [{"text": "🟢  Turn ON",  "callback_data": "admin:maintenance_on"}],
            [{"text": "🔴  Turn OFF", "callback_data": "admin:maintenance_off"}],
            [{"text": "🔙  Back",     "callback_data": "admin:back"}],
        ]}
        admin_send_message(bot_number, chat_id,
                           f"🛠 <b>MAINTENANCE MODE</b>\n"
                           f"Current: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>", kb)
        return

    if data == "admin:maintenance_on":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        MAINTENANCE_MODE = True
        log_admin(chat_id, "maintenance ON")
        admin_answer_callback(bot_number, cb_id, "🛠 ON")
        admin_send_message(bot_number, chat_id, "🛠 Maintenance mode <b>ON</b>.",
                           admin_main_keyboard())
        return

    if data == "admin:maintenance_off":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        MAINTENANCE_MODE = False
        log_admin(chat_id, "maintenance OFF")
        admin_answer_callback(bot_number, cb_id, "✅ OFF")
        admin_send_message(bot_number, chat_id, "✅ Maintenance mode <b>OFF</b>.",
                           admin_main_keyboard())
        return

    if data == "admin:whoami":
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(bot_number, chat_id,
                           f"🆔 <code>{chat_id}</code>\n"
                           f"🏷 Role: <b>{role_badge(chat_id)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>\n"
                           f"🆓 Mode: <b>FREE</b>",
                           admin_main_keyboard())
        return

    if data == "admin:add_admin":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_add_admin"
        admin_send_message(bot_number, chat_id,
                           "➕ <b>ADD ADMIN</b>\n\nSend the Telegram User ID "
                           "to promote.\n\n/cancel to abort.",
                           admin_admins_keyboard())
        return

    if data == "admin:remove_admin":
        if not is_owner(chat_id):
            admin_answer_callback(bot_number, cb_id, "⛔ Owner only")
            return
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_remove_admin"
        admin_send_message(bot_number, chat_id,
                           "➖ <b>REMOVE ADMIN</b>\n\nSend the Telegram User ID "
                           "to demote.\n\n/cancel to abort.",
                           admin_admins_keyboard())
        return

    if data == "admin:broadcast":
        admin_answer_callback(bot_number, cb_id)
        ADMIN_STATE[chat_id] = "awaiting_broadcast"
        admin_send_message(bot_number, chat_id,
                           "📢 <b>BROADCAST</b>\n\nSend the message to broadcast "
                           "to all users. /cancel to abort.",
                           admin_main_keyboard())
        return


# ============================================================
# 19. ADMIN COMMAND HANDLER
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
                           "/add_admin ID · /remove_admin ID\n"
                           "/setpassword NEW · /maintenance on|off\n\n"
                           "/whoami · /logout",
                           admin_main_keyboard())
        return

    if cmd == "/whoami":
        admin_send_message(bot_number, chat_id,
                           f"🆔 <code>{chat_id}</code>\n"
                           f"🏷 Role: <b>{role_badge(chat_id)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>\n"
                           f"🆓 Mode: <b>FREE</b>",
                           admin_main_keyboard())
        return

    if cmd == "/logout":
        if is_owner(chat_id):
            admin_send_message(bot_number, chat_id,
                               "❌ Owner can't logout.", admin_main_keyboard())
            return
        DYNAMIC_ADMINS.discard(chat_id); save_admins()
        log_admin(chat_id, "logout")
        admin_send_message(bot_number, chat_id,
                           "👋 Logged out. Send /login &lt;password&gt; to log back in.")
        return

    if cmd == "/setpassword":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.", admin_main_keyboard())
            return
        if len(args) < 2:
            admin_send_message(bot_number, chat_id,
                               "Usage: <code>/setpassword NEW_PASSWORD</code>",
                               admin_main_keyboard())
            return
        CURRENT_PASSWORD = args[1]; save_password()
        log_admin(chat_id, "setpassword")
        admin_send_message(bot_number, chat_id, "✅ <b>Password updated.</b>",
                           admin_main_keyboard())
        return

    if cmd == "/maintenance":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.", admin_main_keyboard())
            return
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            admin_send_message(bot_number, chat_id,
                               "Usage: <code>/maintenance on|off</code>",
                               admin_main_keyboard())
            return
        MAINTENANCE_MODE = (args[1].lower() == "on")
        log_admin(chat_id, f"maintenance {args[1].lower()}")
        admin_send_message(bot_number, chat_id,
                           f"🛠 Maintenance <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>.",
                           admin_main_keyboard())
        return

    if cmd == "/add_admin":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.", admin_main_keyboard())
            return
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /add_admin ID",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.", admin_main_keyboard())
            return
        DYNAMIC_ADMINS.add(target); save_admins()
        log_admin(chat_id, "add_admin", target)
        admin_send_message(bot_number, chat_id,
                           f"✅ <code>{target}</code> is now Admin.",
                           admin_main_keyboard())
        return

    if cmd == "/remove_admin":
        if not is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "⛔ Owner only.", admin_main_keyboard())
            return
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /remove_admin ID",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.", admin_main_keyboard())
            return
        if is_owner(target):
            admin_send_message(bot_number, chat_id, "❌ Cannot remove owner.",
                               admin_main_keyboard()); return
        DYNAMIC_ADMINS.discard(target); save_admins()
        log_admin(chat_id, "remove_admin", target)
        admin_send_message(bot_number, chat_id,
                           f"✅ <code>{target}</code> removed.", admin_main_keyboard())
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
                               admin_main_keyboard())
        return

    if cmd in ("/check", "/role"):
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, f"Usage: {cmd} USER_ID",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_main_keyboard()); return
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
                           admin_main_keyboard())
        return

    if cmd == "/ban":
        if len(args) < 2:
            admin_send_message(bot_number, chat_id, "Usage: /ban ID [reason]",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_main_keyboard()); return
        if is_admin(target):
            admin_send_message(bot_number, chat_id, "❌ Cannot ban an admin.",
                               admin_main_keyboard()); return
        reason = " ".join(args[2:]) or "—"
        BANNED_USERS[target] = {"reason": reason, "ts": int(time.time()), "by": chat_id}
        save_banned()
        log_admin(chat_id, "ban", f"{target} ({reason})")
        send_message(target, t(get_lang(target), "banned_msg"))
        admin_send_message(bot_number, chat_id,
                           f"🚫 <code>{target}</code> banned. Reason: {reason}",
                           admin_main_keyboard())
        return

    if cmd == "/unban":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /unban ID",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_main_keyboard()); return
        if target in BANNED_USERS:
            del BANNED_USERS[target]; save_banned()
            log_admin(chat_id, "unban", target)
            admin_send_message(bot_number, chat_id,
                               f"✅ <code>{target}</code> unbanned.",
                               admin_main_keyboard())
        else:
            admin_send_message(bot_number, chat_id, "❌ Not banned.",
                               admin_main_keyboard())
        return

    if cmd == "/msg":
        if len(args) < 3:
            admin_send_message(bot_number, chat_id, "Usage: /msg ID TEXT",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_main_keyboard()); return
        body = " ".join(args[2:])
        res = send_message(target, f"📩 <b>Message from admin</b>\n\n{body}")
        if res and res.get("ok"):
            log_admin(chat_id, "msg", target)
            admin_send_message(bot_number, chat_id, "✅ Sent.", admin_main_keyboard())
        else:
            admin_send_message(bot_number, chat_id,
                               "❌ Failed (user may have blocked bot).",
                               admin_main_keyboard())
        return

    if cmd == "/note":
        if len(args) < 3:
            admin_send_message(bot_number, chat_id, "Usage: /note ID TEXT",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_main_keyboard()); return
        USER_NOTES.setdefault(target, []).append(
            {"text": " ".join(args[2:]), "ts": int(time.time()), "by": chat_id})
        save_notes()
        log_admin(chat_id, "note", target)
        admin_send_message(bot_number, chat_id, "📝 Note added.", admin_main_keyboard())
        return

    if cmd == "/notes":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /notes ID",
                               admin_main_keyboard()); return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_main_keyboard()); return
        notes = USER_NOTES.get(target, [])
        if not notes:
            admin_send_message(bot_number, chat_id, "📭 No notes.",
                               admin_main_keyboard()); return
        lines = [f"📝 <b>NOTES — <code>{target}</code></b>\n━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, n in enumerate(notes):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(n["ts"]))
            lines.append(f"{i + 1}. {n['text']}\n   <i>{ts} · by {n['by']}</i>")
        admin_send_message(bot_number, chat_id, "\n".join(lines), admin_main_keyboard())
        return

    if cmd == "/delnote":
        if len(args) != 3:
            admin_send_message(bot_number, chat_id, "Usage: /delnote ID N",
                               admin_main_keyboard()); return
        try:
            target = int(args[1]); idx = int(args[2]) - 1
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid.",
                               admin_main_keyboard()); return
        notes = USER_NOTES.get(target, [])
        if 0 <= idx < len(notes):
            notes.pop(idx); save_notes()
            log_admin(chat_id, "delnote", f"{target} #{idx + 1}")
            admin_send_message(bot_number, chat_id, "🗑 Deleted.", admin_main_keyboard())
        else:
            admin_send_message(bot_number, chat_id, "❌ Note index out of range.",
                               admin_main_keyboard())
        return

    admin_send_message(bot_number, chat_id,
                       "❓ Unknown command. Use /help.", admin_main_keyboard())


# ============================================================
# 20. ADMIN UPDATE ROUTER
# ============================================================
def process_admin_update(bot_number, update):
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
                admin_send_message(bot_number, chat_id, "Usage: <code>/login YOUR_PASSWORD</code>")
                return
            supplied = parts[1].strip()
            if supplied == CURRENT_PASSWORD or supplied in (ADMIN_BOT_TOKEN, USER_BOT_TOKEN):
                DYNAMIC_ADMINS.add(chat_id); save_admins()
                log_admin(chat_id, "login")
                admin_send_message(bot_number, chat_id,
                                   f"✅ <b>Login OK.</b>\n🏷 Role: <b>{role_badge(chat_id)}</b>",
                                   admin_main_keyboard())
            else:
                admin_send_message(bot_number, chat_id, "❌ Incorrect password.")
            return
        if text and text == CURRENT_PASSWORD:
            DYNAMIC_ADMINS.add(chat_id); save_admins()
            log_admin(chat_id, "login (bare)")
            admin_send_message(bot_number, chat_id,
                               f"✅ <b>Login OK.</b>\n🏷 Role: <b>{role_badge(chat_id)}</b>",
                               admin_main_keyboard())
            return
        admin_send_message(bot_number, chat_id,
                           f"⛔ Not authorized.\n🆔 Your ID: <code>{chat_id}</code>\n\n"
                           f"Send <code>/login YOUR_PASSWORD</code>.")
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_add_admin":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.", admin_admins_keyboard())
            return
        try:
            new_admin = int(text)
            DYNAMIC_ADMINS.add(new_admin); save_admins()
            ADMIN_STATE.pop(chat_id, None)
            log_admin(chat_id, "add_admin", new_admin)
            admin_send_message(bot_number, chat_id,
                               f"✅ <code>{new_admin}</code> is now Admin.",
                               admin_admins_keyboard())
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_admins_keyboard())
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_remove_admin":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.", admin_admins_keyboard())
            return
        try:
            target = int(text)
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               admin_admins_keyboard()); return
        if is_owner(target):
            admin_send_message(bot_number, chat_id, "❌ Cannot remove owner.",
                               admin_admins_keyboard()); return
        if target in DYNAMIC_ADMINS:
            DYNAMIC_ADMINS.discard(target); save_admins()
            ADMIN_STATE.pop(chat_id, None)
            log_admin(chat_id, "remove_admin", target)
            admin_send_message(bot_number, chat_id,
                               f"✅ <code>{target}</code> removed.",
                               admin_admins_keyboard())
        else:
            admin_send_message(bot_number, chat_id, "❌ Not an admin.",
                               admin_admins_keyboard())
        return

    if ADMIN_STATE.get(chat_id) == "awaiting_broadcast":
        if text == "/cancel":
            ADMIN_STATE.pop(chat_id, None)
            admin_send_message(bot_number, chat_id, "❌ Cancelled.", admin_main_keyboard())
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
                           admin_main_keyboard())
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
# 21. USER BOT POLLING
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
# 22. SELF-PING
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
# 23. BOOTSTRAP
# ============================================================
def _hook_health_runtime():
    _RUNTIME["total_users"]  = lambda: len(LAST_SEEN)
    _RUNTIME["banned_users"] = lambda: len(BANNED_USERS)
    _RUNTIME["maintenance"]  = lambda: MAINTENANCE_MODE


def main():
    if not USER_BOT_TOKEN or "YOUR_USER_BOT_TOKEN" in USER_BOT_TOKEN:
        print("ERROR: USER_BOT_TOKEN missing.")
        return

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
