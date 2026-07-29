#!/usr/bin/env python3
# VIEDIET SHOPSY ULTIMATE - Backend + Bot (Railway)

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
from datetime import datetime
from flask import Flask, request, jsonify
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

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "viediet_ultimate.db")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== FLASK APP ====================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "viediet-secret-2026")
CORS(app)

# ==================== BOT ====================
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
        referral_code TEXT,
        referrals_count INTEGER DEFAULT 0,
        is_unlocked INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        mining_active INTEGER DEFAULT 0,
        total_accounts_added INTEGER DEFAULT 0,
        max_accounts_allowed INTEGER DEFAULT 5,
        web_token TEXT UNIQUE,
        credits INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS shopsy_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        phone TEXT,
        session_data TEXT,
        login_method TEXT DEFAULT 'JSON',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        is_valid INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE
    )''')
    conn.commit()
    conn.close()
    logger.info("Database initialized")

init_db()

# ==================== DB FUNCTIONS ====================
def get_user(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0], 'username': row[1], 'first_name': row[2],
            'status': row[3], 'registered_at': row[4], 'last_used': row[5],
            'referred_by': row[6], 'referral_code': row[7],
            'referrals_count': row[8] if len(row) > 8 else 0,
            'is_unlocked': row[9] if len(row) > 9 else 0,
            'is_premium': row[10] if len(row) > 10 else 0,
            'mining_active': row[11] if len(row) > 11 else 0,
            'total_accounts_added': row[12] if len(row) > 12 else 0,
            'max_accounts_allowed': row[13] if len(row) > 13 else 5,
            'web_token': row[14] if len(row) > 14 else None,
            'credits': row[15] if len(row) > 15 else 0
        }
    return None

def get_user_by_token(token):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE web_token = ?', (token,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0], 'first_name': row[2], 'is_premium': row[10] if len(row) > 10 else 0,
            'mining_active': row[11] if len(row) > 11 else 0, 'web_token': row[14] if len(row) > 14 else None
        }
    return None

def create_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    token = f"WEB{uuid.uuid4().hex[:16]}"
    c.execute('''INSERT OR IGNORE INTO users 
        (user_id, username, first_name, registered_at, last_used, referral_code, web_token)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (user_id, username, first_name, now, now, f"REF{user_id}", token))
    conn.commit()
    conn.close()
    return token

def update_user(user_id, **kwargs):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    updates = [f"{k} = ?" for k in kwargs]
    values = list(kwargs.values()) + [user_id]
    c.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", values)
    conn.commit()
    conn.close()

def get_all_sessions(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT phone, login_method FROM shopsy_sessions WHERE user_id = ?', (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{'phone': r[0], 'login_method': r[1]} for r in rows]

def save_session(user_id, phone, session_data, method="JSON"):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO shopsy_sessions (user_id, phone, session_data, login_method) VALUES (?, ?, ?, ?)',
              (user_id, phone, json.dumps(session_data), method))
    conn.commit()
    conn.close()
    update_user(user_id, total_accounts_added=len(get_all_sessions(user_id)))

def save_mining_history(user_id, phone, coins, games, gems):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('INSERT INTO mining_history (user_id, phone, coins_earned, games_played, gems_earned) VALUES (?, ?, ?, ?, ?)',
              (user_id, phone, coins, games, gems))
    conn.commit()
    conn.close()

def get_mining_history(user_id, limit=50):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT phone, coins_earned, games_played, gems_earned, mined_at FROM mining_history WHERE user_id = ? ORDER BY mined_at DESC LIMIT ?',
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{'phone': r[0], 'coins_earned': r[1], 'games_played': r[2], 'gems_earned': r[3], 'mined_at': r[4]} for r in rows]

def get_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND is_valid = 1', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

# ==================== WEB API ROUTES ====================

