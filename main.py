#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NRTECNO SYSTEM - PREMIUM PRIVATE BOT v5.0
# ENHANCED: Unlimited accounts, Dual login, Premium UI

import os
import logging
import telebot
import json
import time
import threading
import random
import sqlite3
import asyncio
import uuid
import sys
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from curl_cffi import requests as cffi_requests

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN environment variable not set.")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_ID", 1364476174))
ALLOWED_USERS_FILE = "allowed_users.json"

# ===== PERSISTENT STORAGE =====
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "premium_bot.db")
SESSIONS_DIR = os.path.join(DATA_DIR, "shopsy_sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==================== ALLOWED USERS ====================
def load_allowed_users():
    if os.path.exists(ALLOWED_USERS_FILE):
        try:
            with open(ALLOWED_USERS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_allowed_users(users):
    with open(ALLOWED_USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

ALLOWED_USERS = load_allowed_users()

def is_allowed(user_id):
    return str(user_id) in ALLOWED_USERS or user_id == ADMIN_ID

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        status TEXT DEFAULT 'ACTIVE',
        registered_at TEXT,
        last_used TEXT,
        referral_code TEXT UNIQUE,
        referrals_count INTEGER DEFAULT 0,
        shopsy_logged_in INTEGER DEFAULT 0,
        shopsy_phone TEXT DEFAULT NULL,
        mining_active INTEGER DEFAULT 0,
        total_accounts_added INTEGER DEFAULT 0,
        premium_status TEXT DEFAULT 'STANDARD'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS shopsy_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT,
        session_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, phone)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS mining_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT,
        coins_earned INTEGER,
        games_played INTEGER,
        gems_earned INTEGER,
        mined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS json_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT,
        json_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, phone)
    )''')
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at: {DB_PATH}")

init_db()

# ==================== DATABASE FUNCTIONS ====================
def get_user(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'status': row[3],
            'registered_at': row[4],
            'last_used': row[5],
            'referral_code': row[6],
            'referrals_count': row[7] if len(row) > 7 else 0,
            'shopsy_logged_in': row[8] if len(row) > 8 else 0,
            'shopsy_phone': row[9] if len(row) > 9 else None,
            'mining_active': row[10] if len(row) > 10 else 0,
            'total_accounts_added': row[11] if len(row) > 11 else 0,
            'premium_status': row[12] if len(row) > 12 else 'STANDARD'
        }
    return None

def create_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    ref_code = f"REF{user_id}{random.randint(1000, 9999)}"
    
    c.execute('''INSERT OR IGNORE INTO users 
        (user_id, username, first_name, status, registered_at, last_used, referral_code, referrals_count, shopsy_logged_in, mining_active, total_accounts_added, premium_status)
        VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, 0, 0, 0, 0, 'STANDARD')''',
        (user_id, username, first_name, now, now, ref_code))
    conn.commit()
    conn.close()

def update_user(user_id, **kwargs):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    updates = []
    values = []
    for key, value in kwargs.items():
        updates.append(f"{key} = ?")
        values.append(value)
    values.append(user_id)
    c.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", values)
    conn.commit()
    conn.close()

def get_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_all_sessions(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT phone, session_data FROM shopsy_sessions WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for phone, session_data in rows:
        try:
            if isinstance(session_data, str):
                data = json.loads(session_data)
            else:
                data = session_data
            if not isinstance(data, dict):
                data = {}
            result.append((phone, data))
        except:
            result.append((phone, {}))
    return result

def get_accounts_count(user_id):
    return len(get_all_sessions(user_id))

def save_session(user_id, phone, session_data):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    if isinstance(session_data, dict):
        session_json = json.dumps(session_data)
    else:
        session_json = session_data if isinstance(session_data, str) else json.dumps({})
    
    c.execute('''INSERT OR REPLACE INTO shopsy_sessions (user_id, phone, session_data, updated_at) 
                 VALUES (?, ?, ?, ?)''',
              (user_id, phone, session_json, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    user = get_user(user_id)
    if user:
        total = user.get('total_accounts_added', 0) + 1
        update_user(user_id, shopsy_phone=phone, shopsy_logged_in=1, total_accounts_added=total)

def save_json_session(user_id, phone, json_data):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO json_sessions (user_id, phone, json_data, created_at) 
                 VALUES (?, ?, ?, ?)''',
              (user_id, phone, json_data, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_json_sessions(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT phone, json_data FROM json_sessions WHERE user_id = ?', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def logout_user(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('DELETE FROM shopsy_sessions WHERE user_id = ?', (user_id,))
    c.execute('DELETE FROM json_sessions WHERE user_id = ?', (user_id,))
    c.execute('UPDATE users SET shopsy_logged_in = 0, shopsy_phone = NULL, mining_active = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def save_mining_history(user_id, phone, coins_earned, games_played, gems_earned):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('INSERT INTO mining_history (user_id, phone, coins_earned, games_played, gems_earned) VALUES (?, ?, ?, ?, ?)',
              (user_id, phone, coins_earned, games_played, gems_earned))
    conn.commit()
    conn.close()

def get_mining_history(user_id, limit=20):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT phone, coins_earned, games_played, gems_earned, mined_at FROM mining_history WHERE user_id = ? ORDER BY mined_at DESC LIMIT ?', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

def get_total_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_all_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT user_id, username, status, referrals_count, premium_status FROM users ORDER BY registered_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

# ==================== SHOPSY API FUNCTIONS ====================
def sync_api_request(method, url_path, json_body, session_data, is_game=False):
    if not isinstance(session_data, dict):
        session_data = {}
    
    device_id = session_data.get("device_id") or uuid.uuid4().hex[:32]
    visit_id = session_data.get("visit_id") or f"{uuid.uuid4().hex[:32]}-{int(time.time() * 1000)}"
    app_sess = session_data.get("app_session_id") or f"{uuid.uuid4()}_{int(time.time()*1000)}"

    if is_game:
        headers = {
            "x-user-agent": f"Mozilla/5.0 (Linux; Android 9; OPPO:CPH2083 Build/{device_id[:13]}) FKUA/Retail/2291170/Android/Mobile (OPPO/OPPO:CPH2083/{device_id})",
            "sessionid": "session_id",
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/4.9.2",
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive",
            "city": "Delhi"
        }
    else:
        headers = {
            "X-PARTNER-CONTEXT": '{"source":"reseller"}',
            "FK-TENANT-ID": "SHOPSY",
            "business": "reseller",
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/4.9.2",
            "X-User-Agent": f"Mozilla/5.0 (Linux; Android 9; CPH2083 Build/PPR1.180610.011) FKUA/Retail/2291170/Android/Mobile (OPPO/CPH2083/{device_id})",
            "X-Visit-Id": visit_id,
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive",
            "city": "Delhi",
            "X-AppSession-ID": app_sess
        }
        for k in ["at", "sn", "secureToken"]:
            if session_data.get(k):
                headers[k] = session_data[k]

    req_session = cffi_requests.Session(impersonate="chrome110")

    for attempt in range(1, 4):
        dc = session_data.get("current_dc", "1")
        url = f"https://{dc}.rome.api.flipkart.net{url_path}"
        try:
            resp = req_session.post(url, json=json_body, headers=headers, timeout=30, verify=False) if method == "POST" else req_session.get(url, headers=headers, timeout=30, verify=False)

            try:
                resp_json = resp.json()
            except:
                resp_json = {}

            if resp.status_code == 406 and resp_json.get("ERROR_MESSAGE") == "DC Change":
                new_dc = resp_json.get("RESPONSE", {}).get("id") or resp_json.get("RESPONSE", {}).get("dc")
                if new_dc:
                    session_data["current_dc"] = str(new_dc)
                    continue

            return resp.status_code, resp_json, dict(resp.headers), session_data
        except Exception as e:
            if attempt == 3:
                return 500, {"error": str(e)}, {}, session_data
            time.sleep(2)

    return 500, {"error": "Max retries"}, {}, session_data

def update_session(session_data, resp_json, resp_headers):
    if not isinstance(session_data, dict):
        session_data = {}
    
    if isinstance(resp_json, dict):
        sess_block = resp_json.get("SESSION") or resp_json.get("RESPONSE", {}).get("SESSION") or {}
        for k in ["accountId", "at", "rt", "sn", "secureToken", "nsid", "vid", "email", "firstName", "lastName"]:
            if sess_block.get(k):
                session_data[k] = sess_block[k]
        if session_data.get("firstName"):
            session_data["userName"] = f"{session_data.get('firstName', '')} {session_data.get('lastName', '')}".strip()
        if sess_block.get("isLoggedIn") is not None:
            session_data["isLoggedIn"] = sess_block["isLoggedIn"]
    if resp_headers:
        headers_lower = {k.lower(): v for k, v in resp_headers.items()}
        for k in ["at", "rt", "sn", "nsid", "vid"]:
            if k in headers_lower:
                session_data[k] = headers_lower[k]
        if headers_lower.get("securecookie"):
            session_data["secureCookie"] = headers_lower.get("securecookie")
    return session_data

async def run_sh_user_state(session_data):
    if not isinstance(session_data, dict):
        session_data = {}
    
    body = {
        "location": {"pincode": None},
        "ad": {"adId": str(uuid.uuid4()), "doNotPersonalizeAds": False, "sdkAdId": "", "adSdkVersion": "2.12.0"},
        "locale": {"deviceLanguage": "en", "shouldRefreshLanguage": False},
        "versions": {
            "cart": 1167987101,
            "userAccountState": 0,
            "abResponse": -2054295432,
            "abVariables": 0,
            "accountDetails": 1220048498,
            "wishlist": 0,
            "notifications": 861101,
            "location": 23273,
            "lockinResponse": 426889274
        }
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(sync_api_request, "POST", "/4/user/state", body, session_data, False)
    return update_session(session_data, resp_json, headers)

async def get_user_info_tg(session_data):
    if not isinstance(session_data, dict):
        session_data = {}
    
    body = {
        "requestMethod": "GET",
        "routeUri": "user/get-user",
        "payload": {"userId": session_data.get("accountId", ""), "userName": session_data.get("userName", "User")}
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(sync_api_request, "POST", "/1/shopsy/games", body, session_data, True)
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"]
    return None

async def get_config_tg(session_data):
    if not isinstance(session_data, dict):
        session_data = {}
    
    body = {"requestMethod": "GET", "routeUri": "config/get-config", "payload": {}}
    st, resp_json, headers, session_data = await asyncio.to_thread(sync_api_request, "POST", "/1/shopsy/games", body, session_data, True)
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"]
    return None

async def claim_gullak_tg(session_data):
    if not isinstance(session_data, dict):
        session_data = {}
    
    body = {
        "requestMethod": "POST",
        "routeUri": "gullak/claim-gullak",
        "payload": {"userId": session_data.get("accountId", "")}
    }
    await asyncio.to_thread(sync_api_request, "POST", "/1/shopsy/games", body, session_data, True)

async def start_game_tg(session_data, game_id):
    if not isinstance(session_data, dict):
        session_data = {}
    
    body = {
        "requestMethod": "POST",
        "routeUri": "game/game-started",
        "payload": {"userId": session_data.get("accountId", ""), "gameId": game_id}
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(sync_api_request, "POST", "/1/shopsy/games", body, session_data, True)
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"].get("sessionId"), resp_json["data"]
    return None, resp_json

async def end_game_tg(session_data, game_id, game_session_id, play_time, gems_earned):
    if not isinstance(session_data, dict):
        session_data = {}
    
    body = {
        "requestMethod": "POST",
        "routeUri": "game/game-ended",
        "payload": {
            "userId": session_data.get("accountId", ""),
            "gameId": game_id,
            "sessionId": game_session_id,
            "gemsEarned": gems_earned,
            "playTimeInSec": play_time
        }
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(sync_api_request, "POST", "/1/shopsy/games", body, session_data, True)
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"]
    return None

async def mine_single_account(session_data, status_callback=None):
    if not isinstance(session_data, dict):
        session_data = {}
    
    phone = session_data.get("phone", "Unknown")
    
    if status_callback:
        await status_callback(f"📱 **Account:** +91{phone}\n━━━━━━━━━━━━━━━━━━━━━")
    
    if status_callback:
        await status_callback(f"🔄 Fetching user state...")
    session_data = await run_sh_user_state(session_data)
    
    if status_callback:
        await status_callback(f"💰 Getting balance...")
    initial_user_data = await get_user_info_tg(session_data)
    if not initial_user_data:
        return {"status": "fail", "earned": 0, "msg": "Session expired", "phone": phone}
    initial_coins = initial_user_data.get("earnings", {}).get("coinsEarnedTotal", 0)

    if status_callback:
        await status_callback(f"🎁 Claiming gullak...")
    await claim_gullak_tg(session_data)

    if status_callback:
        await status_callback(f"🎮 Fetching games...")
    config_data = await get_config_tg(session_data)
    games = config_data.get("games", []) if config_data else []
    if not games:
        return {"status": "fail", "earned": 0, "msg": "No active games", "phone": phone}

    total = len(games)
    played_count = 0
    total_gems = 0
    
    for i, g in enumerate(games):
        game_id = g.get("id")
        game_name = g.get("name", game_id)
        
        if status_callback:
            await status_callback(f"🎮 [{i+1}/{total}] Playing {game_name}...")
        
        game_sess_id, _ = await start_game_tg(session_data, game_id)
        if game_sess_id:
            wait = random.randint(10, 15)
            for sec in range(wait, 0, -1):
                if sec % 5 == 0 or sec <= 3:
                    if status_callback:
                        await status_callback(f"⏳ {game_name}... {sec}s remaining")
                await asyncio.sleep(1)
            
            gems = random.randint(3000, 5000)
            end_data = await end_game_tg(session_data, game_id, game_sess_id, wait, gems)
            if end_data:
                played_count += 1
                total_gems += gems
                if status_callback:
                    await status_callback(f"✅ Earned {gems} gems from {game_name}")
            else:
                if status_callback:
                    await status_callback(f"⚠️ Failed to complete {game_name}")
        else:
            if status_callback:
                await status_callback(f"❌ Could not start {game_name}")
        await asyncio.sleep(1)

    if status_callback:
        await status_callback(f"📊 Finalizing...")
    final_user_data = await get_user_info_tg(session_data)
    final_coins = final_user_data.get("earnings", {}).get("coinsEarnedTotal", 0) if final_user_data else initial_coins
    earned = max(0, final_coins - initial_coins)

    result = {
        "status": "success",
        "earned": earned,
        "final_coins": final_coins,
        "played": played_count,
        "total": total,
        "gems": total_gems,
        "phone": phone
    }
    
    if status_callback:
        await status_callback(f"✅ **Complete!** +{earned} coins | {played_count}/{total} games | 💎{total_gems} gems")
        await status_callback(f"━━━━━━━━━━━━━━━━━━━━━")
    
    return result

# ==================== AUTO MINING ENGINE ====================
class AutoMiningEngine:
    def __init__(self, bot, user_id, chat_id):
        self.bot = bot
        self.user_id = user_id
        self.chat_id = chat_id
        self.is_running = False
        self.thread = None
        self.total_earned = 0
        self.total_gems = 0
        self.total_played = 0
        self.success_count = 0
        self.status_msg_id = None
    
    def start_auto_mining(self):
        if self.is_running:
            return "⚠️ Mining already in progress!"
        
        sessions = get_all_sessions(self.user_id)
        if not sessions:
            return "❌ No saved accounts! Please login first."
        
        self.is_running = True
        self.total_earned = 0
        self.total_gems = 0
        self.total_played = 0
        self.success_count = 0
        update_user(self.user_id, mining_active=1)
        
        self.thread = threading.Thread(target=self._run_auto_mining, daemon=True)
        self.thread.start()
        return f"✅ Auto-mining started with {len(sessions)} accounts!"
    
    def _send_progress(self, msg):
        try:
            if self.status_msg_id:
                self.bot.edit_message_text(
                    msg,
                    chat_id=self.chat_id,
                    message_id=self.status_msg_id,
                    parse_mode="Markdown"
                )
            else:
                sent = self.bot.send_message(
                    self.chat_id,
                    msg,
                    parse_mode="Markdown"
                )
                self.status_msg_id = sent.message_id
        except Exception as e:
            if "message is not modified" not in str(e):
                sent = self.bot.send_message(
                    self.chat_id,
                    msg,
                    parse_mode="Markdown"
                )
                self.status_msg_id = sent.message_id
    
    def _run_auto_mining(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        sessions = get_all_sessions(self.user_id)
        
        valid_sessions = []
        for phone, session_data in sessions:
            if isinstance(session_data, dict) and session_data:
                valid_sessions.append((phone, session_data))
            else:
                logger.warning(f"Invalid session data for {phone}, skipping")
        
        if not valid_sessions:
            self._send_progress("❌ No valid sessions found! Please re-login.")
            update_user(self.user_id, mining_active=0)
            self.is_running = False
            return
        
        initial_msg = f"""
🚀 AUTO-MINING STARTED!

📱 Accounts: {len(valid_sessions)}
⏳ Processing one by one...

━━━━━━━━━━━━━━━━━━━━━
"""
        self._send_progress(initial_msg)
        
        async def status_callback(msg):
            try:
                current_msg = f"""
🚀 AUTO-MINING IN PROGRESS...

📱 Accounts: {len(valid_sessions)}
✅ Completed: {self.success_count}
💰 Total Earned: {self.total_earned} coins
💎 Total Gems: {self.total_gems}

━━━━━━━━━━━━━━━━━━━━━
{msg}
"""
                self._send_progress(current_msg)
            except Exception as e:
                logger.error(f"Status callback error: {e}")
        
        async def run():
            results = []
            
            for idx, (phone, session_data) in enumerate(valid_sessions):
                session_data["phone"] = phone
                session_data["user_id"] = self.user_id
                
                await status_callback(f"📱 **Account {idx+1}/{len(valid_sessions)}:** +91{phone}")
                await status_callback(f"━━━━━━━━━━━━━━━━━━━━━")
                
                try:
                    result = await mine_single_account(session_data, status_callback)
                    results.append(result)
                except Exception as e:
                    logger.error(f"Mining error for {phone}: {e}")
                    await status_callback(f"❌ Error: {str(e)[:50]}")
                    results.append({"status": "fail", "phone": phone, "msg": str(e)})
                
                if result.get("status") == "success":
                    self.success_count += 1
                    self.total_earned += result.get("earned", 0)
                    self.total_gems += result.get("gems", 0)
                    self.total_played += result.get("played", 0)
                    save_mining_history(self.user_id, phone, result.get("earned", 0), result.get("played", 0), result.get("gems", 0))
                
                await asyncio.sleep(2)
            
            summary_msg = f"""
✅ AUTO-MINING COMPLETE!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📱 Accounts Mined: {len(results)}
✅ Successful: {self.success_count}
💰 Total Coins Earned: {self.total_earned}
💎 Total Gems Earned: {self.total_gems}
🎮 Total Games Played: {self.total_played}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 Next Steps:
• Click 🚀 AUTO MINE again for daily mining
• Add more accounts by logging in
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            
            for r in results:
                if r.get("status") == "success":
                    summary_msg += f"\n✅ +91{r['phone']} → +{r['earned']} coins"
                else:
                    summary_msg += f"\n❌ +91{r.get('phone', '?')} → {r.get('msg', 'Failed')}"
            
            self._send_progress(summary_msg)
            
            update_user(self.user_id, mining_active=0)
            self.is_running = False
        
        try:
            loop.run_until_complete(run())
        except Exception as e:
            logger.error(f"Auto mining error: {e}")
            self._send_progress(f"❌ **Error:** {str(e)[:100]}")
            update_user(self.user_id, mining_active=0)
            self.is_running = False
        finally:
            loop.close()

# ==================== GLOBAL STATES ====================
mining_engines = {}
user_shopsy_state = {}
shopsy_otp_data = {}

# ==================== PREMIUM UI FUNCTIONS ====================
def premium_menu_keyboard(user_id):
    kb = InlineKeyboardMarkup(row_width=2)
    user = get_user(user_id)
    mining_active = user.get('mining_active', 0) if user else 0
    accounts_count = get_accounts_count(user_id)
    
    if mining_active:
        kb.row(InlineKeyboardButton("⏳ MINING IN PROGRESS", callback_data="mining_status"))
    else:
        kb.row(InlineKeyboardButton("🚀 AUTO MINE", callback_data="start_auto_mining"))
    
    kb.row(
        InlineKeyboardButton("📱 ADD ACCOUNT", callback_data="add_account_menu"),
        InlineKeyboardButton("📋 JSON LOGIN", callback_data="json_login_menu")
    )
    kb.row(
        InlineKeyboardButton("👥 ACCOUNTS", callback_data="view_accounts"),
        InlineKeyboardButton("📊 HISTORY", callback_data="view_history")
    )
    kb.row(
        InlineKeyboardButton("🔓 LOGOUT ALL", callback_data="logout_all"),
        InlineKeyboardButton("🔄 REFRESH", callback_data="refresh_menu")
    )
    kb.row(InlineKeyboardButton("📢 SUPPORT", callback_data="support_menu"))
    
    return kb

def premium_header(user_id):
    user = get_user(user_id)
    if not user:
        return "🚀 **NRTECNO PREMIUM**"
    
    accounts = get_accounts_count(user_id)
    mining = "🔴" if user.get('mining_active', 0) else "🟢"
    premium = "⭐" if user.get('premium_status') == 'PREMIUM' else "💠"
    
    return f"""
    
🚀 VIEDIET PVT SHOPSY BOT        
║ {premium} User: {user.get('first_name', 'User')}
║ 📱 Accounts: {accounts} (Unlimited)
║ {mining} Status: {"Mining" if user.get('mining_active', 0) else "Idle"}
║ ⚡ Premium: {user.get('premium_status', 'STANDARD')}
╚══════════════════════════════════════╝
"""

def add_account_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("📱 OTP LOGIN", callback_data="login_otp"),
        InlineKeyboardButton("📋 JSON LOGIN", callback_data="login_json")
    )
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def view_accounts_text(user_id):
    sessions = get_all_sessions(user_id)
    json_sessions = get_json_sessions(user_id)
    total = len(sessions) + len(json_sessions)
    
    if total == 0:
        return f"""
📋 YOUR ACCOUNTS

❌ No accounts found

💡 Add your first account using:
• 📱 OTP Login - via mobile number
• 📋 JSON Login - upload JSON file
"""
    
    text = f"""
📋 YOUR ACCOUNTS

📱 Total Accounts: {total}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    if sessions:
        text += "\n**📱 OTP Accounts:**\n"
        for phone, _ in sessions:
            text += f"   ✅ +91{phone}\n"
    
    if json_sessions:
        text += "\n**📋 JSON Accounts:**\n"
        for phone, _ in json_sessions:
            text += f"   ✅ +91{phone}\n"
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 All accounts will be mined automatically
🔗 No account limits - add as many as you want!
"""
    return text

def history_text(user_id):
    history = get_mining_history(user_id, limit=25)
    
    if not history:
        return """
📊 MINING HISTORY

❌ No mining data yet

Start auto-mining to track your earnings.
"""
    
    total_coins = sum(h[1] for h in history)
    total_gems = sum(h[3] for h in history)
    total_games = sum(h[2] for h in history)
    
    text = f"""
📊 MINING HISTORY

💰 Total Coins: {total_coins}
💎 Total Gems: {total_gems}
🎮 Total Games: {total_games}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Recent Activity:**
"""
    
    for phone, coins, games, gems, mined_at in history[:15]:
        date = mined_at[:10] if mined_at else "N/A"
        text += f"\n📱 +91{phone} | +{coins} coins | {games} games | 💎{gems} | {date}"
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return text

def support_text():
    return """
📢 SUPPORT & HELP

💬 Commands:
• /start - Restart bot

📱 Login Methods:
1. OTP Login: Receive OTP on mobile
2. JSON Login: Upload JSON session

⚡Features:
• Unlimited accounts
• Auto-mining all accounts
• Earnings tracking
• Premium support

📞 Contact Support:
Contact your admin for assistance.
Group - @viedietlooterschat
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ==================== COMMAND HANDLERS ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    
    if not is_allowed(user_id):
        bot.send_message(
            user_id,
            "🔒 **ACCESS DENIED**\n\n"
            "This is a private bot. You don't have permission to use it.\n\n"
            "Contact the administrator for access.",
            parse_mode="HTML"
        )
        return
    
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "User"
    
    user = get_user(user_id)
    if not user:
        create_user(user_id, username, first_name)
        user = get_user(user_id)
    
    bot.send_message(
        user_id,
        premium_header(user_id) + "\n\n" + 
        "Welcome to your premium automation system.\n"
        "Select an option below to get started:",
        reply_markup=premium_menu_keyboard(user_id),
        parse_mode="HTML"
    )

@bot.message_handler(commands=['admin'])
def admin_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized access.")
        return
    
    text = """
👑 ADMIN PANEL

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Statistics:
• Total Users: {total}
• Total Accounts: {accounts}

👑 Commands:
• /adduser USER_ID - Grant access
• /removeuser USER_ID - Revoke access
• /listusers - Show all users
• /broadcast - Send announcement
• /unlock USER_ID - Unlock user
• /lock USER_ID - Lock user

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".format(
    total=get_total_users(),
    accounts=sum(get_accounts_count(u[0]) for u in get_all_users())
)
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['adduser'])
def adduser_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized.")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /adduser USER_ID")
            return
        
        target_id = str(parts[1])
        if target_id not in ALLOWED_USERS:
            ALLOWED_USERS.append(target_id)
            save_allowed_users(ALLOWED_USERS)
            bot.reply_to(message, f"✅ User {target_id} granted access!")
            
            try:
                bot.send_message(
                    int(target_id),
                    "🎉 ACCESS GRANTED\n\n"
                    "You now have access to NRTECNO Premium Bot.\n"
                    "Click /start to begin.",
                    parse_mode="HTML"
                )
            except:
                pass
        else:
            bot.reply_to(message, f"⚠️ User {target_id} already has access.")
    except:
        bot.reply_to(message, "❌ Invalid USER_ID.")

@bot.message_handler(commands=['removeuser'])
def removeuser_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized.")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /removeuser USER_ID")
            return
        
        target_id = str(parts[1])
        if target_id in ALLOWED_USERS:
            ALLOWED_USERS.remove(target_id)
            save_allowed_users(ALLOWED_USERS)
            bot.reply_to(message, f"✅ User {target_id} access revoked.")
            
            try:
                bot.send_message(
                    int(target_id),
                    "🔒 ACCESS REVOKED\n\n"
                    "You no longer have access to this bot.",
                    parse_mode="HTML"
                )
            except:
                pass
        else:
            bot.reply_to(message, f"⚠️ User {target_id} not found.")
    except:
        bot.reply_to(message, "❌ Invalid USER_ID.")

@bot.message_handler(commands=['listusers'])
def listusers_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized.")
        return
    
    users = get_all_users()
    if not users:
        bot.reply_to(message, "No users found.")
        return
    
    text = "👥 ALL USERS\n\n"
    for uid, username, status, referrals, premium in users[:20]:
        name = username or f"User_{uid}"
        icon = "🔓" if status == "ACTIVE" else "🔒"
        premium_icon = "⭐" if premium == "PREMIUM" else "💠"
        text += f"{icon} {premium_icon} {name} - {status}\n"
    
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated)"
    
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized.")
        return
    
    msg = bot.reply_to(
        message,
        "📢 BROADCAST MESSAGE\n\n"
        "Send the message to broadcast to all users.\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, broadcast_handler)

def broadcast_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Broadcast cancelled.")
        return
    
    users = get_all_users()
    if not users:
        bot.reply_to(message, "❌ No users found.")
        return
    
    success = 0
    failed = 0
    status_msg = bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    
    for uid, username, status, referrals, premium in users:
        try:
            bot.send_message(
                uid,
                f"📢 **ANNOUNCEMENT**\n\n{message.text}",
                parse_mode="HTML"
            )
            success += 1
            time.sleep(0.05)
        except:
            failed += 1
    
    bot.edit_message_text(
        f"✅ **BROADCAST COMPLETE**\n\n"
        f"✅ Sent: {success}\n"
        f"❌ Failed: {failed}",
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        parse_mode="HTML"
    )

@bot.message_handler(commands=['unlock'])
def unlock_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized.")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /unlock USER_ID")
            return
        
        target_id = int(parts[1])
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ User {target_id} not found.")
            return
        
        update_user(target_id, status='ACTIVE')
        bot.reply_to(message, f"✅ User {target_id} unlocked.")
    except:
        bot.reply_to(message, "❌ Invalid USER_ID.")

@bot.message_handler(commands=['lock'])
def lock_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized.")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /lock USER_ID")
            return
        
        target_id = int(parts[1])
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ User {target_id} not found.")
            return
        
        update_user(target_id, status='LOCKED')
        bot.reply_to(message, f"✅ User {target_id} locked.")
    except:
        bot.reply_to(message, "❌ Invalid USER_ID.")

# ==================== CALLBACK HANDLER ====================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    
    if not is_allowed(user_id):
        bot.answer_callback_query(call.id, "❌ Access denied!")
        return
    
    user = get_user(user_id)
    if not user:
        bot.answer_callback_query(call.id, "User not found")
        return
    
    data = call.data
    
    # ===== BACK MENU =====
    if data == "back_menu":
        bot.edit_message_text(
            premium_header(user_id) + "\n\nSelect an option below:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=premium_menu_keyboard(user_id),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== REFRESH =====
    if data == "refresh_menu":
        bot.edit_message_text(
            premium_header(user_id) + "\n\n✅ Refreshed!",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=premium_menu_keyboard(user_id),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== ADD ACCOUNT MENU =====
    if data == "add_account_menu":
        bot.edit_message_text(
            "📱 ADD ACCOUNT\n\n"
            "Choose your login method:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 OTP Login - Login via mobile number\n"
            "📋 JSON Login - Upload JSON session file\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=add_account_menu_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== JSON LOGIN =====
    if data == "login_json":
        bot.edit_message_text(
            "📋 **JSON LOGIN**\n\n"
            "Please send your JSON session file as a **document**.\n\n"
            "The JSON should contain valid session data.\n\n"
            "Send /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== OTP LOGIN =====
    if data == "login_otp":
        user_shopsy_state[user_id] = "waiting_phone"
        bot.edit_message_text(
            "📱 **OTP LOGIN**\n\n"
            "Enter your 10-digit mobile number:\n\n"
            "Send /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== VIEW ACCOUNTS =====
    if data == "view_accounts":
        text = view_accounts_text(user_id)
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== VIEW HISTORY =====
    if data == "view_history":
        text = history_text(user_id)
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== SUPPORT =====
    if data == "support_menu":
        text = support_text()
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== LOGOUT ALL =====
    if data == "logout_all":
        logout_user(user_id)
        bot.answer_callback_query(
            call.id,
            "✅ All accounts logged out successfully!",
            show_alert=True
        )
        bot.edit_message_text(
            premium_header(user_id) + "\n\n✅ All accounts cleared.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=premium_menu_keyboard(user_id),
            parse_mode="HTML"
        )
        return
    
    # ===== START AUTO MINING =====
    if data == "start_auto_mining":
        if user.get('mining_active', 0) == 1:
            bot.answer_callback_query(
                call.id,
                "⚠️ Mining already in progress!",
                show_alert=True
            )
            return
        
        sessions = get_all_sessions(user_id)
        json_sessions = get_json_sessions(user_id)
        total = len(sessions) + len(json_sessions)
        
        if total == 0:
            bot.answer_callback_query(
                call.id,
                "❌ No accounts found! Add account first.",
                show_alert=True
            )
            return
        
        engine = AutoMiningEngine(bot, user_id, call.message.chat.id)
        mining_engines[user_id] = engine
        result = engine.start_auto_mining()
        
        if "✅" in result:
            bot.edit_message_text(
                f"🚀 **AUTO-MINING STARTED!**\n\n"
                f"📱 Accounts: {total}\n"
                f"⏳ Processing one by one...\n\n"
                f"_Progress will appear here._",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, result, show_alert=True)
        
        bot.answer_callback_query(call.id)
        return
    
    # ===== MINING STATUS =====
    if data == "mining_status":
        accounts = get_accounts_count(user_id)
        status_text = "🟢 Active" if user.get('mining_active', 0) else "🟡 Idle"
        bot.answer_callback_query(
            call.id,
            f"📊 Mining Status:\n📱 Accounts: {accounts}\n{status_text}",
            show_alert=True
        )
        return

# ==================== JSON LOGIN HANDLER ====================
@bot.message_handler(content_types=['document'])
def json_login_handler(message):
    user_id = message.from_user.id
    
    if not is_allowed(user_id):
        bot.reply_to(message, "❌ Access denied.")
        return
    
    user = get_user(user_id)
    if not user:
        bot.reply_to(message, "❌ User not found. Please /start first.")
        return
    
    file_id = message.document.file_id
    file_name = message.document.file_name or "session.json"
    
    if not file_name.endswith('.json'):
        bot.reply_to(
            message,
            "❌ Invalid file format. Please send a JSON file.\n\n"
            "File must end with .json",
            parse_mode="HTML"
        )
        return
    
    try:
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        json_data = json.loads(downloaded_file.decode('utf-8'))
        
        if not isinstance(json_data, dict):
            bot.reply_to(message, "❌ Invalid JSON format. Must be a JSON object.")
            return
        
        phone = json_data.get("phone") or json_data.get("accountId") or json_data.get("userName")
        if not phone:
            bot.reply_to(
                message,
                "❌ Could not detect phone number in JSON.\n\n"
                "Make sure the JSON contains 'phone' or 'accountId' field.",
                parse_mode="HTML"
            )
            return
        
        # Save the JSON session
        save_json_session(user_id, phone, json.dumps(json_data))
        
        # Also save as regular session
        save_session(user_id, phone, json_data)
        
        accounts_count = get_accounts_count(user_id)
        
        bot.reply_to(
            message,
            f"✅ JSON LOGIN SUCCESSFUL\n\n"
            f"📱 Phone: +91{phone}\n"
            f"📊 Total Accounts: {accounts_count}\n"
            f"💾 Session saved successfully!\n\n"
            f"Click 🚀 AUTO MINE to start mining.",
            parse_mode="HTML"
        )
        
        # Show main menu
        bot.send_message(
            user_id,
            premium_header(user_id) + "\n\nSelect an option below:",
            reply_markup=premium_menu_keyboard(user_id),
            parse_mode="HTML"
        )
        
    except json.JSONDecodeError as e:
        bot.reply_to(
            message,
            f"❌ **Invalid JSON**\n\nError: {str(e)}\n\n"
            "Please send a valid JSON file.",
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(
            message,
            f"❌ **Error processing file**\n\n{str(e)[:200]}",
            parse_mode="HTML"
        )

# ==================== OTP LOGIN HANDLERS ====================
@bot.message_handler(func=lambda message: user_shopsy_state.get(message.from_user.id) == "waiting_phone")
def shopsy_phone_handler(message):
    user_id = message.from_user.id
    phone = message.text.strip()
    
    if phone.lower() in ['/cancel', 'cancel']:
        user_shopsy_state[user_id] = None
        bot.reply_to(message, "❌ Login cancelled.")
        return
    
    if not phone.isdigit() or len(phone) != 10:
        bot.reply_to(
            message,
            "❌ Please enter exactly 10 digits.\n\nSend /cancel to abort.",
            parse_mode="HTML"
        )
        return
    
    user_shopsy_state[user_id] = "waiting_otp"
    shopsy_otp_data[user_id] = {"phone": phone}
    
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("❌ Cancel", callback_data="back_menu"))
    
    status_msg = bot.reply_to(
        message,
        f"📱 Sending OTP to +91{phone}...",
        reply_markup=kb,
        parse_mode="HTML"
    )
    
    def send_otp_thread():
        try:
            result, error = shopsy_request_otp(phone)
            
            if error:
                bot.edit_message_text(
                    f"❌ Failed to send OTP: {error}\n\nPlease try again.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                user_shopsy_state[user_id] = None
                if user_id in shopsy_otp_data:
                    del shopsy_otp_data[user_id]
                return
            
            if result:
                shopsy_otp_data[user_id]["session_data"] = result["session_data"]
                shopsy_otp_data[user_id]["request_id"] = result["request_id"]
                
                bot.edit_message_text(
                    f"✅ OTP sent to +91{phone}!\n\n"
                    f"Enter the 6-digit OTP code:\n"
                    f"Send /cancel to abort.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            
        except Exception as e:
            bot.edit_message_text(
                f"❌ Error: {str(e)[:200]}",
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                parse_mode="HTML"
            )
            user_shopsy_state[user_id] = None
            if user_id in shopsy_otp_data:
                del shopsy_otp_data[user_id]
    
    threading.Thread(target=send_otp_thread).start()

@bot.message_handler(func=lambda message: user_shopsy_state.get(message.from_user.id) == "waiting_otp")
def shopsy_otp_handler(message):
    user_id = message.from_user.id
    otp = message.text.strip()
    
    if otp.lower() in ['/cancel', 'cancel']:
        user_shopsy_state[user_id] = None
        if user_id in shopsy_otp_data:
            del shopsy_otp_data[user_id]
        bot.reply_to(message, "❌ Login cancelled.")
        return
    
    if not otp.isdigit() or len(otp) != 6:
        bot.reply_to(
            message,
            "❌ Please enter a valid 6-digit OTP.\n\nSend /cancel to abort.",
            parse_mode="HTML"
        )
        return
    
    if user_id not in shopsy_otp_data:
        bot.reply_to(
            message,
            "❌ Session expired. Please start again.\nClick /start to restart.",
            parse_mode="HTML"
        )
        user_shopsy_state[user_id] = None
        return
    
    data = shopsy_otp_data[user_id]
    phone = data.get("phone")
    session_data = data.get("session_data")
    req_id = data.get("request_id")
    
    if not session_data or not req_id:
        bot.reply_to(
            message,
            "❌ Invalid session data. Please start again.\nClick /start to restart.",
            parse_mode="HTML"
        )
        user_shopsy_state[user_id] = None
        if user_id in shopsy_otp_data:
            del shopsy_otp_data[user_id]
        return
    
    status_msg = bot.reply_to(message, "🔄 Verifying OTP...", parse_mode="HTML")
    
    def verify_thread():
        try:
            result, error = shopsy_verify_otp(phone, session_data, req_id, otp)
            
            if error:
                bot.edit_message_text(
                    f"❌ {error}\n\nPlease try again.\nEnter the 6-digit OTP code:\nSend /cancel to abort.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                user_shopsy_state[user_id] = "waiting_otp"
                return
            
            if result:
                save_session(user_id, phone, result)
                
                accounts_count = get_accounts_count(user_id)
                
                bot.edit_message_text(
                    f"✅ LOGIN SUCCESSFUL\n\n"
                    f"📱 Phone: +91{phone}\n"
                    f"💾 Account saved!\n"
                    f"📊 Total Accounts: {accounts_count}\n"
                    f"🚀 Click AUTO MINE to start mining!",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                
                user_shopsy_state[user_id] = None
                if user_id in shopsy_otp_data:
                    del shopsy_otp_data[user_id]
                
                bot.send_message(
                    user_id,
                    premium_header(user_id) + "\n\nSelect an option below:",
                    reply_markup=premium_menu_keyboard(user_id),
                    parse_mode="HTML"
                )
            
        except Exception as e:
            bot.edit_message_text(
                f"❌ Error: {str(e)[:200]}\n\nPlease try again.\nClick /start to restart.",
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                parse_mode="HTML"
            )
            user_shopsy_state[user_id] = None
            if user_id in shopsy_otp_data:
                del shopsy_otp_data[user_id]
    
    threading.Thread(target=verify_thread).start()

# ==================== SHOPSY API HELPERS ====================
def shopsy_request_otp(phone):
    try:
        d_id = uuid.uuid4().hex[:32]
        v_id = f"{uuid.uuid4().hex[:32]}-{int(time.time() * 1000)}"
        s_id = f"{uuid.uuid4()}_{int(time.time()*1000)}"
        
        session_data = {
            "phone": phone,
            "device_id": d_id,
            "visit_id": v_id,
            "app_session_id": s_id,
            "current_dc": "1"
        }
        
        body = {
            "actionRequestContext": {
                "type": "LOGIN_IDENTITY_VERIFY_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "phoneNumberFormat": "E164",
                "addAppHash": True,
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "clientQueryParamMap": None
            }
        }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st, resp, hdrs, session_data = loop.run_until_complete(
            asyncio.to_thread(sync_api_request, "POST", "/1/action/view", body, session_data, False)
        )
        loop.close()
        
        if st != 200:
            return None, f"HTTP {st}"
        
        session_data = update_session(session_data, resp, hdrs)
        req_id = resp.get("RESPONSE", {}).get("actionResponseContext", {}).get("requestId") or resp.get("requestId")
        
        if not req_id:
            return None, "No request ID"
        
        return {"session_data": session_data, "request_id": req_id}, None
        
    except Exception as e:
        return None, str(e)

def shopsy_verify_otp(phone, session_data, req_id, otp):
    try:
        body = {
            "actionRequestContext": {
                "type": "LOGIN_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "password": None,
                "otp": otp,
                "otpRequestId": req_id,
                "remainingAttempts": 5,
                "phoneNumberFormat": "E164",
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "churned": False
            }
        }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st, resp, hdrs, session_data = loop.run_until_complete(
            asyncio.to_thread(sync_api_request, "POST", "/1/action/view", body, session_data, False)
        )
        loop.close()
        
        if st == 200 and resp.get("RESPONSE", {}).get("actionResponseContext", {}).get("authenticationSuccess", False):
            session_data = update_session(session_data, resp, hdrs)
            session_data["isLoggedIn"] = True
            return session_data, None
        else:
            return None, "Invalid OTP"
            
    except Exception as e:
        return None, str(e)

# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 NRTECNO PREMIUM PRIVATE BOT v5.0")
    logger.info("=" * 60)
    logger.info("👑 Admin ID: {}".format(ADMIN_ID))
    logger.info("📱 Allowed Users: {}".format(len(ALLOWED_USERS)))
    logger.info("🔓 Account Limit: UNLIMITED")
    logger.info("📱 Login Methods: OTP + JSON")
    logger.info("💾 DATA_DIR: {}".format(DATA_DIR))
    logger.info("=" * 60)
    
    try:
        bot.remove_webhook()
        time.sleep(2)
    except:
        pass
    
    while True:
        try:
            logger.info("🔄 Starting polling...")
            bot.polling(non_stop=False, interval=1, timeout=30)
        except Exception as e:
            if "409" in str(e):
                logger.warning("⚠️ Conflict detected. Waiting 15 seconds...")
                time.sleep(15)
            else:
                logger.error(f"Polling error: {e}")
                logger.info("🔄 Restarting polling in 5 seconds...")
                time.sleep(5)
