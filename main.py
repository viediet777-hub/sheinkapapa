#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SWIGGY BUZZ AUTO-COLLECTOR - WITH PRE-ADDED LINKS
Complete Single Script - Railway Ready
Made by @viediet - FIXED by NRTECNO
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

ADMIN_IDS = [1364476174]  # Change to your Telegram ID
DB_PATH = "swiggy_buzz.db"
MAX_EARN_PER_ACCOUNT = 1000
REQUEST_DELAY = 0.8
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

def today_ist():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")

def yesterday_ist():
    return (datetime.now(timezone(timedelta(hours=5, minutes=30))) - timedelta(days=1)).strftime("%Y-%m-%d")

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

    def has_collected_today(self, account_id):
        row = self._execute("SELECT last_collection_date, streak_days FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            return False, 0
        return (row["last_collection_date"] or "") == today_ist(), row["streak_days"] or 0

    def today_earnings(self, account_id):
        cur = self._execute("SELECT COALESCE(SUM(amount), 0) AS total FROM buzz_logs WHERE account_id = ? AND date(created_at) = ? AND status = 'ok'",
                           (account_id, today_ist()))
        return cur.fetchone()["total"]

    def finish_collection(self, account_id):
        row = self._execute("SELECT last_collection_date, streak_days FROM accounts WHERE id = ?", (account_id,)).fetchone()
        last = (row["last_collection_date"] or "") if row else ""
        streak = (row["streak_days"] or 0) if row else 0
        today = today_ist()
        if last == yesterday_ist():
            streak += 1
        elif last != today:
            streak = 1
        self._execute("UPDATE accounts SET last_collection_date = ?, streak_days = ? WHERE id = ?", (today, streak, account_id))
        return streak

    def add_links(self, entries, added_by):
        count = 0
        for url, campaign_id in entries:
            cur = self._execute("INSERT OR IGNORE INTO buzz_links (link_url, campaign_id, added_by, created_at) VALUES (?,?,?,?)",
                               (url, campaign_id, added_by, now()))
            if cur.rowcount:
                count += 1
        return count

    def get_all_links(self):
        cur = self._execute("SELECT * FROM buzz_links ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def delete_link(self, link_id):
        self._execute("DELETE FROM buzz_links WHERE id = ?", (link_id,))

    def log(self, account_id, link_id, action, amount, status):
        self._execute("INSERT INTO buzz_logs (account_id, link_id, action, amount, status, created_at) VALUES (?,?,?,?,?,?)",
                     (account_id, link_id, action, amount, status, now()))

    def add_earned(self, account_id, amount):
        self._execute("UPDATE accounts SET total_earned = total_earned + ? WHERE id = ?", (amount, account_id))

    def get_stats(self, telegram_id):
        accounts = self.get_accounts(telegram_id)
        total = sum(a["total_earned"] for a in accounts)
        return accounts, total

db = Database()

# ============================== PRE-ADD LINKS ==============================

def add_default_links():
    """Add all provided links - duplicates automatically skipped"""
    links = [
        "https://r.swiggy.com/buzzstreaks/ougwl_MTI2NjkzMjc1I05pa2hpbA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjQ1MjU2NTE1I1JlaGFuYQ==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTA4ODg5MjA4I0FrYXNo",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjQ5NzI1OTM0I3VldXN1ZQ==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjIzNTE0NDk5I1BpeXVzaA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjU5MzAxMjYxI1NoYWhp",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjEwODY2MTYzI2tlc2hhdg==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTg5MzMyNTE1I0RpbmVzaA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjAwMjkzNzMzI2FiaGlzaGVr",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTc1Njg5MjcyI1BhcnRo",
        "https://r.swiggy.com/buzzstreaks/ougwl_NjY3NTUzNjcjcmFrZXNo",
        "https://r.swiggy.com/buzzstreaks/ougwl_NDgwMzU2MzUjQW5zaA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjAzNzc1MTk4I1NhbWFydGg=",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTk5ODM4OTEwI0Job29taWth",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTgxMTU2NzE4I1NoYXRha3NoaQ==",
        "https://r.swiggy.com/buzzstreaks/ougwl_NzAyODY3OTgjU2hhaHplYg==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjY2ODAzNTE5I1NpZGhhcnRo",
        "https://r.swiggy.com/buzzstreaks/ougwl_NTYxNjIyMzMjU3dhbWk=",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTM1MTg2NTM2I0F5dXNo",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjI3NTE2MjczI0hhcHB5",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjYzMjM0NDQ5I1NoYW1iaGF2aQ==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjcxOTYzNzU1I1Jpendhbg==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjA5NTUxNDY2I2FsYWlrYQ==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjYxNTQyOTU5I2hhcnNo",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjYzMjE5Mjg4I1ZydXR2aWs=",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTE0MjAzMjAyI1NoYWh6YWQ=",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjI2MTk5NTg0I1JhbQ==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTMwMTI4NTc4I0hhcnNo",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTQ5MTk2MzE2I01vaGl0",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjY3MjE2OTg3I1JvdW5haw==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTMyOTIzNzI1I3N1aml0",
        "https://r.swiggy.com/buzzstreaks/ougwl_NTU5MjQ5MjEjQmhhdmVzaA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjcyMzEyMzAzI1V6bWE=",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTU4MzIzNDM2I0Fua3VzaA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjcyMzEzNTkzI1Nhdw==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjY3MDYyNTg3I0t1bWFy",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjY2ODAyNTEwI0lvaXk=",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTQ2NzAzNTg1I3Zpa2Fz",
        "https://r.swiggy.com/buzzstreaks/ougwl_NzE4MjkwMzAjTW9oZA==",
        "https://r.swiggy.com/buzzstreaks/ougwl_MjMwOTEyOTQ1I1ZhbnNo",
        "https://r.swiggy.com/buzzstreaks/ougwl_MTA3Njk4ODQ5I1Zpa2Fz",
    ]
    
    entries = []
    for url in links:
        # Extract campaign ID from URL
        match = re.search(r"buzzstreaks/([^/?#\s]+)", url)
        if match:
            campaign_id = match.group(1).rstrip("=")
            entries.append((url, campaign_id))
    
    # Add all links (duplicates automatically skipped by UNIQUE constraint)
    added = db.add_links(entries, 1364476174)
    log.info(f"✅ Added {added} new links (duplicates skipped)")
    return added

# Add links on startup
add_default_links()

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
        # Deep search
        def walk(node):
            nonlocal total
            if isinstance(node, dict):
                for k, v in node.items():
                    if any(x in str(k).lower() for x in ["amount", "reward", "cashback"]):
                        if isinstance(v, (int, float)) and v > 0:
                            total = max(total, float(v))
                    elif isinstance(v, (dict, list)):
                        walk(v)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        walk(item)
        walk(payload)
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
        
        if data.get("tid"):
            self.tid = str(data["tid"])
        if data.get("sid"):
            self.sid = str(data["sid"])
        
        if data.get("errorCode") or data.get("errorMessage"):
            return {"status": "error", "message": data.get("errorMessage", "Unknown error")}
        
        if "captcha" in json.dumps(data).lower():
            return {"status": "captcha", "message": "Captcha required. Try different device/network."}
        
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
        
        url = REWARDS_URL
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
progress_messages = {}
collecting_tasks = {}

def tg_id(update):
    return update.effective_user.id if update.effective_user else 0

def chat_id(update):
    return update.effective_chat.id if update.effective_chat else 0

def is_admin(user_id):
    return user_id in ADMIN_IDS

def main_menu(update):
    user_id = tg_id(update)
    rows = [
        [InlineKeyboardButton("🔐 Login Account", callback_data="btn_login")],
        [InlineKeyboardButton("👤 My Accounts", callback_data="btn_accounts")],
        [InlineKeyboardButton("🎁 Collect Buzz", callback_data="btn_collect")],
        [InlineKeyboardButton("📊 My Stats", callback_data="btn_stats")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="btn_admin")])
    return InlineKeyboardMarkup(rows)

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Links", callback_data="adm_add")],
        [InlineKeyboardButton("📋 View Links", callback_data="adm_links")],
        [InlineKeyboardButton("🗑 Delete Link", callback_data="adm_del")],
        [InlineKeyboardButton("⬅️ Back", callback_data="btn_back")],
    ])

