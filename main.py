#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SWIGGY BUZZ AUTO-COLLECTOR BOT
Complete Single Script - Railway Ready
Made by @viediet
"""

import os
import re
import sqlite3
import json
import time
import uuid
import logging
import threading
from datetime import datetime, timedelta, timezone

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("papu")

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8607959399:AAFhSjG2DZm-9cTy-ufhe0W_rC9TnxDbLAY")
CHANNEL_USERNAME = "viedietlooters"
CHANNEL_LINK = "https://t.me/viedietlooters"
MAX_ACCOUNTS = 2
FREE_POINTS_ON_JOIN = 2
POINTS_PER_COLLECTION = 1
POINTS_PER_REFERRAL = 2
CASH_EXPIRY_DAYS = 3
CLAIM_COOLDOWN_HOURS = 24
DB_PATH = "papu.db"

REWARDS = {1: 100, 2: 100, 3: 150, 4: 200, 5: 250, 6: 250}

# ===================== SWIGGY API =====================
SWIGGY_BASE = "https://www.swiggy.com/dapi"
SMS_LOGIN_URL = SWIGGY_BASE + "/auth/smsotp/login"
BUZZ_STATUS_URL = SWIGGY_BASE + "/buzz/status"
BUZZ_JOIN_BONUS_URL = SWIGGY_BASE + "/buzz/join"
BUZZ_CLAIM_URL = SWIGGY_BASE + "/buzz/claim"
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

PENDING_LOGIN = {}
COLLECT_LOCKS = {}

# ===================== DATABASE =====================
def utcnow():
    return datetime.now(timezone.utc)

def now_iso():
    return utcnow().isoformat()

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_column(conn, table, column, ddl):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points_balance INTEGER DEFAULT 0,
                referred_by INTEGER,
                referral_points INTEGER DEFAULT 0,
                joined_channel INTEGER DEFAULT 0,
                free_points_given INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                phone TEXT,
                swiggy_token TEXT,
                collection_day INTEGER DEFAULT 1,
                bonus_claimed INTEGER DEFAULT 0,
                last_claimed_at TEXT,
                total_claimed INTEGER DEFAULT 0,
                invalid INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                credited INTEGER DEFAULT 0,
                created_at TEXT
            );
            """
        )
        ensure_column(conn, "users", "points_balance", "INTEGER DEFAULT 0")
        ensure_column(conn, "users", "referred_by", "INTEGER")
        ensure_column(conn, "users", "referral_points", "INTEGER DEFAULT 0")
        ensure_column(conn, "users", "joined_channel", "INTEGER DEFAULT 0")

def ensure_user(user_id, username, first_name, referred_by=None):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, points_balance, "
                "referred_by, referral_points, joined_channel, free_points_given, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, username, first_name, 0, referred_by, 0, 0, 0, now_iso()),
            )
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        else:
            conn.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, user_id),
            )
        return row

def get_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def get_accounts(user_id, include_invalid=True):
    with get_conn() as conn:
        if include_invalid:
            return conn.execute(
                "SELECT * FROM accounts WHERE user_id=? ORDER BY id", (user_id,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM accounts WHERE user_id=? AND invalid=0 ORDER BY id", (user_id,)
        ).fetchall()

def mask_phone(phone):
    digits = re.sub(r"\D", "", phone)
    return "+91-XXXX" + digits[-4:] if len(digits) >= 4 else phone

def expiry_text(last_claimed_at):
    if not last_claimed_at:
        return "No cash yet"
    dt = datetime.fromisoformat(last_claimed_at) + timedelta(days=CASH_EXPIRY_DAYS)
    return dt.strftime("%d %b %Y, %I:%M %p")

# ===================== BOT HELPERS =====================
def main_menu():
    kb = [
        [InlineKeyboardButton("💰 Collect Now", callback_data="collect")],
        [
            InlineKeyboardButton("👤 My Accounts", callback_data="accounts"),
            InlineKeyboardButton("➕ Add Account", callback_data="add"),
        ],
        [
            InlineKeyboardButton("🔗 Refer & Earn", callback_data="refer"),
            InlineKeyboardButton("⭐ My Points", callback_data="points"),
        ],
    ]
    return InlineKeyboardMarkup(kb)

def join_button():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)]]
    )

# ===================== FIXED CHANNEL CHECK =====================
async def is_channel_member(context, user_id):
    try:
        # Try with @username format
        member = await context.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error(f"Channel check error: {e}")
        return False

