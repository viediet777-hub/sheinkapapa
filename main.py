#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SWIGGY BUZZ AUTO-COLLECTOR BOT
Complete Single Script - Railway Ready - FIXED
Made by @viediet
"""

import os
import sys
import json
import time
import re
import sqlite3
import threading
import asyncio
import logging
import html
import uuid
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ==================== CONFIG (ENV VARIABLES) ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN environment variable required!")
    sys.exit(1)

ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS", "")
if admin_ids_str:
    for x in admin_ids_str.split(","):
        try:
            ADMIN_IDS.append(int(x.strip()))
        except:
            pass
if not ADMIN_IDS:
    ADMIN_IDS = [1364476174]

DB_PATH = os.getenv("DB_PATH", "swiggy_buzz.db")
MAX_EARN_PER_ACCOUNT = float(os.getenv("MAX_EARN_PER_ACCOUNT", "1000"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.8"))
BRAND = "⚡ Made by @viediet"

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

# ==================== LOGGING ====================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("buzzbot")

# ==================== DATABASE ====================
class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    token TEXT,
                    tid TEXT,
                    sid TEXT,
                    device_id TEXT,
                    swuid TEXT,
                    customer_id TEXT,
                    total_earned REAL DEFAULT 0,
                    active INTEGER DEFAULT 0,
                    created_at TEXT
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
                """
            )
            self._conn.commit()

    def _execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_account(self, telegram_id, phone, device_id, swuid, token, tid, sid, customer_id):
        now_s = self.now()
        cur = self._execute(
            "SELECT id FROM accounts WHERE telegram_id = ? AND phone = ?",
            (telegram_id, phone),
        )
        row = cur.fetchone()
        if row:
            self._execute(
                "UPDATE accounts SET token = ?, tid = ?, sid = ?, device_id = ?, swuid = ?, customer_id = ?, active = 1 WHERE id = ?",
                (token, tid, sid, device_id, swuid, customer_id, row["id"]),
            )
            account_id = row["id"]
        else:
            cur = self._execute(
                "INSERT INTO accounts (telegram_id, phone, token, tid, sid, device_id, swuid, customer_id, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (telegram_id, phone, token, tid, sid, device_id, swuid, customer_id, now_s),
            )
            account_id = cur.lastrowid
        self._execute("UPDATE accounts SET active = 0 WHERE telegram_id = ? AND id != ?", (telegram_id, account_id))
        return account_id

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

    def add_links(self, entries, added_by):
        count = 0
        for url, campaign_id in entries:
            cur = self._execute(
                "INSERT OR IGNORE INTO buzz_links (link_url, campaign_id, added_by, created_at) VALUES (?, ?, ?, ?)",
                (url, campaign_id, added_by, self.now()),
            )
            if cur.rowcount:
                count += 1
        return count

    def get_all_links(self):
        cur = self._execute("SELECT * FROM buzz_links ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def delete_link(self, link_id):
        self._execute("DELETE FROM buzz_links WHERE id = ?", (link_id,))

    def log(self, account_id, link_id, action, amount, status):
        self._execute(
            "INSERT INTO buzz_logs (account_id, link_id, action, amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, link_id, action, amount, status, self.now()),
        )

    def add_earned(self, account_id, amount):
        self._execute("UPDATE accounts SET total_earned = total_earned + ? WHERE id = ?", (amount, account_id))

    def get_stats(self, telegram_id):
        accounts = self.get_accounts(telegram_id)
        total = sum(a["total_earned"] for a in accounts)
        return accounts, total

    def all_accounts(self):
        cur = self._execute("SELECT * FROM accounts ORDER BY telegram_id, id")
        return [dict(r) for r in cur.fetchall()]

    def total_earnings(self):
        cur = self._execute("SELECT COALESCE(SUM(amount), 0) AS total FROM buzz_logs")
        return cur.fetchone()["total"]

    def total_logs(self):
        cur = self._execute("SELECT COUNT(*) AS total FROM buzz_logs")
        return cur.fetchone()["total"]

db = Database()

# ==================== SWIGGY API ====================
CAMPAIGN_ID_RE = re.compile(r"buzzstreaks/([^/?#\s]+)")
FALLBACK_CAMPAIGN_RE = re.compile(r"([A-Za-z0-9]{4,}_[A-Za-z0-9]{3,})")
REWARD_KEYS = ("amount", "rewardvalue", "reward_amount", "points", "earned", "cashback")

def generate_device_id():
    return str(uuid.uuid4()).upper()

def generate_swuid():
    return "SW-" + uuid.uuid4().hex[:12].upper()

def extract_campaign_id(url):
    if not url:
        return None
    match = CAMPAIGN_ID_RE.search(url)
    if match:
        return match.group(1)
    match = FALLBACK_CAMPAIGN_RE.search(url)
    return match.group(1) if match else None

def find_key(node, key, depth=0):
    if depth > 10 or not isinstance(node, (dict, list)):
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() == key.lower():
                return v
            found = find_key(v, key, depth + 1)
            if found:
                return found
    else:
        for item in node:
            found = find_key(item, key, depth + 1)
            if found:
                return found
    return None

def parse_session(data):
    """Parse login response to extract token, tid, sid, customer_id"""
    log.info(f"Parsing session data: {json.dumps(data)[:500]}")
    
    result = {
        "token": "",
        "tid": "",
        "sid": "",
        "customer_id": "",
    }
    
    # Try to get from data.data first
    if isinstance(data, dict):
        # Direct fields in response
        result["tid"] = data.get("tid", "")
        result["sid"] = data.get("sid", "")
        
        # Get token from data.data
        inner_data = data.get("data", {})
        if isinstance(inner_data, dict):
            result["token"] = inner_data.get("token", "")
            result["customer_id"] = str(inner_data.get("customer_id", ""))
            
            # Also check juspay for customer_id
            if not result["customer_id"]:
                juspay = inner_data.get("juspay", {})
                if isinstance(juspay, dict):
                    result["customer_id"] = str(juspay.get("customer_id", ""))
        
        # If token still not found, search deeper
        if not result["token"]:
            result["token"] = find_key(data, "token") or ""
        
        if not result["customer_id"]:
            customer_id = find_key(data, "customer_id")
            if customer_id:
                result["customer_id"] = str(customer_id)
    
    log.info(f"Parsed: token={result['token'][:30] if result['token'] else 'None'}..., tid={result['tid'][:30] if result['tid'] else 'None'}...")
    return result

def parse_amount(payload):
    total = 0.0
    def walk(node):
        nonlocal total
        if isinstance(node, dict):
            for key, val in node.items():
                lowered = str(key).lower()
                if lowered in REWARD_KEYS and isinstance(val, (int, float)) and val > 0:
                    total += float(val)
                elif isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item)
    walk(payload)
    return round(total, 2)