async def answer(update, text, markup=None):
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return
        except:
            pass
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

async def start(update, context):
    await update.message.reply_text(
        "🤖 <b>Swiggy Buzz Auto-Collector</b>\n\n"
        "Collect all your Swiggy Buzz rewards automatically!\n\n"
        f"{BRAND}",
        reply_markup=main_menu(update),
        parse_mode=ParseMode.HTML
    )

async def login_start(update, context):
    await answer(update, "📱 <b>Enter your 10-digit phone number:</b>\n\nExample: <code>9876543210</code>\n\nSend /cancel to abort.")
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
    
    await update.message.reply_text(f"✅ <b>OTP sent to +91 {phone}!</b>\n\nEnter the 6-digit OTP:", parse_mode=ParseMode.HTML)
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
    account = db.get_active_account(user_id)
    
    await update.message.reply_text(
        f"✅ <b>Logged in as +91 {phone}!</b>\n\nTap Collect Buzz to claim rewards.",
        reply_markup=main_menu(update),
        parse_mode=ParseMode.HTML
    )
    
    if account:
        start_collection(update, context, account)
    
    return ConversationHandler.END

async def cancel_login(update, context):
    user_id = tg_id(update)
    login_sessions.pop(user_id, None)
    await update.message.reply_text("❌ Login cancelled.", reply_markup=main_menu(update))
    return ConversationHandler.END

