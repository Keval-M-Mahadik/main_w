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


# All persisted JSON data lives inside this folder
DATA_DIR = _env("DATA_DIR", "data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as _e:
    print(f"⚠️ Could not create data dir: {_e}")


def _data_file(name):
    return os.path.join(DATA_DIR, name)


USER_BOT_TOKEN  = _env("USER_BOT_TOKEN",
                       "8771414496:AAEZYXZa3TXHPcJoEYxHmI105c60F8VSYxo")
ADMIN_BOT_TOKEN = _env("ADMIN_BOT_TOKEN_1",
                       "8916442795:AAETD7lL1snL27ab0RVVzpMPBWvnJ7_Xyn4")

# --- Leakosint API (used by Aadhaar, Number, PAN, Email) ---
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

IMAGES = {
    "welcome":     _env("WELCOME_IMG",     "images/welcome.png"),
    "osint":       _env("OSINT_IMG",       "images/osint.png"),
    "join":        _env("JOIN_IMG",        "images/join.png"),
    "maintenance": _env("MAINTENANCE_IMG", "images/maintenance.png"),
}
IMAGE_CACHE = {}

# ── Token system configuration ──
# Economy:
#   • 480 tokens on a full tank
#   • 40 tokens per search  →  exactly 12 searches on a full tank
#   • default regen = 3s/token (slow, ~24 min to refill from empty)
#   • upgraded regen = 0.5s/token (6× faster, paid upgrade or 30 refs)
TOKEN_CFG = {
    "start_balance":     int(_env("TOKEN_START",          "480")),
    "default_max":       int(_env("TOKEN_DEFAULT_MAX",    "480")),
    "search_cost":       int(_env("TOKEN_SEARCH_COST",    "40")),
    "default_regen_ms":  int(_env("TOKEN_REGEN_MS",       "3000")),
    "upgraded_regen_ms": int(_env("TOKEN_UPGRADED_REGEN", "500")),
    "max_upgrade_add":   int(_env("TOKEN_MAX_UPGRADE",    "500")),
    "price_max_upgrade": int(_env("TOKEN_PRICE_MAX",      "50")),
    "price_regen":       int(_env("TOKEN_PRICE_REGEN",    "40")),
    "price_refill":      int(_env("TOKEN_PRICE_REFILL",   "5")),
    "ref_milestone_instant": int(_env("REF_INSTANT_AT",   "2")),
    "ref_milestone_regen":   int(_env("REF_REGEN_AT",     "30")),
    "instant_minutes":       int(_env("REF_INSTANT_MIN",  "60")),
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

USER_TOKENS = {}
REF_INDEX   = {}

LOGIN_ATTEMPTS = {}   # {chat_id: {"fails": int, "locked_until": float}}

_BOT_USERNAME_CACHE = {"username": None}

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
API_CONNECT, API_READ = 5, 30   # bumped for slow APIs like Render / Netlify cold starts

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
        _safe_save(LOGIN_ATTEMPTS_FILE,
                   {str(k): v for k, v in LOGIN_ATTEMPTS.items()})


# ── Login lockout policy ──
LOCKOUT_SECONDS   = int(_env("LOGIN_LOCKOUT_SECONDS", "3600"))   # 1 hour
LOCKOUT_MAX_FAILS = int(_env("LOGIN_MAX_FAILS",       "1"))      # 1 wrong → lock


def _now():
    return time.time()


def login_lock_remaining(chat_id):
    """Seconds remaining on the lockout, or 0 if not locked."""
    rec = LOGIN_ATTEMPTS.get(chat_id)
    if not rec:
        return 0
    rem = int(rec.get("locked_until", 0) - _now())
    return rem if rem > 0 else 0


def login_register_failure(chat_id):
    """Record a failed attempt. Returns (locked_bool, seconds_locked)."""
    rec = LOGIN_ATTEMPTS.setdefault(chat_id, {"fails": 0, "locked_until": 0.0})
    rec["fails"] = int(rec.get("fails", 0)) + 1
    if rec["fails"] >= LOCKOUT_MAX_FAILS:
        rec["locked_until"] = _now() + LOCKOUT_SECONDS
    save_login_attempts()
    return rec["locked_until"] > _now(), max(0, int(rec["locked_until"] - _now()))


def login_clear(chat_id):
    """Reset the failure counter after a successful login."""
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
    entry = {"ts": int(time.time()), "by": int(by),
             "action": str(action), "target": str(target)}
    ADMIN_LOG.append(entry)
    if len(ADMIN_LOG) > ADMIN_LOG_MAX:
        del ADMIN_LOG[:-ADMIN_LOG_MAX]
    save_log()


# ============================================================
# 5c. TOKEN SYSTEM PERSISTENCE + LOGIC
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
    print(f"🎟 Loaded {len(USER_TOKENS)} token records · {len(REF_INDEX)} refs")


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


def tokens_has_instant(uid):
    _ensure_user_tokens(uid)
    return USER_TOKENS[uid]["instant_until"] > time.time()


def tokens_instant_seconds_left(uid):
    _ensure_user_tokens(uid)
    return max(0, int(USER_TOKENS[uid]["instant_until"] - time.time()))


def tokens_spend(uid, amount):
    _ensure_user_tokens(uid)
    if tokens_has_instant(uid):
        return True, tokens_balance(uid), "instant"
    bal = tokens_apply_regen(uid)
    if bal < amount:
        save_tokens()
        return False, bal, "insufficient"
    USER_TOKENS[uid]["tokens"] = bal - amount
    save_tokens()
    return True, USER_TOKENS[uid]["tokens"], "ok"


def tokens_add(uid, amount):
    _ensure_user_tokens(uid)
    rec = USER_TOKENS[uid]
    rec["tokens"] = min(rec["max"], rec["tokens"] + amount)
    save_tokens()
    return rec["tokens"]


def tokens_extend_max(uid, extra):
    _ensure_user_tokens(uid)
    rec = USER_TOKENS[uid]
    rec["max"] += extra
    rec["max_upgraded"] = True
    rec["tokens"] = min(rec["max"], rec["tokens"] + extra)
    save_tokens()
    return rec["max"]


def tokens_upgrade_regen(uid):
    _ensure_user_tokens(uid)
    rec = USER_TOKENS[uid]
    if rec["regen_upgraded"]:
        return False, rec["regen_ms"]
    rec["regen_ms"] = TOKEN_CFG["upgraded_regen_ms"]
    rec["regen_upgraded"] = True
    save_tokens()
    return True, rec["regen_ms"]


def tokens_grant_instant(uid, minutes):
    _ensure_user_tokens(uid)
    rec = USER_TOKENS[uid]
    rec["instant_until"] = max(rec["instant_until"], time.time()) + minutes * 60
    save_tokens()
    return rec["instant_until"]


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

    milestone = None
    if rec["refs"] >= TOKEN_CFG["ref_milestone_instant"] and rec["instant_until"] <= time.time():
        tokens_grant_instant(referrer_uid, TOKEN_CFG["instant_minutes"])
        milestone = f"instant_{TOKEN_CFG['instant_minutes']}min"
    if rec["refs"] >= TOKEN_CFG["ref_milestone_regen"] and not rec["regen_upgraded"]:
        tokens_upgrade_regen(referrer_uid)
        milestone = "regen_upgrade"

    save_tokens()
    return True, rec["refs"], milestone


# ============================================================
# 5b. OWNER JSON FILE MANAGER
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
        admin_send_message(bot_number, chat_id, "❌ Unknown file.", owner_files_keyboard())
        return
    if not os.path.exists(path):
        admin_send_message(bot_number, chat_id,
                           f"❌ File not found: <code>{fname}</code>", owner_files_keyboard())
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        except Exception:
            pretty = raw
    except Exception as e:
        admin_send_message(bot_number, chat_id, f"❌ Read failed: {e}", owner_files_keyboard())
        return

    header = f"📄 <b>{fname}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    body = _escape_html(pretty)
    chunk = 3500
    if len(body) <= chunk:
        admin_send_message(bot_number, chat_id, header + f"<pre>{body}</pre>",
                           owner_files_keyboard())
        return
    admin_send_message(bot_number, chat_id, header + "<i>(truncated preview)</i>")
    for i in range(0, len(body), chunk):
        admin_send_message(bot_number, chat_id, f"<pre>{body[i:i + chunk]}</pre>")
    admin_send_message(bot_number, chat_id, "✅ End of preview.", owner_files_keyboard())


def _owner_download_file(bot_number, chat_id, fname):
    path = JSON_FILE_MAP.get(fname)
    if not path or not os.path.exists(path):
        admin_send_message(bot_number, chat_id,
                           f"❌ File not found: <code>{fname}</code>", owner_files_keyboard())
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
        admin_send_message(bot_number, chat_id, f"❌ ZIP failed: {e}", owner_files_keyboard())


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
# 8. i18n — ALL 32 languages
# ============================================================
LANGUAGES = {
    "en": "🇬🇧 English", "hi": "🇮🇳 हिन्दी", "bn": "🇧🇩 বাংলা", "ur": "🇵🇰 اردو",
    "ar": "🇸🇦 العربية", "es": "🇪🇸 Español", "fr": "🇫🇷 Français", "de": "🇩🇪 Deutsch",
    "pt": "🇧🇷 Português", "ru": "🇷🇺 Русский", "zh": "🇨🇳 中文", "ja": "🇯🇵 日本語",
    "ko": "🇰🇷 한국어", "id": "🇮🇩 Indonesia", "tr": "🇹🇷 Türkçe", "fa": "🇮🇷 فارسی",
    "it": "🇮🇹 Italiano", "vi": "🇻🇳 Tiếng Việt", "th": "🇹🇭 ไทย", "ta": "🇮🇳 தமிழ்",
    "te": "🇮🇳 తెలుగు", "mr": "🇮🇳 मराठी", "gu": "🇮🇳 ગુજરાતી", "pa": "🇮🇳 ਪੰਜਾਬੀ",
    "ml": "🇮🇳 മലയാളം", "nl": "🇳🇱 Nederlands", "pl": "🇵🇱 Polski", "uk": "🇺🇦 Українська",
    "ro": "🇷🇴 Română", "sw": "🇰🇪 Kiswahili", "ms": "🇲🇾 Bahasa Melayu", "fil": "🇵🇭 Filipino",
}

_LANG_ALIASES = {
    "en": "en", "en-us": "en", "en-gb": "en", "hi": "hi", "hi-in": "hi",
    "bn": "bn", "bn-bd": "bn", "bn-in": "bn", "ur": "ur", "ur-pk": "ur",
    "ar": "ar", "ar-sa": "ar", "ar-eg": "ar", "es": "es", "es-es": "es", "es-mx": "es",
    "fr": "fr", "fr-fr": "fr", "de": "de", "de-de": "de",
    "pt": "pt", "pt-br": "pt", "pt-pt": "pt", "ru": "ru", "ru-ru": "ru",
    "zh": "zh", "zh-cn": "zh", "zh-hans": "zh", "zh-tw": "zh", "zh-hant": "zh",
    "ja": "ja", "ja-jp": "ja", "ko": "ko", "ko-kr": "ko",
    "id": "id", "id-id": "id", "in": "id", "tr": "tr", "tr-tr": "tr",
    "fa": "fa", "fa-ir": "fa", "it": "it", "it-it": "it",
    "vi": "vi", "vi-vn": "vi", "th": "th", "th-th": "th",
    "ta": "ta", "ta-in": "ta", "te": "te", "te-in": "te",
    "mr": "mr", "mr-in": "mr", "gu": "gu", "gu-in": "gu",
    "pa": "pa", "pa-in": "pa", "ml": "ml", "ml-in": "ml",
    "nl": "nl", "nl-nl": "nl", "pl": "pl", "pl-pl": "pl",
    "uk": "uk", "uk-ua": "uk", "ro": "ro", "ro-ro": "ro",
    "sw": "sw", "sw-ke": "sw", "ms": "ms", "ms-my": "ms",
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
        "feature_unavailable": (
            "🚧 <b>F E A T U R E   C O M I N G   S O O N</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ This feature is <b>not available</b> right now.\n"
            "It is either under development or temporarily disabled.\n\n"
            "🔔 Please check back later!\n\n"
            "💬 <b>Support:</b> @Tony_M_unlock"
        ),
        "select_option": "❓ Please select an option from the menu.",
        "back": "🔙 Back",
        "cancel_btn": "❌ Cancel",
        "lang_btn": "🌐 Language",
        "support_btn": "💬 Support",
        "tokens_btn": "🪙 Tokens",
        "refer_btn": "🎁 Refer",
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
}

for _code in LANGUAGES:
    if _code not in TEXTS:
        TEXTS[_code] = dict(TEXTS["en"])


def t(lang, key, **kwargs):
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
        "url": "https://leakosintapi.com/",
        "prompt": "🪪 Send a 12-digit Aadhaar number.",
        "command": "/adhr",
    },
    "📞 Number Info": {
        "url": "https://leakosintapi.com/",
        "prompt": "📞 Send a 10-digit Indian number (with +91).\nExample: +919712073901",
        "command": "/num",
    },
    "📭 PIN Code": {
        "url": "https://ghost-pincode-lookup-api.vercel.app/api/pincode/india/",
        "prompt": "📭 Send a 6-digit PIN code.\nExample: 560076",
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
        "url": "https://leakosintapi.com/",
        "prompt": "🆔 Send a PAN number.",
        "command": "/pan",
    },
    "📱 TG Chat ID": {
        "url": "https://anon-tg-info.vercel.app/tgReg_beta?userid=",
        "prompt": "📱 Send a Telegram Chat ID.",
        "command": "/tgid2",
    },
    "💳 IFSC Info": {
        "url": "https://dass-api.netlify.app/api/ifsc?code=",
        "prompt": "💳 Send an 11-character IFSC code.\nExample: SBIN0000001",
        "command": "/ifsc",
    },
    "🏦 UPI Info": {
        "url": "https://upi-id-to-info-by-abhigyan.onrender.com/upi/",
        "prompt": "🏦 Send a UPI ID.",
        "command": "/upi",
    },
    "📧 Email Info": {
        "url": "https://leakosintapi.com/",
        "prompt": "📧 Send an email address.",
        "command": "/email",
    },
    "🌐 IP Info": {
        "url": "https://ipwho.is/",
        "prompt": "🌐 Send an IP address.\nExample: 9.9.9.9",
        "command": "/ip",
    },
}

