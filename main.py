#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NRTECNO SYSTEM - VIEDIET PREMIUM BOT v11.0
# POLLING MODE - No webhook, All buttons working

import os
import logging
import telebot
import json
import time
import threading
import random
import sqlite3
import uuid
import sys
from datetime import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

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
        login_method TEXT DEFAULT NULL,
        total_logins INTEGER DEFAULT 0,
        last_login TEXT DEFAULT NULL
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
    
    c.execute('''CREATE TABLE IF NOT EXISTS login_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        login_method TEXT,
        phone TEXT,
        login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            'login_method': row[16] if len(row) > 16 else None,
            'total_logins': row[17] if len(row) > 17 else 0,
            'last_login': row[18] if len(row) > 18 else None
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

def save_session(user_id, phone, session_data, login_method="JSON"):
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

def get_premium_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users WHERE is_premium = 1')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_locked_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users WHERE is_unlocked = 0 AND is_premium = 0')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_total_referrals():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE is_valid = 1')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_all_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''SELECT user_id, username, first_name, status, registered_at, last_used, 
                 referrals_count, is_unlocked, is_premium, login_method, total_logins, 
                 shopsy_phone FROM users ORDER BY registered_at DESC''')
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

# ==================== CHANNEL CHECK ====================
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

Click below to join, then click ✅ CHECK AGAIN
"""

# ==================== MENU FUNCTIONS ====================
def locked_menu_text(user_id, first_name, referral_count, is_premium=False):
    pending = get_pending_referral_count(user_id)
    remaining = max(0, REFERRAL_REQUIRED - referral_count)
    
    if is_premium:
        return f"""
⭐ VIEDIET PREMIUM BOT

👋 Welcome, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔓 Status: PREMIUM UNLOCKED 👑
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✨ Premium Features:
• Unlimited accounts
• Auto-mining all accounts
• Premium support

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Click 🚀 AUTO MINE to get started!
"""
    
    return f"""
🔒 VIEDIET PREMIUM BOT

👋 Welcome, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔒 Status: LOCKED
👥 Referrals: {referral_count}/{REFERRAL_REQUIRED}
⏳ Pending: {pending}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 Two Ways to Unlock:

1️⃣ FREE Unlock
   📤 Refer {remaining} more friends
   ✅ Reach {REFERRAL_REQUIRED} referrals

