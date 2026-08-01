#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SWIGGY BUZZ AUTO-COLLECTOR - WITH AUTO-ACCEPT
₹100/DAY WORKING
Made by Viediet
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

# ===== SWIGGY DIRECT API =====
BASE_URL = "https://profile.swiggy.com/api/v3/app"
SMS_OTP_URL = f"{BASE_URL}/sms_otp"
LOGIN_VERIFY_URL = f"{BASE_URL}/login/verify"
SPNS_BASE = "https://spns.swiggy.com/api/v1/campaign"
REWARDS_URL = f"{SPNS_BASE}/rewards"
ACTION_URL = f"{SPNS_BASE}/action"

# ===== TARGET USERS (from working website) =====
TARGET_USERS = [
    "9905454846", "8302374884", "9569907686", "6019557067",
    "8103200020", "9793231470", "6075716540", "6057085260",
    "6529467214", "4742565540", "2159541308", "5711812412",
    "4805096977", "6306972524", "5810763039", "5767374231",
    "5255411320", "9263536039", "9656243680", "7028403798",
    "2022592103", "4339594714", "9315838951", "5021810039",
    "4179533661", "3969482763", "5378219742", "4622672366",
    "6529938009", "8841032307", "7847081991", "8476578440",
    "4958316534", "6089374148", "3974751011", "9076113237",
    "2405719218", "8557791891", "5237191585", "7504061044",
    "7239858845", "5773101973", "9292974443", "8481419410",
    "4219735233", "9104704566", "3923205642", "1106827431",
    "9066285442", "8745675335"
]