COMMAND_FEATURE_MAP = {v["command"]: k for k, v in API_CONFIG.items() if v.get("command")}


def is_feature_available(api_name):
    """Return True only if the feature has a real, usable API endpoint."""
    cfg = API_CONFIG.get(api_name)
    if not cfg:
        return False
    url = (cfg.get("url") or "").strip()
    if not url:
        return False
    low = url.lower()
    if "your_" in low or "example.com" in low or "placeholder" in low or low in ("none", "null", "-"):
        return False
    return True


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


def send_chat_action(chat_id, action="typing"):
    try:
        HTTP.post(f"{USER_TG_API}/sendChatAction",
                  data={"chat_id": chat_id, "action": action},
                  timeout=(3, 6))
    except Exception:
        pass


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
    try:
        HTTP.post(f"{USER_TG_API}/leaveChat", data={"chat_id": chat_id}, timeout=(5, 10))
        print(f"👋 User bot left chat {chat_id}")
    except Exception as e:
        print(f"leaveChat err: {e}")


def leave_admin_chat(chat_id):
    try:
        ADMIN_HTTP.post(f"{ADMIN_TG_APIS[1]}/leaveChat", data={"chat_id": chat_id}, timeout=(5, 10))
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
    feature_order = [
        "🪪 Aadhaar Info", "🆔 PAN Info",
        "📞 Number Info",  "📧 Email Info",
        "🤖 TG Username",  "📱 TG Chat ID",
        "🏦 UPI Info",     "💳 IFSC Info",
        "📭 PIN Code",     "🌐 IP Info",
        "🚘 Vehicle Info"
    ]
    keyboard = [feature_order[i:i + 2] for i in range(0, len(feature_order), 2)]
    if len(keyboard[-1]) == 1:
        keyboard[-1].append(t(lang, "cancel_btn"))
    else:
        keyboard.append([t(lang, "cancel_btn")])
    keyboard.append([t(lang, "tokens_btn"), t(lang, "refer_btn")])
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


