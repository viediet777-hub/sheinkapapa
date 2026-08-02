#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VIEDIET REWARD SPIN BOT
# Made by viediet
# All rights reserved.

"""
VIEDIET REWARD SPIN BOT
=======================
A fully functional Telegram bot that lets users earn reward spins by
referring friends. Each spin costs 1 point, each successful referral gives
2 points. The bot uses the Ujala Happiest Onam public API to send OTP,
verify it, spin the wheel and claim the reward.

FEATURES
--------
* Force channel join before using the bot
* Referral system with points (2 points per referral, awarded on join)
* Manual phone / OTP entry (no Firebase scanning)
* Clean button based UI (InlineKeyboardMarkup)
* Admin panel: stats, user management, broadcast, analytics, user details
* Robust Ujala API integration with retries (max 2 attempts)

SETUP
-----
    export BOT_TOKEN="YOUR_BOT_TOKEN"
    export ADMIN_ID="1364476174"
    export CHANNEL_USERNAME="viedietlooters"
    export DATA_DIR="./data"
    python3 viediet_reward_bot.py

REQUIREMENTS
------------
    pip install pyTelegramBotAPI requests
"""

# ════════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ════════════════════════════════════════════════════════════════════════════════
import os
import sys
import json
import time
import uuid
import base64
import hmac
import random
import string
import logging
import hashlib
import sqlite3
import threading
import urllib.parse
from datetime import datetime
from html import escape

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ════════════════════════════════════════════════════════════════════════════════
# 1. CONFIGURATION (environment variables)
# ════════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "").strip() or "0")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "viedietlooters").strip().lstrip("@")
DATA_DIR = os.environ.get("DATA_DIR", "./data").strip()

PRODUCT_CODE = "8902102126232"   # Ujala product code (hardcoded)
SPIN_COST = 1                    # points needed per spin
REFERRAL_POINTS = 2              # points earned per successful referral
PAGE_SIZE = 10                   # admin user list pagination size
API_RETRIES = 2                  # max attempts for every Ujala API call

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN environment variable is not set.")
    print("       export BOT_TOKEN=\"YOUR_BOT_TOKEN\"")
    sys.exit(1)
if ADMIN_ID <= 0:
    print("ERROR: ADMIN_ID environment variable is not set.")
    print("       export ADMIN_ID=\"YOUR_TELEGRAM_ID\"")
    sys.exit(1)

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "reward_bot.db")

# ════════════════════════════════════════════════════════════════════════════════
# 2. LOGGING (console + file)
# ════════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(DATA_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("viediet_reward_bot")

# ════════════════════════════════════════════════════════════════════════════════
# 3. BOT INITIALIZATION
# ════════════════════════════════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ════════════════════════════════════════════════════════════════════════════════
# 4. DATABASE (SQLite)
# ════════════════════════════════════════════════════════════════════════════════

# One global lock so concurrent threads never corrupt SQLite state
_db_lock = threading.RLock()


def get_conn():
    """Open a connection with check_same_thread disabled (thread-safe usage)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all required tables if they do not exist yet."""
    with _db_lock:
        conn = get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id        INTEGER PRIMARY KEY,
                    username       TEXT,
                    first_name     TEXT,
                    registered_at  TEXT,
                    referral_code  TEXT UNIQUE,
                    referred_by    INTEGER,
                    points         INTEGER DEFAULT 0,
                    channel_joined INTEGER DEFAULT 0,
                    is_admin       INTEGER DEFAULT 0,
                    last_spin      TEXT,
                    banned         INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS referrals (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id    INTEGER,
                    referred_id    INTEGER UNIQUE,
                    timestamp      TEXT,
                    points_awarded INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS spin_history (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   INTEGER,
                    phone     TEXT,
                    reward    TEXT,
                    spin_time TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()
    logger.info("Database initialized: %s", DB_PATH)


# ─────────────────────────── users ───────────────────────────

def get_user(user_id):
    """Fetch one user row as dict (or None)."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def create_user(user_id, username, first_name, referred_by=None):
    """
    Insert a new user (IGNORE if exists) with a unique referral code.
    Returns True if the user was newly created.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ref_code = f"REF{user_id}{uuid.uuid4().hex[:6].upper()}"
    is_admin = 1 if user_id == ADMIN_ID else 0
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO users
                   (user_id, username, first_name, registered_at, referral_code,
                    referred_by, points, channel_joined, is_admin)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                (user_id, username, first_name, now, ref_code, referred_by, is_admin),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def update_user(user_id, **fields):
    """Generic column updater for the users table."""
    if not fields:
        return
    with _db_lock:
        conn = get_conn()
        try:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?",
                         (*fields.values(), user_id))
            conn.commit()
        finally:
            conn.close()


def try_deduct_point(user_id):
    """
    Atomically deduct SPIN_COST points, only if balance is sufficient.
    Returns True on success. Used so deduction only happens on successful spin.
    """
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "UPDATE users SET points = points - ? WHERE user_id = ? AND points >= ?",
                (SPIN_COST, user_id, SPIN_COST),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def add_points(user_id, amount):
    """Add points to a user balance (admin action)."""
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?",
                         (amount, user_id))
            conn.commit()
        finally:
            conn.close()


def remove_points(user_id, amount):
    """Remove points from a user balance (admin action, floors at 0)."""
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE users SET points = MAX(points - ?, 0) WHERE user_id = ?",
                         (amount, user_id))
            conn.commit()
        finally:
            conn.close()


# ─────────────────────────── referrals ───────────────────────────

def award_referral(referrer_id, referred_id):
    """
    Award REFERRAL_POINTS to the referrer immediately (one referral per
    referred user - enforced by the UNIQUE constraint on referred_id).
    Returns True when awarded, False if duplicate / referrer missing.
    """
    with _db_lock:
        conn = get_conn()
        try:
            ref = conn.execute("SELECT user_id FROM users WHERE user_id = ?",
                               (referrer_id,)).fetchone()
            if not ref:
                return False
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                """INSERT INTO referrals (referrer_id, referred_id, timestamp, points_awarded)
                   VALUES (?, ?, ?, ?)""",
                (referrer_id, referred_id, now, REFERRAL_POINTS),
            )
            if cur.rowcount > 0:
                conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?",
                             (REFERRAL_POINTS, referrer_id))
                conn.commit()
                return True
            return False
        except sqlite3.IntegrityError:
            return False  # referred_id already exists -> duplicate referral
        finally:
            conn.close()