# ============================== DATABASE ==============================

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def today_ist():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")

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
                    secrettoken TEXT,
                    customer_id TEXT,
                    total_earned REAL DEFAULT 0,
                    active INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_collection_date TEXT DEFAULT '',
                    streak_days INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS buzz_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    target_user_id TEXT,
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

    def add_account(self, telegram_id, phone, device_id, token, tid, sid, secrettoken, customer_id):
        cur = self._execute("SELECT id FROM accounts WHERE telegram_id = ? AND phone = ?", (telegram_id, phone))
        row = cur.fetchone()
        if row:
            self._execute("""UPDATE accounts SET token=?, tid=?, sid=?, device_id=?, secrettoken=?, customer_id=?, active=1 
                           WHERE id=?""", (token, tid, sid, device_id, secrettoken, customer_id, row["id"]))
            return row["id"]
        cur = self._execute("""INSERT INTO accounts 
                           (telegram_id, phone, token, tid, sid, device_id, secrettoken, customer_id, active, created_at) 
                           VALUES (?,?,?,?,?,?,?,?,1,?)""",
                           (telegram_id, phone, token, tid, sid, device_id, secrettoken, customer_id, now()))
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

    def has_collected_today(self, account_id):
        row = self._execute("SELECT last_collection_date, streak_days FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            return False, 0
        return (row["last_collection_date"] or "") == today_ist(), row["streak_days"] or 0

    def finish_collection(self, account_id):
        row = self._execute("SELECT last_collection_date, streak_days FROM accounts WHERE id = ?", (account_id,)).fetchone()
        streak = (row["streak_days"] or 0) + 1 if row else 1
        self._execute("UPDATE accounts SET last_collection_date = ?, streak_days = ? WHERE id = ?", (today_ist(), streak, account_id))
        return streak

    def log(self, account_id, target_user_id, action, amount, status):
        self._execute("INSERT INTO buzz_logs (account_id, target_user_id, action, amount, status, created_at) VALUES (?,?,?,?,?,?)",
                     (account_id, target_user_id, action, amount, status, now()))

    def add_earned(self, account_id, amount):
        self._execute("UPDATE accounts SET total_earned = total_earned + ? WHERE id = ?", (amount, account_id))
    
    def all_accounts(self):
        cur = self._execute("SELECT * FROM accounts ORDER BY telegram_id, id")
        return [dict(r) for r in cur.fetchall()]
    
    def total_earnings(self):
        cur = self._execute("SELECT COALESCE(SUM(total_earned), 0) AS total FROM accounts")
        return cur.fetchone()["total"]

db = Database()

# ============================== SWIGGY CLIENT ==============================

class SwiggyClient:
    def __init__(self):
        self.device_id = uuid.uuid4().hex[:16]
        self.session = requests.Session()
        self.tid = ""
        self.sid = ""
        self.token = ""
        self.secrettoken = ""
        self.customer_id = ""

    def _headers(self, extra=None):
        headers = {
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
            "user-agent": "Swiggy-Android/4.113.0 (Android 11; Pixel 4)",
            "deviceid": self.device_id,
            "swuid": self.device_id,
        }
        if self.tid:
            headers["tid"] = self.tid
        if self.sid:
            headers["sid"] = self.sid
        if self.token:
            headers["token"] = self.token
        if extra:
            headers.update(extra)
        return headers

    def send_otp(self, phone):
        url = f"{SMS_OTP_URL}?mobile={phone}"
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        data = resp.json()
        if data.get("statusCode") == 0:
            self.tid = data.get("tid", "")
            self.sid = data.get("sid", "")
            self.device_id = data.get("deviceId", self.device_id)
            return {"status": "ok"}
        return {"status": "error", "message": data.get("statusMessage", "Unknown error")}

    def verify_otp(self, phone, otp):
        url = f"{LOGIN_VERIFY_URL}?otp_source=Sms-manual"
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
        if resp.status_code != 200:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        data = resp.json()
        if data.get("statusCode") == 0:
            inner = data.get("data", {})
            self.token = inner.get("token", "")
            self.tid = data.get("tid", "")
            self.sid = data.get("sid", "")
            self.customer_id = str(inner.get("customer_id", ""))
            self.device_id = data.get("deviceId", self.device_id)
            self.secrettoken = self.token
            return {
                "status": "ok",
                "name": inner.get("name", ""),
                "secrettoken": self.secrettoken,
                "customer_id": self.customer_id,
            }
        return {"status": "error", "message": data.get("statusMessage", "Verification failed")}

    def _spns_headers(self, secrettoken=None):
        token = secrettoken or self.secrettoken
        headers = {
            "client-id": "portal",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4) AppleWebKit/537.36",
            "content-type": "application/json",
            "accept": "*/*",
            "origin": "https://webviews.swiggy.com",
            "x-requested-with": "in.swiggy.android",
            "referer": "https://webviews.swiggy.com/moments-iw/buzz-your-friend/",
        }
        if token:
            headers["token"] = token
        if self.tid:
            headers["tid"] = self.tid
        if self.sid:
            headers["sid"] = self.sid
        return headers

    def check_buzz(self, secrettoken=None):
        url = REWARDS_URL
        body = {
            "generalContext": {"requestContext": {"clientId": "portal_banner"}},
            "campaignRewardRequests": [{
                "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
                "campaignId": "ougwl",
                "rollingFreecashParams": {
                    "forceRefresh": True,
                    "requestParams": {"dataRequested": "wallet,connections,transactions", "source": "banner"}
                }
            }]
        }
        headers = self._spns_headers(secrettoken)
        resp = self.session.post(url, headers=headers, json=body, timeout=30)
        return resp.json() if resp.content else {}

    def initiate_buzz(self, target_user_id, secrettoken=None):
        url = ACTION_URL
        body = {
            "generalContext": {"requestContext": {"clientId": "portal_banner"}},
            "consumerContext": {"consumerId": self.customer_id},
            "campaignUserActionRequest": {
                "campaignId": "ougwl",
                "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
                "action": {"actionType": "ACTION_TYPE_CONNECT", "targetEntityId": target_user_id}
            }
        }
        headers = self._spns_headers(secrettoken)
        resp = self.session.post(url, headers=headers, json=body, timeout=30)
        return resp.json() if resp.content else {}

    def complete_buzz(self, target_user_id, secrettoken=None):
        url = ACTION_URL
        body = {
            "generalContext": {"requestContext": {"clientId": "portal_banner"}},
            "consumerContext": {"consumerId": self.customer_id},
            "campaignUserActionRequest": {
                "campaignId": "ougwl",
                "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
                "action": {"actionType": "ACTION_TYPE_ACCEPT", "targetEntityId": target_user_id}
            }
        }
        headers = self._spns_headers(secrettoken)
        resp = self.session.post(url, headers=headers, json=body, timeout=30)
        return resp.json() if resp.content else {}

    def auto_accept_incoming(self, secrettoken=None):
        """Auto-accept ALL pending requests from others"""
        token = secrettoken or self.secrettoken
        accepted = 0
        
        status = self.check_buzz(token)
        connections = status.get("data", {}).get("campaignRewardResponses", [{}])[0].get("connections", {}).get("connections", [])
        
        for conn in connections:
            progress = conn.get("progress", {})
            if progress.get("status") == "PROGRESS_STATUS_IN_PROGRESS_ACCEPT_INVITE_PENDING":
                user_id = conn.get("connectedUserId")
                resp = self.complete_buzz(user_id, token)
                if resp.get("statusCode") == 0:
                    accepted += 1
                    log.info(f"✅ Auto-accepted: {user_id}")
                time.sleep(0.3)
        
        return accepted

    def run_buzz_collection(self, secrettoken=None):
        token = secrettoken or self.secrettoken
        results = {"total_earned": 0, "successful": 0, "failed": 0}
        
        if not token:
            results["failed"] = 1
            return results
        
        # STEP 1: Auto-accept incoming requests
        accepted = self.auto_accept_incoming(token)
        log.info(f"✅ Auto-accepted {accepted} incoming requests")
        
        # STEP 2: Check initial balance
        status = self.check_buzz(token)
        initial_earned = self._extract_earned(status)
        log.info(f"[DEBUG] Initial earned: ₹{initial_earned}")
        
        # STEP 3: Send new requests
        for user_id in TARGET_USERS:
            try:
                init_resp = self.initiate_buzz(user_id, token)
                if init_resp.get("statusCode") != 0:
                    results["failed"] += 1
                    continue
                
                time.sleep(0.3)
                
                comp_resp = self.complete_buzz(user_id, token)
                if comp_resp.get("statusCode") == 0:
                    results["successful"] += 1
                else:
                    results["failed"] += 1
                
                time.sleep(0.3)
                
            except Exception as e:
                log.error(f"Error: {e}")
                results["failed"] += 1
        
        # STEP 4: Check final balance
        final_status = self.check_buzz(token)
        final_earned = self._extract_earned(final_status)
        results["total_earned"] = final_earned - initial_earned
        
        return results

    def _extract_earned(self, data):
        total = 0.0
        try:
            responses = data.get("data", {}).get("campaignRewardResponses", [])
            for resp in responses:
                rewards = resp.get("rewards", [])
                for reward in rewards:
                    rolling = reward.get("rollingFreecash", {})
                    earned = rolling.get("totalEarned", {})
                    units = earned.get("units", "0")
                    total = float(units)
        except:
            pass
        return total