def is_success(data):
    if not isinstance(data, dict):
        return True
    if data.get("errors"):
        return False
    code = data.get("statusCode", data.get("code"))
    if isinstance(code, int):
        return code in (0, 200)
    if isinstance(code, str):
        return code.lower() in ("success", "ok", "200", "0")
    return True

class SwiggyClient:
    def __init__(self, device_id=None, swuid=None):
        self.device_id = device_id or generate_device_id()
        self.swuid = swuid or generate_swuid()
        self.session = requests.Session()

    def _headers(self, extra=None):
        headers = dict(BASE_HEADERS)
        headers["deviceid"] = self.device_id
        headers["swuid"] = self.swuid
        if extra:
            headers.update(extra)
        return headers

    def send_otp(self, phone):
        resp = self.session.get(f"{OTP_URL}?mobile={phone}", headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        try:
            data = resp.json()
        except ValueError:
            return {"status": "error", "message": "Invalid response from server"}
        if "captcha" in json.dumps(data).lower():
            return {"status": "captcha", "message": "OTP blocked by captcha. Try again later or from a fresh device."}
        if not isinstance(data, dict) or data.get("errorCode") or data.get("errorMessage"):
            return {"status": "error", "message": str(data)[:300]}
        return {"status": "ok", "data": data}

    def verify_otp(self, phone, otp):
        body = {
            "cloningSignalsData": {
                "versionCode": 1795,
                "appVersion": "4.113.0",
                "osVersion": "11",
                "osName": "android",
                "deviceId": self.device_id,
            },
            "otp": otp,
        }
        resp = self.session.post(
            f"{VERIFY_URL}?otp_source=Sms-manual",
            headers=self._headers(),
            json=body,
            timeout=30,
        )
        if resp.status_code != 200:
            raise Exception(f"Login verify returned HTTP {resp.status_code}")
        return resp.json()

    def _auth_headers(self, account):
        headers = self._headers()
        if account.get("token"):
            headers["token"] = account["token"]
        if account.get("tid"):
            headers["tid"] = account["tid"]
        if account.get("sid"):
            headers["sid"] = account["sid"]
        return headers

    def _post(self, url, headers, body, attempts=2):
        last_exc = None
        for attempt in range(attempts + 1):
            try:
                resp = self.session.post(url, headers=headers, json=body, timeout=30)
                data = resp.json() if resp.content else {}
                if not is_success(data):
                    last_exc = Exception(f"API error: {json.dumps(data)[:300]}")
                else:
                    return data
            except Exception as exc:
                last_exc = Exception(f"network error: {exc}")
            if attempt < attempts:
                time.sleep(1.5 * (attempt + 1))
        raise last_exc

    def collect_campaign(self, account, campaign_id, client_id="web"):
        body = {
            "clientId": client_id,
            "campaignIds": [campaign_id],
            "userId": account.get("customer_id", ""),
        }
        return self._post(REWARDS_URL, self._auth_headers(account), body)

    def buzz_back(self, account, campaign_id):
        return self.collect_campaign(account, campaign_id, client_id="portal_invite")

# ==================== BOT VARIABLES ====================
login_sessions = {}
progress_messages = {}
collecting_tasks = {}

# ==================== BOT HELPERS ====================
def tg_id(update):
    user = update.effective_user
    return user.id if user else 0

def chat_id(update):
    chat = update.effective_chat
    return chat.id if chat else 0

def is_admin(user_id):
    return user_id in ADMIN_IDS

def main_menu(update):
    user_id = tg_id(update)
    rows = [
        [
            InlineKeyboardButton("🔐 Login Account", callback_data="btn_login"),
            InlineKeyboardButton("👤 My Accounts", callback_data="btn_accounts"),
        ],
        [InlineKeyboardButton("🎁 Collect Buzz", callback_data="btn_collect")],
        [
            InlineKeyboardButton("📊 My Stats", callback_data="btn_stats"),
            InlineKeyboardButton("🆘 Help", callback_data="btn_help"),
        ],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="btn_admin")])
    return InlineKeyboardMarkup(rows)

