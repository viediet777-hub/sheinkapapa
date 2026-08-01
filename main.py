#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SWIGGY BUZZ AUTO-COLLECTOR - WORKING OTP VERSION
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("buzzbot")

# ============================== CONFIG ==============================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN required!")
    os._exit(1)

ADMIN_IDS = [1364476174]
DB_PATH = "swiggy_buzz.db"
BRAND = "⚡ Made by Viediet"

# ===== SWIGGY API =====
OTP_URL = "https://profile.swiggy.com/api/v3/app/sms_otp"
VERIFY_URL = "https://profile.swiggy.com/api/v3/app/login/verify"
REWARDS_URL = "https://spns.swiggy.com/api/v1/campaign/rewards"
CAMPAIGN_ACTION_URL = "https://spns.swiggy.com/api/v1/campaign/action"

BASE_HEADERS = {
    "pl-version": "138",
    "version-code": "1795",
    "app-version": "4.113.0",
    "os-version": "11",
    "latitude": "22.7421633",
    "longitude": "75.907875",
    "current-latitude": "22.7421633",
    "current-longitude": "75.907875",
    "accessibility_enabled": "false",
    "x-network-quality": "GOOD",
    "faw-flags": "1354",
    "accept": "application/json; charset=utf-8",
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "user-agent": "Swiggy-Android",
}

SPNS_HEADERS = {
    "client-id": "portal",
    "user-agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4) AppleWebKit/537.36",
    "content-type": "application/json",
    "accept": "*/*",
    "origin": "https://webviews.swiggy.com",
    "x-requested-with": "in.swiggy.android",
}