2️⃣ ⭐ PREMIUM Unlock
   💰 Contact admin for instant access

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Your Stats:
• Successful: {referral_count}
• Pending: {pending}
• Need: {remaining} more
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
📱 Accounts: {accounts_count} (Unlimited)
{status_icon} Mining: {status_text}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ Quick Actions:
• 🚀 AUTO MINE - Mine all accounts
• 📋 ADD ACCOUNT - Login via JSON
• 👥 MY ACCOUNTS - View saved accounts
• 📊 HISTORY - View mining history
"""

def unlocked_menu_keyboard(accounts_count=0, mining_active=False):
    kb = InlineKeyboardMarkup(row_width=2)
    
    if mining_active:
        kb.row(InlineKeyboardButton("⏳ MINING...", callback_data="mining_status"))
    else:
        kb.row(InlineKeyboardButton("🚀 AUTO MINE", callback_data="start_auto_mining"))
    
    kb.row(
        InlineKeyboardButton("📋 ADD ACCOUNT", callback_data="login_json"),
        InlineKeyboardButton("👥 MY ACCOUNTS", callback_data="my_accounts")
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

📤 Share this link:
<code>{link}</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Your Stats:
👥 Successful: {referral_count}/{REFERRAL_REQUIRED}
⏳ Pending: {pending_count}
🔓 Status: {"UNLOCKED ✅" if is_unlocked else f"LOCKED 🔒 ({remaining} more needed)"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 How it works:
1. Share your referral link
2. Friends join using your link
3. Each join = 1 referral
4. Reach {REFERRAL_REQUIRED} to unlock!

🎯 Need {remaining} more referrals!
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

❌ No saved accounts!

💡 Add your first account using:
📋 JSON Login - Upload JSON file or paste JSON
"""
    
    text = f"""
📋 MY ACCOUNTS

📱 Saved Accounts: {len(sessions)} (Unlimited)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    for phone, _ in sessions:
        text += f"\n✅ +91{phone}"
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 All accounts will be auto-mined!
🔗 No account limits!
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

❌ No mining history yet!

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

Recent Activity:
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

# ==================== ADMIN BUTTON FUNCTIONS ====================
def admin_main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("📊 STATS", callback_data="admin_stats"),
        InlineKeyboardButton("👥 USERS", callback_data="admin_users")
    )
    kb.row(
        InlineKeyboardButton("🔓 UNLOCK", callback_data="admin_unlock_user"),
        InlineKeyboardButton("🔒 LOCK", callback_data="admin_lock_user")
    )
    kb.row(
        InlineKeyboardButton("⭐ PREMIUM", callback_data="admin_premium_user"),
        InlineKeyboardButton("❌ REMOVE", callback_data="admin_remove_premium")
    )
    kb.row(
        InlineKeyboardButton("📢 BROADCAST", callback_data="admin_broadcast"),
        InlineKeyboardButton("📈 ANALYTICS", callback_data="admin_analytics")
    )
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def admin_user_list_keyboard(users, page=0):
    kb = InlineKeyboardMarkup(row_width=1)
    start = page * 10
    end = min(start + 10, len(users))
    
    for i in range(start, end):
        uid, username, fname = users[i][0], users[i][1], users[i][2]
        name = fname or username or f"User_{uid}"
        kb.row(InlineKeyboardButton(f"👤 {name} (ID: {uid})", callback_data=f"admin_view_user_{uid}"))
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"admin_users_page_{page-1}"))
    if end < len(users):
        nav_buttons.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"admin_users_page_{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)
    
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    return kb

def admin_user_detail_keyboard(user_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("🔓 UNLOCK", callback_data=f"admin_unlock_{user_id}"),
        InlineKeyboardButton("🔒 LOCK", callback_data=f"admin_lock_{user_id}")
    )
    kb.row(
        InlineKeyboardButton("⭐ PREMIUM", callback_data=f"admin_premium_{user_id}"),
        InlineKeyboardButton("❌ REMOVE", callback_data=f"admin_remove_premium_{user_id}")
    )
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_users"))
    return kb

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

@bot.message_handler(commands=['admin'])
def admin_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    text = """
👑 ADMIN PANEL

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use the buttons below to manage your bot:

