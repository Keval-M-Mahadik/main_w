import os
import json
import time
import threading
import requests

from flask import Flask
from urllib.parse import quote_plus


# ============================================================
# BOT CONFIGURATION
# ============================================================

USER_BOT_TOKEN = os.getenv("USER_BOT_TOKEN", "8771414496:AAEZYXZa3TXHPcJoEYxHmI105c60F8VSYxo").strip()

GROUP_URL = "https://t.me/Dark911_osint"
CHANNEL_URL = "https://t.me/Cyber_Warriors_22"

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "-1004344445959").strip()
CHANNEL_CHAT_ID = os.getenv("CHANNEL_CHAT_ID", "-1004360588658").strip()


# ============================================================
# RENDER WEB SERVER
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot status: Active"

def run_server():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

threading.Thread(target=run_server, daemon=True).start()


# ============================================================
# API CONFIGURATION
# ============================================================

API_CONFIG = {
    "🪪 Aadhaar Info ": {
        "url": "https://anon-num-info.vercel.app/aadhar?key=aadhar28008&id=",
        "prompt": "🪪 Send a 12 Digit Aadhaar Number"
    },
    "📞 Number Info ": {
        "url": "https://anon-num-info.vercel.app/num?key=temp2808num&num=",
        "prompt": "📞 Send a 10 Digit Indian Number (Without +91)"
    },
    "📍PIN Code Lookup": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "📍 Send PIN code"
    },
    "🚘 Vehicle Info": {
        "url": "https://parivahan-x.paskhinpf9.workers.dev/?vehicle=",
        "prompt": "🚘 Send Vehicle Number"
    },
    "🚘 Vehicle Info 2.0": {
        "url": "https://parivahan-x.paskhinpf9.workers.dev/?vehicle=",
        "prompt": "🚘 Send Vehicle Number 2.0"
    },
    "🤖 Telegram ID / Username ": {
        "url": "https://anon-tg-info.vercel.app/telegram?key=temp1750&username=",
        "prompt": "🤖 Send Telegram username"
    },
    "🆔 PAN Info ": {
        "url": "https://paninfo.noob73613.workers.dev/pan?pan=",
        "prompt": "🆔 Send PAN reference"
    },
    "📱 Telegram Chat ID ": {
        "url": "https://anon-tg-info.vercel.app/tgReg_beta?userid=",
        "prompt": "📱 Send Chat ID"
    },
    "💳 IFSC Info ": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "💳 Send IFSC code"
    },
    "🏦 UPI INFO ": {
        "url": "https://upi-id-to-info-by-abhigyan.onrender.com/upi/<UPI_ID>",
        "prompt": "🏦 Send UPI ID"
    },
    "🇵🇰 Pakistan Number Info ": {
        "url": "https://YOUR-AUTHORIZED-API.example/pakistan-number?query=",
        "prompt": "coming soon"
    },
    "📧 Advanced Email Info ": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "📧 Send email"
    },
    "📈 GST Info Advanced": {
        "url": "https://YOUR-AUTHORIZED-API.example/gst?query=",
        "prompt": "coming soon"
    },
    "🌐 IP Address Info": {
        "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
        "prompt": "🌐 Send IP address"
    },
    "👤 Instagram Username Info": {
        "url": "https://anon-social-info.vercel.app/igdl?&url=",
        "prompt": "coming soon"
    }
}


# ============================================================
# COMMAND MAPPING
# ============================================================

COMMAND_FEATURE_MAP = {
    "/num":    "📞 Number Info ",
    "/tgid":   "🤖 Telegram ID / Username ",
    "/pan":    "🆔 PAN Info ",
    "/adhr":   "🪪 Aadhaar Info ",
    "/vech":   "🚘 Vehicle Info",
    "/vechrc": "🚘 Vehicle Info 2.0",
    "/upi":    "🏦 UPI INFO ",
    "/ifsc":   "💳 IFSC Info ",
    "/ip":     "🌐 IP Address Info",
    "/pin":    "📍PIN Code Lookup",
    "/insta":  "👤 Instagram Username Info"
}

FEATURE_TO_COMMAND = {v: k for k, v in COMMAND_FEATURE_MAP.items()}


# ============================================================
# HTTP SESSION
# ============================================================

HTTP = requests.Session()
HTTP.headers.update({
    "Connection": "keep-alive",
    "User-Agent": "TelegramMembershipBot/1.0"
})

TELEGRAM_CONNECT_TIMEOUT = 5
TELEGRAM_READ_TIMEOUT = 35
API_CONNECT_TIMEOUT = 3
API_READ_TIMEOUT = 12

