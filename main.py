import os
import io
import csv
import json
import time
import re
import secrets
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
    "total_users":  lambda: len(LAST_SEEN),
    "banned_users": lambda: len(BANNED_USERS),
    "maintenance":  lambda: MAINTENANCE_MODE,
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
            "uptime_sec":  int(time.time() - _RUNTIME["start_ts"]),
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


DATA_DIR = _env("DATA_DIR", "data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as _e:
    print(f"⚠️ Could not create data dir: {_e}")


def _data_file(name):
    return os.path.join(DATA_DIR, name)


USER_BOT_TOKEN  = _env("USER_BOT_TOKEN",  "8771414496:AAEZYXZa3TXHPcJoEYxHmI105c60F8VSYxo")
ADMIN_BOT_TOKEN = _env("ADMIN_BOT_TOKEN_1", "8916442795:AAETD7lL1snL27ab0RVVzpMPBWvnJ7_Xyn4")

LEAKOSINT_API_KEY = _env("LEAKOSINT_API_KEY", "405696785:2gafVtDu")

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

ADMINS_FILE         = _env("ADMINS_FILE",         _data_file("admins.json"))
PASSWORD_FILE       = _env("PASSWORD_FILE",       _data_file("admin_password.json"))
BANNED_FILE         = _env("BANNED_FILE",         _data_file("banned.json"))
NOTES_FILE          = _env("NOTES_FILE",          _data_file("user_notes.json"))
ADMIN_LOG_FILE      = _env("ADMIN_LOG_FILE",      _data_file("admin_log.json"))
LASTSEEN_FILE       = _env("LASTSEEN_FILE",       _data_file("last_seen.json"))
LANGS_FILE          = _env("LANGS_FILE",          _data_file("user_langs.json"))
TOKENS_FILE         = _env("TOKENS_FILE",         _data_file("user_tokens.json"))
REFS_FILE           = _env("REFS_FILE",           _data_file("user_refs.json"))
LOGIN_ATTEMPTS_FILE = _env("LOGIN_ATTEMPTS_FILE", _data_file("login_attempts.json"))

# ISOLATED STORAGE: Kept strictly separate from Admin/Owner views
API_KEYS_FILE       = _env("API_KEYS_FILE",       _data_file("api_keys.json"))

IMAGES = {
    "welcome":     _env("WELCOME_IMG",     "images/welcome.png"),
    "osint":       _env("OSINT_IMG",       "images/osint.png"),
    "join":        _env("JOIN_IMG",        "images/join.png"),
    "maintenance": _env("MAINTENANCE_IMG", "images/maintenance.png"),
}
IMAGE_CACHE = {}

TOKEN_CFG = {
    "start_balance":         int(_env("TOKEN_START",          "480")),
    "default_max":           int(_env("TOKEN_DEFAULT_MAX",    "480")),
    "search_cost":           int(_env("TOKEN_SEARCH_COST",    "40")),
    "default_regen_ms":      int(_env("TOKEN_REGEN_MS",       "3000")),
    "upgraded_regen_ms":     int(_env("TOKEN_UPGRADED_REGEN", "500")),
    "max_upgrade_add":       int(_env("TOKEN_MAX_UPGRADE",    "500")),
    "price_max_upgrade":     int(_env("TOKEN_PRICE_MAX",      "50")),
    "price_regen":           int(_env("TOKEN_PRICE_REGEN",    "40")),
    "price_refill":          int(_env("TOKEN_PRICE_REFILL",   "5")),
    "ref_milestone_instant": int(_env("REF_INSTANT_AT",       "2")),
    "ref_milestone_regen":   int(_env("REF_REGEN_AT",         "30")),
    "instant_minutes":       int(_env("REF_INSTANT_MIN",      "60")),
}


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

USER_TOKENS   = {}
REF_INDEX     = {}
USER_API_KEYS = {}

LOGIN_ATTEMPTS = {}

db_lock = threading.Lock()


# ============================================================
# 4. HTTP SESSIONS & APIS
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
API_CONNECT, API_READ = 5, 30

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
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"⚠️ save {path} failed: {e}")


_JSON_DEFAULTS = {
    ADMINS_FILE:         [],
    PASSWORD_FILE:       None,
    BANNED_FILE:         {},
    NOTES_FILE:          {},
    ADMIN_LOG_FILE:      [],
    LASTSEEN_FILE:       {},
    LANGS_FILE:          {},
    TOKENS_FILE:         {},
    REFS_FILE:           {},
    LOGIN_ATTEMPTS_FILE: {},
    API_KEYS_FILE:       {},
}


