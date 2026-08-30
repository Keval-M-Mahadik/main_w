import os
import json
import time
import threading
import requests

from flask import Flask
from urllib.parse import quote_plus


# ============================================================
# TELEGRAM MEMBERSHIP BOT
#
# /start
#   ↓
# Join Group + Channel
#   ↓
# I Joined - Check
#   ↓
# Verify BOTH memberships
#   ↓
# Feature buttons appear
# ============================================================


# ============================================================
# BOT CONFIGURATION
# ============================================================

# IMPORTANT:
# Put your NEW token in Render Environment Variables.
#
# USER_BOT_TOKEN=your_new_bot_token
#
# Do NOT hard-code your Telegram bot token.
USER_BOT_TOKEN = os.getenv("USER_BOT_TOKEN", "8771414496:AAFBw-cGZhExTMbkZcvecolcDxAV9nzpjt8").strip()

GROUP_URL = "https://t.me/dark_22_group"
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

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


threading.Thread(
    target=run_server,
    daemon=True
).start()


# ============================================================
# API CONFIGURATION
#
# Use only APIs/data that you are authorized to access.
#
# Replace the example URLs with your own authorized endpoints.
# ============================================================

API_CONFIG = {

     "🪪 Aadhaar Info ": {
            "url": "https://anon-num-info.vercel.app/aadhar?key=aadhar28008&id=",
            "prompt": "🪪 Send a 12 Digit Aadhaar Number to Get🪪 information 💀"
        },
    
    "📞 Number Info ": {
            "url": "https://anon-num-info.vercel.app/num?key=temp2808num&num=",
            "prompt": "📞Send a 10 Digit Indian Number (Without +91) to Get🪪 information 💀     Example (9712073901)"
        },
    
    "📍PIN Code Lookup": {
            "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
            "prompt": "📍 Send PIN code to get information 💀 (number)"
        },
    "🚘 Vehicle Info": {
                "url": "https://parivahan-x.paskhinpf9.workers.dev/?vehicle=",
                "prompt": "🚘 Send Vehicle Number 2.0 to get information💀(write in small letters)"
            },
    
    "🚘 Vehicle Info 2.0": {
            "url": "https://parivahan-x.paskhinpf9.workers.dev/?vehicle=",
            "prompt": "🚘 Send Vehicle Number 2.0 to get information💀(write in small letters)"
        },
    
    "🤖 Telegram ID / Username ": {
            "url": "https://anon-tg-info.vercel.app/telegram?key=temp1750&username=",
            "prompt": "🤖 Send the authorized Telegram username:"
        },
    
    "🆔 PAN Info ": {
            "url": "https://paninfo.noob73613.workers.dev/pan?pan=",
            "prompt": "🆔 Send the authorized PAN reference:"
        },
    
    "📱 Telegram Chat ID ": {
            "url": "https://anon-tg-info.vercel.app/tgReg_beta?userid=",
            "prompt": "📱 Send the authorized Chat ID:"
        },
    
    "💳 IFSC Info ": {
            "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
            "prompt": "💳 Send the IFSC code number:-"
        },
    
    "🏦 UPI INFO ": {
            "url": "https://upi-id-to-info-by-abhigyan.onrender.com/upi/<UPI_ID>",
            "prompt": "🏦 Send the authorized UPI ID:"
        },
    
    "🇵🇰 Pakistan Number Info ": {
            "url": "https://YOUR-AUTHORIZED-API.example/pakistan-number?query=",
            "prompt": "coming soon"
        },
    
    "📧 Advanced Email Info ": {
            "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
            "prompt": "📧 Send the authorized email:"
        },
    
    "📈 GST Info Advanced": {
            "url": "https://YOUR-AUTHORIZED-API.example/gst?query=",
            "prompt": "coming soon"
        },
    
    "🌐 IP Address Info": {
            "url": "https://talks-chain-restrictions-statistics.trycloudflare.com/search?query=",
            "prompt": "🌐 Send the IP address:"
        },
    
    "👤 Instagram Username Info": {
            "url": "https://anon-social-info.vercel.app/igdl?&url=",
            "prompt": "coming soon"
        }
}


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


# ============================================================
# TELEGRAM API
# ============================================================

TELEGRAM_API = (
    "https://api.telegram.org/bot"
    + USER_BOT_TOKEN
)


# ============================================================
# USER STATE
# ============================================================

USER_STATE = {}


# ============================================================
# SEND MESSAGE
# ============================================================