# ============================== TELEGRAM BOT ==============================

PHONE, OTP = range(2)
login_sessions = {}
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
        [InlineKeyboardButton("🎁 Collect ₹100", callback_data="btn_collect")],
        [InlineKeyboardButton("📊 My Stats", callback_data="btn_stats")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="btn_admin")])
    return InlineKeyboardMarkup(rows)

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Run Collection", callback_data="adm_collect")],
        [InlineKeyboardButton("📊 All Users", callback_data="adm_stats")],
        [InlineKeyboardButton("💰 Total Earnings", callback_data="adm_earn")],
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
        "🎯 <b>Viediet Buzz - Swiggy Collector</b>\n\n"
        "💰 Get ₹100 every day automatically!\n\n"
        "1️⃣ Login with your Swiggy phone number\n"
        "2️⃣ Tap Collect ₹100\n"
        "3️⃣ Watch your earnings grow!\n\n"
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
    phone = re.sub(r"\D", "", raw)
    if len(phone) != 10:
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
    
    login_sessions[user_id] = {"phone": phone, "client": client}
    await update.message.reply_text(f"✅ <b>OTP sent to +91 {phone}!</b>\n\nEnter the 6-digit OTP:")
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
        result = await asyncio.to_thread(client.verify_otp, phone, otp)
    except Exception as e:
        await update.message.reply_text(f"❌ {str(e)[:200]}\nTry again:")
        return OTP
    
    if result.get("status") != "ok":
        await update.message.reply_text(f"❌ {result.get('message', 'Invalid OTP')}\nTry again:")
        return OTP
    
    db.add_account(
        user_id, phone, client.device_id, client.token,
        client.tid, client.sid, client.secrettoken, client.customer_id
    )
    login_sessions.pop(user_id, None)
    
    await update.message.reply_text(
        f"✅ <b>Logged in as +91 {phone}!</b>\n\n"
        f"👤 Name: {result.get('name', 'N/A')}\n"
        f"💰 Tap Collect ₹100 to start earning!\n\n"
        f"{BRAND}",
        reply_markup=main_menu(update),
        parse_mode=ParseMode.HTML
    )
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