def token_status_keyboard(uid, lang="en"):
    rows = []
    rec = USER_TOKENS.get(uid) or {}
    if not rec.get("max_upgraded"):
        rows.append([{"text": f"💠 Extend Max (+{TOKEN_CFG['max_upgrade_add']}) — ${TOKEN_CFG['price_max_upgrade']}",
                      "callback_data": "tok:buy:max"}])
    if not rec.get("regen_upgraded"):
        rows.append([{"text": f"⚡ Fast Regen (0.5s) — ${TOKEN_CFG['price_regen']}",
                      "callback_data": "tok:buy:regen"}])
    rows.append([{"text": f"🔋 Instant Refill — ${TOKEN_CFG['price_refill']}",
                  "callback_data": "tok:buy:refill"}])
    rows.append([{"text": "🎁 My Referral Link", "callback_data": "tok:ref"}])
    rows.append([{"text": "❌ Close", "callback_data": "user:cancel"}])
    return {"inline_keyboard": rows}


def referral_inline_keyboard(uid):
    bot_un = _BOT_USERNAME_CACHE.get("username") or "YourBot"
    link = f"https://t.me/{bot_un}?start=ref_{uid}"
    rows = [
        [{"text": "🔗 Copy Referral Link", "copy_text": {"text": link}}],
        [{"text": "🔙 Back", "callback_data": "tok:status"}],
    ]
    return {"inline_keyboard": rows}


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
# 14. API CALL HELPER (with diagnostics)
# ============================================================
def call_api(url, query):
    print(f"🔍 [API] {url}  ←  query={query!r}")

    # ── Leakosint (POST) ──
    if url == "https://leakosintapi.com/":
        payload = {
            "token": LEAKOSINT_API_KEY,
            "request": query,
            "limit": 100,
            "lang": "en",
        }
        try:
            r = HTTP.post(url, json=payload, timeout=(API_CONNECT, API_READ))
            print(f"🔍 [Leakosint] HTTP {r.status_code} · {r.text[:300]}")
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                return {"ok": False, "error": f"Non-JSON: {r.text[:150]}"}
            if isinstance(data, dict) and "Error code" in data:
                return {"ok": False, "error": f"API error: {data['Error code']}"}
            return {"ok": True, "data": data}
        except requests.exceptions.Timeout:
            return {"ok": False, "error": "timeout"}
        except requests.exceptions.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── Generic GET ──
    full_url = url + quote_plus(query)
    try:
        r = HTTP.get(full_url, timeout=(API_CONNECT, API_READ))
        print(f"🔍 [GET] {full_url}")
        print(f"🔍 [GET] HTTP {r.status_code} · {r.text[:300]}")
        r.raise_for_status()
        try:
            return {"ok": True, "data": r.json()}
        except ValueError:
            return {"ok": True, "data": r.text[:3900]}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "timeout"}
    except requests.exceptions.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "connection failed"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _html_escape(s):
    return (str(s).replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))