def require_member(f):
    async def wrapper(update, context):
        user_id = update.effective_user.id
        if not await is_channel_member(context, user_id):
            await update.effective_chat.send_message(
                "⚠️ You must join our channel first to use this bot.\n\n"
                f"👉 {CHANNEL_LINK}\n\nAfter joining, press /start again.",
                reply_markup=join_button(),
            )
            return None
        return await f(update, context)
    return wrapper

# ===================== SWIGGY API FUNCTIONS =====================
def buzz_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

def _post_json(url, token, payload=None):
    try:
        response = requests.post(
            url,
            headers=buzz_headers(token),
            json=payload or {},
            timeout=20
        )
        return response.json()
    except Exception as e:
        logger.error(f"POST {url} error: {e}")
        raise

def buzz_status(token):
    try:
        response = requests.get(
            BUZZ_STATUS_URL,
            headers=buzz_headers(token),
            timeout=20
        )
        return response.json()
    except Exception as e:
        logger.error(f"Buzz status error: {e}")
        raise

def buzz_claim_join_bonus(token):
    return _post_json(BUZZ_JOIN_BONUS_URL, token)

def buzz_claim_reward(token):
    return _post_json(BUZZ_CLAIM_URL, token)

def swiggy_send_otp(phone):
    device_id = uuid.uuid4().hex
    payload = {"deviceId": device_id, "mobile": phone, "otp": ""}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    response = requests.post(SMS_LOGIN_URL, json=payload, headers=headers, timeout=20)
    return device_id

def swiggy_verify_otp(phone, otp, device_id):
    payload = {"deviceId": device_id, "mobile": phone, "otp": otp}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    response = requests.post(SMS_LOGIN_URL, json=payload, headers=headers, timeout=20)
    data = response.json()
    data = data.get("data") or {}
    token = data.get("access_token") or data.get("token")
    return token

def process_account(acc):
    token = acc["swiggy_token"]
    try:
        status = buzz_status(token)
        data = status.get("data") or {}
    except Exception:
        return "❌ Invalid session", 0, None

    if data.get("joiningBonusApplicable") and not acc["bonus_claimed"]:
        try:
            jr = buzz_claim_join_bonus(token)
            if isinstance(jr, dict) and jr.get("statusCode") in (None, 200):
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE accounts SET bonus_claimed=1 WHERE id=?",
                        (acc["id"],),
                    )
        except Exception:
            logger.exception("bonus claim failed for account %s", acc["id"])

    if acc["last_claimed_at"]:
        last = datetime.fromisoformat(acc["last_claimed_at"])
        if utcnow() - last < timedelta(hours=CLAIM_COOLDOWN_HOURS):
            return "⚠️ Already Done", 0, None

    try:
        cr = buzz_claim_reward(token)
        amount = parse_amount(cr, acc["collection_day"])
    except Exception:
        return "❌ Invalid session", 0, None

    if amount <= 0:
        return "⚠️ Nothing to claim", 0, None

    day = acc["collection_day"]
    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET collection_day = MIN(collection_day + 1, 6), "
            "last_claimed_at=?, total_claimed = total_claimed + ? WHERE id=?",
            (now_iso(), amount, acc["id"]),
        )
    return f"✅ Claimed ₹{amount}", amount, day

def parse_amount(j, day):
    data = j.get("data") or {}
    for key in ("amount", "rewardAmount", "reward", "cashback", "value"):
        v = data.get(key)
        if v is not None:
            try:
                return int(float(v))
            except (TypeError, ValueError):
                pass
    return REWARDS.get(day, REWARDS[6])

def credit_referral_if_needed(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referred_by FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row or not row["referred_by"]:
            return
        ref = row["referred_by"]
        already = conn.execute(
            "SELECT id FROM referrals WHERE referred_id=?", (user_id,)
        ).fetchone()
        if already:
            return
        exists = conn.execute(
            "SELECT user_id FROM users WHERE user_id=?", (ref,)
        ).fetchone()
        if not exists:
            return
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, credited, created_at) "
            "VALUES (?,?,1,?)",
            (ref, user_id, now_iso()),
        )
        conn.execute(
            "UPDATE users SET points_balance = points_balance + ?, "
            "referral_points = referral_points + ? WHERE user_id=?",
            (POINTS_PER_REFERRAL, POINTS_PER_REFERRAL, ref),
        )