# ============================== DATABASE ==============================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    token TEXT,
                    tid TEXT,
                    sid TEXT,
                    device_id TEXT,
                    swuid TEXT,
                    total_earned REAL DEFAULT 0,
                    active INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_collection_date TEXT DEFAULT '',
                    streak_days INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS buzz_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link_url TEXT UNIQUE NOT NULL,
                    campaign_id TEXT NOT NULL,
                    added_by INTEGER,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS buzz_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    link_id INTEGER,
                    action TEXT,
                    amount REAL DEFAULT 0,
                    status TEXT,
                    created_at TEXT
                );
            """)
            self._conn.commit()

    def _execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def add_account(self, telegram_id, phone, device_id, swuid, token, tid, sid):
        cur = self._execute("SELECT id FROM accounts WHERE telegram_id = ? AND phone = ?", (telegram_id, phone))
        row = cur.fetchone()
        if row:
            self._execute("UPDATE accounts SET token=?, tid=?, sid=?, device_id=?, swuid=?, active=1 WHERE id=?", 
                         (token, tid, sid, device_id, swuid, row["id"]))
            return row["id"]
        cur = self._execute("INSERT INTO accounts (telegram_id, phone, token, tid, sid, device_id, swuid, active, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
                           (telegram_id, phone, token, tid, sid, device_id, swuid, now()))
        return cur.lastrowid

    def get_accounts(self, telegram_id):
        cur = self._execute("SELECT * FROM accounts WHERE telegram_id = ? ORDER BY id", (telegram_id,))
        return [dict(r) for r in cur.fetchall()]

    def get_account(self, account_id):
        cur = self._execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_active_account(self, telegram_id):
        cur = self._execute("SELECT * FROM accounts WHERE telegram_id = ? AND active = 1", (telegram_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def set_active(self, telegram_id, account_id):
        self._execute("UPDATE accounts SET active = 0 WHERE telegram_id = ?", (telegram_id,))
        self._execute("UPDATE accounts SET active = 1 WHERE id = ? AND telegram_id = ?", (account_id, telegram_id))

    def remove_account(self, account_id):
        self._execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def get_all_links(self):
        cur = self._execute("SELECT * FROM buzz_links ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def add_links(self, entries, added_by):
        count = 0
        for url, campaign_id in entries:
            cur = self._execute("INSERT OR IGNORE INTO buzz_links (link_url, campaign_id, added_by, created_at) VALUES (?,?,?,?)",
                               (url, campaign_id, added_by, now()))
            if cur.rowcount:
                count += 1
        return count

    def delete_link(self, link_id):
        self._execute("DELETE FROM buzz_links WHERE id = ?", (link_id,))

    def log(self, account_id, link_id, action, amount, status):
        self._execute("INSERT INTO buzz_logs (account_id, link_id, action, amount, status, created_at) VALUES (?,?,?,?,?,?)",
                     (account_id, link_id, action, amount, status, now()))

    def add_earned(self, account_id, amount):
        self._execute("UPDATE accounts SET total_earned = total_earned + ? WHERE id = ?", (amount, account_id))

db = Database()

# ============================== HELPERS ==============================

PHONE_RE = re.compile(r"^\d{10}$")

def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) > 10:
        digits = digits[-10:]
    if not PHONE_RE.match(digits) or digits[0] not in ("6", "7", "8", "9"):
        return None
    return digits

def generate_device_id():
    return str(uuid.uuid4()).upper()

def generate_swuid():
    return "SW-" + uuid.uuid4().hex[:12].upper()

def extract_campaign_id(url):
    match = re.search(r"buzzstreaks/([^/?#\s]+)", url or "")
    if match:
        return match.group(1).rstrip("=")
    match = re.search(r"([A-Za-z0-9]{4,}_[A-Za-z0-9]{3,})", url or "")
    if match:
        return match.group(1).rstrip("=")
    return None

def parse_reward_amount(payload):
    total = 0.0
    if isinstance(payload, dict):
        for key in ["amount", "rewardAmount", "reward", "cashback", "value", "rewardValue"]:
            val = payload.get(key)
            if val and isinstance(val, (int, float)) and val > 0:
                total = max(total, float(val))
        data = payload.get("data", {})
        if isinstance(data, dict):
            for key in ["amount", "rewardAmount", "reward", "cashback", "value"]:
                val = data.get(key)
                if val and isinstance(val, (int, float)) and val > 0:
                    total = max(total, float(val))
    return round(total, 2)

# ============================== SWIGGY CLIENT ==============================

class SwiggyClient:
    def __init__(self):
        self.device_id = generate_device_id()
        self.swuid = generate_swuid()
        self.tid = ""
        self.sid = ""
        self.session = requests.Session()

    def _headers(self, extra=None):
        headers = dict(BASE_HEADERS)
        headers["deviceid"] = self.device_id
        headers["swuid"] = self.swuid
        if self.tid:
            headers["tid"] = self.tid
        if self.sid:
            headers["sid"] = self.sid
        if extra:
            headers.update(extra)
        return headers

    def send_otp(self, phone):
        phone = normalize_phone(phone)
        if not phone:
            return {"status": "error", "message": "Invalid phone number"}
        
        url = f"{OTP_URL}?mobile={phone}"
        log.info(f"[DEBUG] Sending OTP to {phone}")
        
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        log.info(f"[DEBUG] OTP response status: {resp.status_code}")
        
        if resp.status_code != 200:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        
        try:
            data = resp.json()
        except:
            return {"status": "error", "message": "Invalid response"}
        
        log.info(f"[DEBUG] OTP response: {json.dumps(data)[:500]}")
        
        # Check if OTP was actually sent
        if data.get("tid"):
            self.tid = str(data["tid"])
        if data.get("sid"):
            self.sid = str(data["sid"])
        
        # Check for errors
        if data.get("errorCode") or data.get("errorMessage"):
            return {"status": "error", "message": data.get("errorMessage", "Unknown error")}
        
        if "captcha" in json.dumps(data).lower():
            return {"status": "captcha", "message": "Captcha required. Try using a different device/network."}
        
        return {"status": "ok", "data": data, "tid": self.tid, "sid": self.sid}

    def verify_otp(self, phone, otp):
        phone = normalize_phone(phone)
        if not phone:
            raise Exception("Invalid phone number")
        
        url = f"{VERIFY_URL}?otp_source=Sms-manual"
        body = {
            "cloningSignalsData": {
                "appFilesDirPathInvalid": 0,
                "developerModeEnabled": 1,
                "deviceModelVmos": 0,
                "emulatorStatus": 0,
                "packageName": "in.swiggy.android",
                "workProfileEnabled": 0,
            },
            "otp": otp,
        }
        
        headers = self._headers()
        headers["manufacturer"] = "GOOGLE"
        headers["model-name"] = "PIXEL 4"
        
        resp = self.session.post(url, headers=headers, json=body, timeout=30)
        log.info(f"[DEBUG] Verify response status: {resp.status_code}")
        
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        
        data = resp.json()
        log.info(f"[DEBUG] Verify response: {json.dumps(data)[:500]}")
        
        # Extract token
        token = data.get("data", {}).get("token") or data.get("token")
        if not token:
            raise Exception("No token received")
        
        return token

    def collect_reward(self, account):
        """Claim daily reward - returns amount"""
        headers = {
            "Authorization": "Bearer " + (account.get("token") or ""),
            "client-id": "portal",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4) AppleWebKit/537.36",
            "content-type": "application/json",
            "accept": "*/*",
        }
        
        # First try to claim via rewards endpoint
        url = "https://spns.swiggy.com/api/v1/campaign/rewards"
        body = {
            "generalContext": {
                "requestContext": {"clientId": "portal_banner"}
            },
            "campaignRewardRequests": [
                {
                    "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
                    "campaignId": "ougwl",
                    "rollingFreecashParams": {
                        "forceRefresh": True,
                        "requestParams": {
                            "dataRequested": "wallet,connections,transactions",
                            "source": "banner",
                        },
                    },
                }
            ],
        }
        
        resp = self.session.post(url, headers=headers, json=body, timeout=30)
        data = resp.json() if resp.content else {}
        log.info(f"[DEBUG] Reward response: {json.dumps(data)[:500]}")
        
        return parse_reward_amount(data)

# ============================== BOT ==============================

PHONE, OTP = range(2)
login_sessions = {}
collecting_tasks = {}

def tg_id(update):
    return update.effective_user.id if update.effective_user else 0

def chat_id(update):
    return update.effective_chat.id if update.effective_chat else 0

def is_admin(user_id):
    return user_id in ADMIN_IDS

def main_menu():
    rows = [
        [InlineKeyboardButton("🔐 Login", callback_data="btn_login")],
        [InlineKeyboardButton("👤 Accounts", callback_data="btn_accounts")],
        [InlineKeyboardButton("🎁 Collect", callback_data="btn_collect")],
        [InlineKeyboardButton("📊 Stats", callback_data="btn_stats")],
    ]
    return InlineKeyboardMarkup(rows)

async def start(update, context):
    await update.message.reply_text(
        "🤖 Swiggy Buzz Collector\n\n"
        "Tap Login to add your account.\n"
        "Then tap Collect to claim rewards.",
        reply_markup=main_menu()
    )

async def login_start(update, context):
    await answer(update, "📱 Enter your 10-digit phone number:\n\nExample: 9876543210")
    return PHONE

async def phone_received(update, context):
    user_id = tg_id(update)
    raw = update.message.text.strip()
    phone = normalize_phone(raw)
    
    if not phone:
        await update.message.reply_text("❌ Invalid number. Enter 10 digits only:")
        return PHONE
    
    client = SwiggyClient()
    try:
        result = await asyncio.to_thread(client.send_otp, phone)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}\nTry again:")
        return PHONE
    
    if result.get("status") != "ok":
        await update.message.reply_text(f"❌ {result.get('message', 'Unknown error')}\nTry again:")
        return PHONE
    
    login_sessions[user_id] = {
        "phone": phone,
        "client": client,
        "tid": client.tid,
        "sid": client.sid,
    }
    
    await update.message.reply_text(f"✅ OTP sent to +91 {phone}!\n\nEnter the 6-digit OTP:")
    return OTP

async def otp_received(update, context):
    user_id = tg_id(update)
    session = login_sessions.get(user_id)
    
    if not session:
        await update.message.reply_text("⏳ Session expired. Start /start again.")
        return ConversationHandler.END
    
    otp = update.message.text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ Enter 6-digit OTP:")
        return OTP
    
    client = session["client"]
    phone = session["phone"]
    
    try:
        token = await asyncio.to_thread(client.verify_otp, phone, otp)
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:200]}\nTry again:")
        return OTP
    
    if not token:
        await update.message.reply_text("❌ Invalid OTP. Try again:")
        return OTP
    
    # Save account
    db.add_account(
        user_id,
        phone,
        client.device_id,
        client.swuid,
        token,
        session.get("tid", ""),
        session.get("sid", ""),
    )
    
    login_sessions.pop(user_id, None)
    await update.message.reply_text(
        f"✅ Logged in as +91 {phone}!\n\nTap Collect to claim rewards.",
        reply_markup=main_menu()
    )
    return ConversationHandler.END

async def collect(update, context):
    user_id = tg_id(update)
    account = db.get_active_account(user_id)
    
    if not account:
        await answer(update, "❌ No account found. Login first.", main_menu())
        return
    
    # Check if already collected today
    if account.get("last_collection_date") == datetime.now().strftime("%Y-%m-%d"):
        await answer(update, "✅ Already collected today!", main_menu())
        return
    
    # Start collection
    client = SwiggyClient()
    try:
        amount = await asyncio.to_thread(client.collect_reward, account)
    except Exception as e:
        await answer(update, f"❌ Error: {str(e)[:200]}", main_menu())
        return
    
    if amount > 0:
        db.add_earned(account["id"], amount)
        db._execute(
            "UPDATE accounts SET last_collection_date = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d"), account["id"])
        )
        db.log(account["id"], 0, "claim", amount, "ok")
        await answer(update, f"✅ Claimed ₹{amount}!", main_menu())
    else:
        await answer(update, "❌ No reward available.", main_menu())

async def accounts(update):
    user_id = tg_id(update)
    accounts = db.get_accounts(user_id)
    
    if not accounts:
        await answer(update, "📭 No accounts.", main_menu())
        return
    
    text = "👤 Your Accounts:\n\n"
    for a in accounts:
        status = "🟢 Active" if a["active"] else "⚪ Inactive"
        text += f"📱 +91 {a['phone']} - ₹{a['total_earned']:.2f} - {status}\n"
    
    await answer(update, text, main_menu())

async def stats(update):
    user_id = tg_id(update)
    accounts = db.get_accounts(user_id)
    total = sum(a["total_earned"] for a in accounts)
    
    text = f"📊 Total Earnings: ₹{total:.2f}\n\n"
    for a in accounts:
        text += f"📱 +91 {a['phone']}: ₹{a['total_earned']:.2f}\n"
    
    await answer(update, text, main_menu())

async def answer(update, text, markup=None):
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)

async def on_callback(update, context):
    data = update.callback_query.data
    await update.callback_query.answer()
    
    if data == "btn_login":
        await login_start(update, context)
    elif data == "btn_collect":
        await collect(update, context)
    elif data == "btn_accounts":
        await accounts(update)
    elif data == "btn_stats":
        await stats(update)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_start, pattern="^btn_login$")],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_received)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(on_callback))
    
    app.run_polling()

if __name__ == "__main__":
    main()