def get_referral_count(user_id):
    """Number of successful (awarded) referrals for a user."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND points_awarded > 0",
                (user_id,),
            ).fetchone()
            return row[0]
        finally:
            conn.close()


def get_referrals_list(user_id, limit=5):
    """Last N referred users (user_id, name, timestamp) for display."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT r.referred_id, u.first_name, r.timestamp
                   FROM referrals r LEFT JOIN users u ON u.user_id = r.referred_id
                   WHERE r.referrer_id = ? ORDER BY r.id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def award_pending_referrals_safety():
    """
    Safety net: any referral row still marked 0 (pending) gets awarded.
    Normally points are awarded instantly on join, this only handles legacy rows.
    """
    with _db_lock:
        conn = get_conn()
        try:
            pending = conn.execute(
                "SELECT id, referrer_id FROM referrals WHERE points_awarded = 0"
            ).fetchall()
            for row in pending:
                ref = conn.execute("SELECT user_id FROM users WHERE user_id = ?",
                                   (row["referrer_id"],)).fetchone()
                if ref:
                    conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?",
                                 (REFERRAL_POINTS, row["referrer_id"]))
                conn.execute("UPDATE referrals SET points_awarded = ? WHERE id = ?",
                             (REFERRAL_POINTS, row["id"]))
            conn.commit()
        finally:
            conn.close()


# ─────────────────────────── spin history ───────────────────────────

def record_spin(user_id, phone, reward):
    """Insert one row into spin_history and stamp last_spin on the user."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO spin_history (user_id, phone, reward, spin_time) VALUES (?, ?, ?, ?)",
                (user_id, phone, reward, now),
            )
            conn.execute("UPDATE users SET last_spin = ? WHERE user_id = ?", (now, user_id))
            conn.commit()
        finally:
            conn.close()


def get_spin_history(user_id, limit=10):
    """Most recent spins of a user."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT phone, reward, spin_time FROM spin_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_spin_count(user_id):
    """Total number of spins done by a user."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM spin_history WHERE user_id = ?",
                               (user_id,)).fetchone()
            return row[0]
        finally:
            conn.close()


# ─────────────────────────── admin queries ───────────────────────────

def get_all_user_ids():
    """All user ids (used by broadcast)."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            return [r["user_id"] for r in rows]
        finally:
            conn.close()