@app.route('/api/web/login', methods=['POST'])
def web_login():
    data = request.json
    user_id = data.get('user_id')
    password = data.get('password', '123456')
    
    user = get_user(user_id)
    if not user:
        token = create_user(user_id, f"user_{user_id}", "User")
        user = get_user(user_id)
    else:
        token = user.get('web_token')
        if not token:
            token = f"WEB{uuid.uuid4().hex[:16]}"
            update_user(user_id, web_token=token)
    
    if password == '123456' or user.get('is_premium', 0) == 1:
        return jsonify({'success': True, 'token': token, 'user': user})
    return jsonify({'error': 'Invalid password'}), 401

@app.route('/api/web/dashboard', methods=['GET'])
def web_dashboard():
    token = request.args.get('token')
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    user_id = user['user_id']
    sessions = get_all_sessions(user_id)
    history = get_mining_history(user_id)
    total_coins = sum(h['coins_earned'] for h in history)
    today_coins = sum(h['coins_earned'] for h in history if h['mined_at'].startswith(datetime.now().strftime('%Y-%m-%d')))
    referrals = get_referral_count(user_id)
    
    return jsonify({
        'user': user,
        'accounts': len(sessions),
        'sessions': sessions,
        'referrals': referrals,
        'mining_stats': {'total_coins': total_coins},
        'today_coins': today_coins
    })

@app.route('/api/web/otp/request', methods=['POST'])
def web_otp_request():
    data = request.json
    phone = data.get('phone')
    token = data.get('token')
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    # Simulate OTP (in production, call Shopsy API)
    return jsonify({'success': True, 'request_id': f"REQ{uuid.uuid4().hex[:8]}"})

@app.route('/api/web/otp/verify', methods=['POST'])
def web_otp_verify():
    data = request.json
    phone = data.get('phone')
    otp = data.get('otp')
    token = data.get('token')
    
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    if otp and len(otp) == 6:
        session_data = {'phone': phone, 'accountId': f"ACC{uuid.uuid4().hex[:8]}", 'isLoggedIn': True}
        save_session(user['user_id'], phone, session_data, 'OTP')
        return jsonify({'success': True})
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
        phone = json_data.get('phone') or json_data.get('accountId') or str(random.randint(7000000000, 9999999999))
        save_session(user['user_id'], phone, json_data, 'JSON')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/web/mining/start', methods=['POST'])
def web_mining_start():
    token = request.args.get('token')
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    
    sessions = get_all_sessions(user['user_id'])
    if not sessions:
        return jsonify({'error': 'No accounts found'}), 400
    
    results = []
    total_coins = 0
    total_gems = 0
    
    for s in sessions:
        coins = random.randint(50, 300)
        gems = random.randint(1000, 5000)
        games = random.randint(1, 4)
        save_mining_history(user['user_id'], s['phone'], coins, games, gems)
        results.append({'phone': s['phone'], 'coins': coins, 'gems': gems})
        total_coins += coins
        total_gems += gems
    
    return jsonify({'success': True, 'results': results, 'total_coins': total_coins, 'total_gems': total_gems})

@app.route('/api/web/accounts', methods=['GET'])
def web_accounts():
    token = request.args.get('token')
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    return jsonify({'accounts': get_all_sessions(user['user_id'])})

@app.route('/api/web/history', methods=['GET'])
def web_history():
    token = request.args.get('token')
    user = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 401
    return jsonify({'history': get_mining_history(user['user_id'])})

# ==================== TELEGRAM BOT ====================

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "User"
    
    user = get_user(user_id)
    if not user:
        token = create_user(user_id, username, first_name)
        user = get_user(user_id)
    
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'https://your-railway-url.railway.app')
    bot.send_message(
        user_id,
        f"🚀 <b>VIEDIET SHOPSY ULTIMATE</b>\n\n"
        f"👋 Welcome, {first_name}!\n\n"
        f"🌐 Visit your dashboard:\n<code>{domain}</code>\n\n"
        f"🔑 Your Token: <code>{user['web_token']}</code>\n"
        f"🔒 Default Password: 123456\n\n"
        f"Use this token to login to web dashboard!"
    )

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.reply_to(message, "👑 Admin panel loaded. Use /stats for details.")

# ==================== MAIN ====================

def run_bot():
    logger.info("🤖 Starting bot...")
    try:
        bot.remove_webhook()
        bot.polling(non_stop=True, interval=1, timeout=30)
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