def _pretty_field(k):
    return str(k).replace("_", " ").replace("-", " ").strip().title()


def _truncate(val, n=180):
    s = str(val).strip()
    if len(s) <= n:
        return s
    return s[:n] + "…"


def _format_leakosint_data(data):
    if not isinstance(data, dict):
        return f"<code>{_html_escape(str(data))[:500]}</code>"

    if "Error code" in data:
        return (f"❌ <b>API Error</b>\n"
                f"<code>{_html_escape(data['Error code'])}</code>")

    if "List" not in data:
        return "❌ <b>No results found.</b>"

    databases = data["List"]
    real_dbs = [k for k in databases if k != "No results found"]
    total_records = 0
    for d in real_dbs:
        info = databases[d] or {}
        records = info.get("Data") or []
        total_records += len(records)

    if not real_dbs or total_records == 0:
        return (
            "🔍 <b>N O   R E S U L T S</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Nothing was found in any database.\n\n"
            "💡 <i>Try a different query or format.</i>"
        )

    lines = [
        "🔍 <b>L E A K O S I N T   R E S U L T</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>{len(real_dbs)}</b> database(s) · <b>{total_records}</b> record(s)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for db_index, db_name in enumerate(real_dbs, start=1):
        db_info = databases.get(db_name) or {}
        records = db_info.get("Data") or []
        info_leak = (db_info.get("InfoLeak") or "").strip()

        lines.append("")
        lines.append(f"🗂 <b>[{db_index}/{len(real_dbs)}] {_html_escape(db_name)}</b>")
        lines.append(f"└ <i>{len(records)} record(s)</i>")

        if info_leak:
            lines.append(f"\n📝 <i>{_html_escape(_truncate(info_leak, 200))}</i>")

        if not records:
            lines.append("\n<i>(no records in this database)</i>")
            continue

        for r_index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                lines.append(f"\n  • <code>{_html_escape(_truncate(record))}</code>")
                continue

            clean_items = []
            for k, v in record.items():
                sval = "" if v is None else str(v).strip()
                if sval and sval.lower() not in ("none", "null", "n/a", "-"):
                    clean_items.append((k, sval))

            if not clean_items:
                lines.append(f"\n<b>📄 Record {r_index}</b> — <i>empty</i>")
                continue

            lines.append(f"\n<b>📄 Record {r_index}</b>")
            for j, (k, v) in enumerate(clean_items):
                prefix = "├" if j < len(clean_items) - 1 else "└"
                field = _html_escape(_pretty_field(k))
                value = _html_escape(_truncate(v, 200))
                lines.append(f"{prefix} <b>{field}:</b> <code>{value}</code>")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Powered by LeakosintAPI</i>")

    return "\n".join(lines)


def _format_api_data(data, depth=0):
    if isinstance(data, dict) and "List" in data:
        return _format_leakosint_data(data)

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


def _send_long_or_file(chat_id, text, keyboard=None):
    max_len = 3900
    if len(text) <= max_len:
        send_message(chat_id, text, keyboard)
        return

    send_message(chat_id, text[:max_len])

    try:
        plain = (text
                 .replace("<b>", "").replace("</b>", "")
                 .replace("<i>", "").replace("</i>", "")
                 .replace("<code>", "").replace("</code>", "")
                 .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))

        buf = io.BytesIO(plain.encode("utf-8"))
        url = f"{USER_TG_API}/sendDocument"
        HTTP.post(url,
                  data={"chat_id": chat_id,
                        "caption": "📄 <b>Full result</b>",
                        "parse_mode": "HTML"},
                  files={"document": ("leakosint_result.txt", buf, "text/plain")},
                  timeout=(10, 60))
    except Exception as e:
        print("send full file err:", e)

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
# 16. TOKEN STATUS PANEL
# ============================================================
def _human_rate(ms):
    secs = ms / 1000.0
    if secs < 1:
        return f"{secs:.1f}s"
    if secs == int(secs):
        return f"{int(secs)}s"
    return f"{secs:.2f}s"