def get_all_users():
    """All users sorted by registration date (newest first)."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY registered_at DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_stats():
    """Aggregate numbers shown in the admin STATS menu."""
    with _db_lock:
        conn = get_conn()
        try:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            joined_today = conn.execute(
                "SELECT COUNT(*) FROM users WHERE date(registered_at) = date('now')"
            ).fetchone()[0]
            channel_joined = conn.execute(
                "SELECT COUNT(*) FROM users WHERE channel_joined = 1"
            ).fetchone()[0]
            banned = conn.execute("SELECT COUNT(*) FROM users WHERE banned = 1").fetchone()[0]
            total_referrals = conn.execute(
                "SELECT COUNT(*) FROM referrals WHERE points_awarded > 0"
            ).fetchone()[0]
            total_spins = conn.execute("SELECT COUNT(*) FROM spin_history").fetchone()[0]
            spins_today = conn.execute(
                "SELECT COUNT(*) FROM spin_history WHERE date(spin_time) = date('now')"
            ).fetchone()[0]
            points_awarded = conn.execute(
                "SELECT COALESCE(SUM(points_awarded), 0) FROM referrals"
            ).fetchone()[0]
            points_spent = conn.execute("SELECT COUNT(*) FROM spin_history").fetchone()[0]
            points_in_wallets = conn.execute("SELECT COALESCE(SUM(points), 0) FROM users").fetchone()[0]
            return {
                "total_users": total_users,
                "joined_today": joined_today,
                "channel_joined": channel_joined,
                "banned": banned,
                "total_referrals": total_referrals,
                "total_spins": total_spins,
                "spins_today": spins_today,
                "points_awarded": points_awarded,
                "points_spent": points_spent,
                "points_in_wallets": points_in_wallets,
            }
        finally:
            conn.close()


def get_analytics():
    """Detailed breakdown shown in the admin ANALYTICS menu."""
    stats = get_stats()
    with _db_lock:
        conn = get_conn()
        try:
            spinners = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM spin_history"
            ).fetchone()[0]
            top = conn.execute(
                """SELECT referrer_id, COUNT(*) AS cnt FROM referrals
                   WHERE points_awarded > 0
                   GROUP BY referrer_id ORDER BY cnt DESC LIMIT 5"""
            ).fetchall()
            top_list = []
            for row in top:
                u = conn.execute("SELECT first_name, username FROM users WHERE user_id = ?",
                                 (row["referrer_id"],)).fetchone()
                name = (u["first_name"] if u and u["first_name"] else "Unknown") if u else "Unknown"
                handle = ("@" + u["username"]) if u and u["username"] else ""
                top_list.append({"id": row["referrer_id"], "name": name, "handle": handle,
                                 "count": row["cnt"]})
            return {**stats, "spinners": spinners, "top_referrers": top_list}
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════════════════
# 5. UJALA HAPPIEST ONAM API INTEGRATION
# ════════════════════════════════════════════════════════════════════════════════

BASE_URL = "https://www.ujalahappiestonam.com/api/users"
MASTER_KEY = "660395654"

# 1x1 pixel dummy JPEG used as the "pack" image (image is not needed by the
# bot flow, but the API multipart form requires a file).
DUMMY_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA"
    "/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEA"
    "AD8AVN//2Q=="
)

api_session = requests.Session()
api_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.ujalahappiestonam.com",
    "Referer": "https://www.ujalahappiestonam.com/",
})

_dummy_image_path = None


def get_dummy_image():
    """Lazily write the dummy JPEG once and return its path."""
    global _dummy_image_path
    if _dummy_image_path is None:
        _dummy_image_path = os.path.join(DATA_DIR, "dummy_pack.jpg")
        if not os.path.exists(_dummy_image_path):
            with open(_dummy_image_path, "wb") as f:
                f.write(base64.b64decode(DUMMY_JPEG_B64))
    return _dummy_image_path


def get_timestamp():
    """Ujala API expects millisecond timestamps."""
    return int(time.time() * 1000)


def generate_signature_data(payload, user_key, data_key):
    """
    Replicates the HMAC based signature from refer.py.
    Output format: base64(ts) . base64(payload) . obfuscated-signature
    """
    payload_str = json.dumps(payload, separators=(",", ":"))
    a = base64.b64encode(payload_str.encode()).decode()
    ts = str(payload["t"])
    u = base64.b64encode(ts.encode()).decode()
    hmac_key = data_key[4:18].encode()
    message = f"{u}.{a}".encode()
    h = hmac.new(hmac_key, message, hashlib.sha256)
    hex_sig = h.hexdigest()
    f = base64.b64encode(hex_sig.encode()).decode()
    m = random.randint(1, 6)
    k = random.randint(2, 8)
    alphabet = string.ascii_letters + string.digits
    h_rand = "".join(random.choice(alphabet) for _ in range(k))
    g = f"{k}{m}{f[0:m]}{h_rand}{f[m:]}"
    return f"{u}.{a}.{g}"


def decrypt_resp(encrypted):
    """Responses are base64 JSON blobs; decode them safely."""
    try:
        return json.loads(base64.b64decode(encrypted).decode()), True
    except Exception:
        return {"error": "decrypt_failed", "raw": str(encrypted)[:200]}, False


def api_create_user():
    """Create a new Ujala session -> (user_key, data_key) or (None, None)."""
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = api_session.post(BASE_URL, json={"masterKey": MASTER_KEY}, timeout=10)
            decoded, ok = decrypt_resp(r.json().get("resp", ""))
            if ok and decoded.get("statusCode") == 200:
                return str(decoded["userKey"]), decoded["dataKey"]
        except Exception as e:
            logger.error("api_create_user attempt %d failed: %s", attempt, e)
        time.sleep(1)
    return None, None


def send_otp(user_key, data_key, name, mobile, code, city="Kerala"):
    """Request an OTP on the given mobile. Returns True/False (with retries)."""
    image_path = get_dummy_image()
    for attempt in range(1, API_RETRIES + 1):
        try:
            t = get_timestamp()
            payload = {
                "name": name, "mobile": mobile, "email": "", "city": city,
                "code": code, "agreed1": "Yes", "agreed2": "Yes",
                "userKey": int(user_key), "t": t,
            }
            data_value = generate_signature_data(payload, user_key, data_key)
            files = {"pack": ("pack.jpg", open(image_path, "rb"), "image/jpeg")}
            form_data = {"t": str(t), "userKey": user_key, "data": data_value}
            r = api_session.post(f"{BASE_URL}/getOTP/{user_key}?t={t}",
                                 data=form_data, files=files, timeout=15)
            files["pack"][1].close()
            decoded, ok = decrypt_resp(r.json().get("resp", ""))
            if ok and decoded.get("statusCode") == 200:
                return True
            logger.warning("send_otp bad response: %s", decoded)
        except Exception as e:
            logger.error("send_otp attempt %d failed: %s", attempt, e)
        time.sleep(1)
    return False


def verify_otp(user_key, data_key, otp):
    """Verify the OTP -> returns auth token or None (with retries)."""
    for attempt in range(1, API_RETRIES + 1):
        try:
            t = get_timestamp()
            payload = {"otp": otp, "userKey": int(user_key), "t": t}
            data_value = generate_signature_data(payload, user_key, data_key)
            u, a, g = data_value.split(".", 2)
            body = (f"userKey={user_key}&data={urllib.parse.quote_plus(u)}."
                    f"{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}")
            r = api_session.post(
                f"{BASE_URL}/verifyOTP/{user_key}?t={t}", data=body,
                headers={"content-type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=10,
            )
            decoded, ok = decrypt_resp(r.json().get("resp", ""))
            if ok and decoded.get("statusCode") == 200:
                return decoded.get("token")
            logger.warning("verify_otp bad response: %s", decoded)
        except Exception as e:
            logger.error("verify_otp attempt %d failed: %s", attempt, e)
        time.sleep(1)
    return None


def spin_wheel(user_key, data_key, token):
    """Spin the wheel -> returns reward string or None (with retries)."""
    for attempt in range(1, API_RETRIES + 1):
        try:
            t = get_timestamp()
            payload = {"userKey": int(user_key), "t": t}
            data_value = generate_signature_data(payload, user_key, data_key)
            u, a, g = data_value.split(".", 2)
            body = (f"userKey={user_key}&data={urllib.parse.quote_plus(u)}."
                    f"{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}")
            headers = {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "authorization": f"Bearer {token}",
            }
            r = api_session.post(f"{BASE_URL}/speenTheWheel/{user_key}?t={t}",
                                 data=body, headers=headers, timeout=10)
            decoded, ok = decrypt_resp(r.json().get("resp", ""))
            if ok and decoded.get("statusCode") == 200:
                return decoded.get("reward", "Unknown")
            logger.warning("spin_wheel bad response: %s", decoded)
        except Exception as e:
            logger.error("spin_wheel attempt %d failed: %s", attempt, e)
        time.sleep(1)
    return None


def claim_reward(user_key, data_key, token):
    """Claim the won reward -> True/False (with retries)."""
    for attempt in range(1, API_RETRIES + 1):
        try:
            t = get_timestamp()
            payload = {"userKey": int(user_key), "t": t}
            data_value = generate_signature_data(payload, user_key, data_key)
            u, a, g = data_value.split(".", 2)
            body = (f"userKey={user_key}&data={urllib.parse.quote_plus(u)}."
                    f"{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}")
            headers = {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "authorization": f"Bearer {token}",
            }
            r = api_session.post(f"{BASE_URL}/claimNow/{user_key}?t={t}",
                                 data=body, headers=headers, timeout=10)
            decoded, ok = decrypt_resp(r.json().get("resp", ""))
            if ok and decoded.get("statusCode") == 200:
                return True
            logger.warning("claim_reward bad response: %s", decoded)
        except Exception as e:
            logger.error("claim_reward attempt %d failed: %s", attempt, e)
        time.sleep(1)
    return False

# ════════════════════════════════════════════════════════════════════════════════
# 6. FORCE CHANNEL JOIN
# ════════════════════════════════════════════════════════════════════════════════

FOOTER = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 Made by viediet"


def check_channel_membership(user_id):
    """Return True if the user is a member of the required channel."""
    try:
        member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        joined = member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error("Channel check failed for %s: %s", user_id, e)
        joined = False
    if joined:
        update_user(user_id, channel_joined=1)
    return joined


def channel_join_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(InlineKeyboardButton(f"📢 JOIN @{CHANNEL_USERNAME}",
                                url=f"https://t.me/{CHANNEL_USERNAME}"))
    kb.row(InlineKeyboardButton("✅ CHECK AGAIN", callback_data="check_channel"))
    return kb


def channel_join_text():
    return (
        f"🔒 <b>CHANNEL REQUIRED</b>\n\n"
        f"⚠️ To use this bot you must join our channel:\n\n"
        f"📢 <b>@{CHANNEL_USERNAME}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Join the channel, then press <b>CHECK AGAIN</b>.\n"
        f"{FOOTER}"
    )


def send_join_required(chat_id):
    """Force channel join prompt."""
    bot.send_message(chat_id, channel_join_text(),
                     reply_markup=channel_join_keyboard(), parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════════════
# 7. UI HELPERS (footers, keyboards, texts)
# ════════════════════════════════════════════════════════════════════════════════

def main_menu_keyboard(user):
    """Main menu buttons; admin gets an extra ADMIN PANEL button."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(InlineKeyboardButton("🎡 SPIN NOW", callback_data="spin_now"))
    kb.row(InlineKeyboardButton("👥 MY REFERRALS", callback_data="my_referrals"),
           InlineKeyboardButton("🔗 REFERRAL LINK", callback_data="referral_link"))
    kb.row(InlineKeyboardButton("📊 MY HISTORY", callback_data="my_history"),
           InlineKeyboardButton("🆘 HELP", callback_data="help"))
    if user and user.get("is_admin"):
        kb.row(InlineKeyboardButton("👑 ADMIN PANEL", callback_data="admin_panel"))
    return kb