def send_message(
    chat_id,
    text,
    keyboard=None,
    parse_mode=None
):

    url = TELEGRAM_API + "/sendMessage"

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard is not None:
        data["reply_markup"] = json.dumps(
            keyboard,
            ensure_ascii=False
        )

    if parse_mode:
        data["parse_mode"] = parse_mode

    try:

        response = HTTP.post(
            url,
            data=data,
            timeout=(
                TELEGRAM_CONNECT_TIMEOUT,
                TELEGRAM_READ_TIMEOUT
            )
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            print("Telegram API error:", result)

        return result

    except requests.exceptions.RequestException as error:

        print("sendMessage error:", error)

        return None


# ============================================================
# GET UPDATES
# ============================================================

def get_updates(offset=None):

    params = {
        "timeout": 30
    }

    if offset is not None:
        params["offset"] = offset

    try:

        response = HTTP.get(
            TELEGRAM_API + "/getUpdates",
            params=params,
            timeout=(
                TELEGRAM_CONNECT_TIMEOUT,
                TELEGRAM_READ_TIMEOUT + 5
            )
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as error:

        print("getUpdates error:", error)

        return None


# ============================================================
# JOIN KEYBOARD
# ============================================================

def join_keyboard():

    return {

        "inline_keyboard": [

            [
                {
                    "text": "👥 Join Group",
                    "url": GROUP_URL
                }
            ],

            [
                {
                    "text": "📢 Join Channel",
                    "url": CHANNEL_URL
                }
            ],

            [
                {
                    "text": "✅ I Joined - Check",
                    "callback_data": "check_join"
                }
            ]

        ]
    }


# ============================================================
# FEATURE KEYBOARD
# ============================================================

def feature_keyboard():

    buttons = list(API_CONFIG.keys())

    keyboard = []

    for i in range(0, len(buttons), 2):

        keyboard.append(
            buttons[i:i + 2]
        )

    keyboard.append(
        ["❌ Cancel"]
    )

    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False
    }


# ============================================================
# ANSWER CALLBACK
# ============================================================

def answer_callback_query(
    callback_query_id,
    text=""
):

    if not callback_query_id:
        return

    try:

        HTTP.post(
            TELEGRAM_API + "/answerCallbackQuery",

            data={
                "callback_query_id": callback_query_id,
                "text": text[:200],
                "show_alert": False
            },

            timeout=(
                TELEGRAM_CONNECT_TIMEOUT,
                10
            )
        )

    except requests.exceptions.RequestException as error:

        print("Callback error:", error)


# ============================================================
# GET CHAT MEMBER
# ============================================================

def get_chat_member(
    chat_id,
    user_id
):

    if not chat_id:
        return None

    try:

        response = HTTP.get(

            TELEGRAM_API + "/getChatMember",

            params={
                "chat_id": chat_id,
                "user_id": user_id
            },

            timeout=(
                TELEGRAM_CONNECT_TIMEOUT,
                10
            )
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as error:

        print("getChatMember error:", error)

        return None


# ============================================================
# MEMBER STATUS
# ============================================================

def is_member_status(member_result):

    if not member_result:
        return False

    if not member_result.get("ok"):
        return False

    status = (
        member_result
        .get("result", {})
        .get("status")
    )

    return status in (
        "creator",
        "administrator",
        "member"
    )


# ============================================================
# CHECK GROUP + CHANNEL
# ============================================================

def check_user_joined(user_id):

    if not GROUP_CHAT_ID:

        return (
            False,
            "❌ GROUP_CHAT_ID is not configured."
        )

    if not CHANNEL_CHAT_ID:

        return (
            False,
            "❌ CHANNEL_CHAT_ID is not configured."
        )

    # --------------------------------------------------------
    # GROUP
    # --------------------------------------------------------

    group_result = get_chat_member(
        GROUP_CHAT_ID,
        user_id
    )

    if not is_member_status(group_result):

        return (
            False,
            "❌ Please join the group first."
        )

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    channel_result = get_chat_member(
        CHANNEL_CHAT_ID,
        user_id
    )

    if not is_member_status(channel_result):

        return (
            False,
            "❌ Please join the channel first."
        )

    return (
        True,
        "✅ Both memberships verified."
    )


# ============================================================
# SHOW JOIN MESSAGE
# ============================================================

def show_join_message(chat_id):

    send_message(

        chat_id,

        "<b>🔒 BOT LOCKED</b>\n\n"
        "To unlock the bot:\n\n"
        "1️⃣ Join the group\n"
        "2️⃣ Join the channel\n"
        "3️⃣ Press <b>✅ I Joined - Check</b>\n\n"
        "⚠️ You must be a member of BOTH.",

        join_keyboard(),

        parse_mode="HTML"
    )


# ============================================================
# SHOW FEATURE MENU
# ============================================================

def show_feature_menu(chat_id):

    send_message(

        chat_id,

        "<b>🎉 Verification successful!</b>\n\n"
        "✅ Group: Joined\n"
        "✅ Channel: Joined\n\n"
        "🔓 <b>BOT UNLOCKED</b>\n\n"
        "👇 Select a feature:",

        feature_keyboard(),

        parse_mode="HTML"
    )


# ============================================================
# HTML ESCAPE
# ============================================================

def escape_html(text):

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# CALL AUTHORIZED API
# ============================================================

def call_api(
    api_name,
    api_url,
    query
):

    if not api_url:

        return {
            "status": "not_configured"
        }

    if "YOUR-AUTHORIZED-API.example" in api_url:

        return {
            "status": "not_configured"
        }

    try:

        request_url = (
            api_url
            + quote_plus(query)
        )

        print(
            f"[API] {api_name}: request started"
        )

        response = HTTP.get(

            request_url,

            timeout=(
                API_CONNECT_TIMEOUT,
                API_READ_TIMEOUT
            )
        )

        response.raise_for_status()

        try:

            result = response.json()

        except ValueError:

            return {
                "status": "error"
            }

        return {

            "status": "success",

            "result": result

        }

    except requests.exceptions.Timeout:

        print(
            f"[API] {api_name}: timeout"
        )

        return {
            "status": "timeout"
        }

    except requests.exceptions.RequestException as error:

        print(
            f"[API] {api_name}: request error:",
            error
        )

        return {
            "status": "error"
        }

    except Exception as error:

        print(
            f"[API] {api_name}: unexpected error:",
            error
        )

        return {
            "status": "error"
        }


# ============================================================
# SEND LONG MESSAGE
# ============================================================

def send_long_message(
    chat_id,
    text,
    keyboard=None
):

    max_length = 3900

    if len(text) <= max_length:

        send_message(
            chat_id,
            text,
            keyboard,
            parse_mode="HTML"
        )

        return

    for start in range(
        0,
        len(text),
        max_length
    ):

        send_message(

            chat_id,

            text[
                start:start + max_length
            ],

            parse_mode="HTML"
        )

    if keyboard is not None:

        send_message(
            chat_id,
            "✅ Finished.",
            keyboard
        )


# ============================================================
# PROCESS CALLBACK
# ============================================================

def process_callback(update):

    callback = update.get(
        "callback_query",
        {}
    )

    callback_id = callback.get("id")

    data = callback.get("data")

    from_user = callback.get(
        "from",
        {}
    )

    user_id = from_user.get("id")

    message = callback.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get("id")

    if data != "check_join":
        return

    if not user_id or not chat_id:
        return

    # Live membership check
    joined, reason = check_user_joined(
        user_id
    )

    if joined:

        answer_callback_query(
            callback_id,
            "✅ Both joined! Bot unlocked."
        )

        show_feature_menu(
            chat_id
        )

    else:

        answer_callback_query(
            callback_id,
            reason
        )

        show_join_message(
            chat_id
        )


# ============================================================
# PROCESS MESSAGE
# ============================================================

def process_message(update):

    message = update.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get("id")

    user = message.get(
        "from",
        {}
    )

    user_id = user.get("id")

    if not chat_id or not user_id:
        return

    text = message.get(
        "text",
        ""
    )

    if not isinstance(text, str):
        return

    text = text.strip()

    # ========================================================
    # /START
    # ========================================================

    if text == "/start":

        USER_STATE.pop(
            chat_id,
            None
        )

        joined, _ = check_user_joined(
            user_id
        )

        if joined:

            show_feature_menu(
                chat_id
            )

        else:

            show_join_message(
                chat_id
            )

        return

    # ========================================================
    # LIVE MEMBERSHIP CHECK
    # ========================================================

    joined, _ = check_user_joined(
        user_id
    )

    if not joined:

        USER_STATE.pop(
            chat_id,
            None
        )

        show_join_message(
            chat_id
        )

        return

    # ========================================================
    # CANCEL
    # ========================================================

    if text in (
        "/cancel",
        "❌ Cancel"
    ):

        USER_STATE.pop(
            chat_id,
            None
        )

        send_message(

            chat_id,

            "❌ Cancelled.\n\n"
            "👇 Select a feature:",

            feature_keyboard()
        )

        return

    # ========================================================
    # FIND FEATURE
    # ========================================================

    feature = None

    for name in API_CONFIG:

        if name.strip() == text.strip():

            feature = name
            break

    if feature is not None:

        USER_STATE[chat_id] = {
            "feature": feature
        }

        config = API_CONFIG[feature]

        prompt = config.get(
            "prompt",
            "Send your input:"
        )

        send_message(

            chat_id,

            prompt
            + "\n\n"
            + "Send /cancel to cancel."
        )

        return

    # ========================================================
    # FEATURE INPUT
    # ========================================================

    if chat_id in USER_STATE:

        state = USER_STATE.get(
            chat_id,
            {}
        )

        feature = state.get(
            "feature"
        )

        if not feature:

            USER_STATE.pop(
                chat_id,
                None
            )

            show_feature_menu(
                chat_id
            )

            return

        query = text.strip()

        if not query:

            send_message(
                chat_id,
                "❌ Input cannot be empty."
            )

            return

        # ----------------------------------------------------
        # MEMBERSHIP CHECK AGAIN
        # ----------------------------------------------------

        joined, _ = check_user_joined(
            user_id
        )

        if not joined:

            USER_STATE.pop(
                chat_id,
                None
            )

            show_join_message(
                chat_id
            )

            return

        # ----------------------------------------------------
        # API CONFIG
        # ----------------------------------------------------

        config = API_CONFIG.get(
            feature
        )

        if not config:

            USER_STATE.pop(
                chat_id,
                None
            )

            send_message(
                chat_id,
                "❌ API configuration unavailable.",
                feature_keyboard()
            )

            return

        api_url = config.get(
            "url",
            ""
        )

        # ----------------------------------------------------
        # SEARCH
        # ----------------------------------------------------

        send_message(
            chat_id,
            "⏳ Searching..."
        )

        result = call_api(
            feature,
            api_url,
            query
        )

        if result.get("status") != "success":

            USER_STATE.pop(
                chat_id,
                None
            )

            send_message(

                chat_id,

                "❌ No result available or.\n\n"
                " API is not working. Please wait for some time.",

                feature_keyboard()
            )

            return

        # ----------------------------------------------------
        # FORMAT RESULT
        # ----------------------------------------------------

        api_result = result.get(
            "result"
        )

        try:

            formatted = json.dumps(
                api_result,
                indent=2,
                ensure_ascii=False
            )

        except Exception:

            formatted = str(
                api_result
            )

        response_text = (
            "<pre>"
            + escape_html(formatted)
            + "</pre>"
        )

        send_long_message(

            chat_id,

            response_text,

            feature_keyboard()
        )

        USER_STATE.pop(
            chat_id,
            None
        )

        return

    # ========================================================
    # UNKNOWN MESSAGE
    # ========================================================

    send_message(

        chat_id,

        "❓ Please select a feature "
        "from the menu.",

        feature_keyboard()
    )


# ============================================================
# PROCESS UPDATE
# ============================================================

def process_update(update):

    try:

        if "callback_query" in update:

            process_callback(update)
            return

        if "message" in update:

            process_message(update)

    except Exception as error:

        print(
            "Processing error:",
            error
        )


# ============================================================
# MAIN
# ============================================================

def main():

    if not USER_BOT_TOKEN:

        print(
            "ERROR: USER_BOT_TOKEN is not configured."
        )

        return

    if not GROUP_CHAT_ID:

        print(
            "ERROR: GROUP_CHAT_ID is not configured."
        )

        return

    if not CHANNEL_CHAT_ID:

        print(
            "ERROR: CHANNEL_CHAT_ID is not configured."
        )

        return

    # ========================================================
    # TEST TELEGRAM CONNECTION
    # ========================================================

    try:

        response = HTTP.get(

            TELEGRAM_API + "/getMe",

            timeout=(
                TELEGRAM_CONNECT_TIMEOUT,
                10
            )
        )

        response.raise_for_status()

        bot_info = response.json()

        if not bot_info.get("ok"):

            print(
                "ERROR: Invalid Telegram bot token."
            )

            return

        username = (
            bot_info
            .get("result", {})
            .get("username", "unknown")
        )

        print(
            "Connected to Telegram bot:",
            username
        )

    except Exception as error:

        print(
            "Could not connect to Telegram:",
            error
        )

        return

    print("=" * 60)
    print("Telegram Membership Bot Started")
    print("=" * 60)
    print("Features:", len(API_CONFIG))
    print("=" * 60)

    # ========================================================
    # UPDATE LOOP
    # ========================================================

    offset = None

    while True:

        try:

            result = get_updates(
                offset
            )

            if result is None:

                time.sleep(3)
                continue

            if not result.get("ok"):

                print(
                    "Telegram API error:",
                    result
                )

                time.sleep(3)
                continue

            for update in result.get(
                "result",
                []
            ):

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = update_id + 1

                process_update(
                    update
                )

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            break

        except Exception as error:

            print(
                "Main loop error:",
                error
            )

            time.sleep(3)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