async def collect_buzz(update, context):
    user_id = tg_id(update)
    cid = chat_id(update)
    
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        await answer(update, "⏳ Collection already running!", main_menu(update))
        return
    
    account = db.get_active_account(user_id)
    if not account:
        await answer(update, "❌ No account found. Login first!", main_menu(update))
        return
    
    already, streak = db.has_collected_today(account["id"])
    if already:
        await answer(update, f"✅ Already collected today!\n🔥 Streak: {streak} days", main_menu(update))
        return
    
    await answer(update, "🔄 <b>Starting collection...</b>\n\n⏳ Processing 50 users...\nThis will take 1-2 minutes.")
    collecting_tasks[cid] = asyncio.create_task(run_collection(update, context, account))

async def run_collection(update, context, account):
    cid = chat_id(update)
    try:
        client = SwiggyClient()
        client.tid = account.get("tid", "")
        client.sid = account.get("sid", "")
        client.token = account.get("token", "")
        client.secrettoken = account.get("secrettoken", "")
        client.customer_id = account.get("customer_id", "")
        client.device_id = account.get("device_id", "")
        
        results = await asyncio.to_thread(client.run_buzz_collection, client.secrettoken)
        
        amount = results.get("total_earned", 0)
        if amount > 0:
            db.add_earned(account["id"], amount)
            db.finish_collection(account["id"])
        
        text = (
            f"✅ <b>Collection Complete!</b>\n\n"
            f"💰 <b>Earned: ₹{amount:.2f}</b>\n"
            f"✅ Successful: {results['successful']}\n"
            f"❌ Failed: {results['failed']}\n"
            f"🔥 Streak: {db.get_account(account['id']).get('streak_days', 0)} days\n\n"
            f"{BRAND}\n\n"
            f"💡 <b>Tip:</b> Share this bot with friends to get more acceptances!"
        )
        await context.bot.send_message(cid, text, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        log.exception("collection failed")
        await context.bot.send_message(cid, f"❌ <b>Error:</b> {str(e)[:200]}", parse_mode=ParseMode.HTML)
    finally:
        collecting_tasks.pop(cid, None)

async def accounts_menu(update):
    user_id = tg_id(update)
    accounts = db.get_accounts(user_id)
    if not accounts:
        await answer(update, "❌ No accounts linked.", main_menu(update))
        return
    lines = [f"{'🟢' if a['active'] else '⚪'} <b>+{a['phone']}</b> — ₹{a['total_earned']:.2f}" for a in accounts]
    await answer(update, "👤 <b>Your Accounts</b>\n\n" + "\n".join(lines), main_menu(update))

async def stats_menu(update):
    user_id = tg_id(update)
    accounts = db.get_accounts(user_id)
    total = sum(a["total_earned"] for a in accounts)
    if not accounts:
        await answer(update, "❌ No accounts.", main_menu(update))
        return
    lines = [f"📱 +{a['phone']} → ₹{a['total_earned']:.2f}" for a in accounts]
    await answer(update, "📊 <b>Your Stats</b>\n\n" + "\n".join(lines) + f"\n\n💰 <b>Total: ₹{total:.2f}</b>", main_menu(update))

async def admin_menu_handler(update):
    await answer(update, "👑 <b>Admin Panel</b>", admin_menu())

async def admin_stats(update):
    accounts = db.all_accounts()
    if not accounts:
        await answer(update, "📊 No users.", admin_menu())
        return
    lines = [f"👤 {a['telegram_id']} 📱 +{a['phone']} → ₹{a['total_earned']:.2f}" for a in accounts[:40]]
    await answer(update, "📊 <b>All Users</b>\n\n" + "\n".join(lines), admin_menu())

async def admin_earnings(update):
    total = db.total_earnings()
    accounts = db.all_accounts()
    text = f"💰 <b>Total System Earnings: ₹{total:.2f}</b>\n\n"
    lines = [f"📱 +{a['phone']} → ₹{a['total_earned']:.2f}" for a in accounts[:40]]
    await answer(update, text + "\n".join(lines), admin_menu())

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
            await collect_buzz(update, context)
        elif data == "btn_stats":
            await stats_menu(update)
        elif data == "btn_admin":
            if is_admin(user_id):
                await admin_menu_handler(update)
        elif data == "adm_collect":
            if is_admin(user_id):
                await collect_buzz(update, context)
        elif data == "adm_stats":
            if is_admin(user_id):
                await admin_stats(update)
        elif data == "adm_earn":
            if is_admin(user_id):
                await admin_earnings(update)
    except Exception as exc:
        log.exception("callback error")
        await answer(update, f"❌ Error: {str(exc)[:150]}", main_menu(update))

async def error_handler(update, context):
    log.error("Update %s caused error %s", update, context.error)

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
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(error_handler)
    
    log.info("🚀 Viediet Buzz bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