TELEGRAM_API = "https://api.telegram.org/bot" + USER_BOT_TOKEN

# Cache bot ID for self-check
BOT_ID = None


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def send_message(chat_id, text, keyboard=None, parse_mode=None):
    url = TELEGRAM_API + "/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if keyboard is not None:
        data["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
    if parse_mode:
        data["parse_mode"] = parse_mode
    try:
        resp = HTTP.post(url, data=data, timeout=(TELEGRAM_CONNECT_TIMEOUT, TELEGRAM_READ_TIMEOUT))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("sendMessage error:", e)
        return None

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = HTTP.get(TELEGRAM_API + "/getUpdates", params=params,
                        timeout=(TELEGRAM_CONNECT_TIMEOUT, TELEGRAM_READ_TIMEOUT + 5))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("getUpdates error:", e)
        return None

def answer_callback_query(callback_id, text=""):
    if not callback_id:
        return
    try:
        HTTP.post(TELEGRAM_API + "/answerCallbackQuery",
                  data={"callback_query_id": callback_id, "text": text[:200], "show_alert": False},
                  timeout=(TELEGRAM_CONNECT_TIMEOUT, 10))
    except Exception as e:
        print("Callback error:", e)

def get_chat_member(chat_id, user_id):
    if not chat_id:
        return None
    try:
        resp = HTTP.get(TELEGRAM_API + "/getChatMember",
                        params={"chat_id": chat_id, "user_id": user_id},
                        timeout=(TELEGRAM_CONNECT_TIMEOUT, 10))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("getChatMember error:", e)
        return None

def is_member_status(member_result):
    if not member_result or not member_result.get("ok"):
        return False
    status = member_result.get("result", {}).get("status")
    return status in ("creator", "administrator", "member")

def bot_can_check_members():
    """Check if the bot itself is a member of both group and channel."""
    global BOT_ID
    if BOT_ID is None:
        try:
            me = HTTP.get(TELEGRAM_API + "/getMe", timeout=(5, 10))
            me.raise_for_status()
            BOT_ID = me.json().get("result", {}).get("id")
        except:
            return False
    if not BOT_ID:
        return False
    # Check group
    g = get_chat_member(GROUP_CHAT_ID, BOT_ID)
    if not is_member_status(g):
        return False
    # Check channel
    c = get_chat_member(CHANNEL_CHAT_ID, BOT_ID)
    if not is_member_status(c):
        return False
    return True

def check_user_joined(user_id):
    # First, ensure the bot itself is a member
    if not bot_can_check_members():
        return False, "⚠️ Bot is not a member of the group/channel. Please add the bot to both."
    # Now check the user
    if not GROUP_CHAT_ID:
        return False, "❌ GROUP_CHAT_ID not configured."
    if not CHANNEL_CHAT_ID:
        return False, "❌ CHANNEL_CHAT_ID not configured."

    group = get_chat_member(GROUP_CHAT_ID, user_id)
    if not is_member_status(group):
        return False, "❌ Please join the group first."

    channel = get_chat_member(CHANNEL_CHAT_ID, user_id)
    if not is_member_status(channel):
        return False, "❌ Please join the channel first."

    return True, "✅ Both memberships verified."

def escape_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def call_api(api_name, api_url, query):
    if not api_url or "YOUR-AUTHORIZED-API.example" in api_url:
        return {"status": "not_configured"}
    try:
        request_url = api_url + quote_plus(query)
        print(f"[API] {api_name}: request started")
        resp = HTTP.get(request_url, timeout=(API_CONNECT_TIMEOUT, API_READ_TIMEOUT))
        resp.raise_for_status()
        try:
            result = resp.json()
        except ValueError:
            return {"status": "error"}
        return {"status": "success", "result": result}
    except requests.exceptions.Timeout:
        print(f"[API] {api_name}: timeout")
        return {"status": "timeout"}
    except Exception as e:
        print(f"[API] {api_name}: error:", e)
        return {"status": "error"}

def send_long_message(chat_id, text, keyboard=None):
    max_len = 3900
    if len(text) <= max_len:
        send_message(chat_id, text, keyboard, parse_mode="HTML")
        return
    for i in range(0, len(text), max_len):
        send_message(chat_id, text[i:i+max_len], parse_mode="HTML")
    if keyboard is not None:
        send_message(chat_id, "✅ Finished.", keyboard)


# ============================================================
# KEYBOARDS
# ============================================================

def join_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "👥 Join Group", "url": GROUP_URL}],
            [{"text": "📢 Join Channel", "url": CHANNEL_URL}],
            [{"text": "✅ I Joined - Check", "callback_data": "check_join"}]
        ]
    }