def main_menu_text(user, first_name):
    name = escape(first_name or "User")
    points = user["points"] if user else 0
    return (
        f"🎡 <b>VIEDIET REWARD SPIN BOT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, <b>{name}</b>!\n"
        f"💎 Your Points: <b>{points}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎡 Each spin costs <b>1 point</b>\n"
        f"👥 Each referral gives <b>+2 points</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Spin the wheel and win exciting rewards! 🎉"
        f"{FOOTER}"
    )


def show_main_menu(chat_id, message_id=None, edit=True):
    """Show (or edit into) the main menu."""
    user = get_user(chat_id)
    if not user:
        return
    text = main_menu_text(user, user.get("first_name"))
    markup = main_menu_keyboard(user)
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def safe_edit(chat_id, message_id, text, markup=None):
    """Edit a message; fall back to sending a new one on failure."""
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                              reply_markup=markup, parse_mode="HTML",
                              disable_web_page_preview=True)
    except Exception as e:
        if "message is not modified" in str(e):
            return  # identical content, nothing to do
        try:
            bot.send_message(chat_id, text, reply_markup=markup,
                             parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e2:
            logger.error("safe_edit failed: %s", e2)


def mask_phone(phone):
    """Mask a 10 digit number like +91******1234 for privacy."""
    if phone and len(phone) >= 4:
        return f"+91{'*' * (len(phone) - 4)}{phone[-4:]}"
    return phone or "N/A"


def back_markup(callback_data="main_menu"):
    """Simple back-button markup used by several sub menus."""
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data=callback_data))
    return kb


# ════════════════════════════════════════════════════════════════════════════════
# 8. GLOBAL STATE (spin flows, admin flows, concurrency guards)
# ════════════════════════════════════════════════════════════════════════════════

_state_lock = threading.RLock()
spin_sessions = {}       # user_id -> {step, user_key, data_key, phone}
admin_states = {}        # user_id -> {type, target}
broadcast_msgs = {}      # user_id -> text awaiting confirmation
admin_confirm_pts = {}   # user_id -> {action, target, amount}


def clear_state(user_id):
    """Remove every temporary state for a user."""
    with _state_lock:
        spin_sessions.pop(user_id, None)
        admin_states.pop(user_id, None)
        broadcast_msgs.pop(user_id, None)
        admin_confirm_pts.pop(user_id, None)


def is_admin(user):
    """User-level admin check (DB flag or env ADMIN_ID)."""
    return user is not None and (user.get("is_admin") == 1 or user.get("user_id") == ADMIN_ID)


# ════════════════════════════════════════════════════════════════════════════════
# 9. USER SIDE MESSAGES & FLOWS
# ════════════════════════════════════════════════════════════════════════════════

def send_help(chat_id, message_id=None, edit=True):
    """HELP menu: how to earn points, how to spin, reward possibilities."""
    text = (
        f"🆘 <b>HELP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎡 <b>HOW TO SPIN</b>\n"
        f"1️⃣ Press <b>SPIN NOW</b> (costs 1 point)\n"
        f"2️⃣ Enter your 10 digit mobile number\n"
        f"3️⃣ Enter the OTP received via SMS\n"
        f"4️⃣ Watch the wheel spin! 🎉\n\n"
        f"👥 <b>HOW TO EARN POINTS</b>\n"
        f"• Share your referral link with friends\n"
        f"• Each friend who joins gives <b>+2 points</b>\n"
        f"• Unlimited referrals = unlimited points\n\n"
        f"🎁 <b>POSSIBLE REWARDS</b>\n"
        f"• 💰 Cashback rewards\n"
        f"• 🎫 Coupons & vouchers\n"
        f"• 🎁 Mystery prizes\n\n"
        f"💡 One spin = 1 point. No daily limit!\n"
        f"{FOOTER}"
    )
    markup = back_markup()
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def send_referral_link(chat_id, message_id=None, edit=True):
    """Referral link menu with share button + referral stats."""
    user = get_user(chat_id)
    if not user:
        return
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start=ref_{chat_id}"
    count = get_referral_count(chat_id)
    points_earned = count * REFERRAL_POINTS
    refs = get_referrals_list(chat_id)
    ref_text = "\n".join(
        f"• {escape(r['first_name'] or 'User')} — {r['timestamp']}"
        for r in refs
    ) or "• No referrals yet"

    text = (
        f"🔗 <b>YOUR REFERRAL LINK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 Share this link with friends:\n"
        f"<code>{escape(link)}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Successful referrals: <b>{count}</b>\n"
        f"💎 Points earned: <b>{points_earned}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Recent referrals:\n{ref_text}\n\n"
        f"💡 Friend joins = <b>+{REFERRAL_POINTS} points</b> for you!\n"
        f"{FOOTER}"
    )
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(link)}"
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📤 SHARE LINK", url=share_url))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="main_menu"))
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def send_my_referrals(chat_id, message_id=None, edit=True):
    """MY REFERRALS menu: show count and points earned."""
    count = get_referral_count(chat_id)
    points_earned = count * REFERRAL_POINTS
    text = (
        f"👥 <b>MY REFERRALS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Successful referrals: <b>{count}</b>\n"
        f"💎 Points earned: <b>{points_earned}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Invite more friends to earn more!\n"
        f"Each referral = <b>+{REFERRAL_POINTS} points</b>\n\n"
        f"Get your link from the menu 👉 🔗 REFERRAL LINK\n"
        f"{FOOTER}"
    )
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔗 GET MY LINK", callback_data="referral_link"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="main_menu"))
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def send_history(chat_id, message_id=None, edit=True):
    """MY HISTORY menu: list spins with rewards."""
    history = get_spin_history(chat_id)
    if not history:
        body = (
            f"📊 <b>MY HISTORY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ No spins yet!\n\n"
            f"Press 🎡 SPIN NOW to try your luck!\n"
            f"{FOOTER}"
        )
    else:
        lines = "\n".join(
            f"• {mask_phone(h['phone'])} — 🎁 {escape(h['reward'])}\n   ⏰ {h['spin_time']}"
            for h in history
        )
        body = (
            f"📊 <b>MY HISTORY</b> (last {len(history)})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Total spins: <b>{get_spin_count(chat_id)}</b>\n"
            f"{FOOTER}"
        )
    markup = back_markup()
    if edit and message_id:
        safe_edit(chat_id, message_id, body, markup)
    else:
        bot.send_message(chat_id, body, reply_markup=markup, parse_mode="HTML")