async def conv_fallback(update, context):
    user_id = tg_id(update)
    login_sessions.pop(user_id, None)
    await answer(update, "👈 Login reset.", main_menu(update))
    return ConversationHandler.END

# ============================== COLLECTION ==============================

def start_collection(update, context, account):
    cid = chat_id(update)
    if not cid:
        return
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        return
    collecting_tasks[cid] = asyncio.create_task(run_collection(update, context, account))

def progress_text(done, total, earned, last_ok):
    bar_len = 12
    filled = min(bar_len, int(bar_len * done / max(total, 1)))
    bar = "🟩" * filled + "⬜" * (bar_len - filled)
    mark = "✅" if last_ok else "❌"
    return f"🎁 <b>Collecting... [{done}/{total}]</b>\n{bar}\n💰 Today: ₹{earned:.2f}\nLast: {mark}"

def final_text(done, total, earned, account_total, streak):
    return f"✅ <b>Collection finished! [{done}/{total}]</b>\n\n💰 <b>Today: ₹{earned:.2f}</b>\n🏆 Total: ₹{account_total:.2f}\n🔥 Streak: {streak} day{'s' if streak != 1 else ''}\n\n{BRAND}"

async def edit_progress(cid, context, text):
    msg_id = progress_messages.get(cid)
    if not msg_id:
        return
    try:
        await context.bot.edit_message_text(text, chat_id=cid, message_id=msg_id, parse_mode=ParseMode.HTML)
    except:
        pass

async def run_collection(update, context, account):
    cid = chat_id(update)
    account_id = account["id"]
    links = db.get_all_links()
    total_new = 0.0
    done = 0
    last_ok = True
    streak = 0
    
    try:
        if not links:
            await send_plain(update, context, "📭 No buzz links. Ask admin to add links.")
            return
        
        row = db.get_account(account_id)
        if not row:
            return
        
        already, streak = db.has_collected_today(account_id)
        if already:
            today_earned = db.today_earnings(account_id)
            await send_plain(update, context, f"✅ Already collected today!\n💰 Today: ₹{today_earned:.2f}\n🔥 Streak: {streak} days")
            return
        
        first = await send_plain(update, context, "🎁 <b>Collecting...</b>")
        if first:
            progress_messages[cid] = first.message_id
        
        client = SwiggyClient()
        
        for index, link in enumerate(links, 1):
            row = db.get_account(account_id)
            if not row:
                break
            if row["total_earned"] >= MAX_EARN_PER_ACCOUNT:
                await edit_progress(cid, context, f"🏆 Max limit ₹{MAX_EARN_PER_ACCOUNT} reached!")
                return
            
            gained = 0.0
            try:
                result = await asyncio.to_thread(client.collect_reward, row)
                amount = parse_reward_amount(result)
                if amount > 0:
                    gained = amount
                    db.log(row["id"], link["id"], "claim", amount, "ok")
                    last_ok = True
                else:
                    db.log(row["id"], link["id"], "claim", 0, "no_reward")
                    last_ok = False
            except Exception as exc:
                db.log(row["id"], link["id"], "claim", 0, "failed")
                last_ok = False
                log.warning("Claim failed: %s", exc)
            
            db.add_earned(row["id"], gained)
            total_new += gained
            done += 1
            await edit_progress(cid, context, progress_text(done, len(links), total_new, last_ok))
            await asyncio.sleep(REQUEST_DELAY)
        
        row = db.get_account(account_id)
        if done > 0:
            streak = db.finish_collection(account_id)
        final_total = row["total_earned"] if row else total_new
        await edit_progress(cid, context, final_text(done, len(links), total_new, final_total, streak))
        
    except Exception as exc:
        log.exception("collection crashed")
        try:
            await edit_progress(cid, context, f"❌ <b>Error:</b> {str(exc)[:200]}")
        except:
            pass
    finally:
        progress_messages.pop(cid, None)
        collecting_tasks.pop(cid, None)