def run_collection_sync(user_id, chat_id, message_id, context):
    row = get_user(user_id)
    if row["points_balance"] < POINTS_PER_COLLECTION:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="🚫 Not enough points. Get points via /refer.",
        )
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET points_balance = points_balance - ? WHERE user_id=?",
            (POINTS_PER_COLLECTION, user_id),
        )

    accts = get_accounts(user_id, include_invalid=False)
    results = []
    total = 0
    any_success = False
    for acc in accts:
        status, amount, day = process_account(acc)
        if amount:
            total += amount
        if day is not None:
            any_success = True
        results.append((acc, status, amount))

    if any_success:
        credit_referral_if_needed(user_id)

    lines = []
    for acc, status, amount in results:
        if amount:
            lines.append(f"📞 {mask_phone(acc['phone'])}: {status}")
        else:
            lines.append(f"📞 {mask_phone(acc['phone'])}: {status}")

    row = get_user(user_id)
    text = (
        "✅ Collection finished!\n\n"
        + "\n".join(lines)
        + f"\n\n💰 Total claimed this run: ₹{total}\n"
        f"⭐ Points left: {row['points_balance']}\n"
        "⚠️ Cash expires in 3 days — collect daily to keep it fresh!"
    )
    try:
        context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text
        )
    except Exception:
        context.bot.send_message(chat_id=chat_id, text=text)

# ===================== TELEGRAM HANDLERS =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referred_by = int(context.args[0][4:])
        except ValueError:
            referred_by = None
        if referred_by == user.id:
            referred_by = None

    row = ensure_user(user.id, user.username, user.first_name, referred_by)

    if not await is_channel_member(context, user.id):
        await update.message.reply_text(
            "⚠️ You must join our channel first to use this bot.\n\n"
            f"👉 {CHANNEL_LINK}\n\nAfter joining, press /start again.",
            reply_markup=join_button(),
        )
        return

    got_bonus = False
    with get_conn() as conn:
        conn.execute("UPDATE users SET joined_channel=1 WHERE user_id=?", (user.id,))
        if not row["free_points_given"]:
            conn.execute(
                "UPDATE users SET points_balance = points_balance + ?, "
                "free_points_given = 1 WHERE user_id=?",
                (FREE_POINTS_ON_JOIN, user.id),
            )
            got_bonus = True

    text = f"🎉 Welcome, {user.first_name}!\n\n"
    if got_bonus:
        text += f"🎁 You received {FREE_POINTS_ON_JOIN} free points!\n"
    text += (
        "🤖 This bot auto-collects Swiggy Buzz rewards for you.\n\n"
        "💡 How it works:\n"
        f"• Add your Swiggy account (max {MAX_ACCOUNTS})\n"
        "• Press Collect — rewards are claimed automatically\n"
        f"• Each collection costs {POINTS_PER_COLLECTION} point\n"
        f"• Invite friends, earn {POINTS_PER_REFERRAL} points each\n"
        "• Cash rewards expire in 3 days\n\n"
        "Use the buttons below 👇"
    )
    await update.message.reply_text(text, reply_markup=main_menu())

@require_member
async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user.id}"
    await update.message.reply_text(
        "🔗 Your referral link:\n\n"
        f"`{link}`\n\n"
        "📌 How it works:\n"
        f"• Friend joins the channel & starts the bot → gets {FREE_POINTS_ON_JOIN} free points\n"
        f"• When your friend completes their first collection, you earn {POINTS_PER_REFERRAL} points\n"
        "• No limit on referrals!",
        parse_mode="Markdown",
    )

@require_member
async def cmd_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = get_user(user.id)
    accts = get_accounts(user.id)
    lines = []
    for a in accts:
        if a["invalid"]:
            status = "❌ Invalid session"
        else:
            status = f"Day {a['collection_day']} · total claimed ₹{a['total_claimed']}"
        lines.append(f"• {mask_phone(a['phone'])} — {status}")
    text = (
        f"⭐ Points balance: {row['points_balance']}\n"
        f"🏅 Earned from referrals: {row['referral_points']}\n"
        f"💳 Collection cost: {POINTS_PER_COLLECTION} point per run\n\n"
        "📅 Cash expiry (3 days from claim):\n" + "\n".join(
            f"• {mask_phone(a['phone'])} — {expiry_text(a['last_claimed_at'])}"
            for a in accts
        )
    )
    await update.message.reply_text(text)