def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Links", callback_data="adm_add"),
            InlineKeyboardButton("📋 View Links", callback_data="adm_links"),
        ],
        [
            InlineKeyboardButton("🗑 Delete Link", callback_data="adm_del"),
            InlineKeyboardButton("📊 User Stats", callback_data="adm_stats"),
        ],
        [InlineKeyboardButton("💰 Earnings", callback_data="adm_earn")],
        [InlineKeyboardButton("⬅️ Back", callback_data="btn_back")],
    ])

async def answer(update, text, markup=None):
    query = update.callback_query
    if query is not None:
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return
        except BadRequest as exc:
            if "message is not modified" not in str(exc):
                log.debug("edit_message_text failed: %s", exc)
    message = update.message
    if message is not None:
        await message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

async def send_plain(update, text):
    cid = chat_id(update)
    if not cid:
        return None
    bot = update.get_bot()
    return await bot.send_message(cid, text, parse_mode=ParseMode.HTML)

# ==================== BOT HANDLERS ====================
async def start(update, context):
    if not tg_id(update):
        return
    text = (
        "<b>🤖 Swiggy Buzz Auto-Collector</b>\n\n"
        "Collect all your Swiggy Buzz rewards automatically — no manual clicking!\n\n"
        f"{BRAND}"
    )
    await update.message.reply_text(text, reply_markup=main_menu(update), parse_mode=ParseMode.HTML)