async def send_plain(update, context, text):
    cid = chat_id(update)
    if not cid:
        return None
    return await context.bot.send_message(cid, text, parse_mode=ParseMode.HTML)

# ============================== MENU HANDLERS ==============================

async def accounts_menu(update):
    user_id = tg_id(update)
    accounts = db.get_accounts(user_id)
    if not accounts:
        await answer(update, "❌ No accounts linked.", main_menu(update))
        return
    lines = [f"{'🟢' if a['active'] else '⚪'} <b>+{a['phone']}</b> — ₹{a['total_earned']:.2f}" for a in accounts]
    rows = [[InlineKeyboardButton(f"{'✅' if a['active'] else '👆'} +{a['phone']}", callback_data=f"pick_{a['id']}"),
             InlineKeyboardButton("🗑️", callback_data=f"logout_{a['id']}")] for a in accounts]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="btn_back")])
    await answer(update, "👤 <b>Your Accounts</b>\n\n" + "\n".join(lines), InlineKeyboardMarkup(rows))

async def pick_account(update, account_id):
    user_id = tg_id(update)
    account = db.get_account(int(account_id))
    if not account or account["telegram_id"] != user_id:
        await answer(update, "❌ Account not found.", main_menu(update))
        return
    db.set_active(user_id, int(account_id))
    await answer(update, f"✅ Active: +{account['phone']}", main_menu(update))

async def logout_account(update, account_id):
    user_id = tg_id(update)
    account = db.get_account(int(account_id))
    if not account or account["telegram_id"] != user_id:
        await answer(update, "❌ Account not found.", main_menu(update))
        return
    db.remove_account(int(account_id))
    await answer(update, f"🗑️ Removed +{account['phone']}", main_menu(update))

async def collect_menu(update, context):
    user_id = tg_id(update)
    cid = chat_id(update)
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        await answer(update, "⏳ Collection running...", main_menu(update))
        return
    accounts = db.get_accounts(user_id)
    if not accounts:
        await answer(update, "❌ No accounts. Login first.", main_menu(update))
        return
    active = db.get_active_account(user_id)
    account = active or accounts[0]
    already, streak = db.has_collected_today(account["id"])
    if already:
        today_earned = db.today_earnings(account["id"])
        await answer(update, f"✅ Already collected today!\n💰 ₹{today_earned:.2f}\n🔥 {streak} days", main_menu(update))
        return
    start_collection(update, context, account)
    await answer(update, f"🎁 Starting collection for +{account['phone']}...", main_menu(update))

async def stats_menu(update):
    user_id = tg_id(update)
    accounts, total = db.get_stats(user_id)
    if not accounts:
        await answer(update, "❌ No accounts.", main_menu(update))
        return
    lines = []
    for a in accounts:
        streak = a.get("streak_days") or 0
        collected = (a.get("last_collection_date") or "") == today_ist()
        today_earned = db.today_earnings(a["id"]) if collected else 0.0
        line = f"📱 +{a['phone']} → ₹{a['total_earned']:.2f}"
        if a["active"]:
            line += " 🟢"
        if streak > 0:
            line += f" | 🔥 {streak}d"
        if collected:
            line += f" | ✅ ₹{today_earned:.2f}"
        lines.append(line)
    await answer(update, "📊 <b>Your Stats</b>\n\n" + "\n".join(lines) + f"\n\n💰 <b>Total: ₹{total:.2f}</b>\n\n{BRAND}", main_menu(update))

async def help_menu(update):
    await answer(update, "<b>🤖 How to Use</b>\n\n1️⃣ Login with phone\n2️⃣ Enter OTP\n3️⃣ Tap Collect Buzz\n\n📅 Collect once per day\n💰 ₹100 per day per account", main_menu(update))

# ============================== ADMIN HANDLERS ==============================

async def admin_links_add(update, context):
    context.user_data["adm_add"] = True
    await answer(update, "📎 Send links one per line.", admin_menu())