@require_member
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accts = get_accounts(update.effective_user.id)
    if not accts:
        await update.message.reply_text(
            "📭 No accounts yet. Use /add to connect your first Swiggy account.",
            reply_markup=main_menu(),
        )
        return
    lines = []
    for a in accts:
        if a["invalid"]:
            lines.append(
                f"❌ {mask_phone(a['phone'])} — Invalid session (re-add)"
            )
        else:
            lines.append(
                f"✅ {mask_phone(a['phone'])} — Day {a['collection_day']} · "
                f"claimed ₹{a['total_claimed']} · bonus {'done' if a['bonus_claimed'] else 'pending'}\n"
                f"   Cash expires: {expiry_text(a['last_claimed_at'])}"
            )
    await update.message.reply_text("👤 Your accounts:\n\n" + "\n".join(lines))

@require_member
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id
    accts = get_accounts(user_id, include_invalid=False)
    if len(accts) >= MAX_ACCOUNTS:
        await update.message.reply_text(
            f"🚫 Account limit reached!\n\nYou can add max {MAX_ACCOUNTS} accounts. "
            "Delete one first from /accounts."
        )
        return
    PENDING_LOGIN[chat.id] = {"step": "phone"}
    await update.message.reply_text(
        "📞 Send your Swiggy-registered mobile number with country code.\n"
        "Example: `+919876543210`\n\nSend /cancel to abort.",
        parse_mode="Markdown",
    )

@require_member
async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    accts = get_accounts(user_id, include_invalid=False)
    if not accts:
        await update.message.reply_text(
            "📭 No accounts added. Use /add first.", reply_markup=main_menu()
        )
        return
    row = get_user(user_id)
    if row["points_balance"] < POINTS_PER_COLLECTION:
        await update.message.reply_text(
            f"🚫 Not enough points!\n\nYou need {POINTS_PER_COLLECTION} point to collect. "
            "Get points via /refer (2 pts per friend)."
        )
        return
    msg = await update.message.reply_text(
        f"⏳ Collecting from {len(accts)} account(s)..."
    )
    threading.Thread(
        target=run_collection_sync,
        args=(user_id, update.effective_chat.id, msg.message_id, context)
    ).start()

