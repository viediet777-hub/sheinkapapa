#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NRTECNO SYSTEM - VIEDIET PREMIUM BOT v6.0
# ENHANCED: Referral system, Dual unlock, Channel force, JSON login fix

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
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from curl_cffi import requests as cffi_requests

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN environment variable not set.")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_ID", 1364476174))
CHANNEL_USERNAME = "viedietlooters"
REFERRAL_REQUIRED = 6

# ===== PERSISTENT STORAGE =====
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "viediet_premium.db")
SESSIONS_DIR = os.path.join(DATA_DIR, "shopsy_sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        status TEXT DEFAULT 'LOCKED',
        registered_at TEXT,
        last_used TEXT,
        referred_by INTEGER DEFAULT NULL,
        referral_code TEXT UNIQUE,
        referrals_count INTEGER DEFAULT 0,
        is_unlocked INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        shopsy_logged_in INTEGER DEFAULT 0,
        shopsy_phone TEXT DEFAULT NULL,
        mining_active INTEGER DEFAULT 0,
        channel_joined INTEGER DEFAULT 0,
        total_accounts_added INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        join_timestamp TEXT,
        is_valid INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS pending_referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        join_timestamp TEXT
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
            'referred_by': row[6],
            'referral_code': row[7],
            'referrals_count': row[8] if len(row) > 8 else 0,
            'is_unlocked': row[9] if len(row) > 9 else 0,
            'is_premium': row[10] if len(row) > 10 else 0,
            'shopsy_logged_in': row[11] if len(row) > 11 else 0,
            'shopsy_phone': row[12] if len(row) > 12 else None,
            'mining_active': row[13] if len(row) > 13 else 0,
            'channel_joined': row[14] if len(row) > 14 else 0,
            'total_accounts_added': row[15] if len(row) > 15 else 0
        }
    return None

def create_user(user_id, username, first_name, referred_by=None):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    ref_code = f"REF{user_id}{random.randint(1000, 9999)}"
    
    c.execute('''INSERT OR IGNORE INTO users 
        (user_id, username, first_name, status, registered_at, last_used, referred_by, referral_code, referrals_count, is_unlocked, is_premium, shopsy_logged_in, mining_active, channel_joined, total_accounts_added)
        VALUES (?, ?, ?, 'LOCKED', ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0)''',
        (user_id, username, first_name, now, now, referred_by, ref_code))
    conn.commit()
    conn.close()
    
    if referred_by:
        add_pending_referral(referred_by, user_id)
    return

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

def add_pending_referral(referrer_id, referred_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('INSERT INTO pending_referrals (referrer_id, referred_id, join_timestamp) VALUES (?, ?, ?)',
                  (referrer_id, referred_id, now))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def get_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND is_valid = 1', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_pending_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM pending_referrals WHERE referrer_id = ?', (user_id,))
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