# ─────────────────────────── SPIN FLOW ───────────────────────────

def start_spin(chat_id, message_id=None):
    """Entry point of the spin flow (called from the SPIN NOW button)."""
    user = get_user(chat_id)
    if not user:
        return
    if user.get("banned"):
        bot.send_message(chat_id, "⛔ You are banned from using this bot.")
        return
    with _state_lock:
        if chat_id in spin_sessions:
            bot.send_message(chat_id,
                             "⚠️ A spin is already in progress.\n"
                             "Finish it or send /cancel to abort.")
            return
        if (user.get("points") or 0) < SPIN_COST:
            kb = InlineKeyboardMarkup()
            kb.row(InlineKeyboardButton("🔗 GET REFERRAL LINK", callback_data="referral_link"))
            bot.send_message(
                chat_id,
                f"❌ You need at least <b>{SPIN_COST} point</b> to spin!\n\n"
                f"💡 Invite friends using your referral link to earn "
                f"<b>+{REFERRAL_POINTS} points</b> each!\n"
                f"{FOOTER}",
                reply_markup=kb,
            )
            return
        spin_sessions[chat_id] = {"step": "phone", "_ts": time.time()}
    text = (
        f"🎡 <b>SPIN THE WHEEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Please send your <b>10 digit mobile number</b>.\n"
        f"(No country code needed)\n\n"
        f"Example: <code>9876543210</code>\n\n"
        f"❌ Send /cancel to abort.\n"
        f"{FOOTER}"
    )
    if message_id:
        safe_edit(chat_id, message_id, text, None)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML")


def handle_spin_phone(chat_id, text):
    """Validate the phone number and request an OTP from the API."""
    if not (text.isdigit() and len(text) == 10):
        bot.send_message(chat_id,
                         "❌ Invalid number. Please send exactly <b>10 digits</b>.\n"
                         "Send /cancel to abort.")
        return
    user = get_user(chat_id)
    if not user:
        return
    bot.send_message(chat_id, "⏳ Contacting Ujala server...")
    user_key, data_key = api_create_user()
    if not user_key or not data_key:
        with _state_lock:
            spin_sessions.pop(chat_id, None)
        bot.send_message(chat_id,
                         "❌ Server error: could not create session.\n"
                         "Please try again later.\n"
                         f"{FOOTER}")
        return
    name = (user.get("first_name") or "User")[:30]
    if not send_otp(user_key, data_key, name, text, PRODUCT_CODE):
        with _state_lock:
            spin_sessions.pop(chat_id, None)
        bot.send_message(chat_id,
                         "❌ Could not send OTP. This number may already be "
                         "registered, or the server is busy.\n"
                         "Please try again later.\n"
                         f"{FOOTER}")
        return
    with _state_lock:
        spin_sessions[chat_id] = {
            "step": "otp",
            "user_key": user_key,
            "data_key": data_key,
            "phone": text,
            "_ts": time.time(),
        }
    bot.send_message(
        chat_id,
        f"✅ OTP sent to <b>{mask_phone(text)}</b>!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 Please enter the <b>6 digit OTP</b> you received.\n\n"
        f"❌ Send /cancel to abort.\n"
        f"{FOOTER}",
    )


def handle_spin_otp(chat_id, text):
    """Verify OTP, spin the wheel, claim the reward, deduct 1 point."""
    with _state_lock:
        state = spin_sessions.get(chat_id)
    if not state:
        return
    if not (text.isdigit() and len(text) == 6):
        bot.send_message(chat_id,
                         "❌ Invalid OTP format. Please send the <b>6 digit</b> code.\n"
                         "Send /cancel to abort.")
        return
    user_key, data_key, phone = state["user_key"], state["data_key"], state["phone"]
    bot.send_message(chat_id, "🔑 Verifying OTP...")
    token = verify_otp(user_key, data_key, text)
    if not token:
        # Keep the state so the user can retry the correct OTP
        bot.send_message(chat_id,
                         "❌ Invalid OTP or verification failed.\n"
                         "Please check the code and try again.\n"
                         "Send /cancel to abort.")
        return
    bot.send_message(chat_id, "🎡 Spinning the wheel...")
    reward = spin_wheel(user_key, data_key, token)
    if not reward:
        with _state_lock:
            spin_sessions.pop(chat_id, None)
        bot.send_message(chat_id,
                         "❌ Spin failed (server error). Your points are safe.\n"
                         "Please try again later.\n"
                         f"{FOOTER}")
        return
    bot.send_message(chat_id, "💰 Claiming your reward...")
    claimed = claim_reward(user_key, data_key, token)
    if not claimed:
        with _state_lock:
            spin_sessions.pop(chat_id, None)
        bot.send_message(chat_id,
                         f"⚠️ You won <b>{escape(reward)}</b> but the claim "
                         f"request failed.\nPlease contact support or try "
                         f"again later.\nYour points were NOT deducted.\n"
                         f"{FOOTER}")
        return
    # Success: deduct exactly one point (only on success) and record history
    if not try_deduct_point(chat_id):
        with _state_lock:
            spin_sessions.pop(chat_id, None)
        bot.send_message(chat_id,
                         f"🎉 You won <b>{escape(reward)}</b>! But your point "
                         f"balance was already too low - contact admin.\n"
                         f"{FOOTER}")
        return
    record_spin(chat_id, phone, reward)
    with _state_lock:
        spin_sessions.pop(chat_id, None)
    user = get_user(chat_id)
    points_left = user["points"] if user else 0
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🎡 SPIN AGAIN", callback_data="spin_now"))
    kb.row(InlineKeyboardButton("🔙 MAIN MENU", callback_data="main_menu"))
    bot.send_message(
        chat_id,
        f"🎉 <b>CONGRATULATIONS!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 You won: <b>{escape(reward)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Points used: <b>{SPIN_COST}</b>\n"
        f"💎 Points left: <b>{points_left}</b>\n\n"
        f"Spin again or refer friends for more points!\n"
        f"{FOOTER}",
        reply_markup=kb,
    )

# ════════════════════════════════════════════════════════════════════════════════
# 10. ADMIN PANEL
# ════════════════════════════════════════════════════════════════════════════════