async def admin_links_view(update):
    links = db.get_all_links()
    if not links:
        await answer(update, "📋 No links.", admin_menu())
        return
    lines = [f"{i}. <code>{l['campaign_id']}</code>" for i, l in enumerate(links[:50], 1)]
    await answer(update, f"📋 <b>Total: {len(links)}</b>\n\n" + "\n".join(lines), admin_menu())

async def admin_links_delete(update):
    links = db.get_all_links()
    if not links:
        await answer(update, "📋 No links to delete.", admin_menu())
        return
    rows = [[InlineKeyboardButton(f"🗑 {l['campaign_id']}", callback_data=f"del_{l['id']}")] for l in links[:30]]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="btn_admin")])
    await answer(update, "🗑 Tap to delete:", InlineKeyboardMarkup(rows))

async def admin_links_received(update, context):
    if not context.user_data.get("adm_add"):
        return
    user_id = tg_id(update)
    if not is_admin(user_id):
        context.user_data["adm_add"] = False
        return
    context.user_data["adm_add"] = False
    text = update.message.text.strip()
    entries = []
    for line in text.splitlines():
        line = line.strip()
        match = re.search(r"buzzstreaks/([^/?#\s]+)", line)
        if match:
            campaign_id = match.group(1).rstrip("=")
            entries.append((line, campaign_id))
    if not entries:
        await update.message.reply_text("❌ No valid links found.")
        return
    added = db.add_links(entries, user_id)
    await update.message.reply_text(f"✅ Added <b>{added}</b> new links (duplicates skipped).", parse_mode=ParseMode.HTML)

async def admin_stats(update):
    accounts = db.all_accounts()
    if not accounts:
        await answer(update, "📊 No users.", admin_menu())
        return
    lines = [f"👤 {a['telegram_id']} 📱 +{a['phone']} → ₹{a['total_earned']:.2f}" for a in accounts[:40]]
    await answer(update, "📊 <b>All Users</b>\n\n" + "\n".join(lines), admin_menu())

async def admin_earnings(update):
    total = db.total_earnings()
    logs = db.total_logs()
    accounts = db.all_accounts()
    text = f"💰 <b>Total: ₹{total:.2f}</b> ({logs} claims)\n\n"
    lines = [f"📱 +{a['phone']} → ₹{a['total_earned']:.2f}" for a in accounts[:40]]
    await answer(update, text + "\n".join(lines), admin_menu())

# ============================== CALLBACK HANDLER ==============================

async def on_callback(update, context):
    query = update.callback_query
    user_id = tg_id(update)
    if not user_id:
        await query.answer("Session expired.", show_alert=True)
        return
    data = query.data or ""
    await query.answer()
    
    try:
        if data == "btn_back":
            await answer(update, "🔹 <b>Main Menu</b>", main_menu(update))
        elif data == "btn_login":
            await login_start(update, context)
        elif data == "btn_accounts":
            await accounts_menu(update)
        elif data == "btn_collect":
            await collect_menu(update, context)
        elif data == "btn_stats":
            await stats_menu(update)
        elif data == "btn_help":
            await help_menu(update)
        elif data == "btn_admin":
            if is_admin(user_id):
                await answer(update, "👑 <b>Admin Panel</b>", admin_menu())
        elif data == "adm_add":
            await admin_links_add(update, context)
        elif data == "adm_links":
            await admin_links_view(update)
        elif data == "adm_del":
            await admin_links_delete(update)
        elif data == "adm_stats":
            await admin_stats(update)
        elif data == "adm_earn":
            await admin_earnings(update)
        elif data.startswith("pick_"):
            await pick_account(update, data.split("_", 1)[1])
        elif data.startswith("logout_"):
            await logout_account(update, data.split("_", 1)[1])
        elif data.startswith("del_"):
            link_id = int(data.split("_", 1)[1])
            db.delete_link(link_id)
            await admin_links_delete(update)
    except Exception as exc:
        log.exception("callback error")
        await answer(update, f"❌ Error: {str(exc)[:150]}", main_menu(update))

async def error_handler(update, context):
    log.error("Update %s caused error %s", update, context.error)

# ============================== MAIN ==============================

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_start, pattern="^btn_login$")],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_received)],
        },
        fallbacks=[
            CallbackQueryHandler(conv_fallback, pattern="^btn_"),
            CommandHandler("start", start),
            CommandHandler("cancel", cancel_login),
        ],
        allow_reentry=True,
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_links_received))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(error_handler)
    
    log.info("🚀 Swiggy Buzz bot starting...")
    log.info(f"📋 Total links loaded: {len(db.get_all_links())}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