def logout_user(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('DELETE FROM shopsy_sessions WHERE user_id = ?', (user_id,))
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

def get_unlocked_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users WHERE is_unlocked = 1 OR is_premium = 1')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_all_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT user_id, username, status, referrals_count, is_unlocked, is_premium FROM users ORDER BY registered_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

def check_and_award_referrals():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT id, referrer_id, referred_id, join_timestamp FROM pending_referrals')
    pending = c.fetchall()
    
    for pid, referrer_id, referred_id, join_ts in pending:
        try:
            c.execute('SELECT status FROM users WHERE user_id = ?', (referred_id,))
            user_row = c.fetchone()
            if user_row and user_row[0] in ['LOCKED', 'ACTIVE']:
                c.execute('UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?', (referrer_id,))
                c.execute('INSERT INTO referrals (referrer_id, referred_id, join_timestamp, is_valid) VALUES (?, ?, ?, 1)',
                          (referrer_id, referred_id, join_ts))
                c.execute('DELETE FROM pending_referrals WHERE id = ?', (pid,))
                conn.commit()
                
                c.execute('SELECT referrals_count FROM users WHERE user_id = ?', (referrer_id,))
                count = c.fetchone()[0]
                
                if count >= REFERRAL_REQUIRED:
                    c.execute('UPDATE users SET is_unlocked = 1, status = "ACTIVE" WHERE user_id = ?', (referrer_id,))
                    conn.commit()
                    try:
                        bot.send_message(
                            referrer_id,
                            f"🎉 CONGRATULATIONS!\n\n"
                            f"You have completed <b>{REFERRAL_REQUIRED} referrals</b>!\n"
                            f"🔓 <b>Bot is now UNLOCKED!</b>\n\n"
                            f"📱 You can now add unlimited accounts!\n"
                            f"🚀 Click <b>AUTO MINE</b> to begin!",
                            parse_mode="HTML"
                        )
                    except:
                        pass
                else:
                    try:
                        bot.send_message(
                            referrer_id,
                            f"🎉 Referral Bonus!\n\n"
                            f"👥 You now have <b>{count}</b> referrals!\n"
                            f"🎯 Need <b>{REFERRAL_REQUIRED - count}</b> more to unlock!",
                            parse_mode="HTML"
                        )
                    except:
                        pass
        except Exception as e:
            logger.error(f"Referral award error: {e}")
    
    conn.close()

def run_scheduled_tasks():
    while True:
        try:
            check_and_award_referrals()
        except Exception as e:
            logger.error(f"Scheduled task error: {e}")
        time.sleep(60)

# ==================== CHANNEL CHECK FUNCTIONS ====================
def check_channel_membership(user_id):
    try:
        member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        is_member = member.status in ['member', 'administrator', 'creator']
        if is_member:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('UPDATE users SET channel_joined = 1 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        return is_member
    except:
        return False

def channel_join_force_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(InlineKeyboardButton("📢 JOIN CHANNEL", url=f"https://t.me/{CHANNEL_USERNAME}"))
    kb.row(InlineKeyboardButton("✅ CHECK AGAIN", callback_data="check_channel"))
    return kb

def channel_join_message():
    return f"""
🔒 CHANNEL REQUIRED

⚠️ <b>You must join our channel to use this bot!</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📢 <b>Channel:</b> @{CHANNEL_USERNAME}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>Why join?</b>
• Exclusive updates
• Referral system access
• Premium bot features

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Click below to join, then click ✅ CHECK AGAIN
"""

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

📱 Accounts:** {len(valid_sessions)}
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

# ==================== MENU FUNCTIONS ====================
def locked_menu_text(user_id, first_name, referral_count, is_premium=False):
    pending = get_pending_referral_count(user_id)
    remaining = max(0, REFERRAL_REQUIRED - referral_count)
    
    if is_premium:
        return f"""
⭐ VIEDIET PVT SHOPSY BOT

👋 Welcome, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔓 <b>Status:</b> PREMIUM UNLOCKED 👑
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>✨ Premium Features:</b>
• Unlimited accounts
• Auto-mining all accounts
• Premium support
• Early access to updates

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Click <b>🚀 AUTO MINE</b> to get started!
"""
    
    return f"""
🔒 VIEDIET PVT SHOPSY BOT 

👋 Welcome, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔒 <b>Status:</b> LOCKED
👥 <b>Referrals:</b> {referral_count}/{REFERRAL_REQUIRED}
⏳ <b>Pending:</b> {pending}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 Two Ways to Unlock:</b>

1️⃣ <b>FREE Unlock</b>
   📤 Refer <b>{remaining} more friends</b>
   Each successful referral = +1 point
   ✅ Reach {REFERRAL_REQUIRED} referrals to unlock

2️⃣ <b>⭐ PREMIUM Unlock</b>
   💰 Contact admin for premium access
   Get instant unlock without referrals!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📊 Your Referral Stats:</b>
• Successful: {referral_count}
• Pending: {pending}
• Need: {remaining} more

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

def locked_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(InlineKeyboardButton("🔗 GET REFERRAL LINK", callback_data="referral_link"))
    kb.row(InlineKeyboardButton("📊 CHECK STATUS", callback_data="check_status"))
    kb.row(InlineKeyboardButton("🔄 REFRESH", callback_data="refresh"))
    return kb

def unlocked_menu_text(user_id, first_name, accounts_count=0, mining_active=False):
    status_icon = "🟢" if mining_active else "🟡"
    status_text = "Mining..." if mining_active else "Ready"
    user = get_user(user_id)
    is_premium = user.get('is_premium', 0) if user else 0
    
    premium_badge = "⭐ PREMIUM" if is_premium else "🔓 UNLOCKED"
    
    return f"""
🚀 VIEDIET PREMIUM BOT

👋 Welcome, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{premium_badge}
📱 <b>Accounts:</b> {accounts_count} (Unlimited)
{status_icon} <b>Mining:</b> {status_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>⚡ Quick Actions:</b>
• 🚀 AUTO MINE - Mine all accounts
• ➕ ADD ACCOUNT - Login new account
• 📋 MY ACCOUNTS - View saved accounts
• 📊 HISTORY - View mining history
"""

def unlocked_menu_keyboard(accounts_count=0, mining_active=False):
    kb = InlineKeyboardMarkup(row_width=2)
    
    if mining_active:
        kb.row(InlineKeyboardButton("⏳ MINING...", callback_data="mining_status"))
    else:
        kb.row(InlineKeyboardButton("🚀 AUTO MINE", callback_data="start_auto_mining"))
    
    kb.row(
        InlineKeyboardButton("➕ ADD ACCOUNT", callback_data="add_account_menu"),
        InlineKeyboardButton("📋 MY ACCOUNTS", callback_data="my_accounts")
    )
    kb.row(
        InlineKeyboardButton("📊 HISTORY", callback_data="history"),
        InlineKeyboardButton("🚪 LOGOUT ALL", callback_data="logout_all")
    )
    kb.row(InlineKeyboardButton("🔄 REFRESH", callback_data="refresh"))
    return kb

def referral_link_text(user_id, first_name, referral_count, pending_count):
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    remaining = max(0, REFERRAL_REQUIRED - referral_count)
    is_unlocked = referral_count >= REFERRAL_REQUIRED
    
    return f"""
🔗 YOUR REFERRAL LINK

📤 <b>Share this link:</b>
<code>{link}</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📊 Your Stats:</b>
👥 Successful: {referral_count}/{REFERRAL_REQUIRED}
⏳ Pending: {pending_count}
🔓 Status: {"UNLOCKED ✅" if is_unlocked else f"LOCKED 🔒 ({remaining} more needed)"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>💡 How it works:</b>
1. Share your referral link
2. Friends join using your link
3. Each join = 1 referral
4. Reach {REFERRAL_REQUIRED} to unlock!

<b>🎯 Need {remaining} more referrals!</b>
"""

def referral_link_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(InlineKeyboardButton("📤 SHARE LINK", callback_data="share_link"))
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def my_accounts_text(user_id):
    sessions = get_all_sessions(user_id)
    
    if not sessions:
        return f"""
📋 MY ACCOUNTS

❌ <b>No saved accounts!</b>

💡 Add your first account using:
• 📱 OTP Login - via mobile number
• 📋 JSON Login - upload JSON file or paste
"""
    
    text = f"""
📋 MY ACCOUNTS

<b>📱 Saved Accounts:</b> {len(sessions)} (Unlimited)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    for phone, _ in sessions:
        text += f"\n✅ +91{phone}"
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 All saved accounts will be auto-mined!
🔗 No account limits - add as many as you want!
"""
    return text

def my_accounts_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def history_text(user_id):
    history = get_mining_history(user_id)
    
    if not history:
        return """
📊 MINING HISTORY

❌ <b>No mining history yet!</b>

Start auto-mining to see results here.
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

<b>Recent Activity:</b>
"""
    
    for phone, coins, games, gems, mined_at in history[:10]:
        date = mined_at[:10] if mined_at else "N/A"
        text += f"\n📱 +91{phone} | +{coins} coins | {games} games | 💎{gems} | {date}"
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return text

def history_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def add_account_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("📱 OTP LOGIN", callback_data="login_otp"),
        InlineKeyboardButton("📋 JSON LOGIN", callback_data="login_json")
    )
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def admin_panel_text():
    total_users = get_total_users()
    unlocked_users = get_unlocked_users()
    
    return f"""
👑 ADMIN PANEL

<b>📊 Statistics:</b>
👥 Total Users: {total_users}
🔓 Unlocked: {unlocked_users}
🔒 Locked: {total_users - unlocked_users}
⭐ Premium: {len([u for u in get_all_users() if u[5] == 1])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>👑 Admin Commands:</b>
• /unlock USER_ID - Unlock user (free)
• /lock USER_ID - Lock user
• /premium USER_ID - Grant premium access
• /removepremium USER_ID - Remove premium
• /listusers - List all users
• /broadcast - Send message to all

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ==================== COMMAND HANDLERS ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "User"
    
    user = get_user(user_id)
    
    referred_by = None
    if message.text and "ref_" in message.text:
        try:
            referred_by = int(message.text.split("ref_")[1].split()[0])
        except:
            pass
    
    if not user:
        create_user(user_id, username, first_name, referred_by)
        user = get_user(user_id)
    
    if not check_channel_membership(user_id):
        bot.send_message(
            user_id,
            channel_join_message(),
            reply_markup=channel_join_force_keyboard(),
            parse_mode="HTML"
        )
        return
    
    if user.get('is_unlocked', 0) == 1 or user.get('is_premium', 0) == 1:
        show_unlocked_menu(message, user_id)
    else:
        show_locked_menu(message, user_id)

def show_locked_menu(message, user_id):
    user = get_user(user_id)
    referral_count = get_referral_count(user_id)
    is_premium = user.get('is_premium', 0)
    
    text = locked_menu_text(user_id, user['first_name'], referral_count, is_premium)
    bot.send_message(
        user_id,
        text,
        reply_markup=locked_menu_keyboard(),
        parse_mode="HTML"
    )

def show_unlocked_menu(message, user_id):
    user = get_user(user_id)
    sessions = get_all_sessions(user_id)
    accounts_count = len(sessions)
    mining_active = user.get('mining_active', 0)
    
    text = unlocked_menu_text(user_id, user['first_name'], accounts_count, mining_active)
    
    if hasattr(message, 'chat'):
        chat_id = message.chat.id
    else:
        chat_id = user_id
    
    bot.send_message(
        chat_id,
        text,
        reply_markup=unlocked_menu_keyboard(accounts_count, mining_active),
        parse_mode="HTML"
    )

# ==================== ADMIN COMMANDS ====================
@bot.message_handler(commands=['unlock'])
def unlock_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /unlock USER_ID")
            return
        
        target_id = int(parts[1])
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            return
        
        update_user(target_id, is_unlocked=1, status='ACTIVE')
        bot.reply_to(
            message,
            f"✅ User {target_id} unlocked successfully!\n\n"
            f"👤 Name: {user['first_name']}",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"🎉 <b>You have been UNLOCKED!</b>\n\n"
                f"Click <b>🚀 AUTO MINE</b> to begin!",
                parse_mode="HTML"
            )
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid USER_ID!")

@bot.message_handler(commands=['lock'])
def lock_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /lock USER_ID")
            return
        
        target_id = int(parts[1])
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            return
        
        update_user(target_id, is_unlocked=0, is_premium=0, status='LOCKED')
        bot.reply_to(
            message,
            f"✅ User {target_id} locked successfully!",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"🔒 <b>You have been LOCKED!</b>\n\n"
                f"You need {REFERRAL_REQUIRED} referrals or premium to unlock.",
                parse_mode="HTML"
            )
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid USER_ID!")

@bot.message_handler(commands=['premium'])
def premium_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /premium USER_ID")
            return
        
        target_id = int(parts[1])
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            return
        
        update_user(target_id, is_premium=1, is_unlocked=1, status='ACTIVE')
        bot.reply_to(
            message,
            f"⭐ User {target_id} granted PREMIUM access!\n\n"
            f"👤 Name: {user['first_name']}",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"⭐ <b>PREMIUM ACCESS GRANTED!</b>\n\n"
                f"🎉 You now have premium access to all features!\n"
                f"Click <b>🚀 AUTO MINE</b> to begin!",
                parse_mode="HTML"
            )
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid USER_ID!")

@bot.message_handler(commands=['removepremium'])
def removepremium_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(message, "❌ Use: /removepremium USER_ID")
            return
        
        target_id = int(parts[1])
        user = get_user(target_id)
        if not user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            return
        
        update_user(target_id, is_premium=0)
        bot.reply_to(
            message,
            f"❌ User {target_id} premium access revoked!",
            parse_mode="HTML"
        )
        
        try:
            bot.send_message(
                target_id,
                f"❌ <b>Premium access removed</b>\n\n"
                f"You can still unlock via referrals ({REFERRAL_REQUIRED} needed).",
                parse_mode="HTML"
            )
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid USER_ID!")

@bot.message_handler(commands=['listusers'])
def listusers_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    users = get_all_users()
    if not users:
        bot.reply_to(message, "No users found.")
        return
    
    text = "👥 <b>All Users:</b>\n\n"
    for uid, username, status, referrals, unlocked, premium in users:
        name = username or f"User_{uid}"
        status_icon = "🔓" if unlocked or premium else "🔒"
        premium_icon = "⭐" if premium else "💠"
        text += f"{status_icon} {premium_icon} {name} - {referrals}/{REFERRAL_REQUIRED} refs\n"
    
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated)"
    
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    msg = bot.reply_to(
        message,
        "📢 <b>Broadcast Message</b>\n\n"
        "Send the message to broadcast to ALL users.\n\n"
        "⚠️ <b>Warning:</b> This will send to ALL users!\n\n"
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
        bot.reply_to(message, "❌ No users to broadcast to!")
        return
    
    success = 0
    failed = 0
    
    status_msg = bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    
    for uid, username, status, referrals, unlocked, premium in users:
        try:
            bot.send_message(uid, f"📢 <b>Announcement</b>\n\n{message.text}", parse_mode="HTML")
            success += 1
            time.sleep(0.05)
        except:
            failed += 1
    
    bot.edit_message_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"✅ Sent: {success}\n"
        f"❌ Failed: {failed}",
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        parse_mode="HTML"
    )

# ==================== CALLBACK HANDLER ====================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data
    
    user = get_user(user_id)
    if not user:
        bot.answer_callback_query(call.id, "User not found")
        return
    
    # ===== CHANNEL CHECK =====
    if data == "check_channel":
        if check_channel_membership(user_id):
            bot.answer_callback_query(call.id, "✅ Channel joined! Welcome!")
            if user.get('is_unlocked', 0) == 1 or user.get('is_premium', 0) == 1:
                show_unlocked_menu(call.message, user_id)
            else:
                show_locked_menu(call.message, user_id)
        else:
            bot.answer_callback_query(call.id, "❌ Please join the channel first!", show_alert=True)
        return
    
    # ===== BACK =====
    if data == "back_menu":
        if user.get('is_unlocked', 0) == 1 or user.get('is_premium', 0) == 1:
            show_unlocked_menu(call.message, user_id)
        else:
            show_locked_menu(call.message, user_id)
        bot.answer_callback_query(call.id)
        return
    
    # ===== REFRESH =====
    if data == "refresh":
        if user.get('is_unlocked', 0) == 1 or user.get('is_premium', 0) == 1:
            show_unlocked_menu(call.message, user_id)
        else:
            show_locked_menu(call.message, user_id)
        bot.answer_callback_query(call.id)
        return
    
    # ===== REFERRAL =====
    if data == "referral_link":
        referral_count = get_referral_count(user_id)
        pending_count = get_pending_referral_count(user_id)
        
        text = referral_link_text(user_id, user['first_name'], referral_count, pending_count)
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=referral_link_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    if data == "share_link":
        bot_username = bot.get_me().username
        link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        bot.answer_callback_query(
            call.id,
            "📤 Copy this link and share with friends!\n\n" + link,
            show_alert=True
        )
        return
    
    # ===== CHECK STATUS =====
    if data == "check_status":
        referral_count = get_referral_count(user_id)
        pending_count = get_pending_referral_count(user_id)
        remaining = max(0, REFERRAL_REQUIRED - referral_count)
        is_unlocked = referral_count >= REFERRAL_REQUIRED
        is_premium = user.get('is_premium', 0)
        
        if is_premium:
            bot.answer_callback_query(
                call.id,
                f"⭐ PREMIUM USER!\n\n"
                f"👥 Referrals: {referral_count}/{REFERRAL_REQUIRED}\n"
                f"🔓 Status: UNLOCKED (Premium)",
                show_alert=True
            )
            return
        
        if is_unlocked:
            update_user(user_id, is_unlocked=1, status='ACTIVE')
            bot.answer_callback_query(
                call.id,
                f"🎉 UNLOCKED!\n\n"
                f"👥 Referrals: {referral_count}/{REFERRAL_REQUIRED}\n"
                f"🔓 Status: UNLOCKED",
                show_alert=True
            )
            show_unlocked_menu(call.message, user_id)
        else:
            bot.answer_callback_query(
                call.id,
                f"🔒 {referral_count}/{REFERRAL_REQUIRED} referrals\n"
                f"⏳ Pending: {pending_count}\n"
                f"🎯 Need {remaining} more to unlock!",
                show_alert=True
            )
        return
    
    # ===== ADD ACCOUNT MENU =====
    if data == "add_account_menu":
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete referrals or get premium.", show_alert=True)
            return
        
        if not check_channel_membership(user_id):
            bot.answer_callback_query(call.id, "❌ Please join channel first!", show_alert=True)
            return
        
        bot.edit_message_text(
            "📱 <b>ADD ACCOUNT</b>\n\n"
            "Choose your login method:\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 OTP Login - Login via mobile number\n"
            "📋 JSON Login - Upload JSON file or paste JSON\n"
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
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete referrals or get premium.", show_alert=True)
            return
        
        bot.edit_message_text(
            "📋 <b>JSON LOGIN</b>\n\n"
            "<b>Two ways to login:</b>\n\n"
            "1️⃣ <b>Upload File:</b>\n"
            "   Send your JSON file as a document\n\n"
            "2️⃣ <b>Paste JSON:</b>\n"
            "   Copy and paste the JSON content directly\n\n"
            "Send /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== OTP LOGIN =====
    if data == "login_otp":
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete referrals or get premium.", show_alert=True)
            return
        
        user_shopsy_state[user_id] = "waiting_phone"
        bot.edit_message_text(
            "📱 <b>OTP LOGIN</b>\n\n"
            "Enter your 10-digit mobile number:\n\n"
            "Send /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== MY ACCOUNTS =====
    if data == "my_accounts":
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete referrals or get premium.", show_alert=True)
            return
        
        text = my_accounts_text(user_id)
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=my_accounts_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== HISTORY =====
    if data == "history":
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete referrals or get premium.", show_alert=True)
            return
        
        text = history_text(user_id)
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=history_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # ===== START AUTO MINING =====
    if data == "start_auto_mining":
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete 6 referrals or get premium.", show_alert=True)
            return
        
        if not check_channel_membership(user_id):
            bot.answer_callback_query(call.id, "❌ Please join channel first!", show_alert=True)
            return
        
        if user.get('mining_active', 0) == 1:
            bot.answer_callback_query(call.id, "⚠️ Mining already in progress!", show_alert=True)
            return
        
        sessions = get_all_sessions(user_id)
        if not sessions:
            bot.answer_callback_query(call.id, "❌ No saved accounts! Add account first.", show_alert=True)
            return
        
        engine = AutoMiningEngine(bot, user_id, call.message.chat.id)
        mining_engines[user_id] = engine
        result = engine.start_auto_mining()
        
        if "✅" in result:
            bot.edit_message_text(
                f"🚀 AUTO-MINING STARTED!\n\n"
                f"📱 Accounts: {len(sessions)}\n"
                f"⏳ Mining one by one...\n\n"
                f"_Live progress will appear here._",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, result, show_alert=True)
        
        bot.answer_callback_query(call.id)
        return
    
    # ===== LOGOUT ALL =====
    if data == "logout_all":
        logout_user(user_id)
        bot.answer_callback_query(call.id, "🚪 All accounts logged out successfully!", show_alert=True)
        show_unlocked_menu(call.message, user_id)
        return
    
    # ===== MINING STATUS =====
    if data == "mining_status":
        user = get_user(user_id)
        sessions = get_all_sessions(user_id)
        mining_active = user.get('mining_active', 0)
        
        status_text = "🟢 Active" if mining_active else "🟡 Idle"
        bot.answer_callback_query(
            call.id,
            f"📊 Mining Status:\n📱 Accounts: {len(sessions)}\n{status_text}",
            show_alert=True
        )
        return
    
    # ===== ADMIN PANEL =====
    if data == "admin_panel":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        bot.edit_message_text(
            admin_panel_text(),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return

# ==================== JSON LOGIN HANDLER ====================
@bot.message_handler(content_types=['document'])
def json_login_handler(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user or (user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1):
        bot.reply_to(message, "❌ Bot is LOCKED! Complete 6 referrals or get premium.")
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
        
        process_json_login(message, user_id, json_data)
        
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

@bot.message_handler(func=lambda message: True, content_types=['text'])
def json_paste_handler(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if not text:
        return
    
    # Check if it's JSON content (starts with { or [)
    if not (text.startswith('{') or text.startswith('[')):
        return
    
    # Check if user is in JSON login state
    if user_shopsy_state.get(user_id) != "json_paste":
        return
    
    user = get_user(user_id)
    if not user or (user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1):
        bot.reply_to(message, "❌ Bot is LOCKED! Complete 6 referrals or get premium.")
        user_shopsy_state[user_id] = None
        return
    
    try:
        json_data = json.loads(text)
        process_json_login(message, user_id, json_data)
        user_shopsy_state[user_id] = None
    except json.JSONDecodeError as e:
        bot.reply_to(
            message,
            f"❌ **Invalid JSON**\n\nError: {str(e)}\n\n"
            "Please send valid JSON content.",
            parse_mode="HTML"
        )

def process_json_login(message, user_id, json_data):
    if not isinstance(json_data, dict):
        bot.reply_to(message, "❌ Invalid JSON format. Must be a JSON object.")
        return
    
    phone = json_data.get("phone") or json_data.get("accountId") or json_data.get("userName")
    if not phone:
        # Try to extract phone from any key
        for key, value in json_data.items():
            if isinstance(value, str) and value.isdigit() and len(value) == 10:
                phone = value
                break
        
        if not phone:
            bot.reply_to(
                message,
                "❌ Could not detect phone number in JSON.\n\n"
                "Make sure the JSON contains 'phone', 'accountId', or a 10-digit number.",
                parse_mode="HTML"
            )
            return
    
    # Save session
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
    
    # Update user
    update_user(user_id, shopsy_phone=phone, shopsy_logged_in=1)
    
    # Show main menu
    show_unlocked_menu(message, user_id)

# ==================== SHOPSY LOGIN HANDLERS ====================
@bot.message_handler(func=lambda message: user_shopsy_state.get(message.from_user.id) == "waiting_phone")
def shopsy_phone_handler(message):
    user_id = message.from_user.id
    phone = message.text.strip()
    
    if phone.lower() in ['/cancel', 'cancel']:
        user_shopsy_state[user_id] = None
        bot.reply_to(message, "❌ Login cancelled.")
        return
    
    if not phone.isdigit() or len(phone) != 10:
        bot.reply_to(message, "❌ Please enter exactly 10 digits.\n\nSend /cancel to abort.")
        return
    
    user = get_user(user_id)
    if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
        bot.reply_to(message, "❌ Bot is LOCKED! Complete 6 referrals or get premium.")
        user_shopsy_state[user_id] = None
        return
    
    user_shopsy_state[user_id] = "waiting_otp"
    
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("❌ Cancel", callback_data="back_menu"))
    
    status_msg = bot.reply_to(message, f"📱 Sending OTP to +91{phone}...", reply_markup=kb)
    
    shopsy_otp_data[user_id] = {"phone": phone}
    
    def send_otp_thread():
        try:
            result, error = shopsy_request_otp(phone)
            
            if error:
                bot.edit_message_text(
                    f"❌ Failed to send OTP: {error}\nPlease try again.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_shopsy_state[user_id] = None
                if user_id in shopsy_otp_data:
                    del shopsy_otp_data[user_id]
                return
            
            if result:
                shopsy_otp_data[user_id]["session_data"] = result["session_data"]
                shopsy_otp_data[user_id]["request_id"] = result["request_id"]
                
                bot.edit_message_text(
                    f"✅ OTP sent to +91{phone}!\n\nEnter the 6-digit OTP code:\nSend /cancel to abort.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    reply_markup=kb
                )
                user_shopsy_state[user_id] = "waiting_otp"
            
        except Exception as e:
            bot.edit_message_text(
                f"❌ Error: {str(e)[:200]}",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
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
        bot.reply_to(message, "❌ Please enter a valid 6-digit OTP.\n\nSend /cancel to abort.")
        return
    
    if user_id not in shopsy_otp_data:
        bot.reply_to(message, "❌ Session expired. Please start again.\nClick /start to restart.")
        user_shopsy_state[user_id] = None
        return
    
    data = shopsy_otp_data[user_id]
    phone = data.get("phone")
    session_data = data.get("session_data")
    req_id = data.get("request_id")
    
    if not session_data or not req_id:
        bot.reply_to(message, "❌ Invalid session data. Please start again.\nClick /start to restart.")
        user_shopsy_state[user_id] = None
        if user_id in shopsy_otp_data:
            del shopsy_otp_data[user_id]
        return
    
    status_msg = bot.reply_to(message, "🔄 Verifying OTP...")
    
    def verify_thread():
        try:
            result, error = shopsy_verify_otp(phone, session_data, req_id, otp)
            
            if error:
                bot.edit_message_text(
                    f"❌ {error}\n\nPlease try again.\nEnter the 6-digit OTP code:\nSend /cancel to abort.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_shopsy_state[user_id] = "waiting_otp"
                return
            
            if result:
                save_session(user_id, phone, result)
                
                accounts_count = get_accounts_count(user_id)
                
                bot.edit_message_text(
                    f"✅ <b>Login Successful!</b>\n\n"
                    f"📱 Phone: +91{phone}\n"
                    f"💾 Account saved!\n"
                    f"📊 Accounts: {accounts_count}\n"
                    f"🚀 You can now run AUTO MINE!",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                
                user_shopsy_state[user_id] = None
                if user_id in shopsy_otp_data:
                    del shopsy_otp_data[user_id]
                
                show_unlocked_menu(message, user_id)
            
        except Exception as e:
            bot.edit_message_text(
                f"❌ Error: {str(e)[:200]}\n\nPlease try again.\nClick /start to restart.",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
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
    task_thread = threading.Thread(target=run_scheduled_tasks, daemon=True)
    task_thread.start()
    
    logger.info("=" * 60)
    logger.info("🚀 VIEDIET PREMIUM BOT v6.0")
    logger.info("=" * 60)
    logger.info("🔒 Referrals Required: 6")
    logger.info("⭐ Premium: /premium USER_ID")
    logger.info("📢 Channel: @{}".format(CHANNEL_USERNAME))
    logger.info("👑 Admin: /unlock, /lock, /premium, /removepremium")
    logger.info("📱 Login: OTP + JSON (File & Paste)")
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