def feature_keyboard():
    buttons = list(API_CONFIG.keys())
    keyboard = []
    for i in range(0, len(buttons), 2):
        keyboard.append(buttons[i:i+2])
    keyboard.append(["❌ Cancel"])
    return {"keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": False}

def show_join_message(chat_id):
    send_message(chat_id,
                 "<b>🔒 BOT LOCKED</b>\n\n"
                 "To unlock the bot:\n"
                 "1️⃣ Join the group\n"
                 "2️⃣ Join the channel\n"
                 "3️⃣ Press <b>✅ I Joined - Check</b>\n\n"
                 "⚠️ You must be a member of BOTH.",
                 join_keyboard(), parse_mode="HTML")

def show_feature_menu(chat_id):
    send_message(chat_id,
                 "<b>🎉 Verification successful!</b>\n\n"
                 "✅ Group: Joined\n"
                 "✅ Channel: Joined\n\n"
                 "🔓 <b>BOT UNLOCKED</b>\n\n"
                 "👇 Select a feature:",
                 feature_keyboard(), parse_mode="HTML")


# ============================================================
# USER STATE
# ============================================================

USER_STATE = {}


# ============================================================
# CALLBACK HANDLER
# ============================================================

def process_callback(update):
    cb = update.get("callback_query", {})
    if cb.get("data") != "check_join":
        return
    user_id = cb.get("from", {}).get("id")
    chat_id = cb.get("message", {}).get("chat", {}).get("id")
    if not user_id or not chat_id:
        return
    joined, reason = check_user_joined(user_id)
    answer_callback_query(cb.get("id"), reason if not joined else "✅ Unlocked!")
    if joined:
        show_feature_menu(chat_id)
    else:
        show_join_message(chat_id)


# ============================================================
# MESSAGE HANDLER
# ============================================================

def process_message(update):
    msg = update.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    user_id = msg.get("from", {}).get("id")
    if not chat_id or not user_id:
        return
    text = msg.get("text", "").strip()
    if not text:
        return

    # ----- /START -----
    if text == "/start":
        USER_STATE.pop(chat_id, None)
        joined, reason = check_user_joined(user_id)
        if joined:
            show_feature_menu(chat_id)
        else:
            # If bot is not a member, show a special warning to the first user (likely the owner)
            if "Bot is not a member" in reason:
                send_message(chat_id,
                             "⚠️ <b>Bot Setup Required</b>\n\n"
                             "The bot is not a member of the group and/or channel.\n"
                             "Please add the bot to both, then try again.",
                             parse_mode="HTML")
            else:
                show_join_message(chat_id)
        return

    # ----- OSINT COMMANDS -----
    for cmd, feature_name in COMMAND_FEATURE_MAP.items():
        if text.startswith(cmd):
            query = text[len(cmd):].strip()
            if not query:
                cfg = API_CONFIG.get(feature_name, {})
                prompt = cfg.get("prompt", "Please provide the required input.")
                send_message(chat_id,
                             f"❌ Please provide the required input.\n\n"
                             f"Usage: {cmd} <value>\nExample: {cmd} 9876543210\n\n{prompt}")
                return

            joined, reason = check_user_joined(user_id)
            if not joined:
                if "Bot is not a member" in reason:
                    send_message(chat_id,
                                 "⚠️ Bot is not a member of the group/channel. Please add the bot.",
                                 parse_mode="HTML")
                else:
                    show_join_message(chat_id)
                return

            cfg = API_CONFIG.get(feature_name)
            if not cfg:
                send_message(chat_id, "❌ Feature not available.")
                return

            api_url = cfg.get("url", "")
            send_message(chat_id, f"⏳ Searching for {query}...")
            result = call_api(feature_name, api_url, query)

            if result.get("status") != "success":
                send_message(chat_id, "❌ No result available or API is not working.")
                return

            api_result = result.get("result")
            try:
                formatted = json.dumps(api_result, indent=2, ensure_ascii=False)
            except Exception:
                formatted = str(api_result)
            response_text = "<pre>" + escape_html(formatted) + "</pre>"
            send_long_message(chat_id, response_text)
            return

    # ----- LIVE MEMBERSHIP CHECK -----
    joined, reason = check_user_joined(user_id)
    if not joined:
        USER_STATE.pop(chat_id, None)
        if "Bot is not a member" in reason:
            send_message(chat_id,
                         "⚠️ Bot is not a member of the group/channel. Please add the bot.",
                         parse_mode="HTML")
        else:
            show_join_message(chat_id)
        return

    # ----- CANCEL -----
    if text in ("/cancel", "❌ Cancel"):
        USER_STATE.pop(chat_id, None)
        send_message(chat_id, "❌ Cancelled.\n\n👇 Select a feature:", feature_keyboard())
        return

    # ----- FEATURE BUTTON SELECTION -----
    feature = None
    for name in API_CONFIG:
        if name.strip() == text.strip():
            feature = name
            break

    if feature is not None:
        USER_STATE[chat_id] = {"feature": feature}
        cfg = API_CONFIG[feature]
        prompt = cfg.get("prompt", "Send your input:")
        cmd = FEATURE_TO_COMMAND.get(feature)
        if cmd:
            prompt += f"\n\n💡 You can also use: {cmd} <value>"
        send_message(chat_id, prompt + "\n\nSend /cancel to cancel.")
        return

    # ----- FEATURE INPUT (state) -----
    if chat_id in USER_STATE:
        state = USER_STATE.get(chat_id, {})
        feature = state.get("feature")
        if not feature:
            USER_STATE.pop(chat_id, None)
            show_feature_menu(chat_id)
            return

        query = text.strip()
        if not query:
            send_message(chat_id, "❌ Input cannot be empty.")
            return

        joined, reason = check_user_joined(user_id)
        if not joined:
            USER_STATE.pop(chat_id, None)
            if "Bot is not a member" in reason:
                send_message(chat_id,
                             "⚠️ Bot is not a member of the group/channel. Please add the bot.",
                             parse_mode="HTML")
            else:
                show_join_message(chat_id)
            return

        cfg = API_CONFIG.get(feature)
        if not cfg:
            USER_STATE.pop(chat_id, None)
            send_message(chat_id, "❌ API configuration unavailable.", feature_keyboard())
            return

        api_url = cfg.get("url", "")
        send_message(chat_id, "⏳ Searching...")
        result = call_api(feature, api_url, query)

        if result.get("status") != "success":
            USER_STATE.pop(chat_id, None)
            send_message(chat_id, "❌ No result available or API is not working.", feature_keyboard())
            return

        api_result = result.get("result")
        try:
            formatted = json.dumps(api_result, indent=2, ensure_ascii=False)
        except Exception:
            formatted = str(api_result)
        response_text = "<pre>" + escape_html(formatted) + "</pre>"
        send_long_message(chat_id, response_text, feature_keyboard())
        USER_STATE.pop(chat_id, None)
        return

    # ----- UNKNOWN -----
    send_message(chat_id, "❓ Please select a feature from the menu.", feature_keyboard())