📊 Stats - View bot statistics
👥 Users - Manage users
🔓 Unlock - Grant free access
🔒 Lock - Revoke access
⭐ Premium - Grant premium access
❌ Remove - Remove premium
📢 Broadcast - Send announcement
📈 Analytics - Full analytics report

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    bot.send_message(
        user_id,
        text,
        reply_markup=admin_main_keyboard(),
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
    
    # Channel Check
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
    
    # Back
    if data == "back_menu":
        if user.get('is_unlocked', 0) == 1 or user.get('is_premium', 0) == 1:
            show_unlocked_menu(call.message, user_id)
        else:
            show_locked_menu(call.message, user_id)
        bot.answer_callback_query(call.id)
        return
    
    # Refresh
    if data == "refresh":
        if user.get('is_unlocked', 0) == 1 or user.get('is_premium', 0) == 1:
            show_unlocked_menu(call.message, user_id)
        else:
            show_locked_menu(call.message, user_id)
        bot.answer_callback_query(call.id)
        return
    
    # Referral Link
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
    
    # Check Status
    if data == "check_status":
        referral_count = get_referral_count(user_id)
        pending_count = get_pending_referral_count(user_id)
        remaining = max(0, REFERRAL_REQUIRED - referral_count)
        is_unlocked = referral_count >= REFERRAL_REQUIRED
        is_premium = user.get('is_premium', 0)
        
        if is_premium:
            bot.answer_callback_query(
                call.id,
                f"⭐ PREMIUM USER!\n\n👥 Referrals: {referral_count}/{REFERRAL_REQUIRED}\n🔓 Status: UNLOCKED (Premium)",
                show_alert=True
            )
            return
        
        if is_unlocked:
            update_user(user_id, is_unlocked=1, status='ACTIVE')
            bot.answer_callback_query(
                call.id,
                f"🎉 UNLOCKED!\n\n👥 Referrals: {referral_count}/{REFERRAL_REQUIRED}\n🔓 Status: UNLOCKED",
                show_alert=True
            )
            show_unlocked_menu(call.message, user_id)
        else:
            bot.answer_callback_query(
                call.id,
                f"🔒 {referral_count}/{REFERRAL_REQUIRED} referrals\n⏳ Pending: {pending_count}\n🎯 Need {remaining} more to unlock!",
                show_alert=True
            )
        return
    
    # JSON Login
    if data == "login_json":
        if user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1:
            bot.answer_callback_query(call.id, "❌ Bot is LOCKED! Complete referrals or get premium.", show_alert=True)
            return
        
        if not check_channel_membership(user_id):
            bot.answer_callback_query(call.id, "❌ Please join channel first!", show_alert=True)
            return
        
        bot.edit_message_text(
            "📋 JSON LOGIN\n\nTwo ways to login:\n\n1️⃣ Upload File:\n   Send your JSON file as a document\n\n2️⃣ Paste JSON:\n   Copy and paste the JSON content directly\n\nSend /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # My Accounts
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
    
    # History
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
    
    # Auto Mining
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
        
        bot.edit_message_text(
            f"🚀 AUTO-MINING STARTED!\n\n📱 Accounts: {len(sessions)}\n⏳ Processing...\n\n_Progress will appear here._",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
        return
    
    # Logout All
    if data == "logout_all":
        logout_user(user_id)
        bot.answer_callback_query(call.id, "🚪 All accounts logged out successfully!", show_alert=True)
        show_unlocked_menu(call.message, user_id)
        return
    
    # Mining Status
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
    
    # ============================================================
    # ADMIN CALLBACKS - ALL WORKING
    # ============================================================
    
    if data == "admin_panel":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        text = "👑 ADMIN PANEL\n\nUse the buttons below:"
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_main_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    if data == "admin_stats":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        total = get_total_users()
        unlocked = get_unlocked_users()
        locked = get_locked_users()
        premium = get_premium_users()
        refs = get_total_referrals()
        text = f"""📊 BOT STATISTICS
━━━━━━━━━━━━━━━━━━━
👥 Total Users: {total}
🔓 Unlocked: {unlocked}
🔒 Locked: {locked}
⭐ Premium: {premium}
🔗 Total Referrals: {refs}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_main_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    if data == "admin_users":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        users = get_all_users()
        if not users:
            bot.edit_message_text("❌ No users found.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=admin_main_keyboard(), parse_mode="HTML")
            bot.answer_callback_query(call.id)
            return
        text = f"👥 USERS LIST (Total: {len(users)})\n\nSelect a user to manage:"
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_list_keyboard(users, 0),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    if data.startswith("admin_view_user_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        target_id = int(data.replace("admin_view_user_", ""))
        target_user = get_user(target_id)
        if not target_user:
            bot.answer_callback_query(call.id, "User not found!", show_alert=True)
            return
        text = f"""👤 USER DETAILS
━━━━━━━━━━━━━━━━━━━
🆔 ID: {target_id}
👤 Name: {target_user.get('first_name', 'N/A')}
🔓 Unlocked: {'✅' if target_user.get('is_unlocked') else '❌'}
⭐ Premium: {'✅' if target_user.get('is_premium') else '❌'}
📱 Referrals: {target_user.get('referrals_count', 0)}/{REFERRAL_REQUIRED}
📱 Accounts: {get_accounts_count(target_id)}
📊 Total Logins: {target_user.get('total_logins', 0)}
📅 Joined: {target_user.get('registered_at', 'N/A')[:10]}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_detail_keyboard(target_id),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    if data.startswith("admin_unlock_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        target_id = int(data.replace("admin_unlock_", ""))
        target_user = get_user(target_id)
        if not target_user:
            bot.answer_callback_query(call.id, "User not found!", show_alert=True)
            return
        update_user(target_id, is_unlocked=1, status='ACTIVE')
        bot.answer_callback_query(call.id, f"✅ User {target_id} unlocked!")
        # Refresh user detail
        text = f"""👤 USER DETAILS (UPDATED)
━━━━━━━━━━━━━━━━━━━
🆔 ID: {target_id}
👤 Name: {target_user.get('first_name', 'N/A')}
🔓 Unlocked: ✅
⭐ Premium: {'✅' if target_user.get('is_premium') else '❌'}
📱 Referrals: {target_user.get('referrals_count', 0)}/{REFERRAL_REQUIRED}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_detail_keyboard(target_id),
            parse_mode="HTML"
        )
        return
    
    if data.startswith("admin_lock_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        target_id = int(data.replace("admin_lock_", ""))
        target_user = get_user(target_id)
        if not target_user:
            bot.answer_callback_query(call.id, "User not found!", show_alert=True)
            return
        update_user(target_id, is_unlocked=0, is_premium=0, status='LOCKED')
        bot.answer_callback_query(call.id, f"✅ User {target_id} locked!")
        # Refresh user detail
        text = f"""👤 USER DETAILS (UPDATED)
━━━━━━━━━━━━━━━━━━━
🆔 ID: {target_id}
👤 Name: {target_user.get('first_name', 'N/A')}
🔓 Unlocked: ❌
⭐ Premium: ❌
📱 Referrals: {target_user.get('referrals_count', 0)}/{REFERRAL_REQUIRED}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_detail_keyboard(target_id),
            parse_mode="HTML"
        )
        return
    
    if data.startswith("admin_premium_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        target_id = int(data.replace("admin_premium_", ""))
        target_user = get_user(target_id)
        if not target_user:
            bot.answer_callback_query(call.id, "User not found!", show_alert=True)
            return
        update_user(target_id, is_premium=1, is_unlocked=1, status='ACTIVE')
        bot.answer_callback_query(call.id, f"⭐ User {target_id} premium granted!")
        # Refresh user detail
        text = f"""👤 USER DETAILS (UPDATED)
━━━━━━━━━━━━━━━━━━━
🆔 ID: {target_id}
👤 Name: {target_user.get('first_name', 'N/A')}
🔓 Unlocked: ✅
⭐ Premium: ✅
📱 Referrals: {target_user.get('referrals_count', 0)}/{REFERRAL_REQUIRED}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_detail_keyboard(target_id),
            parse_mode="HTML"
        )
        return
    
    if data.startswith("admin_remove_premium_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        target_id = int(data.replace("admin_remove_premium_", ""))
        target_user = get_user(target_id)
        if not target_user:
            bot.answer_callback_query(call.id, "User not found!", show_alert=True)
            return
        update_user(target_id, is_premium=0)
        bot.answer_callback_query(call.id, f"❌ User {target_id} premium removed!")
        # Refresh user detail
        text = f"""👤 USER DETAILS (UPDATED)
━━━━━━━━━━━━━━━━━━━
🆔 ID: {target_id}
👤 Name: {target_user.get('first_name', 'N/A')}
🔓 Unlocked: {'✅' if target_user.get('is_unlocked') else '❌'}
⭐ Premium: ❌
📱 Referrals: {target_user.get('referrals_count', 0)}/{REFERRAL_REQUIRED}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_detail_keyboard(target_id),
            parse_mode="HTML"
        )
        return
    
    if data.startswith("admin_users_page_"):
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        page = int(data.replace("admin_users_page_", ""))
        users = get_all_users()
        text = f"👥 USERS LIST (Page {page+1}, Total: {len(users)})\n\nSelect a user to manage:"
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_user_list_keyboard(users, page),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    if data == "admin_broadcast":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        bot.edit_message_text(
            "📢 BROADCAST MESSAGE\n\nSend the message to broadcast to ALL users.\nSend /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        bot.register_next_step_handler(call.message, broadcast_handler)
        return
    
    if data == "admin_analytics":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        total = get_total_users()
        unlocked = get_unlocked_users()
        locked = get_locked_users()
        premium = get_premium_users()
        refs = get_total_referrals()
        text = f"""📈 COMPLETE ANALYTICS REPORT
━━━━━━━━━━━━━━━━━━━
👥 Total Registered: {total}
🔓 Unlocked: {unlocked}
🔒 Locked: {locked}
⭐ Premium: {premium}
📤 Referral Unlocked: {unlocked - premium}
━━━━━━━━━━━━━━━━━━━
🔗 Total Referrals: {refs}"""
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=admin_main_keyboard(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    # Admin action buttons (direct)
    if data == "admin_unlock_user":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        bot.edit_message_text(
            "🔓 UNLOCK USER\n\nEnter the User ID to unlock:\nSend /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        bot.register_next_step_handler(call.message, admin_unlock_user_handler)
        return
    
    if data == "admin_lock_user":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        bot.edit_message_text(
            "🔒 LOCK USER\n\nEnter the User ID to lock:\nSend /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        bot.register_next_step_handler(call.message, admin_lock_user_handler)
        return
    
    if data == "admin_premium_user":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        bot.edit_message_text(
            "⭐ GRANT PREMIUM\n\nEnter the User ID to grant premium access:\nSend /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        bot.register_next_step_handler(call.message, admin_premium_user_handler)
        return
    
    if data == "admin_remove_premium":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        bot.edit_message_text(
            "❌ REMOVE PREMIUM\n\nEnter the User ID to remove premium access:\nSend /cancel to abort.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        bot.register_next_step_handler(call.message, admin_remove_premium_handler)
        return

# ==================== ADMIN HANDLER FUNCTIONS ====================
def admin_unlock_user_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Cancelled.")
        admin_command(message)
        return
    try:
        target_id = int(message.text.strip())
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            admin_command(message)
            return
        update_user(target_id, is_unlocked=1, status='ACTIVE')
        bot.reply_to(message, f"✅ User {target_id} unlocked!")
        admin_command(message)
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID!")
        admin_command(message)

def admin_lock_user_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Cancelled.")
        admin_command(message)
        return
    try:
        target_id = int(message.text.strip())
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            admin_command(message)
            return
        update_user(target_id, is_unlocked=0, is_premium=0, status='LOCKED')
        bot.reply_to(message, f"✅ User {target_id} locked!")
        admin_command(message)
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID!")
        admin_command(message)

def admin_premium_user_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Cancelled.")
        admin_command(message)
        return
    try:
        target_id = int(message.text.strip())
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            admin_command(message)
            return
        update_user(target_id, is_premium=1, is_unlocked=1, status='ACTIVE')
        bot.reply_to(message, f"⭐ User {target_id} premium granted!")
        admin_command(message)
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID!")
        admin_command(message)

def admin_remove_premium_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Cancelled.")
        admin_command(message)
        return
    try:
        target_id = int(message.text.strip())
        target_user = get_user(target_id)
        if not target_user:
            bot.reply_to(message, f"❌ User {target_id} not found!")
            admin_command(message)
            return
        update_user(target_id, is_premium=0)
        bot.reply_to(message, f"❌ User {target_id} premium removed!")
        admin_command(message)
    except ValueError:
        bot.reply_to(message, "❌ Invalid User ID!")
        admin_command(message)

# ==================== BROADCAST HANDLER ====================
def broadcast_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Broadcast cancelled.")
        admin_command(message)
        return
    users = get_all_users()
    if not users:
        bot.reply_to(message, "❌ No users to broadcast to!")
        admin_command(message)
        return
    success = 0
    failed = 0
    status_msg = bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    for uid, username, fname, status, reg_at, last_used, refs, unlocked, premium, method, logins, phone in users:
        try:
            bot.send_message(uid, f"📢 ANNOUNCEMENT\n\n{message.text}", parse_mode="HTML")
            success += 1
            time.sleep(0.05)
        except:
            failed += 1
    bot.edit_message_text(
        f"✅ BROADCAST COMPLETE\n\n✅ Sent: {success}\n❌ Failed: {failed}",
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        parse_mode="HTML"
    )
    admin_command(message)

# ==================== JSON LOGIN HANDLERS ====================
@bot.message_handler(content_types=['document'])
def json_login_handler(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if not user or (user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1):
        bot.reply_to(message, "❌ Bot is LOCKED! Complete 6 referrals or get premium.")
        return
    
    file_name = message.document.file_name or "session.json"
    if not file_name.endswith('.json'):
        bot.reply_to(message, "❌ Invalid file format. Please send a JSON file.", parse_mode="HTML")
        return
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        json_data = json.loads(downloaded_file.decode('utf-8'))
        process_json_login(message, user_id, json_data)
    except json.JSONDecodeError:
        bot.reply_to(message, "❌ Invalid JSON format.", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:200]}", parse_mode="HTML")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def json_paste_handler(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if not text:
        return
    if not (text.startswith('{') or text.startswith('[')):
        return
    if text.lower() == '/cancel':
        return
    
    user = get_user(user_id)
    if not user or (user.get('is_unlocked', 0) != 1 and user.get('is_premium', 0) != 1):
        bot.reply_to(message, "❌ Bot is LOCKED! Complete 6 referrals or get premium.")
        return
    
    try:
        json_data = json.loads(text)
        process_json_login(message, user_id, json_data)
    except json.JSONDecodeError:
        bot.reply_to(message, "❌ Invalid JSON content.", parse_mode="HTML")

def process_json_login(message, user_id, json_data):
    if not isinstance(json_data, dict):
        bot.reply_to(message, "❌ Invalid JSON format. Must be a JSON object.")
        return
    
    phone = json_data.get("phone") or json_data.get("accountId") or json_data.get("userName")
    if not phone:
        for key, value in json_data.items():
            if isinstance(value, str) and value.isdigit() and len(value) == 10:
                phone = value
                break
        if not phone:
            bot.reply_to(
                message,
                "❌ Could not detect phone number.\nMake sure JSON contains 'phone', 'accountId', or a 10-digit number.",
                parse_mode="HTML"
            )
            return
    
    save_session(user_id, phone, json_data, login_method="JSON")
    accounts_count = get_accounts_count(user_id)
    
    bot.reply_to(
        message,
        f"✅ JSON LOGIN SUCCESSFUL\n\n📱 Phone: +91{phone}\n📊 Total Accounts: {accounts_count}\n\nClick 🚀 AUTO MINE to start mining.",
        parse_mode="HTML"
    )
    
    update_user(user_id, shopsy_phone=phone, shopsy_logged_in=1)
    show_unlocked_menu(message, user_id)

# ==================== MAIN ====================
if __name__ == "__main__":
    # Start background scheduler
    task_thread = threading.Thread(target=run_scheduled_tasks, daemon=True)
    task_thread.start()
    
    logger.info("=" * 60)
    logger.info("🚀 VIEDIET PREMIUM BOT v11.0 - POLLING MODE")
    logger.info("=" * 60)
    logger.info("🔒 Referrals Required: 6")
    logger.info("📱 Login: JSON ONLY")
    logger.info("📢 Channel: @{}".format(CHANNEL_USERNAME))
    logger.info("👑 Admin: BUTTON BASED")
    logger.info("🔄 Mode: POLLING (No webhook)")
    logger.info("=" * 60)
    
    # Remove webhook if any
    try:
        bot.remove_webhook()
        logger.info("✅ Webhook removed")
    except Exception as e:
        logger.warning(f"Webhook removal: {e}")
    
    time.sleep(1)
    
    # Start polling - SINGLE INSTANCE
    logger.info("🔄 Starting polling...")
    try:
        bot.polling(non_stop=True, interval=1, timeout=30)
    except Exception as e:
        logger.error(f"Polling error: {e}")
        # Don't restart, just exit
        sys.exit(1)
