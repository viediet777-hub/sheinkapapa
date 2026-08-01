#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SWIGGY BUZZ AUTO-COLLECTOR BOT - COMPLETE FIXED
================================================
Fixes included:
  1. Joining bonus (₹5) auto-claimed on first-time friend connect
  2. Invalid / already-claimed campaigns skipped gracefully
  3. Faster collection (lower delay, no retries, skip dead links)
  4. Account limit: max 2 accounts per user
  5. Referral system: +1 pt per refer, +2 pts new-user signup bonus
  6. Both-sides collection (open + buzz back) on every link
  7. DB columns: referral_points, referred_by, referred_at (+migrations)
  8. parse_total_earned / joining bonus parsing from API responses
  9. Robust error handling - single failure never stops the run
 10. /refer command with referral link + points
 11. Account limit check before adding a new account

Made by @viediet
"""

import asyncio
import base64
import html
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
from telegram.error import BadRequest
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
    print("❌ BOT_TOKEN environment variable required!")
    os._exit(1)

ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS", "")
if admin_ids_str:
    for x in admin_ids_str.split(","):
        try:
            ADMIN_IDS.append(int(x.strip()))
        except Exception:
            pass
if not ADMIN_IDS:
    ADMIN_IDS = [1364476174]

DB_PATH = os.getenv("DB_PATH", "swiggy_buzz.db")
MAX_EARN_PER_ACCOUNT = float(os.getenv("MAX_EARN_PER_ACCOUNT", "1000"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.15"))
MAX_ACCOUNTS = int(os.getenv("MAX_ACCOUNTS", "2"))
REFERRAL_SIGNUP_POINTS = int(os.getenv("REFERRAL_SIGNUP_POINTS", "2"))
REFERRAL_POINTS_PER_REF = int(os.getenv("REFERRAL_POINTS_PER_REF", "1"))
JOINING_BONUS_ENABLED = os.getenv("JOINING_BONUS_ENABLED", "true").lower() != "false"
BRAND = "⚡ Made by Viediet"

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

OTP_URL = "https://profile.swiggy.com/api/v3/app/sms_otp"
VERIFY_URL = "https://profile.swiggy.com/api/v3/app/login/verify"
REWARDS_URL = "https://spns.swiggy.com/api/v1/campaign/rewards"
CAMPAIGN_ACTION_URL = "https://spns.swiggy.com/api/v1/campaign/action"

SPNS_HEADERS = {
    "client-id": "portal",
    "user-agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4 Build/RD2A.211001.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.120 Mobile Safari/537.36",
    "content-type": "application/json",
    "accept": "*/*",
    "origin": "https://webviews.swiggy.com",
    "x-requested-with": "in.swiggy.android",
    "referer": "https://webviews.swiggy.com/moments-iw/buzz-your-friend/?source=banner&campaignId=ougwl&is_promoted=true",
    "accept-encoding": "gzip, deflate",
    "accept-language": "en-IN,en-US;q=0.9,en;q=0.8",
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}

# ============================== DATABASE ==============================


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


IST = timezone(timedelta(hours=5, minutes=30))


def today_ist():
    return datetime.now(IST).strftime("%Y-%m-%d")


def yesterday_ist():
    return (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")


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
                    created_at TEXT,
                    last_collection_date TEXT DEFAULT '',
                    streak_days INTEGER DEFAULT 0,
                    daily_collected INTEGER DEFAULT 0,
                    referral_points INTEGER DEFAULT 0,
                    referred_by INTEGER,
                    referred_at TEXT
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
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER NOT NULL,
                    points INTEGER DEFAULT 1,
                    created_at TEXT
                );
                """
            )
            self._conn.commit()
            self._migrate_accounts()

    def _migrate_accounts(self):
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(accounts)").fetchall()]
        for col, ddl in (
            ("last_collection_date", "TEXT DEFAULT ''"),
            ("streak_days", "INTEGER DEFAULT 0"),
            ("daily_collected", "INTEGER DEFAULT 0"),
            ("referral_points", "INTEGER DEFAULT 0"),
            ("referred_by", "INTEGER DEFAULT NULL"),
            ("referred_at", "TEXT"),
        ):
            if col not in cols:
                try:
                    self._conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {ddl}")
                    self._conn.commit()
                    log.info("Migrated accounts table: added column %s", col)
                except sqlite3.OperationalError as exc:
                    log.warning("migration failed for %s: %s", col, exc)

    def _execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def account_count(self, telegram_id):
        row = self._execute(
            "SELECT COUNT(*) AS total FROM accounts WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return row["total"] if row else 0

    def add_account(self, telegram_id, phone, device_id, swuid, token, tid, sid, customer_id):
        """Add (or refresh) an account. Returns (account_id, is_new)."""
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
            is_new = False
        else:
            cur = self._execute(
                "INSERT INTO accounts (telegram_id, phone, token, tid, sid, device_id, swuid, customer_id, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (telegram_id, phone, token, tid, sid, device_id, swuid, customer_id, now()),
            )
            account_id = cur.lastrowid
            is_new = True
        self._execute("UPDATE accounts SET active = 0 WHERE telegram_id = ? AND id != ?", (telegram_id, account_id))
        return account_id, is_new

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
        row = self._execute(
            "SELECT last_collection_date, streak_days FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row:
            return False, 0
        return (row["last_collection_date"] or "") == today_ist(), row["streak_days"] or 0

    def today_earnings(self, account_id):
        cur = self._execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM buzz_logs "
            "WHERE account_id = ? AND date(created_at) = ? AND status = 'ok'",
            (account_id, today_ist()),
        )
        return cur.fetchone()["total"]

    def finish_collection(self, account_id):
        """Mark today's collection done and update the daily streak."""
        row = self._execute(
            "SELECT last_collection_date, streak_days FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        last = (row["last_collection_date"] or "") if row else ""
        streak = (row["streak_days"] or 0) if row else 0
        today = today_ist()
        if last == yesterday_ist():
            streak += 1
        elif last != today:
            streak = 1
        self._execute(
            "UPDATE accounts SET last_collection_date = ?, streak_days = ?, daily_collected = 1 WHERE id = ?",
            (today, streak, account_id),
        )
        return streak

    def add_links(self, entries, added_by):
        count = 0
        for url, campaign_id in entries:
            cur = self._execute(
                "INSERT OR IGNORE INTO buzz_links (link_url, campaign_id, added_by, created_at) VALUES (?, ?, ?, ?)",
                (url, campaign_id, added_by, now()),
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
            (account_id, link_id, action, amount, status, now()),
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

    # ---- REFERRAL SYSTEM ----

    def credit_referral_points(self, telegram_id, points):
        """Credit referral points to the user's first account row."""
        row = self._execute(
            "SELECT id FROM accounts WHERE telegram_id = ? ORDER BY id LIMIT 1", (telegram_id,)
        ).fetchone()
        if row:
            self._execute(
                "UPDATE accounts SET referral_points = referral_points + ? WHERE id = ?", (points, row["id"])
            )
            return True
        return False

    def record_referral(self, referrer_id, referred_telegram_id, points=1):
        self._execute(
            "INSERT INTO referrals (referrer_id, referred_id, points, created_at) VALUES (?, ?, ?, ?)",
            (referrer_id, referred_telegram_id, points, now()),
        )
        self.credit_referral_points(referrer_id, points)

    def set_referred(self, account_id, referrer_id):
        self._execute(
            "UPDATE accounts SET referred_by = ?, referred_at = ? WHERE id = ?",
            (referrer_id, now(), account_id),
        )

    def get_referral_stats(self, telegram_id):
        """Returns (points, referral_count) for a telegram user."""
        row = self._execute(
            "SELECT COALESCE(SUM(referral_points), 0) AS pts FROM accounts WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        cnt = self._execute(
            "SELECT COUNT(*) AS total FROM referrals WHERE referrer_id = ?", (telegram_id,)
        ).fetchone()
        return (row["pts"] if row else 0), (cnt["total"] if cnt else 0)


db = Database()

# ============================== SWIGGY API ==============================

CAMPAIGN_ID_RE = re.compile(r"buzzstreaks/([^/?#\s]+)")
FALLBACK_CAMPAIGN_RE = re.compile(r"([A-Za-z0-9]{4,}_[A-Za-z0-9]{3,})")
REWARD_KEYS = ("amount", "rewardvalue", "reward_amount", "points", "earned", "cashback")

PHONE_RE = re.compile(r"^\d{10}$")


def normalize_phone(raw):
    """Clean any raw input down to a plain 10-digit Indian mobile number."""
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


def api_status(data):
    """Extract (statusCode, statusMessage) from an API response JSON."""
    if not isinstance(data, dict):
        return None, ""
    code = data.get("statusCode", data.get("code"))
    msg = (
        data.get("statusMessage")
        or data.get("message")
        or data.get("errorMessage")
        or ""
    )
    return code, msg


class ApiError(Exception):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data


def generate_device_id():
    return str(uuid.uuid4()).upper()


def generate_swuid():
    return "SW-" + uuid.uuid4().hex[:12].upper()


def extract_campaign_id(url):
    if not url:
        return None
    match = CAMPAIGN_ID_RE.search(url)
    if match:
        return match.group(1).rstrip("=")
    match = FALLBACK_CAMPAIGN_RE.search(url)
    if match:
        return match.group(1).rstrip("=")
    return None


def split_campaign_id(campaign_id):
    """Split 'ougwl_<base64>' into (base_campaign, encoded_target)."""
    if not campaign_id or "_" not in campaign_id:
        return campaign_id, ""
    return campaign_id.split("_", 1)[0], campaign_id.split("_", 1)[1]


def decode_target_user_id(campaign_id):
    """Decode 'ougwl_<base64(userId#name)>' -> target userId."""
    base, encoded = split_campaign_id(campaign_id)
    if not encoded:
        return None
    padded = encoded + "=" * (-len(encoded) % 4)
    decoded = ""
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
    except Exception:
        try:
            decoded = base64.b64decode(padded).decode("utf-8", "ignore")
        except Exception:
            return None
    if not decoded:
        return None
    if "#" in decoded:
        return decoded.split("#", 1)[0]
    return decoded if decoded.isdigit() else None


def find_key(node, key, depth=0):
    if depth > 10 or not isinstance(node, (dict, list)):
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() == key.lower():
                return v
            found = find_key(v, key, depth + 1)
            if found is not None:
                return found
    else:
        for item in node:
            found = find_key(item, key, depth + 1)
            if found is not None:
                return found
    return None


def parse_session(data):
    """Parse login response to extract token, tid, sid, customer_id"""
    log.debug("Parsing session data: %s", json.dumps(data)[:500])

    result = {
        "token": "",
        "tid": "",
        "sid": "",
        "customer_id": "",
    }

    if isinstance(data, dict):
        result["tid"] = data.get("tid", "")
        result["sid"] = data.get("sid", "")

        inner_data = data.get("data", {})
        if isinstance(inner_data, dict):
            result["token"] = inner_data.get("token", "")
            result["customer_id"] = str(inner_data.get("customer_id", ""))

            if not result["customer_id"]:
                juspay = inner_data.get("juspay", {})
                if isinstance(juspay, dict):
                    result["customer_id"] = str(juspay.get("customer_id", ""))

        if not result["token"]:
            for key in ["token", "access_token", "jwt", "auth_token"]:
                val = data.get(key)
                if val and isinstance(val, str) and len(val) > 10:
                    result["token"] = val
                    break

        if not result["token"]:
            result["token"] = find_key(data, "token") or find_key(data, "access_token") or ""

        if not result["customer_id"]:
            customer_id = find_key(data, "customer_id")
            if customer_id:
                result["customer_id"] = str(customer_id)

    log.debug("Parsed: token=%s...", result["token"][:30] if result["token"] else "None")
    return result


def parse_amount(payload):
    total = 0.0

    def walk(node):
        nonlocal total
        if isinstance(node, dict):
            for key, val in node.items():
                lowered = str(key).lower()
                if lowered == "totalearned" and isinstance(val, dict):
                    try:
                        total += float(val.get("units", 0))
                    except (TypeError, ValueError):
                        pass
                elif lowered in REWARD_KEYS and isinstance(val, (int, float)) and val > 0:
                    total += float(val)
                elif isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item)

    walk(payload)
    return round(total, 2)


def parse_total_earned(payload):
    """Extract the real cumulative totalEarned (₹) from a rewards response.

    Looks for rollingFreecash -> totalEarned -> units, exactly as Swiggy
    returns it. max() is used so nested duplicates never inflate the amount.

    NOTE: the joining bonus (joiningBonusAmount) is NOT added here because
    Swiggy already folds it into totalEarned once claimed - adding it again
    would double-count. It is parsed separately via parse_joining_bonus()
    and parse_connect_bonus() and credited through its own claim path.
    """
    total = 0.0

    def walk(node):
        nonlocal total
        if isinstance(node, dict):
            for key, val in node.items():
                lowered = str(key).lower()
                if lowered == "rollingfreecash" and isinstance(val, dict):
                    earned = val.get("totalEarned") or {}
                    try:
                        total = max(total, float(earned.get("units", 0)))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item)

    walk(payload)
    return round(total, 2)


def parse_joining_bonus(payload):
    """Extract (joiningBonusApplicable, joiningBonusAmount) from a response.

    Swiggy returns these under inviteAndJoin (or top level):
      "joiningBonusApplicable": true,
      "joiningBonusAmount": {"units": 5, ...}
    """
    applicable = find_key(payload, "joiningBonusApplicable")
    amount = find_key(payload, "joiningBonusAmount")
    if isinstance(amount, dict):
        amount = amount.get("units", amount.get("amount", 0))
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if applicable is None:
        applicable = False
    return bool(applicable), amount


def parse_connect_bonus(payload):
    """Extract the joining bonus actually earned from a connect/action response.

    Tries several key names Swiggy uses inside campaignUserActionResponse.
    """
    for key in ("joiningBonusAmount", "bonusAmount", "claimAmount", "amountEarned", "earningAmount"):
        val = find_key(payload, key)
        if val is None:
            continue
        if isinstance(val, dict):
            val = val.get("units", val.get("amount", 0))
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return round(val, 2)
    return 0.0


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


def classify_response(data):
    """Classify a campaign response.

    Returns None (ok), 'claimed' (already claimed/done), or 'error'.
    """
    if not isinstance(data, dict):
        return None
    if is_success(data):
        return None
    text = json.dumps(data)[:2000].lower()
    if "already" in text:
        return "claimed"
    return "error"


class SwiggyClient:
    def __init__(self, device_id=None, swuid=None):
        self.device_id = device_id or generate_device_id()
        self.swuid = swuid or generate_swuid()
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
            return {"status": "error", "message": "Invalid phone number (must be 10 digits)"}
        url = f"{OTP_URL}?mobile={phone}"
        log.info("Sending OTP for phone=%s device=%s", phone, self.device_id)
        resp = self.session.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            return {"status": "error", "message": f"HTTP {resp.status_code}"}
        try:
            data = resp.json()
        except ValueError:
            return {"status": "error", "message": "Invalid response from server"}
        code, msg = api_status(data)
        if code == 999 or (code is not None and not is_success(data)):
            return {
                "status": "error",
                "message": f"{msg or 'Request failed'} (statusCode {code})",
            }
        if "captcha" in json.dumps(data).lower():
            return {"status": "captcha", "message": "OTP blocked by captcha. Try again later or from a fresh device."}
        if not isinstance(data, dict) or data.get("errorCode") or data.get("errorMessage"):
            return {"status": "error", "message": str(data)[:300]}
        if isinstance(data, dict):
            if data.get("tid"):
                self.tid = str(data["tid"])
            if data.get("sid"):
                self.sid = str(data["sid"])
        return {"status": "ok", "data": data, "tid": self.tid, "sid": self.sid}

    def verify_otp(self, phone, otp):
        phone = normalize_phone(phone)
        if not phone:
            raise ApiError("Invalid phone number (must be 10 digits)")
        if not self.tid:
            log.warning("verify_otp called without a tid from send_otp!")
        log.info("Verifying OTP for phone=%s otp=%s", phone, otp)
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
        url = f"{VERIFY_URL}?otp_source=Sms-manual"
        headers = self._headers()
        headers.setdefault("manufacturer", "GOOGLE")
        headers.setdefault("model-name", "PIXEL 4")
        resp = self.session.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code != 200:
            raise ApiError(f"Login verify returned HTTP {resp.status_code}")
        data = resp.json()
        code, msg = api_status(data)
        if code is not None and not is_success(data):
            raise ApiError(f"{msg or 'Verification failed'} (statusCode {code})", data)
        if isinstance(data, dict):
            if data.get("tid"):
                self.tid = str(data["tid"])
            if data.get("sid"):
                self.sid = str(data["sid"])
        return data

    def _auth_headers(self, account):
        headers = self._headers()
        if account.get("token"):
            headers["token"] = account["token"]
        if account.get("tid"):
            headers["tid"] = account["tid"]
        if account.get("sid"):
            headers["sid"] = account["sid"]
        return headers

    def _spns_headers(self, account):
        headers = dict(SPNS_HEADERS)
        if account.get("token"):
            headers["token"] = account["token"]
        if account.get("tid"):
            headers["tid"] = account["tid"]
        if account.get("sid"):
            headers["sid"] = account["sid"]
        headers["deviceid"] = self.device_id
        headers["swuid"] = self.swuid
        return headers

    def _post(self, url, headers, body):
        """POST with NO retry - a single failure is caught by the caller
        and the campaign is skipped, never retried (speed fix #9)."""
        try:
            resp = self.session.post(url, headers=headers, json=body, timeout=30)
            data = resp.json() if resp.content else {}
        except Exception as exc:
            raise ApiError(f"network error: {exc}")
        return data

    def collect_campaign(self, account, campaign_id, client_id="portal_banner", source="banner", force_refresh=True):
        body = {
            "generalContext": {
                "requestContext": {
                    "clientId": client_id,
                }
            },
            "campaignRewardRequests": [
                {
                    "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
                    "campaignId": campaign_id,
                    "rollingFreecashParams": {
                        "forceRefresh": force_refresh,
                        "requestParams": {
                            "dataRequested": "wallet,connections,transactions",
                            "consumerName": "",
                            "source": source,
                        },
                    },
                }
            ],
        }
        return self._post(REWARDS_URL, self._spns_headers(account), body)

    def buzz_back(self, account, campaign_id, claim_bonus=False):
        """Buzz back: connect with the friend who sent the invite.

        When claim_bonus is True (joiningBonusApplicable was true) the
        connect action carries the joining-bonus claim flag so the ₹5
        first-time bonus is collected. The friend on the other side also
        gets their reward -> both sides collect.
        """
        base_campaign, _ = split_campaign_id(campaign_id)
        target_id = decode_target_user_id(campaign_id)
        if not target_id:
            log.warning("No target user id in campaign_id %s, falling back to rewards invite", campaign_id)
            return self.collect_campaign(account, campaign_id, client_id="portal_invite", source="invite")
        action = {"actionType": "ACTION_TYPE_CONNECT", "targetEntityId": target_id}
        if claim_bonus:
            action["joiningBonusRequest"] = True
        body = {
            "generalContext": {
                "requestContext": {
                    "clientId": "portal_invite",
                }
            },
            "consumerContext": {
                "consumerId": account.get("customer_id", ""),
            },
            "campaignUserActionRequest": {
                "campaignId": base_campaign,
                "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
                "action": action,
            },
        }
        log.info("Buzz back: campaign=%s target=%s bonus=%s", base_campaign, target_id, claim_bonus)
        return self._post(CAMPAIGN_ACTION_URL, self._spns_headers(account), body)


# ============================== BOT HELPERS ==============================

PHONE, OTP = range(2)

login_sessions = {}
progress_messages = {}
collecting_tasks = {}


def tg_id(update):
    user = update.effective_user
    return user.id if user else 0


def chat_id(update):
    chat = update.effective_chat
    return chat.id if chat else 0


def is_admin(user_id):
    return user_id in ADMIN_IDS


async def get_bot_username(context):
    me = context.bot_data.get("me")
    if not me:
        me = await context.bot.get_me()
        context.bot_data["me"] = me
    return me.username


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
            InlineKeyboardButton("🔗 Referral", callback_data="btn_refer"),
        ],
        [InlineKeyboardButton("🆘 Help", callback_data="btn_help")],
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