async def cancel_login(update, context):
    user_id = tg_id(update)
    login_sessions.pop(user_id, None)
    if update.message is not None:
        await update.message.reply_text("❌ Login cancelled.", reply_markup=main_menu(update), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def conv_fallback(update, context):
    user_id = tg_id(update)
    login_sessions.pop(user_id, None)
    await answer(update, "👈 Login flow reset.\n\nUse the buttons below.", main_menu(update))
    return ConversationHandler.END

async def login_start(update, context):
    user_id = tg_id(update)
    if not user_id:
        return ConversationHandler.END
    login_sessions.pop(user_id, None)
    await answer(
        update,
        "📱 <b>Login to Swiggy Buzz</b>\n\n"
        "Enter your phone number with country code.\n\n"
        "Example: <code>919876543210</code>\n\n"
        "Send /cancel to abort.",
    )
    return "PHONE"

async def phone_received(update, context):
    user_id = tg_id(update)
    if not user_id:
        return ConversationHandler.END
    phone = (update.message.text or "").strip()
    if not phone.isdigit() or not (10 <= len(phone) <= 13):
        await update.message.reply_text(
            "❌ Invalid phone number. Use digits only, e.g. <code>919876543210</code>",
            parse_mode=ParseMode.HTML,
        )
        return "PHONE"
    client = SwiggyClient()
    try:
        status = await asyncio.to_thread(client.send_otp, phone)
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Could not send OTP: {html.escape(str(exc)[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return "PHONE"
    if status.get("status") != "ok":
        await update.message.reply_text(
            f"❌ OTP request failed:\n{html.escape(str(status.get('message', 'unknown error'))[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return "PHONE"
    login_sessions[user_id] = {"phone": phone, "client": client}
    await update.message.reply_text(
        "✅ <b>OTP sent!</b>\n\nEnter the 6-digit OTP you received on your phone:",
        parse_mode=ParseMode.HTML,
    )
    return "OTP"

async def otp_received(update, context):
    user_id = tg_id(update)
    if not user_id:
        return ConversationHandler.END
    session = login_sessions.get(user_id)
    if not session:
        await update.message.reply_text("⏳ Session expired. Send /start and tap 🔐 Login Account again.")
        return ConversationHandler.END
    otp = (update.message.text or "").strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ Invalid OTP. Enter the 6-digit code:")
        return "OTP"
    client = session["client"]
    try:
        log.info(f"Verifying OTP: {otp} for phone: {session['phone']}")
        data = await asyncio.to_thread(client.verify_otp, session["phone"], otp)
        log.info(f"Verify response received")
    except Exception as exc:
        log.error(f"OTP verification error: {exc}")
        await update.message.reply_text(
            f"❌ OTP verification failed: {html.escape(str(exc)[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return "OTP"
    
    login_info = parse_session(data)
    log.info(f"Parsed login info: token={login_info['token'][:30] if login_info['token'] else 'None'}...")
    
    if not login_info.get("token"):
        await update.message.reply_text("❌ Login failed. Check the OTP and try again.", parse_mode=ParseMode.HTML)
        return "OTP"
    
    account_id = db.add_account(
        user_id,
        session["phone"],
        client.device_id,
        client.swuid,
        login_info["token"],
        login_info["tid"],
        login_info["sid"],
        login_info["customer_id"],
    )
    login_sessions.pop(user_id, None)
    account = db.get_account(account_id)
    if not account:
        await update.message.reply_text("❌ Could not save account. Try again.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    await update.message.reply_text(
        f"✅ <b>Logged in as +{html.escape(session['phone'])}</b>\n\n🎁 Auto-collection started...",
        parse_mode=ParseMode.HTML,
    )
    start_collection(update, account)
    await update.message.reply_text("🔹 <b>Main Menu</b>\n\n" + BRAND, reply_markup=main_menu(update), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

def start_collection(update, account):
    cid = chat_id(update)
    if not cid:
        return
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        log.info("collection already running for chat %s", cid)
        return
    collecting_tasks[cid] = asyncio.create_task(run_collection(update, account))

def progress_text(done, total, earned, last_ok):
    bar_len = 12
    filled = min(bar_len, int(bar_len * done / max(total, 1)))
    bar = "🟩" * filled + "⬜" * (bar_len - filled)
    mark = "✅" if last_ok else "❌"
    return (
        f"🎁 <b>Collecting... [{done}/{total}]</b>\n"
        f"{bar}\n"
        f"💰 Earned: ₹{earned:.2f}\n"
        f"Last result: {mark}"
    )

def final_text(done, total, earned, account_total):
    return (
        f"✅ <b>Collection finished! [{done}/{total}]</b>\n\n"
        f"💰 This run: ₹{earned:.2f}\n"
        f"🏆 Account total: ₹{account_total:.2f}\n\n"
        f"{BRAND}"
    )

async def edit_progress(cid, update, text):
    msg_id = progress_messages.get(cid)
    if not msg_id:
        return
    bot = update.get_bot()
    try:
        await bot.edit_message_text(text, chat_id=cid, message_id=msg_id, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "message is not modified" not in str(exc):
            log.warning("progress edit failed: %s", exc)
    except Exception as exc:
        log.warning("progress edit failed: %s", exc)

async def run_collection(update, account):
    cid = chat_id(update)
    client = SwiggyClient(device_id=account["device_id"], swuid=account["swuid"])
    links = db.get_all_links()
    total_new = 0.0
    done = 0
    last_ok = True
    try:
        if not links:
            await send_plain(update, "📭 No buzz links added yet. Ask an admin to add links first.")
            return
        first = await send_plain(update, "🎁 <b>Collecting... [0/0]</b>\n\nStarting...")
        if first is not None:
            progress_messages[cid] = first.message_id
        for index, link in enumerate(links, 1):
            row = db.get_account(account["id"])
            if not row:
                break
            if row["total_earned"] >= MAX_EARN_PER_ACCOUNT:
                await edit_progress(
                    cid,
                    update,
                    f"🏆 <b>Max limit ₹{MAX_EARN_PER_ACCOUNT:g} reached!</b>\n\n"
                    f"Total earned: ₹{row['total_earned']:.2f}\n\n{BRAND}",
                )
                return
            gained = 0.0
            try:
                result = await asyncio.to_thread(client.collect_campaign, row, link["campaign_id"], "web")
                gained += parse_amount(result)
                db.log(row["id"], link["id"], "open", parse_amount(result), "ok")
                last_ok = True
            except Exception as exc:
                db.log(row["id"], link["id"], "open", 0, "failed")
                last_ok = False
                log.warning("open failed for %s: %s", link["campaign_id"], exc)
            try:
                back = await asyncio.to_thread(client.buzz_back, row, link["campaign_id"])
                back_amt = parse_amount(back)
                gained += back_amt
                db.log(row["id"], link["id"], "buzz_back", back_amt, "ok")
            except Exception as exc:
                db.log(row["id"], link["id"], "buzz_back", 0, "failed")
                log.warning("buzz_back failed for %s: %s", link["campaign_id"], exc)
            db.add_earned(row["id"], gained)
            total_new += gained
            done += 1
            await edit_progress(cid, update, progress_text(done, len(links), total_new, last_ok))
            await asyncio.sleep(REQUEST_DELAY)
        row = db.get_account(account["id"])
        final_total = row["total_earned"] if row else total_new
        await edit_progress(cid, update, final_text(done, len(links), total_new, final_total))
    except Exception as exc:
        log.exception("collection crashed")
        try:
            await edit_progress(cid, update, f"❌ <b>Collection stopped:</b> {html.escape(str(exc)[:200])}")
        except Exception:
            pass
    finally:
        progress_messages.pop(cid, None)
        collecting_tasks.pop(cid, None)

async def accounts_menu(update):
    user_id = tg_id(update)
    accounts = db.get_accounts(user_id)
    if not accounts:
        await answer(update, "❌ No accounts linked yet.\n\nTap 🔐 Login Account to add one.", main_menu(update))
        return
    lines = [
        f"{'🟢' if a['active'] else '⚪'} <b>+{html.escape(a['phone'])}</b> — ₹{a['total_earned']:.2f}"
        for a in accounts
    ]
    rows = [
        [
            InlineKeyboardButton(f"{'✅' if a['active'] else '👆'} +{a['phone']}", callback_data=f"pick_{a['id']}"),
            InlineKeyboardButton("🗑️", callback_data=f"logout_{a['id']}"),
        ]
        for a in accounts
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="btn_back")])
    text = "👤 <b>Your Accounts</b>\n\n" + "\n".join(lines) + "\n\nTap to switch active account. 🗑️ removes it."
    await answer(update, text, InlineKeyboardMarkup(rows))

async def pick_account(update, account_id):
    user_id = tg_id(update)
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return
    account = db.get_account(account_id)
    if not account or account["telegram_id"] != user_id:
        await answer(update, "❌ Account not found.", main_menu(update))
        return
    db.set_active(user_id, account_id)
    await answer(
        update,
        f"✅ Active account set to <b>+{html.escape(account['phone'])}</b>\n\n🎁 Tap Collect Buzz to start collecting.",
        main_menu(update),
    )

async def logout_account(update, account_id):
    user_id = tg_id(update)
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return
    account = db.get_account(account_id)
    if not account or account["telegram_id"] != user_id:
        await answer(update, "❌ Account not found.", main_menu(update))
        return
    db.remove_account(account_id)
    remaining = db.get_active_account(user_id)
    if not remaining:
        others = db.get_accounts(user_id)
        if others:
            db.set_active(user_id, others[0]["id"])
    await answer(update, f"🗑️ Removed <b>+{html.escape(account['phone'])}</b>.", main_menu(update))

async def collect_menu(update):
    user_id = tg_id(update)
    cid = chat_id(update)
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        await answer(update, "⏳ Collection is already running. Please wait...", main_menu(update))
        return
    accounts = db.get_accounts(user_id)
    if not accounts:
        await answer(update, "❌ No accounts linked yet. Tap 🔐 Login Account first.", main_menu(update))
        return
    active = db.get_active_account(user_id)
    if len(accounts) == 1 or active:
        account = active or accounts[0]
        start_collection(update, account)
        await answer(update, f"🎁 Starting collection for <b>+{html.escape(account['phone'])}</b>...", main_menu(update))
        return
    rows = [[InlineKeyboardButton(f"📱 +{a['phone']}", callback_data=f"pick_{a['id']}")] for a in accounts]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="btn_back")])
    await answer(update, "🎁 <b>Choose an account to collect with:</b>", InlineKeyboardMarkup(rows))

async def stats_menu(update):
    user_id = tg_id(update)
    accounts, total = db.get_stats(user_id)
    if not accounts:
        await answer(update, "❌ No accounts yet.", main_menu(update))
        return
    lines = [
        f"📱 +{html.escape(a['phone'])} → ₹{a['total_earned']:.2f}{' 🟢' if a['active'] else ''}"
        for a in accounts
    ]
    text = (
        "📊 <b>Your Stats</b>\n\n"
        + "\n".join(lines)
        + f"\n\n💰 <b>Total earned: ₹{total:.2f}</b>\n\n{BRAND}"
    )
    await answer(update, text, main_menu(update))

async def help_menu(update):
    text = (
        "<b>🤖 How to use Swiggy Buzz Auto-Collector</b>\n\n"
        "1️⃣ Tap <b>🔐 Login Account</b>\n"
        "2️⃣ Enter your phone number with country code\n"
        "3️⃣ Enter the OTP you receive\n"
        "4️⃣ Buzz rewards are collected automatically\n\n"
        "💰 Opening a link + buzz-back = ₹2-10 per link\n"
        f"🏆 Max ₹{MAX_EARN_PER_ACCOUNT:g} per account\n\n"
        f"{BRAND}"
    )
    await answer(update, text, main_menu(update))

async def view_links(update):
    links = db.get_all_links()
    if not links:
        await answer(update, "📋 <b>No links yet.</b>\n\nUse ➕ Add Links to add buzz links.", admin_menu())
        return
    total = len(links)
    lines = [f"{i}. <code>{html.escape(l['campaign_id'])}</code>" for i, l in enumerate(links[:50], 1)]
    more = f"\n... and {total - 50} more" if total > 50 else ""
    await answer(update, f"📋 <b>Total links: {total}</b>\n\n" + "\n".join(lines) + more, admin_menu())

async def delete_menu(update):
    links = db.get_all_links()
    if not links:
        await answer(update, "📋 No links to delete.", admin_menu())
        return
    rows = [
        [InlineKeyboardButton(f"🗑 {html.escape(l['campaign_id'])}", callback_data=f"del_{l['id']}")]
        for l in links[:30]
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="btn_admin")])
    await answer(update, "🗑 <b>Tap a link to delete it:</b>", InlineKeyboardMarkup(rows))

async def delete_link(update, link_id):
    user_id = tg_id(update)
    if not is_admin(user_id):
        await answer(update, "⛔ Admin only.", main_menu(update))
        return
    try:
        link_id = int(link_id)
    except (TypeError, ValueError):
        return
    db.delete_link(link_id)
    await delete_menu(update)

async def admin_actions(update, context, data):
    user_id = tg_id(update)
    if not is_admin(user_id):
        await answer(update, "⛔ Admin only.", main_menu(update))
        return
    if data == "adm_add":
        context.user_data["adm_add"] = True
        await answer(
            update,
            "📎 <b>Send buzz links</b>, one per line.\n\nThey will be added automatically.\n\n"
            "Example:\n<code>https://r.swiggy.com/buzzstreaks/ougwl_abc123</code>",
            admin_menu(),
        )
    elif data == "adm_links":
        await view_links(update)
    elif data == "adm_del":
        await delete_menu(update)
    elif data == "adm_stats":
        accounts = db.all_accounts()
        if not accounts:
            await answer(update, "📊 No users yet.", admin_menu())
            return
        lines = [
            f"👤 tg:{a['telegram_id']} 📱 +{html.escape(a['phone'])} → ₹{a['total_earned']:.2f}"
            for a in accounts
        ]
        text = "📊 <b>All Accounts</b>\n\n" + "\n".join(lines[:40])
        if len(lines) > 40:
            text += f"\n... and {len(lines) - 40} more"
        await answer(update, text, admin_menu())
    elif data == "adm_earn":
        total = db.total_earnings()
        logs = db.total_logs()
        accounts = db.all_accounts()
        text = f"💰 <b>Total earnings: ₹{total:.2f}</b> ({logs} collect actions)\n\n"
        lines = [f"📱 +{html.escape(a['phone'])} → ₹{a['total_earned']:.2f}" for a in accounts]
        text += "\n".join(lines[:40])
        await answer(update, text, admin_menu())

async def admin_links_received(update, context):
    user_id = tg_id(update)
    if not context.user_data.get("adm_add"):
        return
    if not is_admin(user_id):
        context.user_data["adm_add"] = False
        return
    context.user_data["adm_add"] = False
    text = (update.message.text or "").strip()
    entries = []
    for line in text.splitlines():
        line = line.strip()
        cid = extract_campaign_id(line)
        if cid:
            entries.append((line, cid))
    if not entries:
        await update.message.reply_text(
            "❌ No valid buzz links found. Campaign IDs must look like <code>ougwl_xxxxx</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    added = db.add_links(entries, user_id)
    await update.message.reply_text(
        f"✅ Added <b>{added}</b> new links (out of {len(entries)} valid).",
        parse_mode=ParseMode.HTML,
    )

async def on_callback(update, context):
    query = update.callback_query
    user_id = tg_id(update)
    if not user_id:
        await query.answer("Session expired. Send /start.", show_alert=True)
        return
    data = query.data or ""
    await query.answer()
    try:
        if data == "btn_back":
            await answer(update, "🔹 <b>Main Menu</b>\n\n" + BRAND, main_menu(update))
        elif data == "btn_accounts":
            await accounts_menu(update)
        elif data == "btn_collect":
            await collect_menu(update)
        elif data == "btn_stats":
            await stats_menu(update)
        elif data == "btn_help":
            await help_menu(update)
        elif data == "btn_admin":
            if is_admin(user_id):
                await answer(update, "👑 <b>Admin Panel</b>", admin_menu())
        elif data.startswith("pick_"):
            await pick_account(update, data.split("_", 1)[1])
        elif data.startswith("logout_"):
            await logout_account(update, data.split("_", 1)[1])
        elif data.startswith("del_"):
            await delete_link(update, data.split("_", 1)[1])
        elif data.startswith("adm_"):
            await admin_actions(update, context, data)
    except Exception as exc:
        log.exception("callback error")
        await answer(update, f"❌ Something went wrong: {html.escape(str(exc)[:150])}", main_menu(update))

async def error_handler(update, context):
    log.error("Update %s caused error %s", update, context.error)

# ==================== MAIN ====================
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_start, pattern="^btn_login$")],
        states={
            "PHONE": [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received)],
            "OTP": [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_received)],
        },
        fallbacks=[
            CallbackQueryHandler(conv_fallback, pattern="^btn_"),
            CommandHandler("start", start),
            CommandHandler("cancel", cancel_login),
        ],
        allow_reentry=True,
    )
    app.add_handler(login_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_links_received))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(error_handler)

    log.info("🤖 Swiggy Buzz Auto-Collector Bot is running...")
    log.info(f"👑 Admin IDs: {ADMIN_IDS}")
    log.info(f"⚡ Made by @viediet")
    
    await app.bot.delete_webhook(drop_pending_updates=True)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