def _send_token_panel(chat_id, lang):
    uid = chat_id
    _ensure_user_tokens(uid)
    tokens_apply_regen(uid)
    rec = USER_TOKENS[uid]

    bal       = rec["tokens"]
    mx        = rec["max"]
    rate_ms   = rec["regen_ms"]
    refs      = rec["refs"]
    is_inst   = tokens_has_instant(uid)
    inst_left = tokens_instant_seconds_left(uid)
    next_in   = tokens_next_in_seconds(uid)
    upgraded  = rec.get("regen_upgraded", False)

    filled = int((bal / mx) * 20) if mx else 0
    filled = max(0, min(20, filled))
    bar = "█" * filled + "░" * (20 - filled)

    tier_line = ("🚀 <b>Fast Regen</b> · 0.5s/token" if upgraded
                 else "🐢 <b>Standard Regen</b> · 3s/token")

    lines = [
        "🎟 <b>Y O U R   T O K E N S</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔋 Balance:  <b>{bal}</b> / <b>{mx}</b>",
        f"<code>{bar}</code>",
        "",
        tier_line,
        f"⚡ Rate:     <b>1 token / {_human_rate(rate_ms)}</b>",
    ]
    if next_in > 0:
        lines.append(f"⏱ Next in:  <b>{next_in}s</b>")
    else:
        lines.append("✅ <i>Tank is full</i>")

    if is_inst:
        m, s = divmod(inst_left, 60)
        lines.append(f"🚀 <b>Instant Search</b> active · {m}m {s}s left")

    lines.append("")
    lines.append(f"🎁 Referrals:   <b>{refs}</b>")
    lines.append(f"💰 Search cost: <b>{TOKEN_CFG['search_cost']} tokens</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

    send_message(chat_id, "\n".join(lines), token_status_keyboard(uid, lang))


# ============================================================
# 17. USER CALLBACK HANDLER
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

    # ── TOKEN SYSTEM CALLBACKS ──
    if data == "tok:status":
        answer_callback(cb_id)
        _send_token_panel(chat_id, lang)
        return

    if data.startswith("tok:buy:"):
        answer_callback(cb_id)
        kind = data.split(":", 2)[2]

        purchase_kb = {
            "inline_keyboard": [
                [{"text": "💬 Contact Support", "url": SUPPORT_URL}],
                [{"text": "🔙 Back to Tokens", "callback_data": "tok:status"}]
            ]
        }

        payment_notice = (
            "⚠️ <b>Payment Notice:</b>\n"
            "The automatic payment method is currently <b>not working</b>.\n"
            "Please contact our support team directly to complete your purchase.\n\n"
            f"💬 <b>Support:</b> {SUPPORT_HANDLE}"
        )

        if kind == "max":
            price = TOKEN_CFG["price_max_upgrade"]
            add = TOKEN_CFG["max_upgrade_add"]
            send_message(
                chat_id,
                f"💠 <b>Token Capacity Upgrade</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"➕ Increase max by <b>+{add}</b> tokens\n"
                f"🔋 One-time purchase — permanent\n"
                f"💵 Price: <b>${price} USD</b>\n\n"
                f"{payment_notice}",
                purchase_kb,
            )
        elif kind == "regen":
            price = TOKEN_CFG["price_regen"]
            send_message(
                chat_id,
                f"⚡ <b>Regen Speed Upgrade</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🐢 <b>Current:</b> 1 token every <b>3s</b>\n"
                f"🚀 <b>Upgraded:</b> 1 token every <b>0.5s</b>\n"
                f"    ↳ That's <b>6× faster</b> refilling!\n\n"
                f"💵 Price: <b>${price} USD</b>\n\n"
                f"{payment_notice}",
                purchase_kb,
            )
        elif kind == "refill":
            price = TOKEN_CFG["price_refill"]
            send_message(
                chat_id,
                f"🔋 <b>Instant Refill</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"♻️ Fills your tokens back to <b>max instantly</b>\n"
                f"⏱ No waiting for regen\n"
                f"💵 Price: <b>${price} USD</b>\n\n"
                f"{payment_notice}",
                purchase_kb,
            )
        return

    if data == "tok:ref":
        answer_callback(cb_id)
        uid = chat_id
        _ensure_user_tokens(uid)
        bot_un = _BOT_USERNAME_CACHE.get("username") or "YourBot"
        link = f"https://t.me/{bot_un}?start=ref_{uid}"
        refs = USER_TOKENS.get(uid, {}).get("refs", 0)
        need1 = max(0, TOKEN_CFG["ref_milestone_instant"] - refs)
        need2 = max(0, TOKEN_CFG["ref_milestone_regen"] - refs)

        def _status_line(need):
            return f"  <i>({need} more to unlock)</i>" if need > 0 else "  <i>✅ Unlocked!</i>"

        text = (
            "🎁 <b>R E F E R   &amp;   E A R N</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Your referrals: <b>{refs}</b>\n\n"
            "🎯 <b>Reward Milestones</b>\n\n"
            f"🚀 <b>Instant Search</b>\n"
            f"   Invite <b>{TOKEN_CFG['ref_milestone_instant']}</b> friends → "
            f"free instant searches for <b>{TOKEN_CFG['instant_minutes']} min</b>\n"
            f"{_status_line(need1)}\n\n"
            f"⚡ <b>Fast Regen Upgrade</b>\n"
            f"   Invite <b>{TOKEN_CFG['ref_milestone_regen']}</b> friends → "
            f"regen from 3s → <b>0.5s</b> per token\n"
            f"{_status_line(need2)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔗 <b>Your invite link:</b>\n"
            f"<code>{link}</code>\n\n"
            "<i>Tap the button below to copy it.</i>"
        )
        send_message(chat_id, text, referral_inline_keyboard(uid))
        return


# ============================================================
# 18. USER MESSAGE HANDLER
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


def _handle_search(chat_id, lang, api_name, query):
    """Shared search logic for both flow-state and /command paths."""
    cfg = API_CONFIG.get(api_name)
    if not cfg:
        USER_STATE.pop(chat_id, None)
        send_message(chat_id, t(lang, "select_option"), main_keyboard(lang))
        return

    # ── Reject unconfigured / placeholder APIs ──
    if not is_feature_available(api_name):
        USER_STATE.pop(chat_id, None)
        send_message(chat_id, t(lang, "feature_unavailable"), main_keyboard(lang))
        return

    cost = TOKEN_CFG["search_cost"]
    ok, new_bal, reason = tokens_spend(chat_id, cost)
    if not ok:
        next_in = tokens_next_in_seconds(chat_id)
        send_message(
            chat_id,
            f"🪫 <b>Not enough tokens</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎟 Balance: <b>{new_bal}</b> / <b>{USER_TOKENS[chat_id]['max']}</b>\n"
            f"💵 Cost per search: <b>{cost}</b>\n"
            f"⏱ Next token in <b>{next_in}s</b>\n\n"
            f"⚡ Tired of waiting? Tap <b>🪙 Tokens</b> below to buy a\n"
            f"regen upgrade or instant refill.",
            main_keyboard(lang),
        )
        USER_STATE.pop(chat_id, None)
        return

    send_message(chat_id, t(lang, "searching"))
    send_chat_action(chat_id, "typing")
    res = call_api(cfg["url"], query)

    if res["ok"]:
        body = res["data"]
        formatted = _format_api_data(body)
        _send_long_or_file(chat_id, formatted, main_keyboard(lang))
        if reason != "instant":
            rec = USER_TOKENS.get(chat_id) or {}
            send_message(
                chat_id,
                f"🎟 <i>Balance: {rec.get('tokens',0)} / {rec.get('max',0)} · Cost: {cost}</i>",
            )
    else:
        tokens_add(chat_id, cost)  # refund
        err = res.get("error", "unknown")
        send_message(
            chat_id,
            f"❌ <b>Search failed</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔧 Reason: <code>{_html_escape(err)}</code>\n\n"
            f"♻️ Your {cost} tokens were <b>refunded</b>.\n\n"
            f"💬 If this keeps happening, contact {SUPPORT_HANDLE}",
            main_keyboard(lang),
        )
    USER_STATE.pop(chat_id, None)


def process_update(update):
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

    if chat.get("type") != "private":
        return

    user_id = msg.get("from", {}).get("id", chat_id)

    LAST_SEEN[chat_id] = time.time()
    _ensure_user_tokens(chat_id)

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

    if text == "/start" or text.startswith("/start "):
        USER_STATE.pop(chat_id, None)

        parts_start = text.split()
        if len(parts_start) > 1 and parts_start[1].startswith("ref_"):
            try:
                referrer_uid = int(parts_start[1][4:])
                if referrer_uid and referrer_uid != chat_id:
                    ok, refs, milestone = refs_register(chat_id, referrer_uid)
                    if ok:
                        try:
                            send_message(
                                referrer_uid,
                                f"🎉 <b>New Referral!</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"👥 Total referrals: <b>{refs}</b>",
                            )
                            if milestone and isinstance(milestone, str) and milestone.startswith("instant_"):
                                send_message(
                                    referrer_uid,
                                    f"🚀 <b>Milestone Unlocked!</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"Instant search is now active for "
                                    f"<b>{TOKEN_CFG['instant_minutes']} minutes</b>!",
                                )
                            elif milestone == "regen_upgrade":
                                send_message(
                                    referrer_uid,
                                    "⚡ <b>Milestone Unlocked!</b>\n"
                                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    "Your regen speed is now <b>0.5s/token</b> — "
                                    "6× faster than standard!",
                                )
                        except Exception as e:
                            print("referral notify err:", e)
            except Exception as e:
                print("ref parse err:", e)

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

    # ── Token & Refer buttons ──
    if text == "/tokens" or is_button(text, "tokens_btn", lang):
        _send_token_panel(chat_id, lang)
        return

    if text == "/refer" or is_button(text, "refer_btn", lang):
        process_callback({"data": "tok:ref", "message": msg, "id": None})
        return

    if text in ("/help", "/commands"):
        help_text = (
            "📖 <b>C O M M A N D   G U I D E</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>🔍 Search Commands</b>\n"
            "• /adhr &lt;12-digit&gt; — Aadhaar Info\n"
            "• /num &lt;number&gt; — Phone Number Info\n"
            "• /pan &lt;PAN&gt; — PAN Card Info\n"
            "• /email &lt;email&gt; — Email Info\n"
            "• /pin &lt;code&gt; — PIN Code Info\n"
            "• /vech &lt;vehicle&gt; — Vehicle Info\n"
            "• /ifsc &lt;code&gt; — IFSC Info\n"
            "• /upi &lt;id&gt; — UPI Info\n"
            "• /ip &lt;address&gt; — IP Info\n"
            "• /tgid &lt;username&gt; — TG Username\n"
            "• /tgid2 &lt;id&gt; — TG Chat ID\n\n"
            "<b>🎟 Tokens &amp; Referrals</b>\n"
            "• /tokens — View balance &amp; shop\n"
            "• /refer — Your referral link\n\n"
            "<b>⚙️ Other</b>\n"
            "• /start — Restart\n"
            "• /lang — Change language\n"
            "• /cancel — Cancel current action\n"
            "• /support — Contact support\n\n"
            f"💰 <i>Each search costs {TOKEN_CFG['search_cost']} tokens</i>\n\n"
            f"💬 <b>Support:</b> {SUPPORT_HANDLE}"
        )
        send_message(chat_id, help_text, main_keyboard(lang))
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

    # ── Flow-state search (user selected a feature, waiting for input) ──
    state = USER_STATE.get(chat_id, {})
    if state.get("flow") == "api_query":
        is_other_action = False
        if text.startswith("/"):
            is_other_action = True
        elif next((name for name in API_CONFIG if name.strip() == text.strip()), None) is not None:
            is_other_action = True
        elif (is_button(text, "cancel_btn", lang) or is_button(text, "lang_btn", lang) or
              is_button(text, "support_btn", lang) or is_button(text, "tokens_btn", lang) or
              is_button(text, "refer_btn", lang)):
            is_other_action = True

        if is_other_action:
            USER_STATE.pop(chat_id, None)
            # fall through to normal handlers
        else:
            api_name = state.get("api_name")
            _handle_search(chat_id, lang, api_name, text)
            return

    # ── Slash command search ──
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        feature = COMMAND_FEATURE_MAP.get(cmd)
        if feature:
            if not is_feature_available(feature):
                send_message(chat_id, t(lang, "feature_unavailable"),
                             main_keyboard(lang))
                return
            query = parts[1].strip() if len(parts) > 1 else ""
            cfg = API_CONFIG[feature]
            if not query:
                send_message(chat_id, f"{cfg['prompt']}\n\n{t(lang, 'send_cancel')}",
                             main_keyboard(lang))
                return
            _handle_search(chat_id, lang, feature, query)
            return

    # ── Feature button (start flow) ──
    api_name = next((name for name in API_CONFIG if name.strip() == text.strip()), None)
    if api_name is not None:
        if not is_feature_available(api_name):
            send_message(chat_id, t(lang, "feature_unavailable"),
                         main_keyboard(lang))
            return
        cfg = API_CONFIG[api_name]
        USER_STATE[chat_id] = {"flow": "api_query", "api_name": api_name}
        send_message(chat_id, cfg["prompt"] + "\n\n" + t(lang, "send_cancel"),
                     main_keyboard(lang))
        return

    send_message(chat_id, t(lang, "select_option"), main_keyboard(lang))


# ============================================================
# 19. ADMIN BOT HELPERS
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
# 20. ADMIN CALLBACK ROUTER
# ============================================================
def _fmt_user_line(uid):
    lang = USER_LANGS.get(uid, "en")
    ban = "🚫" if uid in BANNED_USERS else ""
    seen = LAST_SEEN.get(uid)
    seen_str = time.strftime("%m-%d %H:%M", time.localtime(seen)) if seen else "—"
    rec = USER_TOKENS.get(uid) or {}
    tok = f"{rec.get('tokens', 0)}/{rec.get('max', 0)}" if rec else "—"
    return f"🆔{ban} <code>{uid}</code> · {lang} · 🎟{tok} · seen {seen_str}"


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
                           f"🎟 Token records: <b>{len(USER_TOKENS)}</b>\n"
                           f"🎁 Referrals: <b>{len(REF_INDEX)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>\n"
                           f"━━━━━━━━━━━━━━━━━━━━━━━━━",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if data == "admin:whoami":
        admin_answer_callback(bot_number, cb_id)
        admin_send_message(bot_number, chat_id,
                           f"🆔 <code>{chat_id}</code>\n"
                           f"🏷 Role: <b>{role_badge(chat_id)}</b>\n"
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>",
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
# 21. ADMIN COMMAND HANDLER
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
                           "<b>🎟 Tokens</b>\n"
                           "/tokens ID · /give_tokens ID N\n"
                           "/upgrade_regen ID · /upgrade_max ID\n"
                           "/grant_instant ID [min]\n\n"
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
                           f"🛠 Maintenance: <b>{'ON' if MAINTENANCE_MODE else 'OFF'}</b>",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/logout":
        if is_owner(chat_id):
            admin_send_message(bot_number, chat_id, "❌ Owner can't logout.",
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
            w.writerow(["user_id", "lang", "banned", "last_seen", "notes",
                        "tokens", "max", "refs", "instant_until"])
            for uid in sorted(LAST_SEEN.keys()):
                lang = USER_LANGS.get(uid, "en")
                banned = "yes" if uid in BANNED_USERS else "no"
                seen = LAST_SEEN.get(uid)
                seen_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(seen)) if seen else ""
                notes = " | ".join(n["text"] for n in USER_NOTES.get(uid, []))
                rec = USER_TOKENS.get(uid, {})
                w.writerow([uid, lang, banned, seen_str, notes,
                            rec.get("tokens", ""), rec.get("max", ""),
                            rec.get("refs", ""), rec.get("instant_until", "")])
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

    # ── TOKEN ADMIN COMMANDS ──
    if cmd == "/tokens":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /tokens USER_ID",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        _ensure_user_tokens(target)
        tokens_apply_regen(target)
        rec = USER_TOKENS[target]
        instant_line = "—"
        if rec["instant_until"] > time.time():
            instant_line = time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(rec["instant_until"]))
        tier = "🚀 Fast (0.5s)" if rec["regen_upgraded"] else "🐢 Standard (3s)"
        admin_send_message(bot_number, chat_id,
                           f"🎟 <b>Tokens — <code>{target}</code></b>\n"
                           f"Balance: <b>{rec['tokens']} / {rec['max']}</b>\n"
                           f"Regen tier: <b>{tier}</b>\n"
                           f"Referrals: <b>{rec['refs']}</b>\n"
                           f"Instant until: <code>{instant_line}</code>",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/give_tokens":
        if len(args) != 3:
            admin_send_message(bot_number, chat_id, "Usage: /give_tokens USER_ID AMOUNT",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            target = int(args[1]); amount = int(args[2])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid args.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        new_bal = tokens_add(target, amount)
        log_admin(chat_id, "give_tokens", f"{target} +{amount}")
        admin_send_message(bot_number, chat_id,
                           f"✅ Added {amount} tokens to <code>{target}</code>.\n"
                           f"New balance: <b>{new_bal}</b>",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/upgrade_regen":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /upgrade_regen USER_ID",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        done, new_ms = tokens_upgrade_regen(target)
        log_admin(chat_id, "upgrade_regen", target)
        msg_out = (f"✅ Regen upgraded for <code>{target}</code> → "
                   f"<b>1 / {_human_rate(new_ms)}</b>."
                   if done else
                   f"ℹ️ <code>{target}</code> already upgraded.")
        admin_send_message(bot_number, chat_id, msg_out,
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/upgrade_max":
        if len(args) != 2:
            admin_send_message(bot_number, chat_id, "Usage: /upgrade_max USER_ID",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            target = int(args[1])
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid ID.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        new_max = tokens_extend_max(target, TOKEN_CFG["max_upgrade_add"])
        log_admin(chat_id, "upgrade_max", target)
        admin_send_message(bot_number, chat_id,
                           f"✅ Max tokens for <code>{target}</code> is now <b>{new_max}</b>.",
                           build_admin_main_keyboard(is_owner(chat_id)))
        return

    if cmd == "/grant_instant":
        if len(args) < 2:
            admin_send_message(bot_number, chat_id, "Usage: /grant_instant USER_ID [MINUTES]",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        try:
            target = int(args[1])
            minutes = int(args[2]) if len(args) > 2 else TOKEN_CFG["instant_minutes"]
        except ValueError:
            admin_send_message(bot_number, chat_id, "❌ Invalid.",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return
        tokens_grant_instant(target, minutes)
        log_admin(chat_id, "grant_instant", f"{target} +{minutes}m")
        admin_send_message(bot_number, chat_id,
                           f"🚀 Instant search granted to <code>{target}</code> for {minutes} min.",
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
        rec = USER_TOKENS.get(target) or {}
        tok_line = f"🎟 Tokens: <b>{rec.get('tokens', 0)} / {rec.get('max', 0)}</b>"
        ref_line = f"🎁 Referrals: <b>{rec.get('refs', 0)}</b>"
        admin_send_message(bot_number, chat_id,
                           f"👤 <b>USER INFO</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                           f"🆔 <code>{target}</code>\n"
                           f"🏷 Role: <b>{role_badge(target)}</b>\n"
                           f"🌐 Lang: {lang}\n"
                           f"{tok_line}\n"
                           f"{ref_line}\n"
                           f"{ban_line}\n"
                           f"👀 Last seen: {seen_str}\n━━━━━━━━━━━━━━━━━━━━━━━━━",
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
# 22. ADMIN UPDATE ROUTER
# ============================================================
def process_admin_update(bot_number, update):
    global CURRENT_PASSWORD

    if "my_chat_member" in update:
        mcm = update["my_chat_member"]
        chat = mcm.get("chat") or {}
        if chat.get("type") != "private":
            new_status = (mcm.get("new_chat_member") or {}).get("status")
            if new_status in ("member", "administrator"):
                leave_admin_chat(chat.get("id"))
        return

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

        # ── 1) If user is locked out, block everything except /start-style info ──
        remaining = login_lock_remaining(chat_id)
        if remaining > 0:
            if text in ("/start", "/help", "/whoami", "/id"):
                admin_send_message(
                    bot_number, chat_id,
                    f"🔐 <b>Admin Bot</b>\n\n"
                    f"🆔 Your Chat ID: <code>{chat_id}</code>\n\n"
                    f"🚫 <b>Too many wrong passwords.</b>\n"
                    f"⏳ Try again in <b>{_fmt_duration(remaining)}</b>."
                )
                return
            # any attempt (including a correct password) is refused during lockout
            admin_send_message(
                bot_number, chat_id,
                f"🚫 <b>Login temporarily blocked</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"You entered the wrong admin password too many times.\n\n"
                f"⏳ You can retry in <b>{_fmt_duration(remaining)}</b>.\n\n"
                f"💬 Need help? Contact the owner."
            )
            return

        # ── 2) Info commands ──
        if text in ("/start", "/help", "/whoami", "/id"):
            admin_send_message(bot_number, chat_id,
                               f"🔐 <b>Admin Bot</b>\n\n"
                               f"🆔 Your Chat ID: <code>{chat_id}</code>\n\n"
                               f"Send <code>/login YOUR_ADMIN_PASSWORD</code>\n\n"
                               f"💡 Owner: set OWNER_ID=<code>{chat_id}</code> in env "
                               f"to auto-login.")
            return

        # ── 3) /login PASSWORD ──
        if text.startswith("/login"):
            parts = text.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                admin_send_message(bot_number, chat_id,
                                   "Usage: <code>/login YOUR_PASSWORD</code>")
                return
            supplied = parts[1].strip()
            if supplied == CURRENT_PASSWORD or supplied in (ADMIN_BOT_TOKEN, USER_BOT_TOKEN):
                login_clear(chat_id)
                DYNAMIC_ADMINS.add(chat_id); save_admins()
                log_admin(chat_id, "login")
                admin_send_message(bot_number, chat_id,
                                   f"✅ <b>Login OK.</b>\n🏷 Role: <b>{role_badge(chat_id)}</b>",
                                   build_admin_main_keyboard(is_owner(chat_id)))
                return
            locked, secs = login_register_failure(chat_id)
            if locked:
                admin_send_message(
                    bot_number, chat_id,
                    f"❌ <b>Incorrect password.</b>\n\n"
                    f"🚫 You are now locked out for <b>{_fmt_duration(secs)}</b>.\n"
                    f"Please try again later."
                )
            else:
                admin_send_message(bot_number, chat_id, "❌ Incorrect password.")
            return

        # ── 4) Bare password as message ──
        if text and text == CURRENT_PASSWORD:
            login_clear(chat_id)
            DYNAMIC_ADMINS.add(chat_id); save_admins()
            log_admin(chat_id, "login (bare)")
            admin_send_message(bot_number, chat_id,
                               f"✅ <b>Login OK.</b>\n🏷 Role: <b>{role_badge(chat_id)}</b>",
                               build_admin_main_keyboard(is_owner(chat_id)))
            return

        # Bare wrong text — treat as a failed login attempt too
        if text and not text.startswith("/") and len(text) <= 128:
            locked, secs = login_register_failure(chat_id)
            if locked:
                admin_send_message(
                    bot_number, chat_id,
                    f"❌ <b>Incorrect password.</b>\n\n"
                    f"🚫 You are now locked out for <b>{_fmt_duration(secs)}</b>.\n"
                    f"Please try again later."
                )
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
# 23. USER BOT POLLING
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
# 24. SELF-PING
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
# 25. BOOTSTRAP
# ============================================================
def _hook_health_runtime():
    _RUNTIME["total_users"]  = lambda: len(LAST_SEEN)
    _RUNTIME["banned_users"] = lambda: len(BANNED_USERS)
    _RUNTIME["maintenance"]  = lambda: MAINTENANCE_MODE


def main():
    if not USER_BOT_TOKEN or "YOUR_USER_BOT_TOKEN" in USER_BOT_TOKEN:
        print("ERROR: USER_BOT_TOKEN missing.")
        return

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
    _hook_health_runtime()

    threading.Thread(target=run_flask, daemon=True).start()

    try:
        r = HTTP.get(f"{USER_TG_API}/getMe", timeout=(5, 10))
        info = r.json()
        if not info.get("ok"):
            print("ERROR: Invalid USER token.")
            return
        _BOT_USERNAME_CACHE["username"] = info["result"]["username"]
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
    print("Bot started · owner:", OWNER_ID or "(unset)")
    print("Support:", SUPPORT_HANDLE)
    print(f"🎟 Token system: {TOKEN_CFG['start_balance']}/{TOKEN_CFG['default_max']} · "
          f"cost {TOKEN_CFG['search_cost']} · "
          f"searches/tank {TOKEN_CFG['default_max'] // TOKEN_CFG['search_cost']}")
    print(f"   · regen default {_human_rate(TOKEN_CFG['default_regen_ms'])} · "
          f"upgraded {_human_rate(TOKEN_CFG['upgraded_regen_ms'])}")
    print(f"🎁 Referrals: {TOKEN_CFG['ref_milestone_instant']} refs → "
          f"{TOKEN_CFG['instant_minutes']}min instant · "
          f"{TOKEN_CFG['ref_milestone_regen']} refs → fast regen")
    print(f"🆓 Free-mode token gate: ON · Admins: {len(DYNAMIC_ADMINS)} · "
          f"Banned: {len(BANNED_USERS)}")
    print("=" * 60)

    user_bot_loop()


if __name__ == "__main__":
    main()