async def send_plain(update, context, text):
    cid = chat_id(update)
    if not cid:
        return None
    return await context.bot.send_message(cid, text, parse_mode=ParseMode.HTML)


async def start(update, context):
    if not tg_id(update):
        return
    user_id = tg_id(update)
    for arg in (context.args or []):
        if arg.startswith("ref_"):
            try:
                ref_id = int(arg.split("_", 1)[1])
                if ref_id and ref_id != user_id:
                    context.user_data["referred_by"] = ref_id
                    log.info("user %s came via referral of %s", user_id, ref_id)
            except (ValueError, TypeError):
                pass
    accounts = db.get_accounts(user_id)
    text = (
        "<b>🤖 Swiggy Buzz Auto-Collector</b>\n\n"
        "Collect all your Swiggy Buzz rewards automatically — no manual clicking!\n\n"
        f"👤 Accounts: {len(accounts)}/{MAX_ACCOUNTS}\n"
        f"🔗 Referral points: {db.get_referral_stats(user_id)[0]}\n\n"
        f"{BRAND}"
    )
    await update.message.reply_text(text, reply_markup=main_menu(update), parse_mode=ParseMode.HTML)


async def refer_command(update, context):
    user_id = tg_id(update)
    if not user_id:
        return
    username = await get_bot_username(context)
    link = f"https://t.me/{username}?start=ref_{user_id}"
    points, ref_count = db.get_referral_stats(user_id)
    text = (
        "🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"💳 <b>Referral points: {points}</b>\n"
        f"👥 <b>Referrals: {ref_count}</b>\n\n"
        f"🤝 Each new user who joins via your link: <b>+{REFERRAL_POINTS_PER_REF} point</b>\n"
        f"🎁 Every new user gets <b>+{REFERRAL_SIGNUP_POINTS} points</b> on first login\n\n"
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
    count = db.account_count(user_id)
    await answer(
        update,
        "📱 <b>Login to Swiggy Buzz</b>\n\n"
        f"👤 Accounts: <b>{count}/{MAX_ACCOUNTS}</b>\n\n"
        "Enter your <b>10-digit</b> mobile number only.\n"
        "Do NOT add +91 or 91 — just the 10 digits.\n\n"
        "Example: <code>9876543210</code>\n\n"
        "Send /cancel to abort.",
    )
    return PHONE


async def phone_received(update, context):
    user_id = tg_id(update)
    if not user_id:
        return ConversationHandler.END
    raw = (update.message.text or "").strip()
    phone = normalize_phone(raw)
    if not phone:
        await update.message.reply_text(
            "❌ <b>Invalid phone number.</b>\n\n"
            "Enter your <b>10-digit</b> mobile number only (no +91, no 91, no spaces).\n"
            "Example: <code>9876543210</code>\n\n"
            "Try again:",
            parse_mode=ParseMode.HTML,
        )
        return PHONE
    log.info("Login flow: cleaned phone %r -> %s", raw, phone)
    client = SwiggyClient()
    try:
        status = await asyncio.to_thread(client.send_otp, phone)
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Could not send OTP: {html.escape(str(exc)[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return PHONE
    if status.get("status") != "ok":
        msg = str(status.get("message", "unknown error"))
        log.error("OTP send failed for phone=%s: %s", phone, msg)
        if "invalid" in msg.lower() or "999" in msg:
            await update.message.reply_text(
                "❌ <b>This mobile number is invalid on Swiggy.</b>\n\n"
                f"API said: {html.escape(msg[:200])}\n\n"
                "Check the number and re-enter your correct <b>10-digit</b> number:",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                f"❌ OTP request failed:\n{html.escape(msg[:200])}\n\nTry again:",
                parse_mode=ParseMode.HTML,
            )
        return PHONE
    login_sessions[user_id] = {
        "phone": phone,
        "client": client,
        "tid": client.tid,
        "sid": client.sid,
    }
    log.info("Session stored for phone=%s tid_len=%s sid=%s", phone, len(client.tid), client.sid)
    await update.message.reply_text(
        f"✅ <b>OTP sent to +91 {phone}!</b>\n\nEnter the 6-digit OTP you received:",
        parse_mode=ParseMode.HTML,
    )
    return OTP


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
        return OTP
    client = session["client"]
    log.info("Verifying OTP for phone=%s with session tid_len=%s sid=%s",
             session["phone"], len(session.get("tid", "")), session.get("sid", ""))
    try:
        data = await asyncio.to_thread(client.verify_otp, session["phone"], otp)
    except ApiError as exc:
        err_msg = str(exc)
        log.error("OTP verification API error for phone=%s: %s", session["phone"], err_msg)
        if "invalid" in err_msg.lower() or "999" in err_msg:
            login_sessions.pop(user_id, None)
            await update.message.reply_text(
                "❌ <b>This mobile number is invalid on Swiggy.</b>\n\n"
                f"API response: {html.escape(err_msg[:200])}\n\n"
                "Tap 🔐 Login Account and re-enter your correct <b>10-digit</b> "
                "number (no +91, no 91).",
                reply_markup=main_menu(update),
                parse_mode=ParseMode.HTML,
            )
            return ConversationHandler.END
        await update.message.reply_text(
            f"❌ OTP verification failed: {html.escape(err_msg[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return OTP
    except Exception as exc:
        log.error("OTP verification error: %s", exc)
        await update.message.reply_text(
            f"❌ OTP verification failed: {html.escape(str(exc)[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return OTP

    token = data.get("data", {}).get("token") or data.get("token")
    tid = data.get("tid") or data.get("data", {}).get("tid")
    sid = data.get("sid") or data.get("data", {}).get("sid")
    customer_id = data.get("data", {}).get("customer_id") or data.get("customer_id")

    if not token:
        await update.message.reply_text("❌ Login failed. Check the OTP and try again.", parse_mode=ParseMode.HTML)
        return OTP

    # ---- ACCOUNT LIMIT CHECK (max 2 per user) ----
    existing = db.get_accounts(user_id)
    phones = {a["phone"] for a in existing}
    if session["phone"] not in phones and len(existing) >= MAX_ACCOUNTS:
        login_sessions.pop(user_id, None)
        await update.message.reply_text(
            f"❌ <b>Account limit reached!</b>\n\n"
            f"You can add up to <b>{MAX_ACCOUNTS}</b> accounts per user.\n\n"
            "Remove an account from 👤 <b>My Accounts</b> before adding a new one.",
            reply_markup=main_menu(update),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    account_id, is_new = db.add_account(
        user_id,
        session["phone"],
        client.device_id,
        client.swuid,
        token,
        tid or "",
        sid or "",
        str(customer_id) if customer_id else "",
    )
    login_sessions.pop(user_id, None)

    # ---- REFERRAL CREDIT ----
    referrer_id = context.user_data.pop("referred_by", None)
    if is_new:
        if referrer_id and referrer_id != user_id:
            db.record_referral(referrer_id, user_id, REFERRAL_POINTS_PER_REF)
            db.set_referred(account_id, referrer_id)
        db.credit_referral_points(user_id, REFERRAL_SIGNUP_POINTS)
        log.info("New user %s credited +%s signup referral points", user_id, REFERRAL_SIGNUP_POINTS)

    account = db.get_account(account_id)
    if not account:
        await update.message.reply_text("❌ Could not save account. Try again.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    bonus_line = ""
    if is_new:
        bonus_line = (
            f"\n\n🎁 <b>+{REFERRAL_SIGNUP_POINTS} referral points</b> credited! "
            f"Use /refer to earn more."
        )
    await update.message.reply_text(
        f"✅ <b>Logged in as +{html.escape(session['phone'])}</b>{bonus_line}\n\n"
        f"🎁 Auto-collection started...",
        parse_mode=ParseMode.HTML,
    )
    start_collection(update, context, account)
    await update.message.reply_text(
        "🔹 <b>Main Menu</b>\n\n" + BRAND,
        reply_markup=main_menu(update),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# ============================== COLLECTION ==============================


def start_collection(update, context, account):
    cid = chat_id(update)
    if not cid:
        return
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        log.info("collection already running for chat %s", cid)
        return
    collecting_tasks[cid] = asyncio.create_task(run_collection(update, context, account))


def progress_text(done, total, earned, last_ok, bonus=0.0, skipped=0):
    bar_len = 12
    filled = min(bar_len, int(bar_len * done / max(total, 1)))
    bar = "🟩" * filled + "⬜" * (bar_len - filled)
    mark = "✅" if last_ok else "❌"
    bonus_line = f"\n🎁 Joining bonus: ₹{bonus:.2f}" if bonus > 0 else ""
    skip_line = f"\n⏭ Skipped: {skipped}" if skipped > 0 else ""
    return (
        f"🎁 <b>Collecting... [{done}/{total}]</b>\n"
        f"{bar}\n"
        f"💰 Collected: ₹{earned:.2f}{bonus_line}{skip_line}\n"
        f"Last result: {mark}"
    )


def final_text(done, total, earned, account_total, streak, bonus=0.0, skipped=0):
    bonus_line = f"\n🎁 Joining bonus: ₹{bonus:.2f}" if bonus > 0 else ""
    skip_line = f"\n⏭ Skipped: {skipped}" if skipped > 0 else ""
    return (
        f"✅ <b>Collection finished! [{done}/{total}]</b>\n\n"
        f"💰 <b>Collected: ₹{earned:.2f}</b>{bonus_line}{skip_line}\n"
        f"🏆 Total lifetime: ₹{account_total:.2f}\n"
        f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}\n"
        f"📅 Next collection: Tomorrow\n\n"
        f"{BRAND}"
    )


async def edit_progress(cid, context, text):
    msg_id = progress_messages.get(cid)
    if not msg_id:
        return
    try:
        await context.bot.edit_message_text(text, chat_id=cid, message_id=msg_id, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "message is not modified" not in str(exc):
            log.warning("progress edit failed: %s", exc)
    except Exception as exc:
        log.warning("progress edit failed: %s", exc)


async def run_collection(update, context, account):
    cid = chat_id(update)
    account_id = account["id"]
    links = db.get_all_links()
    total_new = 0.0
    bonus_total = 0.0
    done = 0
    skipped = 0
    last_ok = True
    streak = 0
    try:
        if not links:
            await send_plain(update, context, "📭 No buzz links added yet. Ask an admin to add links first.")
            return
        row = db.get_account(account_id)
        if not row:
            return
        already, streak = db.has_collected_today(account_id)
        if already:
            today_earned = db.today_earnings(account_id)
            await send_plain(
                update,
                context,
                "✅ <b>Already collected today!</b>\n\n"
                f"💰 Today's collection: ₹{today_earned:.2f}\n"
                f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}\n"
                f"📅 Next collection: Tomorrow\n\n"
                f"{BRAND}",
            )
            return
        first = await send_plain(update, context, "🎁 <b>Collecting...</b>")
        if first is not None:
            progress_messages[cid] = first.message_id
        client = SwiggyClient(device_id=account["device_id"], swuid=account["swuid"])
        snapshot = 0.0
        failed = set()
        try:
            base_campaign, _ = split_campaign_id(links[0]["campaign_id"])
            base_resp = await asyncio.to_thread(client.collect_campaign, row, base_campaign)
            snapshot = parse_total_earned(base_resp)
            log.info("Baseline totalEarned for %s: ₹%.2f", row["phone"], snapshot)
        except Exception as exc:
            log.warning("Baseline fetch failed, starting from 0: %s", exc)

        for index, link in enumerate(links, 1):
            row = db.get_account(account_id)
            if not row:
                break
            if row["total_earned"] >= MAX_EARN_PER_ACCOUNT:
                await edit_progress(
                    cid,
                    context,
                    f"🏆 <b>Max limit ₹{MAX_EARN_PER_ACCOUNT:g} reached!</b>\n\n"
                    f"Total earned: ₹{row['total_earned']:.2f}\n\n{BRAND}",
                )
                return
            cid_ = link["campaign_id"]
            if not cid_ or cid_ in failed:
                skipped += 1
                continue
            gained = 0.0

            # ---- 1) OPEN / COLLECT REWARDS (sender side) ----
            try:
                open_resp = await asyncio.to_thread(client.collect_campaign, row, cid_)
            except Exception as exc:
                db.log(row["id"], link["id"], "open", 0, "failed")
                failed.add(cid_)
                skipped += 1
                last_ok = False
                log.warning("open failed for %s: %s", cid_, exc)
                continue
            status = classify_response(open_resp)
            if status == "claimed":
                db.log(row["id"], link["id"], "open", 0, "already")
                failed.add(cid_)
                skipped += 1
                log.info("already claimed, skipping %s", cid_)
                continue
            if status == "error":
                db.log(row["id"], link["id"], "open", 0, "failed")
                failed.add(cid_)
                skipped += 1
                last_ok = False
                log.warning("invalid campaign %s, skipping", cid_)
                continue
            cur = parse_total_earned(open_resp)
            open_amt = max(0.0, cur - snapshot)
            snapshot = max(snapshot, cur)
            gained += open_amt
            db.log(row["id"], link["id"], "open", open_amt, "ok")

            # ---- 2) JOINING BONUS + BUZZ BACK (both sides) ----
            connect_bonus = 0.0
            applicable, pending_bonus = parse_joining_bonus(open_resp)
            try:
                if applicable and JOINING_BONUS_ENABLED:
                    buzz_resp = await asyncio.to_thread(client.buzz_back, row, cid_, claim_bonus=True)
                    connect_bonus = parse_connect_bonus(buzz_resp) or pending_bonus
                else:
                    await asyncio.to_thread(client.buzz_back, row, cid_)
                if connect_bonus > 0:
                    gained += connect_bonus
                    bonus_total += connect_bonus
                    db.log(row["id"], link["id"], "joining_bonus", connect_bonus, "ok")
                    log.info("🎁 joining bonus claimed: ₹%.2f", connect_bonus)
            except Exception as exc:
                db.log(row["id"], link["id"], "buzz_back", 0, "failed")
                log.warning("buzz_back failed for %s: %s", cid_, exc)

            # ---- 3) VERIFY (any bonus already folded into totalEarned) ----
            try:
                check = await asyncio.to_thread(client.collect_campaign, row, cid_, force_refresh=False)
                cur = parse_total_earned(check)
                back_amt = max(0.0, cur - snapshot)
                snapshot = max(snapshot, cur)
                if connect_bonus > 0:
                    back_amt = max(0.0, back_amt - connect_bonus)
                gained += back_amt
                db.log(row["id"], link["id"], "buzz_back", back_amt, "ok")
            except Exception as exc:
                db.log(row["id"], link["id"], "buzz_back", 0, "failed")
                log.warning("verify failed for %s: %s", cid_, exc)

            db.add_earned(row["id"], gained)
            total_new += gained
            done += 1
            last_ok = gained > 0
            await edit_progress(cid, context, progress_text(done, len(links), total_new, last_ok, bonus_total, skipped))
            await asyncio.sleep(REQUEST_DELAY)

        row = db.get_account(account_id)
        if done > 0:
            streak = db.finish_collection(account_id)
        final_total = row["total_earned"] if row else total_new
        await edit_progress(cid, context, final_text(done, len(links), total_new, final_total, streak, bonus_total, skipped))
    except Exception as exc:
        log.exception("collection crashed")
        try:
            await edit_progress(cid, context, f"❌ <b>Collection stopped:</b> {html.escape(str(exc)[:200])}")
        except Exception:
            pass
    finally:
        progress_messages.pop(cid, None)
        collecting_tasks.pop(cid, None)


# ============================== MENU HANDLERS ==============================


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
    text = (
        "👤 <b>Your Accounts</b>\n\n"
        + "\n".join(lines)
        + f"\n\n🔒 <b>Limit: {len(accounts)}/{MAX_ACCOUNTS}</b>\n\n"
        "Tap to switch active account. 🗑️ removes it."
    )
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


async def collect_menu(update, context):
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
        already, streak = db.has_collected_today(account["id"])
        if already:
            today_earned = db.today_earnings(account["id"])
            await answer(
                update,
                "✅ <b>Already collected today!</b>\n\n"
                f"💰 Today's collection: ₹{today_earned:.2f}\n"
                f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}\n"
                f"📅 Next collection: Tomorrow\n\n"
                f"{BRAND}",
                main_menu(update),
            )
            return
        start_collection(update, context, account)
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
    lines = []
    for a in accounts:
        streak = a.get("streak_days") or 0
        collected = (a.get("last_collection_date") or "") == today_ist()
        today_earned = db.today_earnings(a["id"]) if collected else 0.0
        line = f"📱 +{html.escape(a['phone'])} → ₹{a['total_earned']:.2f}"
        if a["active"]:
            line += " 🟢"
        if streak > 0:
            line += f" | 🔥 {streak}d"
        if collected:
            line += f" | ✅ today ₹{today_earned:.2f}"
        lines.append(line)
    points, ref_count = db.get_referral_stats(user_id)
    text = (
        "📊 <b>Your Stats</b>\n\n"
        + "\n".join(lines)
        + f"\n\n💰 <b>Total lifetime: ₹{total:.2f}</b>\n"
        + f"🔗 <b>Referral points: {points}</b> ({ref_count} referrals)\n\n"
        + f"{BRAND}"
    )
    await answer(update, text, main_menu(update))


async def referral_menu(update, context):
    user_id = tg_id(update)
    if not user_id:
        return
    username = await get_bot_username(context)
    link = f"https://t.me/{username}?start=ref_{user_id}"
    points, ref_count = db.get_referral_stats(user_id)
    text = (
        "🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"💳 <b>Referral points: {points}</b>\n"
        f"👥 <b>Referrals: {ref_count}</b>\n\n"
        f"🤝 Each new user who joins via your link: <b>+{REFERRAL_POINTS_PER_REF} point</b>\n"
        f"🎁 Every new user gets <b>+{REFERRAL_SIGNUP_POINTS} points</b> on first login\n\n"
        f"{BRAND}"
    )
    await answer(update, text, main_menu(update))


async def help_menu(update):
    text = (
        "<b>🤖 How to use Swiggy Buzz Auto-Collector</b>\n\n"
        "1️⃣ Tap <b>🔐 Login Account</b>\n"
        "2️⃣ Enter your <b>10-digit</b> phone number (no +91, no 91)\n"
        "3️⃣ Enter the OTP you receive\n"
        "4️⃣ Tap <b>🎁 Collect Buzz</b>\n\n"
        f"🔒 You can add up to <b>{MAX_ACCOUNTS} accounts</b>\n"
        "🎁 First-time friend connects auto-claim the ₹5 joining bonus\n"
        "🤝 Earn <b>+1 point per referral</b>, new users get <b>+2 points</b>\n"
        "📅 Collect <b>once per day</b> — streaks build daily\n"
        "💰 Real earnings are tracked from Swiggy's API\n"
        f"🏆 Max ₹{MAX_EARN_PER_ACCOUNT:g} per account\n\n"
        f"{BRAND}"
    )
    await answer(update, text, main_menu(update))


# ============================== ADMIN HANDLERS ==============================


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
            f"{' | 🔗' + str(a['referral_points'] or 0) + 'pts' if (a.get('referral_points') or 0) > 0 else ''}"
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
            await collect_menu(update, context)
        elif data == "btn_stats":
            await stats_menu(update)
        elif data == "btn_refer":
            await referral_menu(update, context)
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


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("refer", refer_command))

    login_conv = ConversationHandler(
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
    app.add_handler(login_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_links_received))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(error_handler)

    log.info("Swiggy Buzz bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
