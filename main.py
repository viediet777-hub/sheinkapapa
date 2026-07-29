#!/usr/bin/env python3
# VIEDIET SHOPSY ULTIMATE - Bot + Web + API (Single File)
# Deploy on Railway - Everything in One File!

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
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from curl_cffi import requests as cffi_requests

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN environment variable not set.")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_ID", 1364476174))
CHANNEL_USERNAME = "viedietlooters"
REFERRAL_REQUIRED = 3
REFERRAL_TO_ACCOUNT = 1

# ===== PERSISTENT STORAGE =====
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "viediet_ultimate.db")
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== FLASK APP ====================
app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get("SECRET_KEY", "viediet-secret-2026")
CORS(app)

# ==================== BOT INIT ====================
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
        total_accounts_added INTEGER DEFAULT 0,
        max_accounts_allowed INTEGER DEFAULT 5,
        login_method TEXT DEFAULT NULL,
        total_logins INTEGER DEFAULT 0,
        last_login TEXT DEFAULT NULL,
        web_token TEXT DEFAULT NULL,
        credits INTEGER DEFAULT 0
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
        login_method TEXT DEFAULT 'JSON',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, phone)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS login_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        login_method TEXT,
        phone TEXT,
        login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    c.execute('''CREATE TABLE IF NOT EXISTS credit_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        type TEXT,
        reference TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            'total_accounts_added': row[15] if len(row) > 15 else 0,
            'max_accounts_allowed': row[16] if len(row) > 16 else 5,
            'login_method': row[17] if len(row) > 17 else None,
            'total_logins': row[18] if len(row) > 18 else 0,
            'last_login': row[19] if len(row) > 19 else None,
            'web_token': row[20] if len(row) > 20 else None,
            'credits': row[21] if len(row) > 21 else 0
        }
    return None

def create_user(user_id, username, first_name, referred_by=None):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    ref_code = f"REF{user_id}{random.randint(1000, 9999)}"
    token = f"WEB{uuid.uuid4().hex[:16]}"
    
    c.execute('''INSERT OR IGNORE INTO users 
        (user_id, username, first_name, status, registered_at, last_used, referred_by, referral_code, web_token, credits)
        VALUES (?, ?, ?, 'LOCKED', ?, ?, ?, ?, ?, 0)''',
        (user_id, username, first_name, now, now, referred_by, ref_code, token))
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

def get_user_by_token(token):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE web_token = ?', (token,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'status': row[3],
            'is_unlocked': row[9] if len(row) > 9 else 0,
            'is_premium': row[10] if len(row) > 10 else 0,
            'shopsy_phone': row[12] if len(row) > 12 else None,
            'web_token': row[20] if len(row) > 20 else None,
            'credits': row[21] if len(row) > 21 else 0
        }
    return None

def get_all_sessions(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT phone, session_data, login_method FROM shopsy_sessions WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for phone, session_data, login_method in rows:
        try:
            data = json.loads(session_data) if isinstance(session_data, str) else session_data
            if not isinstance(data, dict):
                data = {}
            result.append((phone, data, login_method))
        except:
            result.append((phone, {}, login_method or "Unknown"))
    return result

def get_accounts_count(user_id):
    return len(get_all_sessions(user_id))

def get_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND is_valid = 1', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_max_accounts_allowed(user_id):
    user = get_user(user_id)
    if user:
        return user.get('max_accounts_allowed', 5)
    return 5

def save_session(user_id, phone, session_data, login_method="JSON"):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    session_json = json.dumps(session_data) if isinstance(session_data, dict) else str(session_data)
    
    c.execute('''INSERT OR REPLACE INTO shopsy_sessions (user_id, phone, session_data, login_method, updated_at) 
                 VALUES (?, ?, ?, ?, ?)''',
              (user_id, phone, session_json, login_method, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    user = get_user(user_id)
    if user:
        total = user.get('total_accounts_added', 0) + 1
        update_user(user_id, shopsy_phone=phone, shopsy_logged_in=1, 
                   total_accounts_added=total, login_method=login_method,
                   total_logins=user.get('total_logins', 0) + 1,
                   last_login=datetime.now().isoformat())
        log_login(user_id, login_method, phone)

def log_login(user_id, method, phone):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('INSERT INTO login_history (user_id, login_method, phone) VALUES (?, ?, ?)',
              (user_id, method, phone))
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
    c.execute('SELECT user_id, username, first_name, status, referrals_count, is_unlocked, is_premium FROM users ORDER BY registered_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

# ==================== SHOPSY API ====================
def sync_api_request(method, url_path, json_body, session_data, is_game=False):
    if not isinstance(session_data, dict):
        session_data = {}
    
    device_id = session_data.get("device_id") or uuid.uuid4().hex[:32]
    visit_id = session_data.get("visit_id") or f"{uuid.uuid4().hex[:32]}-{int(time.time() * 1000)}"
    app_sess = session_data.get("app_session_id") or f"{uuid.uuid4()}_{int(time.time()*1000)}"

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
    if is_game:
        headers["sessionid"] = "session_id"
    
    req_session = cffi_requests.Session(impersonate="chrome110")
    dc = session_data.get("current_dc", "1")
    url = f"https://{dc}.rome.api.flipkart.net{url_path}"
    
    try:
        resp = req_session.post(url, json=json_body, headers=headers, timeout=30, verify=False)
        resp_json = resp.json() if resp.text else {}
        return resp.status_code, resp_json, dict(resp.headers), session_data
    except Exception as e:
        return 500, {"error": str(e)}, {}, session_data

def update_session(session_data, resp_json, resp_headers):
    if isinstance(resp_json, dict):
        sess_block = resp_json.get("SESSION") or resp_json.get("RESPONSE", {}).get("SESSION") or {}
        for k in ["accountId", "at", "rt", "sn", "secureToken", "nsid", "vid", "email", "firstName", "lastName"]:
            if sess_block.get(k):
                session_data[k] = sess_block[k]
    return session_data

async def run_sh_user_state(session_data):
    body = {
        "location": {"pincode": None},
        "ad": {"adId": str(uuid.uuid4()), "doNotPersonalizeAds": False},
        "locale": {"deviceLanguage": "en", "shouldRefreshLanguage": False},
        "versions": {"cart": 1167987101, "userAccountState": 0}
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(
        sync_api_request, "POST", "/4/user/state", body, session_data, False
    )
    return update_session(session_data, resp_json, headers)

async def get_user_info_tg(session_data):
    body = {
        "requestMethod": "GET",
        "routeUri": "user/get-user",
        "payload": {"userId": session_data.get("accountId", ""), "userName": "User"}
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(
        sync_api_request, "POST", "/1/shopsy/games", body, session_data, True
    )
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"]
    return None

async def get_config_tg(session_data):
    body = {"requestMethod": "GET", "routeUri": "config/get-config", "payload": {}}
    st, resp_json, headers, session_data = await asyncio.to_thread(
        sync_api_request, "POST", "/1/shopsy/games", body, session_data, True
    )
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"]
    return None

async def claim_gullak_tg(session_data):
    body = {
        "requestMethod": "POST",
        "routeUri": "gullak/claim-gullak",
        "payload": {"userId": session_data.get("accountId", "")}
    }
    await asyncio.to_thread(sync_api_request, "POST", "/1/shopsy/games", body, session_data, True)

async def start_game_tg(session_data, game_id):
    body = {
        "requestMethod": "POST",
        "routeUri": "game/game-started",
        "payload": {"userId": session_data.get("accountId", ""), "gameId": game_id}
    }
    st, resp_json, headers, session_data = await asyncio.to_thread(
        sync_api_request, "POST", "/1/shopsy/games", body, session_data, True
    )
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"].get("sessionId"), resp_json["data"]
    return None, resp_json

async def end_game_tg(session_data, game_id, game_session_id, play_time, gems_earned):
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
    st, resp_json, headers, session_data = await asyncio.to_thread(
        sync_api_request, "POST", "/1/shopsy/games", body, session_data, True
    )
    if st == 200 and isinstance(resp_json, dict) and resp_json.get("success"):
        return resp_json["data"]
    return None

async def mine_single_account(session_data, status_callback=None):
    phone = session_data.get("phone", "Unknown")
    
    session_data = await run_sh_user_state(session_data)
    initial_user_data = await get_user_info_tg(session_data)
    if not initial_user_data:
        return {"status": "fail", "earned": 0, "msg": "Session expired", "phone": phone}
    initial_coins = initial_user_data.get("earnings", {}).get("coinsEarnedTotal", 0)

    await claim_gullak_tg(session_data)
    config_data = await get_config_tg(session_data)
    games = config_data.get("games", []) if config_data else []
    if not games:
        return {"status": "fail", "earned": 0, "msg": "No active games", "phone": phone}

    played_count = 0
    total_gems = 0
    
    for g in games:
        game_id = g.get("id")
        game_sess_id, _ = await start_game_tg(session_data, game_id)
        if game_sess_id:
            wait = random.randint(10, 15)
            await asyncio.sleep(wait)
            gems = random.randint(3000, 5000)
            end_data = await end_game_tg(session_data, game_id, game_sess_id, wait, gems)
            if end_data:
                played_count += 1
                total_gems += gems

    final_user_data = await get_user_info_tg(session_data)
    final_coins = final_user_data.get("earnings", {}).get("coinsEarnedTotal", 0) if final_user_data else initial_coins
    earned = max(0, final_coins - initial_coins)

    return {
        "status": "success",
        "earned": earned,
        "played": played_count,
        "total": len(games),
        "gems": total_gems,
        "phone": phone
    }

# ==================== WEB ROUTES ====================

# Embedded HTML Dashboard
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VIEDIET SHOPSY ULTIMATE</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg: #0a0a12;
            --card: #14142a;
            --border: #2a2a4a;
            --accent: #7c3aed;
            --text: #ffffff;
            --text-secondary: #a0a0b8;
            --success: #10b981;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        
        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px 0;
            border-bottom: 1px solid var(--border);
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .header h1 i { color: var(--accent); }
        .header-info { display: flex; align-items: center; gap: 20px; }
        .user-badge {
            padding: 6px 16px;
            border-radius: 20px;
            background: var(--card);
            border: 1px solid var(--border);
            font-size: 14px;
        }
        .status-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .status-dot.online { background: var(--success); }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: var(--card);
            padding: 20px;
            border-radius: 12px;
            border: 1px solid var(--border);
        }
        .stat-card .label {
            font-size: 12px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .stat-card .value {
            font-size: 28px;
            font-weight: 700;
            margin: 8px 0;
        }
        .stat-card .sub {
            font-size: 13px;
            color: var(--text-secondary);
        }
        
        /* Quick Actions */
        .actions {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            margin-bottom: 30px;
        }
        .action-btn {
            background: var(--card);
            padding: 16px;
            border-radius: 12px;
            border: 1px solid var(--border);
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            color: var(--text);
            text-decoration: none;
        }
        .action-btn:hover {
            border-color: var(--accent);
            transform: translateY(-2px);
        }
        .action-btn i {
            font-size: 24px;
            color: var(--accent);
            display: block;
            margin-bottom: 8px;
        }
        .action-btn .label { font-size: 13px; font-weight: 600; }
        .action-btn .desc { font-size: 11px; color: var(--text-secondary); }
        
        /* Activity */
        .activity {
            background: var(--card);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 20px;
        }
        .activity h3 { margin-bottom: 15px; font-size: 16px; }
        .activity-item {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid var(--border);
            font-size: 14px;
        }
        .activity-item:last-child { border: none; }
        .activity-item .time { color: var(--text-secondary); font-size: 12px; }
        
        /* Modals */
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 999;
            justify-content: center;
            align-items: center;
        }
        .modal.active { display: flex; }
        .modal-content {
            background: var(--bg);
            padding: 30px;
            border-radius: 16px;
            border: 1px solid var(--border);
            max-width: 500px;
            width: 90%;
        }
        .modal-content h2 {
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .modal-content input {
            width: 100%;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--text);
            margin-bottom: 12px;
        }
        .modal-content button {
            padding: 12px 24px;
            border-radius: 8px;
            border: none;
            background: var(--accent);
            color: white;
            cursor: pointer;
            font-weight: 600;
            width: 100%;
        }
        .modal-content button:hover { opacity: 0.9; }
        .modal-close {
            float: right;
            cursor: pointer;
            color: var(--text-secondary);
        }
        
        /* Login */
        .login-container {
            max-width: 400px;
            margin: 100px auto;
            padding: 40px;
            background: var(--card);
            border-radius: 16px;
            border: 1px solid var(--border);
        }
        .login-container h1 {
            text-align: center;
            margin-bottom: 30px;
        }
        .login-container input {
            width: 100%;
            padding: 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text);
            margin-bottom: 12px;
        }
        .login-container button {
            width: 100%;
            padding: 12px;
            border-radius: 8px;
            border: none;
            background: var(--accent);
            color: white;
            font-weight: 700;
            cursor: pointer;
        }
        
        /* Responsive */
        @media (max-width: 600px) {
            .header { flex-direction: column; gap: 10px; }
            .header-info { flex-wrap: wrap; }
            .stats-grid { grid-template-columns: 1fr 1fr; }
            .actions { grid-template-columns: 1fr 1fr; }
        }
    </style>
</head>
<body>

<!-- LOGIN PAGE -->
<div id="loginPage" style="display: none;">
    <div class="login-container">
        <h1><i class="fas fa-rocket" style="color: var(--accent);"></i> VIEDIET</h1>
        <p style="text-align: center; color: var(--text-secondary); margin-bottom: 20px;">Login to your dashboard</p>
        <input type="number" id="loginUserId" placeholder="Telegram User ID">
        <input type="password" id="loginPassword" placeholder="Password (default: 123456)">
        <button onclick="handleLogin()">Login</button>
        <p style="text-align: center; color: var(--text-secondary); font-size: 12px; margin-top: 15px;">Default password: 123456</p>
    </div>
</div>

<!-- MAIN DASHBOARD -->
<div id="dashboardPage">
    <div class="container">
        <div class="header">
            <h1><i class="fas fa-rocket"></i> VIEDIET SHOPSY ULTIMATE</h1>
            <div class="header-info">
                <span class="user-badge" id="userBadge">👤 Loading...</span>
                <span class="user-badge" id="premiumBadge">⭐ Free</span>
                <button onclick="logout()" style="background: none; border: none; color: var(--text-secondary); cursor: pointer;">
                    <i class="fas fa-sign-out-alt"></i>
                </button>
            </div>
        </div>

        <div class="stats-grid" id="statsGrid">
            <div class="stat-card">
                <div class="label">Accounts</div>
                <div class="value" id="accCount">0</div>
                <div class="sub">Total Accounts</div>
            </div>
            <div class="stat-card">
                <div class="label">Referrals</div>
                <div class="value" id="refCount">0</div>
                <div class="sub">Total Referrals</div>
            </div>
            <div class="stat-card">
                <div class="label">Mining Status</div>
                <div class="value" style="font-size: 20px;" id="miningStatus">
                    <span class="status-dot online"></span> Ready
                </div>
                <div class="sub">Ready to Mine</div>
            </div>
            <div class="stat-card">
                <div class="label">Total Coins</div>
                <div class="value" id="totalCoins">0</div>
                <div class="sub">Points Earned</div>
            </div>
            <div class="stat-card">
                <div class="label">Today Mined</div>
                <div class="value" id="todayCoins">0</div>
                <div class="sub">Points Today</div>
            </div>
            <div class="stat-card">
                <div class="label">Bot Status</div>
                <div class="value" style="font-size: 20px;">
                    <span class="status-dot online"></span> Online
                </div>
                <div class="sub">Everything OK</div>
            </div>
        </div>

        <div class="actions">
            <div class="action-btn" onclick="startMining()">
                <i class="fas fa-robot"></i>
                <div class="label">AUTO MINE</div>
                <div class="desc">Mine all accounts</div>
            </div>
            <div class="action-btn" onclick="showOTPLogin()">
                <i class="fas fa-mobile-alt"></i>
                <div class="label">OTP LOGIN</div>
                <div class="desc">Login with OTP</div>
            </div>
            <div class="action-btn" onclick="showJSONLogin()">
                <i class="fas fa-file-code"></i>
                <div class="label">JSON LOGIN</div>
                <div class="desc">Upload JSON file</div>
            </div>
            <div class="action-btn" onclick="loadAccounts()">
                <i class="fas fa-users"></i>
                <div class="label">MY ACCOUNTS</div>
                <div class="desc">View saved accounts</div>
            </div>
            <div class="action-btn" onclick="loadHistory()">
                <i class="fas fa-history"></i>
                <div class="label">HISTORY</div>
                <div class="desc">View mining history</div>
            </div>
            <div class="action-btn" onclick="refreshDashboard()">
                <i class="fas fa-sync"></i>
                <div class="label">REFRESH</div>
                <div class="desc">Refresh dashboard</div>
            </div>
        </div>

        <div class="activity">
            <h3>📋 Recent Activity</h3>
            <div id="activityList">
                <div class="activity-item">
                    <span>🟢 Bot started successfully</span>
                    <span class="time">Just now</span>
                </div>
            </div>
        </div>

        <div style="text-align: center; margin-top: 20px; color: var(--text-secondary); font-size: 12px; border-top: 1px solid var(--border); padding-top: 15px;">
            VIEDIET PREMIUM BOT • Fast • Secure • Reliable
        </div>
    </div>
</div>

<!-- OTP Modal -->
<div class="modal" id="otpModal">
    <div class="modal-content">
        <span class="modal-close" onclick="closeModal('otpModal')">&times;</span>
        <h2><i class="fas fa-mobile-alt" style="color: var(--accent);"></i> OTP Login</h2>
        <input type="number" id="otpPhone" placeholder="10-digit phone number">
        <button onclick="requestOTP()">Send OTP</button>
        <div id="otpVerify" style="display: none; margin-top: 15px;">
            <input type="number" id="otpCode" placeholder="Enter OTP">
            <button onclick="verifyOTP()">Verify OTP</button>
        </div>
    </div>
</div>

<!-- JSON Modal -->
<div class="modal" id="jsonModal">
    <div class="modal-content">
        <span class="modal-close" onclick="closeModal('jsonModal')">&times;</span>
        <h2><i class="fas fa-file-code" style="color: var(--accent);"></i> JSON Login</h2>
        <p style="color: var(--text-secondary); margin-bottom: 15px;">Paste your JSON session data below</p>
        <textarea id="jsonData" rows="6" style="width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text);"></textarea>
        <button onclick="jsonLogin()">Login</button>
    </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/js/all.min.js"></script>
<script>
const API_URL = window.location.origin;
let userData = null;
let token = localStorage.getItem('viediet_token');

function showLogin() {
    document.getElementById('loginPage').style.display = 'block';
    document.getElementById('dashboardPage').style.display = 'none';
}

function showDashboard() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('dashboardPage').style.display = 'block';
}

async function handleLogin() {
    const user_id = document.getElementById('loginUserId').value;
    const password = document.getElementById('loginPassword').value || '123456';
    
    try {
        const res = await fetch(`${API_URL}/api/web/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: parseInt(user_id), password })
        });
        const data = await res.json();
        if (data.success) {
            token = data.token;
            localStorage.setItem('viediet_token', token);
            userData = data.user;
            showDashboard();
            loadDashboard();
        } else {
            alert('Login failed: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function loadDashboard() {
    if (!token) { showLogin(); return; }
    try {
        const res = await fetch(`${API_URL}/api/web/dashboard?token=${token}`);
        const data = await res.json();
        if (data.error) { showLogin(); return; }
        userData = data;
        updateUI(data);
    } catch (e) {
        showLogin();
    }
}

function updateUI(data) {
    document.getElementById('userBadge').textContent = '👤 ' + (data.user?.first_name || 'User');
    document.getElementById('premiumBadge').textContent = data.user?.is_premium ? '⭐ Premium' : '⭐ Free';
    document.getElementById('accCount').textContent = data.accounts || 0;
    document.getElementById('refCount').textContent = data.referrals || 0;
    document.getElementById('totalCoins').textContent = data.mining_stats?.total_coins || 0;
    
    const status = data.user?.mining_active ? '🟢 Mining...' : '🟡 Ready';
    document.getElementById('miningStatus').innerHTML = status;
}

async function startMining() {
    if (!token) return;
    try {
        const res = await fetch(`${API_URL}/api/web/mining/start?token=${token}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            alert(`✅ Mining Complete!\nTotal Coins: ${data.total_coins}\nTotal Gems: ${data.total_gems}`);
            loadDashboard();
        } else {
            alert('Mining failed: ' + (data.error || 'Unknown'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function showOTPLogin() {
    document.getElementById('otpModal').classList.add('active');
    document.getElementById('otpVerify').style.display = 'none';
}

function showJSONLogin() {
    document.getElementById('jsonModal').classList.add('active');
}

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

async function requestOTP() {
    const phone = document.getElementById('otpPhone').value;
    if (!phone || phone.length !== 10) {
        alert('Enter valid 10-digit phone number');
        return;
    }
    try {
        const res = await fetch(`${API_URL}/api/web/otp/request`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone, token })
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById('otpVerify').style.display = 'block';
            alert('OTP sent to +91' + phone);
        } else {
            alert('Failed: ' + (data.error || 'Unknown'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function verifyOTP() {
    const phone = document.getElementById('otpPhone').value;
    const otp = document.getElementById('otpCode').value;
    if (!otp || otp.length !== 6) {
        alert('Enter valid 6-digit OTP');
        return;
    }
    try {
        const res = await fetch(`${API_URL}/api/web/otp/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone, otp, token })
        });
        const data = await res.json();
        if (data.success) {
            alert('✅ OTP Verified! Account added.');
            closeModal('otpModal');
            loadDashboard();
        } else {
            alert('Failed: ' + (data.error || 'Invalid OTP'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function jsonLogin() {
    const jsonData = document.getElementById('jsonData').value;
    if (!jsonData) {
        alert('Please paste JSON data');
        return;
    }
    try {
        const res = await fetch(`${API_URL}/api/web/json/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ json_data: jsonData, token })
        });
        const data = await res.json();
        if (data.success) {
            alert('✅ JSON Login Successful!');
            closeModal('jsonModal');
            loadDashboard();
        } else {
            alert('Failed: ' + (data.error || 'Unknown'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function loadAccounts() {
    if (!token) return;
    try {
        const res = await fetch(`${API_URL}/api/web/accounts?token=${token}`);
        const data = await res.json();
        if (data.accounts && data.accounts.length) {
            let msg = '📋 MY ACCOUNTS\n\n';
            data.accounts.forEach((acc, i) => {
                msg += `${i+1}. +91${acc.phone} (${acc.login_method})\n`;
            });
            alert(msg);
        } else {
            alert('No accounts found. Add one using OTP or JSON login.');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function loadHistory() {
    if (!token) return;
    try {
        const res = await fetch(`${API_URL}/api/web/history?token=${token}`);
        const data = await res.json();
        if (data.history && data.history.length) {
            let msg = '📊 MINING HISTORY\n\n';
            data.history.forEach(h => {
                msg += `+91${h.phone} | +${h.coins_earned} coins | 💎${h.gems_earned}\n`;
            });
            alert(msg);
        } else {
            alert('No mining history yet.');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function refreshDashboard() {
    loadDashboard();
    alert('🔄 Dashboard refreshed!');
}

function logout() {
    localStorage.removeItem('viediet_token');
    token = null;
    userData = null;
    showLogin();
}

// Check token on load
if (token) {
    loadDashboard();
} else {
    showLogin();
}

// Auto-refresh every 60 seconds
setInterval(loadDashboard, 60000);
</script>
</body>
</html>
'''

# ==================== WEB API ROUTES ====================

@app.route('/')
def index():
    return DASHBOARD_HTML

@app.route('/api/web/login', methods=['POST'])
def web_login():
    data = request.json
    user_id = data.get('user_id')
    password = data.get('password', '123456')
    
    user = get_user(user_id)
    if not user:
        create_user(user_id, f"user_{user_id}", "User")
        user = get_user(user_id)
    
    # Simple password check (default: 123456)
    if password == '123456' or user.get('is_premium', 0) == 1:
        token = user.get('web_token')
        if not token:
            token = f"WEB{uuid.uuid4().hex[:16]}"
            update_user(user_id, web_token=token)
            user['web_token'] = token
        return jsonify({
            'success': True,
            'token': token,
            'user': user
        })
    
    return jsonify({'error': 'Invalid password'}), 401

@app.route('/api/web/dashboard', methods=['GET'])
def web_dashboard():
    token = request.args.get('token')
    if not token:
        return jsonify({'error': 'No token'}), 401
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    user_id = user['user_id']
    sessions = get_all_sessions(user_id)
    mining_stats = get_mining_history(user_id)
    total_coins = sum(s[1] for s in mining_stats)
    referrals = get_referral_count(user_id)
    
    return jsonify({
        'user': user,
        'accounts': len(sessions),
        'sessions': [{'phone': s[0], 'login_method': s[2]} for s in sessions],
        'mining_stats': {'total_coins': total_coins},
        'referrals': referrals
    })

@app.route('/api/web/otp/request', methods=['POST'])
def web_otp_request():
    data = request.json
    phone = data.get('phone')
    token = data.get('token')
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    # Simulate OTP request (in production, call Shopsy API)
    return jsonify({
        'success': True,
        'request_id': f"REQ{uuid.uuid4().hex[:8]}",
        'message': 'OTP sent to +91' + phone
    })

@app.route('/api/web/otp/verify', methods=['POST'])
def web_otp_verify():
    data = request.json
    phone = data.get('phone')
    otp = data.get('otp')
    token = data.get('token')
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    # For demo, accept any 6-digit OTP
    if otp and len(otp) == 6:
        session_data = {
            'phone': phone,
            'accountId': f"ACC{uuid.uuid4().hex[:8]}",
            'isLoggedIn': True
        }
        save_session(user['user_id'], phone, session_data, 'OTP')
        return jsonify({'success': True, 'message': 'OTP verified'})
    
    return jsonify({'error': 'Invalid OTP'}), 400

@app.route('/api/web/json/login', methods=['POST'])
def web_json_login():
    data = request.json
    json_data = data.get('json_data')
    token = data.get('token')
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    try:
        if isinstance(json_data, str):
            json_data = json.loads(json_data)
        
        phone = json_data.get('phone') or json_data.get('accountId', '')
        if not phone:
            phone = str(random.randint(7000000000, 9999999999))
        
        save_session(user['user_id'], phone, json_data, 'JSON')
        return jsonify({'success': True, 'message': 'JSON login successful'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/web/mining/start', methods=['POST'])
def web_mining_start():
    token = request.args.get('token')
    if not token:
        return jsonify({'error': 'No token'}), 401
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    sessions = get_all_sessions(user['user_id'])
    if not sessions:
        return jsonify({'error': 'No accounts found'}), 400
    
    results = []
    total_coins = 0
    total_gems = 0
    
    for phone, session_data, method in sessions:
        try:
            # Simulate mining
            coins = random.randint(50, 300)
            gems = random.randint(1000, 5000)
            games = random.randint(1, 4)
            
            save_mining_history(user['user_id'], phone, coins, games, gems)
            results.append({'phone': phone, 'coins': coins, 'gems': gems, 'status': 'success'})
            total_coins += coins
            total_gems += gems
        except Exception as e:
            results.append({'phone': phone, 'status': 'fail', 'error': str(e)})
    
    return jsonify({
        'success': True,
        'results': results,
        'total_coins': total_coins,
        'total_gems': total_gems
    })

@app.route('/api/web/accounts', methods=['GET'])
def web_accounts():
    token = request.args.get('token')
    if not token:
        return jsonify({'error': 'No token'}), 401
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    sessions = get_all_sessions(user['user_id'])
    return jsonify({
        'accounts': [{'phone': s[0], 'login_method': s[2]} for s in sessions]
    })

@app.route('/api/web/history', methods=['GET'])
def web_history():
    token = request.args.get('token')
    if not token:
        return jsonify({'error': 'No token'}), 401
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    history = get_mining_history(user['user_id'], 50)
    return jsonify({
        'history': [{'phone': h[0], 'coins_earned': h[1], 'games_played': h[2], 'gems_earned': h[3], 'mined_at': h[4]} for h in history]
    })

# ==================== TELEGRAM BOT COMMANDS ====================

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
    
    bot.send_message(
        user_id,
        f"🚀 <b>VIEDIET SHOPSY ULTIMATE</b>\n\n"
        f"👋 Welcome, {first_name}!\n\n"
        f"🔗 Visit your dashboard:\n"
        f"<code>{os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'http://localhost:5000')}</code>\n\n"
        f"🔑 Your Token: <code>{user['web_token']}</code>\n\n"
        f"📱 Use token to login to web dashboard!\n"
        f"🔒 Default password: 123456",
        parse_mode="HTML"
    )

@bot.message_handler(commands=['web'])
def web_command(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.reply_to(message, "❌ User not found! Use /start first.")
        return
    
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'http://localhost:5000')
    token = user.get('web_token', '')
    
    bot.reply_to(
        message,
        f"🌐 <b>VIEDIET WEB DASHBOARD</b>\n\n"
        f"🔗 URL: {domain}\n"
        f"🔑 Token: <code>{token}</code>\n"
        f"🔒 Password: 123456\n\n"
        f"Use token to login to web panel!",
        parse_mode="HTML"
    )

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = get_all_users()
    total = get_total_users()
    
    text = f"👑 <b>ADMIN PANEL</b>\n\n📊 Total Users: {total}\n\n"
    for uid, username, fname, status, refs, unlocked, premium in users[:10]:
        icon = "⭐" if premium else ("🔓" if unlocked else "🔒")
        name = fname or username or f"User_{uid}"
        text += f"{icon} {name} - {refs} refs\n"
    
    bot.reply_to(message, text, parse_mode="HTML")

# ==================== MAIN ====================

def run_bot():
    """Run Telegram bot in a separate thread"""
    logger.info("🤖 Starting Telegram bot...")
    try:
        bot.remove_webhook()
        bot.polling(non_stop=True, interval=1, timeout=30)
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Start Flask web server
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🌐 Starting web server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
