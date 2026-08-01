import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "YOUR_TELEGRAM_ID").split(",")
    if x.strip().lstrip("-").isdigit()
]
DB_PATH = os.getenv("DB_PATH", "swiggy_buzz.db")

MAX_EARN_PER_ACCOUNT = float(os.getenv("MAX_EARN_PER_ACCOUNT", "1000"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.8"))

BASE_HEADERS = {
    "pl-version": "138",
    "version-code": "1795",
    "app-version": "4.113.0",
    "os-version": "11",
    "latitude": "22.7421633",
    "longitude": "75.907875",
    "accept": "application/json",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
}

OTP_URL = "https://profile.swiggy.com/api/v3/app/sms_otp"
VERIFY_URL = "https://profile.swiggy.com/api/v3/app/login/verify"
REWARDS_URL = "https://spns.swiggy.com/api/v1/campaign/rewards"

BRAND = "⚡ Made by @viediet"