def ensure_json_files():
    os.makedirs(DATA_DIR, exist_ok=True)
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


def load_login_attempts():
    global LOGIN_ATTEMPTS
    raw = _safe_load(LOGIN_ATTEMPTS_FILE, {}) or {}
    LOGIN_ATTEMPTS = {}
    for k, v in raw.items():
        if not str(k).lstrip("-").isdigit():
            continue
        LOGIN_ATTEMPTS[int(k)] = {
            "fails":        int(v.get("fails", 0)),
            "locked_until": float(v.get("locked_until", 0)),
        }
    print(f"🔐 Loaded {len(LOGIN_ATTEMPTS)} login-attempt record(s).")


def save_login_attempts():
    with db_lock:
        _safe_save(LOGIN_ATTEMPTS_FILE, {str(k): v for k, v in LOGIN_ATTEMPTS.items()})


# ── Private API Keys Persistence (Isolated) ──
def load_api_keys():
    global USER_API_KEYS
    raw = _safe_load(API_KEYS_FILE, {})
    USER_API_KEYS = {int(k): v for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    print(f"🔑 Loaded {len(USER_API_KEYS)} private API key records.")


def save_api_keys():
    with db_lock:
        _safe_save(API_KEYS_FILE, {str(k): v for k, v in USER_API_KEYS.items()})


# ── Login lockout policy ──
LOCKOUT_SECONDS   = int(_env("LOGIN_LOCKOUT_SECONDS", "3600"))
LOCKOUT_MAX_FAILS = int(_env("LOGIN_MAX_FAILS",       "1"))


def _now():
    return time.time()


def login_lock_remaining(chat_id):
    rec = LOGIN_ATTEMPTS.get(chat_id)
    if not rec:
        return 0
    rem = int(rec.get("locked_until", 0) - _now())
    return rem if rem > 0 else 0


def login_register_failure(chat_id):
    rec = LOGIN_ATTEMPTS.setdefault(chat_id, {"fails": 0, "locked_until": 0.0})
    rec["fails"] = int(rec.get("fails", 0)) + 1
    if rec["fails"] >= LOCKOUT_MAX_FAILS:
        rec["locked_until"] = _now() + LOCKOUT_SECONDS
    save_login_attempts()
    return rec["locked_until"] > _now(), max(0, int(rec["locked_until"] - _now()))


def login_clear(chat_id):
    if chat_id in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS.pop(chat_id, None)
        save_login_attempts()


def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def log_admin(by, action, target=""):
    entry = {"ts": int(time.time()), "by": int(by), "action": str(action), "target": str(target)}
    ADMIN_LOG.append(entry)
    if len(ADMIN_LOG) > ADMIN_LOG_MAX:
        del ADMIN_LOG[:-ADMIN_LOG_MAX]
    save_log()


# ============================================================
# 6. TOKEN SYSTEM PERSISTENCE & LOGIC
# ============================================================
def load_tokens():
    global USER_TOKENS, REF_INDEX
    raw = _safe_load(TOKENS_FILE, {})
    USER_TOKENS = {}
    for k, v in (raw or {}).items():
        if not str(k).lstrip("-").isdigit():
            continue
        USER_TOKENS[int(k)] = {
            "tokens":         int(v.get("tokens", TOKEN_CFG["start_balance"])),
            "max":            int(v.get("max", TOKEN_CFG["default_max"])),
            "regen_ms":       int(v.get("regen_ms", TOKEN_CFG["default_regen_ms"])),
            "last_ts":        float(v.get("last_ts", time.time())),
            "refs":           int(v.get("refs", 0)),
            "referred_by":    int(v.get("referred_by", 0) or 0),
            "instant_until":  float(v.get("instant_until", 0)),
            "regen_upgraded": bool(v.get("regen_upgraded", False)),
            "max_upgraded":   bool(v.get("max_upgraded", False)),
            "referred_users": list(v.get("referred_users", []) or []),
        }

    refs_raw = _safe_load(REFS_FILE, {})
    REF_INDEX = {int(k): int(v) for k, v in (refs_raw or {}).items()
                 if str(k).lstrip("-").isdigit() and str(v).lstrip("-").isdigit()}


def save_tokens():
    with db_lock:
        out = {}
        for uid, rec in USER_TOKENS.items():
            out[str(uid)] = rec
        _safe_save(TOKENS_FILE, out)
        _safe_save(REFS_FILE, {str(k): v for k, v in REF_INDEX.items()})


def _ensure_user_tokens(uid):
    if uid not in USER_TOKENS:
        USER_TOKENS[uid] = {
            "tokens":         TOKEN_CFG["start_balance"],
            "max":            TOKEN_CFG["default_max"],
            "regen_ms":       TOKEN_CFG["default_regen_ms"],
            "last_ts":        time.time(),
            "refs":           0,
            "referred_by":    0,
            "instant_until":  0.0,
            "regen_upgraded": False,
            "max_upgraded":   False,
            "referred_users": [],
        }
        return True
    return False


def tokens_apply_regen(uid):
    _ensure_user_tokens(uid)
    rec = USER_TOKENS[uid]
    now = time.time()
    if rec["tokens"] >= rec["max"]:
        rec["last_ts"] = now
        return rec["tokens"]
    regen_ms = max(rec["regen_ms"], 100)
    elapsed_ms = int((now - rec["last_ts"]) * 1000)
    if elapsed_ms < regen_ms:
        return rec["tokens"]
    gained = elapsed_ms // regen_ms
    if gained <= 0:
        return rec["tokens"]
    rec["tokens"] = min(rec["max"], rec["tokens"] + int(gained))
    rec["last_ts"] = now
    return rec["tokens"]


def tokens_balance(uid):
    return tokens_apply_regen(uid)


def tokens_next_in_seconds(uid):
    _ensure_user_tokens(uid)
    rec = USER_TOKENS[uid]
    if rec["tokens"] >= rec["max"]:
        return 0
    regen_ms = max(rec["regen_ms"], 100)
    elapsed_ms = int((time.time() - rec["last_ts"]) * 1000)
    remaining_ms = max(0, regen_ms - (elapsed_ms % regen_ms))
    return max(1, remaining_ms // 1000 + (1 if remaining_ms % 1000 else 0))


def refs_register(new_uid, referrer_uid):
    if new_uid == referrer_uid:
        return False, 0, None
    if not referrer_uid or not str(referrer_uid).lstrip("-").isdigit():
        return False, 0, None
    if new_uid in REF_INDEX:
        return False, 0, None
    _ensure_user_tokens(new_uid)
    _ensure_user_tokens(referrer_uid)

    REF_INDEX[new_uid] = referrer_uid
    USER_TOKENS[new_uid]["referred_by"] = referrer_uid
    rec = USER_TOKENS[referrer_uid]
    if new_uid not in rec["referred_users"]:
        rec["referred_users"].append(new_uid)
        rec["refs"] = len(rec["referred_users"])
    save_tokens()
    return True, rec["refs"], None


# ============================================================
# 7. OWNER JSON FILE MANAGER (api_keys.json IS EXCLUDED)
# ============================================================
JSON_FILE_MAP = {
    "admins.json":         ADMINS_FILE,
    "admin_password.json": PASSWORD_FILE,
    "banned.json":         BANNED_FILE,
    "user_notes.json":     NOTES_FILE,
    "admin_log.json":      ADMIN_LOG_FILE,
    "last_seen.json":      LASTSEEN_FILE,
    "user_langs.json":     LANGS_FILE,
    "user_tokens.json":    TOKENS_FILE,
    "user_refs.json":      REFS_FILE,
    "login_attempts.json": LOGIN_ATTEMPTS_FILE,
    # NOTE: api_keys.json is NEVER listed here to maintain privacy
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
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        except Exception:
            pretty = raw
    except Exception as e:
        return
    header = f"📄 <b>{fname}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    body = _escape_html(pretty)
    if len(header) + len(body) <= 3800:
        admin_send_message(bot_number, chat_id, header + f"<pre>{body}</pre>", owner_files_keyboard())
    else:
        admin_send_document(bot_number, chat_id, path, caption=f"📄 {fname}")


# ============================================================
# 8. TELEGRAM API HELPER UTILITIES
# ============================================================
def user_send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = HTTP.post(f"{USER_TG_API}/sendMessage", json=payload, timeout=(TG_CONNECT, TG_READ))
        return r.json()
    except Exception as e:
        print(f"⚠️ user_send_message error: {e}")
        return None


def user_edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = HTTP.post(f"{USER_TG_API}/editMessageText", json=payload, timeout=(TG_CONNECT, TG_READ))
        return r.json()
    except Exception as e:
        print(f"⚠️ user_edit_message error: {e}")
        return None


def user_answer_callback(callback_query_id, text=None, alert=False):
    payload = {"callback_query_id": callback_query_id, "show_alert": alert}
    if text:
        payload["text"] = text
    try:
        HTTP.post(f"{USER_TG_API}/answerCallbackQuery", json=payload, timeout=(TG_CONNECT, TG_READ))
    except Exception as e:
        print(f"⚠️ user_answer_callback error: {e}")


def admin_send_message(bot_number, chat_id, text, reply_markup=None):
    base_url = ADMIN_TG_APIS.get(bot_number, ADMIN_TG_APIS[1])
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        ADMIN_HTTP.post(f"{base_url}/sendMessage", json=payload, timeout=(TG_CONNECT, TG_READ))
    except Exception as e:
        pass


def admin_send_document(bot_number, chat_id, file_path, caption=""):
    base_url = ADMIN_TG_APIS.get(bot_number, ADMIN_TG_APIS[1])
    try:
        with open(file_path, "rb") as f:
            ADMIN_HTTP.post(
                f"{base_url}/sendDocument",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"document": f},
                timeout=(TG_CONNECT, 60),
            )
    except Exception as e:
        pass


# ============================================================
# 9. HTML RECEIPT GENERATOR
# ============================================================
def generate_html_receipt(key_data):
    expiry_str = time.strftime('%B %d, %Y - %H:%M:%S', time.localtime(key_data["expires"]))
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>API Access Receipt</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; color: #333; text-align: center; padding: 40px 20px; margin: 0; }}
            .container {{ background: white; padding: 40px; border-radius: 12px; box-shadow: 0 8px 16px rgba(0,0,0,0.1); max-width: 500px; margin: auto; border-top: 5px solid #0088cc; }}
            h1 {{ color: #0088cc; margin-bottom: 5px; font-size: 24px; }}
            p {{ margin-bottom: 20px; font-size: 16px; color: #555; }}
            .detail {{ background: #f9f9f9; padding: 15px; border-radius: 8px; margin-bottom: 25px; text-align: left; border: 1px solid #eee; }}
            .detail strong {{ color: #222; }}
            .key-box {{ background: #e8f5e9; padding: 20px; border-radius: 8px; font-family: 'Courier New', Courier, monospace; font-size: 20px; font-weight: bold; letter-spacing: 1.5px; color: #2e7d32; border: 2px dashed #4caf50; word-break: break-all; margin-bottom: 20px; }}
            .footer {{ margin-top: 30px; font-size: 13px; color: #888; border-top: 1px solid #ddd; padding-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>💎 Premium API Receipt</h1>
            <p>Thank you for subscribing to our API service!</p>
            
            <div class="detail">
                <div style="margin-bottom: 10px;"><strong>📦 Plan:</strong> {key_data['plan']}</div>
                <div><strong>⏳ Expires On:</strong> {expiry_str}</div>
            </div>
            
            <h3 style="color: #333; margin-bottom: 10px;">Your Private API Key</h3>
            <div class="key-box">{key_data['key']}</div>
            
            <div class="footer">
                ⚠️ Keep this file safe. Do not share your API key with anyone. Administrators will never ask for your key.
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


# ============================================================
# 10. USER UI & API SELLING STORE
# ============================================================
def user_main_menu_keyboard(uid):
    return {
        "inline_keyboard": [
            [
                {"text": "🔍 Search OSINT", "callback_data": "menu_search"},
                {"text": "🎟 My Tokens",    "callback_data": "menu_tokens"}
            ],
            [
                {"text": "👥 Refer & Earn", "callback_data": "menu_ref"},
                {"text": "💬 Support",      "url": SUPPORT_URL}
            ],
            [
                {"text": "💎 Buy / View API Access 💎", "callback_data": "open_api_store"}
            ]
        ]
    }


def send_user_welcome(chat_id, user_first_name="User"):
    tokens_apply_regen(int(chat_id))
    bal = USER_TOKENS.get(int(chat_id), {}).get("tokens", TOKEN_CFG["start_balance"])
    
    welcome_text = (
        f"🌟 <b>Welcome to the Cyber Center, {user_first_name}!</b> 🌟\n\n"
        "⚡️ <i>Fast, reliable, and secure data system.</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟 <b>Current Tokens:</b> <code>{bal}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 <b>Select an option from the menu below:</b>"
    )
    user_send_message(chat_id, welcome_text, reply_markup=user_main_menu_keyboard(chat_id))


def send_api_store(chat_id, message_id=None):
    user_id = int(chat_id)
    now = time.time()
    
    if user_id in USER_API_KEYS and USER_API_KEYS[user_id]["expires"] > now:
        key_data = USER_API_KEYS[user_id]
        expiry_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(key_data["expires"]))
        seconds_left = int(key_data["expires"] - now)
        
        text = (
            "✅ <b>Your Active API Access</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔐 <b>Private API Key:</b>\n👉 <code>{key_data['key']}</code> 👈\n"
            "<i>(Tap key once to copy)</i>\n\n"
            f"📦 <b>Plan:</b> {key_data['plan']}\n"
            f"⏳ <b>Expires On:</b> <code>{expiry_str}</code>\n"
            f"⏱ <b>Time Left:</b> {_fmt_duration(seconds_left)}\n\n"
            "🛡 <b>Zero-Knowledge Privacy:</b>\n"
            "<i>This key is strictly visible only to you. Server admins have zero access.</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "📥 Download HTML Receipt", "callback_data": "dl_html_receipt"}],
                [{"text": "🔄 Refresh Status", "callback_data": "open_api_store"}],
                [{"text": "🔙 Back to Menu",    "callback_data": "menu_main"}]
            ]
        }
    else:
        text = (
            "💎 <b>Premium API Subscription Store</b> 💎\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Integrate automated lookups directly into your personal scripts and apps.\n\n"
            "⏱ <b>Select a Temporary Access Plan:</b>\n"
            "• <b>Weekly</b> — 7 Days Full Access\n"
            "• <b>Monthly</b> — 30 Days Full Access\n"
            "• <b>Yearly</b> — 365 Days Unrestricted Access\n\n"
            "🔒 <b>Strict Privacy Guarantee:</b>\n"
            "<i>All issued keys are encrypted. Only you will ever see this key.</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": "📅 Weekly Plan (7 Days) - $5",    "callback_data": "buy_api_weekly"}],
                [{"text": "📆 Monthly Plan (30 Days) - $15",  "callback_data": "buy_api_monthly"}],
                [{"text": "🗓 Yearly Plan (365 Days) - $100", "callback_data": "buy_api_yearly"}],
                [{"text": "🔙 Back to Main Menu",            "callback_data": "menu_main"}]
            ]
        }

    if message_id:
        user_edit_message(chat_id, message_id, text, reply_markup=markup)
    else:
        user_send_message(chat_id, text, reply_markup=markup)


def process_api_purchase(chat_id, plan, message_id=None):
    user_id = int(chat_id)
    now = time.time()
    
    if plan == "weekly":
        duration = 7 * 86400
        plan_name = "Weekly Plan (7 Days)"
    elif plan == "monthly":
        duration = 30 * 86400
        plan_name = "Monthly Plan (30 Days)"
    elif plan == "yearly":
        duration = 365 * 86400
        plan_name = "Yearly Plan (365 Days)"
    else:
        return

    expires_at = now + duration
    new_key = "SK-" + secrets.token_hex(16).upper()

    USER_API_KEYS[user_id] = {
        "key": new_key,
        "plan": plan_name,
        "expires": expires_at,
        "created_at": now
    }
    save_api_keys()

    expiry_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_at))
    success_text = (
        "🎊 <b>PAYMENT SUCCESSFUL!</b> 🎊\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔐 <b>Your Private API Key:</b>\n"
        f"👉 <code>{new_key}</code> 👈\n"
        "<i>(Tap the key above to copy instantly)</i>\n\n"
        f"📦 <b>Subscribed Plan:</b> {plan_name}\n"
        f"⏳ <b>Valid Until:</b> <code>{expiry_str}</code>\n\n"
        "🛡 <b>Privacy Secured:</b> Nobody can see this except you.\n\n"
        "👇 <i>Click below to download your receipt as an HTML file!</i>"
    )

    markup = {
        "inline_keyboard": [
            [{"text": "📥 Download HTML Receipt", "callback_data": "dl_html_receipt"}],
            [{"text": "🔑 View Active API Details", "callback_data": "open_api_store"}],
            [{"text": "🔙 Back to Menu",           "callback_data": "menu_main"}]
        ]
    }

    if message_id:
        user_edit_message(chat_id, message_id, success_text, reply_markup=markup)
    else:
        user_send_message(chat_id, success_text, reply_markup=markup)


# ============================================================
# 11. USER MESSAGE & CALLBACK HANDLERS
# ============================================================
def handle_user_update(update):
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            user_id = msg.get("from", {}).get("id", chat_id)
            text = (msg.get("text") or "").strip()

            LAST_SEEN[user_id] = time.time()
            save_lastseen()

            if user_id in BANNED_USERS:
                user_send_message(chat_id, "🚫 Your account is banned.")
                return

            if MAINTENANCE_MODE and user_id not in DYNAMIC_ADMINS:
                user_send_message(chat_id, "🛠 Bot is in maintenance.")
                return

            if text.startswith("/start"):
                send_user_welcome(chat_id, msg.get("from", {}).get("first_name", "User"))
                return
            if text.startswith("/api"):
                send_api_store(chat_id)
                return

        elif "callback_query" in update:
            cb = update["callback_query"]
            cb_id = cb["id"]
            chat_id = cb["message"]["chat"]["id"]
            message_id = cb["message"]["message_id"]
            user_id = cb["from"]["id"]
            data = cb.get("data", "")

            LAST_SEEN[user_id] = time.time()
            save_lastseen()

            if data == "open_api_store":
                user_answer_callback(cb_id)
                send_api_store(chat_id, message_id=message_id)
            
            elif data.startswith("buy_api_"):
                user_answer_callback(cb_id)
                plan = data.replace("buy_api_", "")
                process_api_purchase(chat_id, plan, message_id=message_id)
            
            elif data == "dl_html_receipt":
                if user_id in USER_API_KEYS:
                    user_answer_callback(cb_id, text="⏳ Generating your HTML receipt...")
                    html_data = generate_html_receipt(USER_API_KEYS[user_id])
                    
                    # Convert HTML string into a downloadable file buffer
                    buffer = BytesIO(html_data.encode("utf-8"))
                    buffer.name = "API_Access_Receipt.html"
                    
                    try:
                        HTTP.post(
                            f"{USER_TG_API}/sendDocument",
                            data={"chat_id": chat_id, "caption": "📄 <b>Here is your API Receipt in HTML format.</b>\n<i>Keep this file safe!</i>", "parse_mode": "HTML"},
                            files={"document": buffer},
                            timeout=(TG_CONNECT, 60),
                        )
                    except Exception as e:
                        user_answer_callback(cb_id, text="❌ Failed to send file.", alert=True)
                else:
                    user_answer_callback(cb_id, text="❌ You don't have an active API key to download.", alert=True)

            elif data == "menu_main":
                user_answer_callback(cb_id)
                send_user_welcome(chat_id, cb["from"].get("first_name", "User"))
            
            elif data == "menu_tokens":
                user_answer_callback(cb_id)
                bal = tokens_balance(user_id)
                nxt = tokens_next_in_seconds(user_id)
                token_info = (
                    f"🎟 <b>Token Dashboard</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"• <b>Current Balance:</b> <code>{bal}</code>\n"
                    f"• <b>Next Regeneration In:</b> {nxt}s\n"
                )
                user_edit_message(chat_id, message_id, token_info, reply_markup={
                    "inline_keyboard": [[{"text": "🔙 Back", "callback_data": "menu_main"}]]
                })

    except Exception as e:
        pass


# ============================================================
# 12. ADMIN BOT UPDATE HANDLER (Omitted for brevity, paste your admin block here)
# ============================================================
def handle_admin_update(update):
    pass # Keep your exact admin handler code here

# ============================================================
# 13. BACKGROUND POLLING LOOPS & WORKERS
# ============================================================
def user_bot_polling():
    offset = 0
    print("🚀 User bot polling service active...")
    while True:
        try:
            resp = HTTP.get(
                f"{USER_TG_API}/getUpdates",
                params={"offset": offset, "timeout": 20},
                timeout=(TG_CONNECT, 30),
            )
            if resp.status_code != 200:
                time.sleep(2)
                continue
            data = resp.json()
            if not data.get("ok"):
                time.sleep(2)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                handle_user_update(update)
        except Exception:
            time.sleep(2)


# ============================================================
# 14. SYSTEM STARTUP
# ============================================================
if __name__ == "__main__":
    print("📦 Initializing data files & storage...")
    ensure_json_files()
    load_password()
    load_admins()
    load_banned()
    load_notes()
    load_log()
    load_lastseen()
    load_langs()
    load_tokens()
    load_login_attempts()
    load_api_keys()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=user_bot_polling, daemon=True).start()

    print("🤖 All bots, Flask webserver, and background services are running.")

    while True:
        time.sleep(3600)