def admin_menu_text():
    return (
        f"👑 <b>ADMIN PANEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Stats — bot statistics\n"
        f"👥 Users — manage users\n"
        f"📢 Broadcast — send message to all\n"
        f"📈 Analytics — detailed breakdown\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Use the buttons below.\n"
        f"{FOOTER}"
    )


def admin_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(InlineKeyboardButton("📊 STATS", callback_data="admin_stats"),
           InlineKeyboardButton("👥 USERS", callback_data="admin_users"))
    kb.row(InlineKeyboardButton("📢 BROADCAST", callback_data="admin_broadcast"),
           InlineKeyboardButton("📈 ANALYTICS", callback_data="admin_analytics"))
    kb.row(InlineKeyboardButton("🔙 USER MENU", callback_data="main_menu"))
    return kb


def send_admin_menu(chat_id, message_id=None, edit=True):
    if edit and message_id:
        safe_edit(chat_id, message_id, admin_menu_text(), admin_menu_keyboard())
    else:
        bot.send_message(chat_id, admin_menu_text(),
                         reply_markup=admin_menu_keyboard(), parse_mode="HTML")


def send_admin_stats(chat_id, message_id=None, edit=True):
    s = get_stats()
    text = (
        f"📊 <b>STATS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total users: <b>{s['total_users']}</b>\n"
        f"🆕 Joined today: <b>{s['joined_today']}</b>\n"
        f"📢 Channel joined: <b>{s['channel_joined']}</b>\n"
        f"⛔ Banned: <b>{s['banned']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 Total referrals: <b>{s['total_referrals']}</b>\n"
        f"🎡 Total spins: <b>{s['total_spins']}</b>\n"
        f"🎡 Spins today: <b>{s['spins_today']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Points given (referrals): <b>{s['points_awarded']}</b>\n"
        f"🎰 Points spent (spins): <b>{s['points_spent']}</b>\n"
        f"👛 Points in wallets: <b>{s['points_in_wallets']}</b>\n"
        f"{FOOTER}"
    )
    markup = back_markup("admin_panel")
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def send_admin_analytics(chat_id, message_id=None, edit=True):
    a = get_analytics()
    top = a["top_referrers"]
    if top:
        top_text = "\n".join(
            f"🥇 {i + 1}. {escape(t['name'])} {escape(t['handle'])} — "
            f"<b>{t['count']}</b> refs"
            for i, t in enumerate(top)
        )
    else:
        top_text = "• No referrals yet"
    avg = round(a["total_referrals"] / a["total_users"], 2) if a["total_users"] else 0
    text = (
        f"📈 <b>ANALYTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total users: <b>{a['total_users']}</b>\n"
        f"🔗 Total referrals: <b>{a['total_referrals']}</b>\n"
        f"📊 Avg referrals / user: <b>{avg}</b>\n"
        f"🎡 Total spins: <b>{a['total_spins']}</b>\n"
        f"🪙 Active spinners: <b>{a['spinners']}</b>\n"
        f"💎 Points awarded: <b>{a['points_awarded']}</b>\n"
        f"🎰 Points spent: <b>{a['points_spent']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>TOP REFERRERS</b>\n"
        f"{top_text}\n"
        f"{FOOTER}"
    )
    markup = back_markup("admin_panel")
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def send_admin_users(chat_id, message_id=None, page=0, edit=True):
    users = get_all_users()
    total_pages = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(users))
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users[start:end]:
        name = escape((u.get("first_name") or u.get("username") or f"User_{u['user_id']}")[:24])
        badge = " ⛔" if u.get("banned") else ""
        kb.row(InlineKeyboardButton(f"👤 {name}{badge}",
                                    callback_data=f"admin_view_user_{u['user_id']}"))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"admin_users_page_{page - 1}"))
    if end < len(users):
        nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"admin_users_page_{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton("🔙 ADMIN MENU", callback_data="admin_panel"))
    text = (
        f"👥 <b>USERS</b> — page {page + 1}/{total_pages}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Tap a user for details.\n"
        f"{FOOTER}"
    )
    if edit and message_id:
        safe_edit(chat_id, message_id, text, kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def send_admin_user_detail(chat_id, message_id, target_id):
    u = get_user(target_id)
    if not u:
        safe_edit(chat_id, message_id, "❌ User not found.", None)
        return
    ref_count = get_referral_count(target_id)
    spin_count = get_spin_count(target_id)
    ref_name = "—"
    if u.get("referred_by"):
        r = get_user(u["referred_by"])
        if r:
            ref_name = escape(r.get("first_name") or f"User_{r['user_id']}")
    text = (
        f"👤 <b>USER DETAIL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Name: <b>{escape(u.get('first_name') or 'N/A')}</b>\n"
        f"📛 Username: @{escape(u.get('username') or 'N/A')}\n"
        f"📅 Joined: {u.get('registered_at') or 'N/A'}\n"
        f"👥 Referred by: {ref_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 Points: <b>{u.get('points', 0)}</b>\n"
        f"🔗 Referrals: <b>{ref_count}</b>\n"
        f"🎡 Spins: <b>{spin_count}</b>\n"
        f"🎰 Last spin: {u.get('last_spin') or 'Never'}\n"
        f"⛔ Banned: {'YES' if u.get('banned') else 'No'}\n"
        f"{FOOTER}"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(InlineKeyboardButton("➕ ADD POINTS", callback_data=f"admin_add_pts_{target_id}"),
           InlineKeyboardButton("➖ REMOVE POINTS", callback_data=f"admin_rem_pts_{target_id}"))
    kb.row(InlineKeyboardButton("🎡 SPIN HISTORY", callback_data=f"admin_spins_{target_id}"),
           InlineKeyboardButton("⛔ BAN/UNBAN", callback_data=f"admin_ban_{target_id}"))
    kb.row(InlineKeyboardButton("🔙 USERS", callback_data="admin_users"))
    safe_edit(chat_id, message_id, text, kb)


def send_admin_user_spins(chat_id, message_id, target_id):
    history = get_spin_history(target_id)
    if not history:
        body = (f"🎡 <b>SPIN HISTORY</b> for <code>{target_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ No spins yet.\n"
                f"{FOOTER}")
    else:
        lines = "\n".join(
            f"• {mask_phone(h['phone'])} — 🎁 {escape(h['reward'])} ⏰ {h['spin_time']}"
            for h in history
        )
        body = (
            f"🎡 <b>SPIN HISTORY</b> for <code>{target_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{lines}\n"
            f"{FOOTER}"
        )
    kb = back_markup(f"admin_view_user_{target_id}")
    safe_edit(chat_id, message_id, body, kb)


def broadcast_to_all(text):
    """Send a message to every user in a background thread."""
    user_ids = get_all_user_ids()
    ok = fail = 0
    for uid in user_ids:
        try:
            bot.send_message(uid, text, parse_mode="HTML")
            ok += 1
        except Exception:
            try:
                bot.send_message(uid, text)  # plain fallback (bad HTML etc.)
                ok += 1
            except Exception:
                fail += 1
        time.sleep(0.05)  # avoid hitting Telegram rate limits
    logger.info("Broadcast done: %d delivered, %d failed", ok, fail)
    try:
        bot.send_message(
            ADMIN_ID,
            f"📢 <b>BROADCAST FINISHED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Delivered: <b>{ok}</b>\n"
            f"❌ Failed: <b>{fail}</b>\n"
            f"👥 Total targets: <b>{len(user_ids)}</b>\n"
            f"{FOOTER}",
        )
    except Exception as e:
        logger.error("Broadcast summary send failed: %s", e)


# ════════════════════════════════════════════════════════════════════════════════
# 11. COMMAND HANDLERS
# ════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "User"

    # Parse deep-link referral: /start ref_123456
    referred_by = None
    if message.text and "ref_" in message.text:
        try:
            referred_by = int(message.text.split("ref_")[1].split()[0])
        except Exception:
            referred_by = None

    if not get_user(user_id):
        is_new = create_user(user_id, username, first_name, referred_by)
        # Award the referrer immediately (points only on first join)
        if is_new and referred_by and referred_by != user_id:
            if award_referral(referred_by, user_id):
                ref_user = get_user(referred_by)
                ref_name = escape(ref_user.get("first_name") or "friend") if ref_user else "friend"
                bot.send_message(
                    referred_by,
                    f"🎉 <b>New referral!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 {ref_name} joined using your link!\n"
                    f"💎 <b>+{REFERRAL_POINTS} points</b> added to your balance!\n"
                    f"{FOOTER}",
                )

    user = get_user(user_id)
    if not user:
        return

    if user.get("banned"):
        bot.send_message(user_id,
                         f"⛔ <b>You are banned</b> from this bot.\n"
                         f"{FOOTER}")
        return

    if not check_channel_membership(user_id):
        send_join_required(user_id)
        return

    show_main_menu(user_id, edit=False)


@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if user_id != ADMIN_ID and not (user and user.get("is_admin")):
        bot.reply_to(message, "❌ Access denied. You are not an admin.")
        return
    send_admin_menu(user_id, edit=False)


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    user_id = message.from_user.id
    clear_state(user_id)
    bot.reply_to(message,
                 f"❌ Action cancelled.\n"
                 f"Use the menu buttons to continue.\n"
                 f"{FOOTER}")


@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    """Route plain text: spin flow -> admin flows -> fallback."""
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if text.startswith("/"):
        return  # unknown command, ignore

    user = get_user(user_id)
    if not user:
        bot.reply_to(message, "Press /start to begin.")
        return
    if user.get("banned"):
        bot.reply_to(message, "⛔ You are banned from this bot.")
        return

    # ---- 1. Active spin flow ----
    with _state_lock:
        spin_state = spin_sessions.get(user_id)
    if spin_state:
        step = spin_state.get("step")
        if step == "phone":
            handle_spin_phone(user_id, text)
            return
        if step == "otp":
            handle_spin_otp(user_id, text)
            return

    # ---- 2. Admin flows ----
    if is_admin(user):
        with _state_lock:
            admin_state = admin_states.get(user_id)
        if admin_state:
            kind = admin_state.get("type")

            if kind == "broadcast":
                with _state_lock:
                    admin_states.pop(user_id, None)
                    broadcast_msgs[user_id] = text
                preview = text[:300] + ("..." if len(text) > 300 else "")
                total = len(get_all_user_ids())
                kb = InlineKeyboardMarkup(row_width=2)
                kb.row(InlineKeyboardButton("✅ SEND", callback_data="admin_broadcast_confirm"))
                kb.row(InlineKeyboardButton("❌ CANCEL", callback_data="admin_broadcast_cancel"))
                bot.send_message(
                    user_id,
                    f"📢 <b>BROADCAST CONFIRMATION</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👥 Will be sent to: <b>{total}</b> users\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{escape(preview)}\n"
                    f"{FOOTER}",
                    reply_markup=kb,
                )
                return

            if kind in ("add_points", "remove_points"):
                target_id = admin_state.get("target")
                with _state_lock:
                    admin_states.pop(user_id, None)
                try:
                    amount = int(text)
                    if amount <= 0:
                        raise ValueError
                except ValueError:
                    bot.send_message(user_id,
                                     "❌ Please send a valid positive number.\n"
                                     "Use /cancel to abort.")
                    return
                if target_id is None:
                    return
                action = "add" if kind == "add_points" else "rem"
                with _state_lock:
                    admin_confirm_pts[user_id] = {"action": action, "target": target_id,
                                                  "amount": amount}
                kb = InlineKeyboardMarkup(row_width=2)
                kb.row(InlineKeyboardButton("✅ CONFIRM",
                                            callback_data=f"admin_pts_confirm_{action}_{target_id}_{amount}"))
                kb.row(InlineKeyboardButton("❌ CANCEL", callback_data="admin_pts_cancel"))
                bot.send_message(
                    user_id,
                    f"{'➕' if action == 'add' else '➖'} Confirm "
                    f"{'adding' if action == 'add' else 'removing'} "
                    f"<b>{amount}</b> points {'to' if action == 'add' else 'from'} "
                    f"user <code>{target_id}</code>?",
                    reply_markup=kb,
                )
                return

    # ---- 3. Fallback ----
    bot.reply_to(message,
                 f"❓ Please use the menu buttons below.\n"
                 f"Press /start to open the main menu.\n"
                 f"{FOOTER}")


# ════════════════════════════════════════════════════════════════════════════════
# 12. CALLBACK QUERY HANDLER (all buttons)
# ════════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    data = call.data or ""
    chat_id = call.message.chat.id if call.message else user_id
    message_id = call.message.message_id if call.message else None
    user = get_user(user_id)

    if not user:
        bot.answer_callback_query(call.id, "Please press /start first.")
        return
    if user.get("banned"):
        bot.answer_callback_query(call.id, "⛔ You are banned from this bot.", show_alert=True)
        return

    # ─────────── FORCE CHANNEL CHECK ───────────
    if data == "check_channel":
        if check_channel_membership(user_id):
            bot.answer_callback_query(call.id, "✅ Channel joined! Welcome!")
            show_main_menu(chat_id, message_id, edit=True)
        else:
            bot.answer_callback_query(call.id,
                                      "❌ You have not joined the channel yet. "
                                      "Tap JOIN first!", show_alert=True)
        return

    # ─────────── USER MENUS ───────────
    if data == "main_menu":
        show_main_menu(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "spin_now":
        start_spin(chat_id, message_id)
        bot.answer_callback_query(call.id)
        return

    if data == "my_referrals":
        send_my_referrals(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "referral_link":
        send_referral_link(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "my_history":
        send_history(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "help":
        send_help(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    # ─────────── ADMIN ROUTING ───────────
    if data.startswith("admin"):
        if not is_admin(user):
            bot.answer_callback_query(call.id, "❌ Unauthorized.", show_alert=True)
            return
        handle_admin_callback(call, chat_id, message_id, data)

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass  # already answered inside an admin path - safe to ignore


def handle_admin_callback(call, chat_id, message_id, data):
    """Route all admin panel button presses."""
    user_id = call.from_user.id

    if data == "admin_panel":
        send_admin_menu(chat_id, message_id, edit=True)
        return

    if data == "admin_stats":
        send_admin_stats(chat_id, message_id, edit=True)
        return

    if data == "admin_analytics":
        send_admin_analytics(chat_id, message_id, edit=True)
        return

    if data == "admin_users":
        send_admin_users(chat_id, message_id, page=0, edit=True)
        return

    if data.startswith("admin_users_page_"):
        try:
            page = int(data.split("_")[-1])
        except ValueError:
            page = 0
        send_admin_users(chat_id, message_id, page=page, edit=True)
        return

    if data.startswith("admin_view_user_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            target = 0
        send_admin_user_detail(chat_id, message_id, target)
        return

    if data.startswith("admin_spins_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            target = 0
        send_admin_user_spins(chat_id, message_id, target)
        return

    # ─────────── Add / remove points (amount entry) ───────────
    if data.startswith("admin_add_pts_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            return
        with _state_lock:
            admin_states[user_id] = {"type": "add_points", "target": target}
        bot.send_message(chat_id,
                         f"➕ Enter the <b>amount of points</b> to add to "
                         f"user <code>{target}</code>:\n"
                         f"(positive integer, /cancel to abort)")
        return

    if data.startswith("admin_rem_pts_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            return
        with _state_lock:
            admin_states[user_id] = {"type": "remove_points", "target": target}
        bot.send_message(chat_id,
                         f"➖ Enter the <b>amount of points</b> to remove from "
                         f"user <code>{target}</code>:\n"
                         f"(positive integer, /cancel to abort)")
        return

    # ─────────── Point change confirmations ───────────
    if data.startswith("admin_pts_confirm_add_") or data.startswith("admin_pts_confirm_rem_"):
        parts = data.split("_")
        action = parts[3]                       # "add" or "rem"
        target = int(parts[4]) if len(parts) > 4 else 0
        amount = int(parts[5]) if len(parts) > 5 else 0
        if action == "add":
            add_points(target, amount)
            note = f"➕ Added <b>{amount}</b> points to user <code>{target}</code>!"
        else:
            remove_points(target, amount)
            note = f"➖ Removed <b>{amount}</b> points from user <code>{target}</code>!"
        with _state_lock:
            admin_confirm_pts.pop(user_id, None)
        safe_edit(chat_id, message_id, note + f"\n{FOOTER}", back_markup(f"admin_view_user_{target}"))
        bot.answer_callback_query(call.id, "✅ Done!", show_alert=False)
        return

    if data == "admin_pts_cancel":
        with _state_lock:
            admin_confirm_pts.pop(user_id, None)
        bot.answer_callback_query(call.id, "❌ Cancelled.")
        return

    # ─────────── Ban / unban toggle ───────────
    if data.startswith("admin_ban_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            return
        u = get_user(target)
        if not u:
            bot.answer_callback_query(call.id, "❌ User not found.", show_alert=True)
            return
        new_state = 0 if u.get("banned") else 1
        update_user(target, banned=new_state)
        state_word = "banned" if new_state else "unbanned"
        bot.answer_callback_query(call.id,
                                  f"✅ User {target} {state_word}.",
                                  show_alert=True)
        send_admin_user_detail(chat_id, message_id, target)
        return

    # ─────────── Broadcast ───────────
    if data == "admin_broadcast":
        with _state_lock:
            admin_states[user_id] = {"type": "broadcast"}
        bot.send_message(chat_id,
                         f"📢 Send the <b>message</b> you want to broadcast "
                         f"to all users:\n"
                         f"(supports HTML, /cancel to abort)\n"
                         f"{FOOTER}")
        return

    if data == "admin_broadcast_confirm":
        with _state_lock:
            msg = broadcast_msgs.pop(user_id, None)
            admin_states.pop(user_id, None)
        if not msg:
            bot.answer_callback_query(call.id, "❌ Nothing to send.", show_alert=True)
            return
        safe_edit(chat_id, message_id,
                  "📢 <b>Broadcasting...</b>\n\nThis may take a moment.",
                  None)
        bot.answer_callback_query(call.id, "📢 Broadcasting started!")
        threading.Thread(target=broadcast_to_all, args=(msg,), daemon=True).start()
        return

    if data == "admin_broadcast_cancel":
        with _state_lock:
            broadcast_msgs.pop(user_id, None)
            admin_states.pop(user_id, None)
        bot.answer_callback_query(call.id, "❌ Broadcast cancelled.")
        return


# ════════════════════════════════════════════════════════════════════════════════
# 13. SCHEDULED TASKS (background thread)
# ════════════════════════════════════════════════════════════════════════════════

def scheduler_loop():
    """Runs every 60s: referral award safety net + stale state cleanup."""
    while True:
        time.sleep(60)
        try:
            award_pending_referrals_safety()
        except Exception as e:
            logger.error("Scheduler referral task failed: %s", e)
        try:
            # Clean stale spin sessions older than 10 minutes
            cutoff = time.time() - 600
            with _state_lock:
                for uid in [u for u in spin_sessions]:
                    if spin_sessions[uid].get("_ts", time.time()) < cutoff:
                        spin_sessions.pop(uid, None)
        except Exception as e:
            logger.error("Scheduler state cleanup failed: %s", e)


# ════════════════════════════════════════════════════════════════════════════════
# 14. MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(" 🎡 VIEDIET REWARD SPIN BOT")
    print("    Made by viediet")
    print("=" * 60)

    init_db()

    # Start the background scheduler
    threading.Thread(target=scheduler_loop, daemon=True).start()

    # Start polling (auto-restarts on network errors)
    logger.info("Bot started polling...")
    try:
        bot.infinity_polling(long_polling_timeout=20, skip_pending=True)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user. Shutting down gracefully...")
    except Exception as e:
        logger.error("Fatal polling error: %s", e)
    finally:
        try:
            bot.stop_polling()
        except Exception:
            pass
    print("👋 Bot stopped. Goodbye!")


if __name__ == "__main__":
    main()