# ============================================================
# PROCESS UPDATE
# ============================================================

def process_update(update):
    try:
        if "callback_query" in update:
            process_callback(update)
        elif "message" in update:
            process_message(update)
    except Exception as e:
        print("Processing error:", e)


# ============================================================
# MAIN
# ============================================================

def main():
    global BOT_ID

    if not USER_BOT_TOKEN:
        print("ERROR: USER_BOT_TOKEN not configured.")
        return
    if not GROUP_CHAT_ID:
        print("ERROR: GROUP_CHAT_ID not configured.")
        return
    if not CHANNEL_CHAT_ID:
        print("ERROR: CHANNEL_CHAT_ID not configured.")
        return

    # Get bot info
    try:
        me = HTTP.get(TELEGRAM_API + "/getMe", timeout=(5, 10))
        me.raise_for_status()
        bot_data = me.json().get("result", {})
        BOT_ID = bot_data.get("id")
        username = bot_data.get("username", "unknown")
        print("Connected to bot:", username)
    except Exception as e:
        print("Could not connect to Telegram:", e)
        return

    # Check if bot is member of group and channel
    if not bot_can_check_members():
        print("WARNING: Bot is not a member of the group and/or channel.")
        print("Please add the bot to both to allow membership checks.")
    else:
        print("Bot is a member of both group and channel.")

    print("=" * 60)
    print("Telegram Membership Bot Started (Buttons + Commands)")
    print("Commands:", len(COMMAND_FEATURE_MAP))
    print("=" * 60)

    offset = None
    while True:
        try:
            result = get_updates(offset)
            if result is None:
                time.sleep(3)
                continue
            if not result.get("ok"):
                print("Telegram API error:", result)
                time.sleep(3)
                continue
            for update in result.get("result", []):
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = update_id + 1
                process_update(update)
        except KeyboardInterrupt:
            print("Bot stopped.")
            break
        except Exception as e:
            print("Main loop error:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