@require_member
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.id in PENDING_LOGIN:
        del PENDING_LOGIN[chat.id]
    await update.message.reply_text("❌ Login cancelled.")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    state = PENDING_LOGIN.get(chat.id)
    if not state:
        return
    text = update.message.text.strip()

    if state["step"] == "phone":
        if not await is_channel_member(context, update.effective_user.id):
            del PENDING_LOGIN[chat.id]
            await update.message.reply_text(
                "⚠️ Channel membership required.", reply_markup=join_button()
            )
            return
        digits = re.sub(r"\D", "", text)
        if len(digits) < 10:
            await update.message.reply_text("❌ Invalid number. Example: `+919876543210`")
            return
        phone = "+" + digits
        try:
            device_id = swiggy_send_otp(phone)
        except Exception:
            del PENDING_LOGIN[chat.id]
            await update.message.reply_text(
                "❌ Could not reach Swiggy. Check the API endpoints in the script and try again."
            )
            return
        state["step"] = "otp"
        state["phone"] = phone
        state["device_id"] = device_id
        await update.message.reply_text(
            "📩 OTP sent to your phone. Reply with the OTP (6 digits)."
        )

    elif state["step"] == "otp":
        otp = re.sub(r"\D", "", text)
        if len(otp) < 4:
            await update.message.reply_text("❌ Invalid OTP. Try again.")
            return
        try:
            token = swiggy_verify_otp(state["phone"], otp, state["device_id"])
        except Exception:
            await update.message.reply_text("❌ OTP verification failed. Try again.")
            return
        if not token:
            await update.message.reply_text("❌ Wrong OTP. Try again or send /cancel.")
            return
        user_id = update.effective_user.id
        accts = get_accounts(user_id, include_invalid=False)
        if len(accts) >= MAX_ACCOUNTS:
            del PENDING_LOGIN[chat.id]
            await update.message.reply_text(
                f"🚫 Limit reached: max {MAX_ACCOUNTS} accounts per user."
            )
            return
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO accounts (user_id, phone, swiggy_token, collection_day, "
                "bonus_claimed, last_claimed_at, total_claimed, invalid, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, state["phone"], token, 1, 0, None, 0, 0, now_iso()),
            )
        del PENDING_LOGIN[chat.id]
        await update.message.reply_text(
            f"✅ Account {mask_phone(state['phone'])} added successfully!\n\n"
            "Press Collect Now to claim rewards. 🎉",
            reply_markup=main_menu(),
        )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id

    if not await is_channel_member(context, user_id):
        await q.edit_message_text(
            "⚠️ You must join our channel first.\n\n" + CHANNEL_LINK,
            reply_markup=join_button(),
        )
        return

    if q.data == "collect":
        accts = get_accounts(user_id, include_invalid=False)
        if not accts:
            await q.edit_message_text(
                "📭 No accounts added. Use /add first.", reply_markup=main_menu()
            )
            return
        row = get_user(user_id)
        if row["points_balance"] < POINTS_PER_COLLECTION:
            await q.edit_message_text(
                f"🚫 Not enough points!\n\nYou need {POINTS_PER_COLLECTION} point to collect. "
                "Get points via /refer (2 pts per friend).",
                reply_markup=main_menu()
            )
            return
        msg = await q.edit_message_text(
            f"⏳ Collecting from {len(accts)} account(s)..."
        )
        threading.Thread(
            target=run_collection_sync,
            args=(user_id, q.message.chat_id, msg.message_id, context)
        ).start()

    elif q.data == "accounts":
        accts = get_accounts(user_id)
        if not accts:
            await q.edit_message_text(
                "📭 No accounts yet.", reply_markup=main_menu()
            )
            return
        kb = []
        for a in accts:
            label = "❌ " if a["invalid"] else "✅ "
            label += mask_phone(a["phone"]) + (
                f" · Day {a['collection_day']}" if not a["invalid"] else " · invalid"
            )
            kb.append([InlineKeyboardButton(label, callback_data=f"del_{a['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="menu")])
        await q.edit_message_text(
            "👤 Your accounts (tap to delete):\n\n"
            "• 1 collection/day per account\n"
            "• Cash expires in 3 days",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif q.data.startswith("del_"):
        acc_id = int(q.data[4:])
        with get_conn() as conn:
            conn.execute("DELETE FROM accounts WHERE id=? AND user_id=?", (acc_id, user_id))
        accts = get_accounts(user_id)
        text = "👤 Accounts left:\n\n" if accts else "📭 No accounts left."
        kb = []
        for a in accts:
            label = "❌ " if a["invalid"] else "✅ "
            label += mask_phone(a["phone"])
            kb.append([InlineKeyboardButton(label, callback_data=f"del_{a['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="menu")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == "refer":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{user_id}"
        await q.edit_message_text(
            f"🔗 Your referral link:\n\n`{link}`\n\n"
            f"• Friend joins → gets {FREE_POINTS_ON_JOIN} free points\n"
            f"• Friend's first collection → you get {POINTS_PER_REFERRAL} points\n"
            "• Unlimited referrals!",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )

    elif q.data == "points":
        row = get_user(user_id)
        accts = get_accounts(user_id)
        text = (
            f"⭐ Points: {row['points_balance']}\n"
            f"🏅 Referral points earned: {row['referral_points']}\n"
            f"💳 Cost per collection: {POINTS_PER_COLLECTION}\n\n"
            "📅 Cash expiry:\n" + "\n".join(
                f"• {mask_phone(a['phone'])} — {expiry_text(a['last_claimed_at'])}"
                for a in accts
            )
        )
        await q.edit_message_text(text, reply_markup=main_menu())

    elif q.data == "add":
        accts = get_accounts(user_id, include_invalid=False)
        if len(accts) >= MAX_ACCOUNTS:
            await q.edit_message_text(
                f"🚫 Account limit reached (max {MAX_ACCOUNTS}).",
                reply_markup=main_menu(),
            )
            return
        PENDING_LOGIN[q.message.chat_id] = {"step": "phone"}
        await q.edit_message_text(
            "📞 Send your Swiggy-registered mobile number with country code.\n"
            "Example: `+919876543210`\n\nSend /cancel to abort.",
            parse_mode="Markdown",
        )

    elif q.data == "menu":
        await q.edit_message_text(
            "🏠 Main Menu", reply_markup=main_menu()
        )

# ===================== MAIN =====================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("points", cmd_points))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("collect", cmd_collect))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    logger.info("🤖 Swiggy Buzz Auto-Collector Bot started")
    logger.info(f"👑 Channel: {CHANNEL_USERNAME}")
    logger.info("⚡ Made by @viediet")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
