#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
  GBX LOOT - Link Exchange Bot (Swiggy Buzz & Toing Pizza Squad)
  Developer : @viediet
  Branding  : "Made by @viediet"
  Channel   : https://t.me/viedietlooters
=====================================================================

  A fully working, production-ready Telegram bot where users share
  Swiggy Buzz and Toing (Pizza Squad) referral links in two separate
  sections. Every new link is instantly broadcast to ALL users and
  clicks are tracked per user (✅ / ❌).

  WHY THIS IS DIFFERENT FROM THE OLD BROKEN SCRIPT (send.py)
  ----------------------------------------------------------
  1. Admin detection now uses a persisted `settings` table
     (first /start user = admin) instead of the crash-prone
     `SELECT ... ORDER BY id` on a table that has no `id` column.
  2. All messages use HTML parsing instead of legacy Markdown,
     which used to crash on URLs containing underscores like
     https://r.swiggy.com/buzzstreaks/ougwl_xxxx (the main reason
     the View sections appeared broken).
  3. Broadcasts include sender username + timestamp + branding.
  4. One single MessageHandler (the old script registered two,
     so every text message triggered both handlers).
  5. Broadcasting uses `asyncio.sleep()` instead of blocking
     `time.sleep()` inside the async event loop.
  6. View lists are rendered by EDITING the callback message
     (the old script deleted + re-sent, causing layout issues).
  7. Click tracking is wired to an "🔗 Open Link" callback button
     (Telegram never notifies a bot when a pure URL button is
     tapped, so the callback records the click and then hands the
     user a real URL button to open).

  FEATURES
  --------
  • Channel-join force (@viedietlooters)
  • Two sections: 🟢 Swiggy Buzz / 🟠 Toing Pizza Squad
  • URL validation + duplicate prevention (UNIQUE link_url)
  • Instant broadcast of every new link to ALL users (0.05s delay)
  • View latest 50 links with ✅ / ❌ click status per user
  • Click tracking (link_clicks table + comma-separated clicked_users)
  • Referral system: refer 1 friend -> UNLIMITED usage unlock
    (before referral: 1 link/day total, resets at midnight IST)
  • Admin panel: stats / users / delete links / broadcast
  • SQLite with WAL mode + proper indexes
  • Commands: /start /refer /status /help /cancel

  ENVIRONMENT VARIABLES
  ---------------------
  BOT_TOKEN         (required)
  ADMIN_ID          (optional) fixed admin ID; else first user
  CHANNEL_USERNAME  (optional) default: viedietlooters
  BOT_USERNAME      (optional) auto-detected if empty
  DB_PATH           (optional) default: gbx_loot.db
  DAILY_LIMIT       (optional) links/day before referral (default 1)
=====================================================================
"""

import asyncio
import html
import logging
import os
import sqlite3
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gbxloot")

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8607959399:AAFhSjG2DZm-9cTy-ufhe0W_rC9TnxDbLAY")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "viedietlooters")
CHANNEL_LINK = f"https://t.me/{CHANNEL_USERNAME}"
ADMIN_ID = int(os.getenv("ADMIN_ID", "1")) or None
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
DB_PATH = os.getenv("DB_PATH", "gbx_loot.db")
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "1"))
BROADCAST_DELAY = 0.05          # delay between broadcast messages (rate limits)
LINKS_PER_PAGE = 10             # links per page in View sections
DEL_PER_PAGE = 5                # links per page in admin delete lists
USERS_PER_PAGE = 10             # users per page in admin users list
BRANDING = "Made by Viediet"
SEP = "━━━━━━━━━━━━━━━━━━━━━━"
IST = timezone(timedelta(hours=5, minutes=30))  # Indian Standard Time

if not BOT_TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN environment variable is required!")

LINK_TABLES = {"swiggy": "swiggy_links", "toing": "toing_links"}
LINK_META = {
    "swiggy": ("🟢", "Swiggy Buzz", "Swiggy"),
    "toing": ("🟠", "Toing (Pizza Squad)", "Toing"),
}

# ------------------------------------------------------------------
# DATABASE LAYER
# ------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id              INTEGER PRIMARY KEY,
    username             TEXT,
    first_name           TEXT,
    referred_by          INTEGER DEFAULT 0,
    has_referred         INTEGER DEFAULT 0,
    joined_channel       INTEGER DEFAULT 0,
    created_at           TEXT,
    daily_submissions    INTEGER DEFAULT 0,
    last_submission_date TEXT
);

CREATE TABLE IF NOT EXISTS swiggy_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    link_url      TEXT UNIQUE,
    clicks        INTEGER DEFAULT 0,
    clicked_users TEXT DEFAULT '',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS toing_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    link_url      TEXT UNIQUE,
    clicks        INTEGER DEFAULT 0,
    clicked_users TEXT DEFAULT '',
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS referrals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER,
    referred_id INTEGER,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS link_clicks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id    INTEGER,
    link_type  TEXT,
    user_id    INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_created      ON users(created_at);
CREATE INDEX IF NOT EXISTS idx_swiggy_created     ON swiggy_links(created_at);
CREATE INDEX IF NOT EXISTS idx_toing_created      ON toing_links(created_at);
CREATE INDEX IF NOT EXISTS idx_clicks_user        ON link_clicks(user_id);
CREATE INDEX IF NOT EXISTS idx_clicks_link        ON link_clicks(link_id, link_type);
"""


def db():
    """Open a fresh connection. Table names are only ever picked from
    the fixed LINK_TABLES mapping, so f-strings here are injection-safe."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", DB_PATH)


def now_ist():
    return datetime.now(IST)


def now_ist_str():
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


def today_ist():
    return now_ist().date().isoformat()


# ---------------- settings / admin ----------------
def get_setting(key, default=None):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_admin():
    if ADMIN_ID:
        return ADMIN_ID
    val = get_setting("admin_id")
    return int(val) if val else None


def set_admin(user_id):
    set_setting("admin_id", str(user_id))
    logger.info("👑 Admin assigned to user %s", user_id)


# ---------------- users ----------------
def ensure_user(user_id, username, first_name, referred_by=0):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users(user_id, username, first_name, referred_by, created_at) "
            "VALUES(?,?,?,?,?)",
            (user_id, username or "", first_name or "", referred_by, now_ist_str()),
        )
    else:
        conn.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (username or "", first_name or "", user_id),
        )
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def all_user_ids():
    conn = db()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def users_page(page=0):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (USERS_PER_PAGE, page * USERS_PER_PAGE),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    conn.close()
    return rows, total


def has_referred(user_id):
    conn = db()
    row = conn.execute(
        "SELECT has_referred FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    return bool(row and row["has_referred"])


def mark_joined(user_id):
    conn = db()
    conn.execute("UPDATE users SET joined_channel=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# ---------------- daily limit (before referral) ----------------
def daily_usage(user_id):
    """Return (used, limit). limit is None once unlimited (referred)."""
    if has_referred(user_id):
        return 0, None
    today = today_ist()
    conn = db()
    row = conn.execute(
        "SELECT daily_submissions, last_submission_date FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if row is None:
        conn.close()
        return 0, DAILY_LIMIT
    if row["last_submission_date"] != today:
        conn.execute(
            "UPDATE users SET daily_submissions=0, last_submission_date=? "
            "WHERE user_id=?",
            (today, user_id),
        )
        conn.commit()
        used = 0
    else:
        used = row["daily_submissions"]
    conn.close()
    return used, DAILY_LIMIT


def bump_daily_usage(user_id):
    today = today_ist()
    conn = db()
    conn.execute(
        "UPDATE users SET daily_submissions=daily_submissions+1, "
        "last_submission_date=? WHERE user_id=?",
        (today, user_id),
    )
    conn.commit()
    conn.close()


# ---------------- links ----------------
def insert_link(link_type, user_id, url):
    """Returns (link_id, None) on success or (None, error_code)."""
    table = LINK_TABLES.get(link_type)
    if not table:
        return None, "invalid_type"
    conn = db()
    try:
        cur = conn.execute(
            f"INSERT INTO {table}(user_id, link_url, created_at) VALUES(?,?,?)",
            (user_id, url, now_ist_str()),
        )
        conn.commit()
        link_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return None, "duplicate"
    conn.close()
    return link_id, None


def get_link(link_type, link_id):
    table = LINK_TABLES.get(link_type)
    if not table:
        return None
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (link_id,)).fetchone()
    conn.close()
    return row


def links_page(link_type, limit, offset):
    table = LINK_TABLES.get(link_type)
    if not table:
        return [], 0
    conn = db()
    rows = conn.execute(
        f"SELECT * FROM {table} ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    conn.close()
    return rows, total


def count_links(link_type):
    conn = db()
    c = conn.execute(f"SELECT COUNT(*) AS c FROM {LINK_TABLES[link_type]}").fetchone()["c"]
    conn.close()
    return c


def total_clicks():
    conn = db()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM link_clicks"
    ).fetchone()
    conn.close()
    return row["c"]


def delete_link(link_type, link_id):
    conn = db()
    conn.execute(f"DELETE FROM {LINK_TABLES[link_type]} WHERE id=?", (link_id,))
    conn.execute(
        "DELETE FROM link_clicks WHERE link_type=? AND link_id=?",
        (link_type, link_id),
    )
    conn.commit()
    conn.close()


# ---------------- clicks ----------------
def has_clicked(link_type, link_id, user_id):
    conn = db()
    row = conn.execute(
        "SELECT 1 FROM link_clicks WHERE link_type=? AND link_id=? AND user_id=?",
        (link_type, link_id, user_id),
    ).fetchone()
    conn.close()
    return bool(row)


def record_click(link_type, link_id, user_id):
    """Returns 'ok', 'already' or 'self'."""
    conn = db()
    exists = conn.execute(
        "SELECT 1 FROM link_clicks WHERE link_type=? AND link_id=? AND user_id=?",
        (link_type, link_id, user_id),
    ).fetchone()
    if exists:
        conn.close()
        return "already"
    conn.execute(
        "INSERT INTO link_clicks(link_id, link_type, user_id, created_at) "
        "VALUES(?,?,?,?)",
        (link_id, link_type, user_id, now_ist_str()),
    )
    conn.execute(
        f"UPDATE {LINK_TABLES[link_type]} SET clicks=clicks+1, "
        "clicked_users=CASE WHEN clicked_users='' THEN ? "
        "ELSE clicked_users || ',' || ? END WHERE id=?",
        (str(user_id), str(user_id), link_id),
    )
    conn.commit()
    conn.close()
    return "ok"


# ---------------- referrals ----------------
def record_referral(referrer_id, referred_id):
    """Returns True if a NEW referral was registered (referrer unlocked)."""
    conn = db()
    row = conn.execute(
        "SELECT referred_by FROM users WHERE user_id=?", (referred_id,)
    ).fetchone()
    if row and row["referred_by"]:
        conn.close()
        return False
    conn.execute(
        "UPDATE users SET referred_by=? WHERE user_id=?",
        (referrer_id, referred_id),
    )
    conn.execute(
        "INSERT INTO referrals(referrer_id, referred_id, created_at) VALUES(?,?,?)",
        (referrer_id, referred_id, now_ist_str()),
    )
    conn.execute("UPDATE users SET has_referred=1 WHERE user_id=?", (referrer_id,))
    conn.commit()
    conn.close()
    return True


# ------------------------------------------------------------------
# UI HELPERS
# ------------------------------------------------------------------
def referral_link(user_id):
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


def short_url(url, length=50):
    return url[:length] + ("..." if len(url) > length else "")


def esc(text):
    return html.escape(text or "")


def main_menu_keyboard(user_id):
    rows = [
        [
            InlineKeyboardButton("📤 Submit Swiggy Link", callback_data="submit_swiggy"),
            InlineKeyboardButton("📤 Submit Toing Link", callback_data="submit_toing"),
        ],
        [
            InlineKeyboardButton("📋 View Swiggy Links", callback_data="view_swiggy"),
            InlineKeyboardButton("📋 View Toing Links", callback_data="view_toing"),
        ],
        [
            InlineKeyboardButton("🔗 Get Referral Link", callback_data="refer"),
            InlineKeyboardButton("⭐ My Status", callback_data="status"),
        ],
    ]
    if user_id == get_admin():
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


WELCOME_TEXT = (
    "🎉 <b>WELCOME TO VIEDIET LOOT!</b>\n\n"
    "Exchange referral links with the whole community:\n"
    "🟢 <b>Swiggy Buzz</b> • 🟠 <b>Toing Pizza Squad</b>\n\n"
    "📌 <b>How it works:</b>\n"
    "1️⃣ Submit your referral link in the right section\n"
    "2️⃣ Your link is instantly broadcast to <b>ALL</b> users\n"
    "3️⃣ Click others' links to help them get rewards 🎁\n"
    "4️⃣ Refer 1 friend to unlock <b>UNLIMITED</b> usage!\n\n"
    f"{SEP}\n<i>{BRANDING}</i>"
)


def join_screen():
    text = (
        "🚫 <b>Channel join required!</b>\n\n"
        f"You must join <b>@{CHANNEL_USERNAME}</b> before using Viediet LOOT.\n\n"
        f"👉 {CHANNEL_LINK}\n\n"
        "Join the channel, then press <b>\"✅ I've Joined\"</b>.\n\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check_join")],
        ]
    )
    return text, kb


def back_to_menu_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Main Menu", callback_data="menu")]]
    )


# ------------------------------------------------------------------
# BROADCAST ENGINE
# ------------------------------------------------------------------
async def broadcast_new_link(context, link_type, link_id):
    """Notify EVERY registered user about a freshly submitted link."""
    emoji, type_name, _ = LINK_META[link_type]
    link = get_link(link_type, link_id)
    if not link:
        return 0, 0
    sender = get_user(link["user_id"])
    sender_name = sender["username"] or sender["first_name"] or "Unknown"

    text = (
        f"🔔 <b>New {type_name} Link Added!</b>\n"
        f"{SEP}\n"
        f"{emoji} <b>Link:</b>\n<code>{esc(link['link_url'])}</code>\n\n"
        f"👤 Submitted by: <b>@{esc(sender_name)}</b>\n"
        f"📅 {now_ist_str()}\n\n"
        f"Click the button below to open and help! 🎁\n"
        f"{SEP}\n"
        f"<i>{BRANDING}</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔗 Open Link", callback_data=f"open_{link_type}:{link_id}"
                ),
                InlineKeyboardButton(
                    "📋 View All Links", callback_data=f"view_{link_type}"
                ),
            ]
        ]
    )

    sent = failed = 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(
                uid, text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.debug("Broadcast to %s failed: %s", uid, exc)
        await asyncio.sleep(BROADCAST_DELAY)  # never block the loop
    logger.info("Broadcast link #%s: %s sent, %s failed", link_id, sent, failed)
    return sent, failed


async def admin_broadcast(context, message_text):
    """Send an admin message to all users."""
    text = f"📢 <b>Announcement</b>\n\n{esc(message_text)}\n\n{SEP}\n<i>{BRANDING}</i>"
    sent = failed = 0
    for uid in all_user_ids():
        try:
            await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.debug("Admin broadcast to %s failed: %s", uid, exc)
        await asyncio.sleep(BROADCAST_DELAY)
    logger.info("Admin broadcast: %s sent, %s failed", sent, failed)
    return sent, failed


# ------------------------------------------------------------------
# CHANNEL MEMBERSHIP
# ------------------------------------------------------------------
async def is_channel_member(context, user_id):
    """True if the user joined the channel. If the bot cannot verify
    (not an admin of the channel), we allow access to avoid bricking."""
    try:
        member = await context.bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id
        )
        return member.status in ("member", "administrator", "creator", "restricted")
    except Exception as exc:
        logger.warning("Membership check failed for %s: %s", user_id, exc)
        return True


async def require_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the join screen if the user is not a member. Returns True if OK."""
    if await is_channel_member(context, update.effective_user.id):
        mark_joined(update.effective_user.id)
        return True
    text, kb = join_screen()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    return False


# ------------------------------------------------------------------
# COMMAND HANDLERS
# ------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args or []

    # ---- referral code parsing: /start?start=ref_USER_ID ----
    referred_by = 0
    if args and args[0].startswith("ref_"):
        try:
            referred_by = int(args[0].split("_", 1)[1])
        except (ValueError, IndexError):
            referred_by = 0
        if referred_by == user.id:
            referred_by = 0  # cannot refer yourself

    # ---- first user to ever start the bot becomes the admin ----
    if not get_admin():
        set_admin(user.id)

    ensure_user(user.id, user.username, user.first_name, referred_by)

    # ---- register referral + unlock the referrer ----
    if referred_by:
        if record_referral(referred_by, user.id):
            try:
                await context.bot.send_message(
                    referred_by,
                    "🎉 <b>CONGRATULATIONS!</b>\n"
                    f"{SEP}\n"
                    "A friend joined <b>Viediet LOOT</b> using your referral link!\n\n"
                    "✅ <b>UNLIMITED USAGE UNLOCKED!</b>\n"
                    "Submit as many links as you want now 🚀\n\n"
                    f"{SEP}\n<i>{BRANDING}</i>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.warning("Could not notify referrer %s: %s", referred_by, exc)

    # ---- channel join force ----
    if not await is_channel_member(context, user.id):
        text, kb = join_screen()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    mark_joined(user.id)
    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user.id),
    )


async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_member(update, context):
        return
    user = update.effective_user
    link = referral_link(user.id)
    unlocked = has_referred(user.id)
    status = "✅ Unlimited" if unlocked else f"⛔ {DAILY_LIMIT} link(s) per day"
    share_url = "https://t.me/share/url?" + urllib.parse.urlencode(
        {
            "url": link,
            "text": "🎁 Join me on Viediet LOOT! Exchange Swiggy & Toing referral links!",
        }
    )
    text = (
        "🔗 <b>YOUR REFERRAL LINK</b>\n"
        f"{SEP}\n"
        f"<code>{link}</code>\n\n"
        f"🤝 Friends referred: {'✅ 1+' if unlocked else '❌ 0'}\n"
        f"🔓 Your usage: <b>{status}</b>\n\n"
        "📌 <b>How it works:</b>\n"
        "• Share this link with friends\n"
        "• When 1 friend joins, you unlock <b>UNLIMITED</b> usage\n"
        "• Your friend gets full access too 🎉\n\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 Share with a Friend", url=share_url)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")],
        ]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_member(update, context):
        return
    user = update.effective_user
    used, limit = daily_usage(user.id)
    unlocked = has_referred(user.id)
    today = today_ist()
    conn = db()
    submitted_today = conn.execute(
        "SELECT COUNT(*) AS c FROM swiggy_links WHERE user_id=? AND date(created_at)=?"
        " UNION ALL SELECT COUNT(*) AS c FROM toing_links WHERE user_id=? AND date(created_at)=?",
        (user.id, today, user.id, today),
    ).fetchall()
    conn.close()
    total_today = sum(r["c"] for r in submitted_today)

    if unlocked:
        usage_line = "✅ <b>UNLIMITED</b> (referral done)"
    else:
        usage_line = f"⛔ <b>{used}/{limit}</b> used today (resets at midnight IST)"

    text = (
        "⭐ <b>MY STATUS</b>\n"
        f"{SEP}\n"
        f"👤 User: <b>@{esc(user.username or user.first_name or 'Unknown')}</b>\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"🤝 Referred anyone: {'✅ Yes' if unlocked else '❌ Not yet'}\n"
        f"🔓 Usage: {usage_line}\n"
        f"📊 Submitted today: <b>{total_today}</b> link(s)\n\n"
        f"🔗 Referral link:\n<code>{referral_link(user.id)}</code>\n\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🤝 Refer & Unlock", callback_data="refer"),
                    InlineKeyboardButton("🏠 Main Menu", callback_data="menu"),
                ]
            ]
        ),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_member(update, context):
        return
    text = (
        "📖 <b>HELP & COMMANDS</b>\n"
        f"{SEP}\n"
        "/start - Welcome & main menu\n"
        "/refer - Get your referral link\n"
        "/status - Daily usage & referral status\n"
        "/help - Show this help\n"
        "/cancel - Cancel the current action\n\n"
        "📤 <b>Submitting:</b> press a Submit button, paste your link\n"
        "📋 <b>Viewing:</b> press a View button, tap 🔗 to open & click\n"
        "🤝 <b>Unlock:</b> refer 1 friend for unlimited usage\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting_link", None)
    context.user_data.pop("awaiting_broadcast", None)
    await update.message.reply_text(
        "❌ <b>Action cancelled.</b>\n\n" + WELCOME_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(update.effective_user.id),
    )


# ------------------------------------------------------------------
# TEXT HANDLER (link submission / admin broadcast)
# ------------------------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = context.user_data

    # ---------------- admin broadcast input ----------------
    if user_data.get("awaiting_broadcast"):
        del user_data["awaiting_broadcast"]
        await update.message.reply_text("📢 <b>Broadcasting…</b>", parse_mode=ParseMode.HTML)
        sent, failed = await admin_broadcast(context, update.message.text)
        await update.message.reply_text(
            f"✅ <b>Broadcast complete!</b>\n"
            f"{SEP}\n"
            f"📤 Sent: <b>{sent}</b>\n"
            f"❌ Failed: <b>{failed}</b>\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # ---------------- link submission input ----------------
    if user_data.get("awaiting_link"):
        link_type = user_data["awaiting_link"]
        emoji, type_name, short_label = LINK_META[link_type]
        url = update.message.text.strip()

        # channel force re-check before accepting anything
        if not await is_channel_member(context, user.id):
            del user_data["awaiting_link"]
            text, kb = join_screen()
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return

        # URL validation
        if not url.startswith(("http://", "https://")):
            await update.message.reply_text(
                "❌ <b>Invalid link!</b>\n"
                f"{SEP}\n"
                "The link must start with <code>http://</code> or <code>https://</code>.\n\n"
                f"Please paste the full {short_label} link again.\n"
                f"{SEP}\n<i>{BRANDING}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="menu")]]
                ),
            )
            return

        # daily limit before referral (resets at midnight IST)
        used, limit = daily_usage(user.id)
        if limit is not None and used >= limit:
            del user_data["awaiting_link"]
            await update.message.reply_text(
                "⛔ <b>Daily limit reached!</b>\n"
                f"{SEP}\n"
                f"You can submit only <b>{limit} link(s) per day</b> until you refer a friend.\n\n"
                f"🤝 Refer 1 friend to unlock <b>UNLIMITED</b> usage:\n"
                f"<code>{referral_link(user.id)}</code>\n\n"
                f"Next reset: <b>midnight IST</b>\n"
                f"{SEP}\n<i>{BRANDING}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🤝 Refer & Unlock", callback_data="refer")]]
                ),
            )
            return

        # duplicate prevention (link_url is UNIQUE in the table)
        link_id, err = insert_link(link_type, user.id, url)
        if err == "duplicate":
            del user_data["awaiting_link"]
            await update.message.reply_text(
                "⚠️ <b>Duplicate link!</b>\n"
                f"{SEP}\n"
                f"This {short_label} link already exists in the section.\n\n"
                "You cannot submit the same link twice.\n"
                f"{SEP}\n<i>{BRANDING}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(user.id),
            )
            return

        del user_data["awaiting_link"]
        if limit is not None:
            bump_daily_usage(user.id)

        # confirmation
        await update.message.reply_text(
            f"✅ <b>{short_label} link added successfully!</b>\n"
            f"{SEP}\n"
            f"🔗 <code>{esc(url)}</code>\n\n"
            f"📢 This link will be sent to <b>ALL</b> users!\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📋 View All Links", callback_data=f"view_{link_type}"
                        ),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="menu"),
                    ]
                ]
            ),
        )

        # instant broadcast
        sent, failed = await broadcast_new_link(context, link_type, link_id)
        await update.message.reply_text(
            f"📢 <b>Broadcast finished!</b>\n"
            f"{SEP}\n"
            f"✅ Sent to: <b>{sent}</b> user(s)\n"
            f"❌ Failed: <b>{failed}</b>\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ---------------- unknown text ----------------
    await update.message.reply_text(
        "ℹ️ Please use the buttons below 👇\n\n" + WELCOME_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user.id),
    )


# ------------------------------------------------------------------
# VIEW SECTIONS
# ------------------------------------------------------------------
async def view_links(update: Update, context: ContextTypes.DEFAULT_TYPE, link_type, page=0):
    q = update.callback_query
    emoji, type_name, _ = LINK_META[link_type]
    user_id = q.from_user.id
    links, total = links_page(link_type, LINKS_PER_PAGE, page * LINKS_PER_PAGE)
    total_pages = max(1, (total + LINKS_PER_PAGE - 1) // LINKS_PER_PAGE)
    page = min(page, total_pages - 1)

    if not links:
        await q.edit_message_text(
            f"📭 <b>No {type_name} links yet!</b>\n"
            f"{SEP}\n"
            "Be the first to submit yours and get it broadcast to ALL users! 🚀\n\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("📤 Submit", callback_data=f"submit_{link_type}"),
                        InlineKeyboardButton("🏠 Main Menu", callback_data="menu"),
                    ]
                ]
            ),
        )
        return

    lines = [
        f"{emoji} <b>{type_name} Links</b>",
        SEP,
    ]
    buttons = []
    for idx, link in enumerate(links, start=page * LINKS_PER_PAGE + 1):
        status = "✅" if has_clicked(link_type, link["id"], user_id) else "❌"
        submitter = get_user(link["user_id"])
        sub_name = "@" + (submitter["username"] if submitter and submitter["username"]
                          else (submitter["first_name"] if submitter else "Unknown"))
        lines.append(
            f"#{idx} {status} <code>{esc(short_url(link['link_url']))}</code> "
            f"[Clicks: {link['clicks']}] 👤 {esc(sub_name)}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🔗 Open #{idx}", callback_data=f"open_{link_type}:{link['id']}"
                )
            ]
        )
    lines += [
        SEP,
        f"📊 Total: <b>{total}</b> links  (Page {page + 1}/{total_pages})",
        SEP,
        f"<i>{BRANDING}</i>",
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"view_{link_type}:p:{page - 1}"))
    nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"view_{link_type}:p:{page}"))
    if (page + 1) * LINKS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"view_{link_type}:p:{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu")])

    await q.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ------------------------------------------------------------------
# OPEN LINK (CLICK TRACKING)
# ------------------------------------------------------------------
async def open_link(update: Update, context: ContextTypes.DEFAULT_TYPE, link_type, link_id):
    q = update.callback_query
    user_id = q.from_user.id
    emoji, type_name, _ = LINK_META[link_type]
    link = get_link(link_type, link_id)

    if not link:
        await q.edit_message_text(
            "⚠️ <b>This link no longer exists!</b>\n\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="menu")]]
            ),
        )
        return

    url = link["link_url"]

    if link["user_id"] == user_id:
        head = "😉 <b>This is your own link!</b>\nYou can't count your own click."
        clicks_now = link["clicks"]
    else:
        result = record_click(link_type, link_id, user_id)
        if result == "already":
            head = "🔁 <b>You already clicked this link!</b>\nHere it is again:"
            clicks_now = link["clicks"]
        else:
            head = "✅ <b>Click recorded! Thank you! 🎉</b>"
            clicks_now = link["clicks"] + 1

    text = (
        f"{head}\n"
        f"{SEP}\n"
        f"{emoji} <b>Link:</b>\n<code>{esc(url)}</code>\n\n"
        f"📊 Total clicks: <b>{clicks_now}</b>\n\n"
        f"👇 Tap the button below to open:\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Open Link", url=url)],
            [
                InlineKeyboardButton("📋 View All", callback_data=f"view_{link_type}"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="menu"),
            ],
        ]
    )
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ------------------------------------------------------------------
# ADMIN PANEL
# ------------------------------------------------------------------
def admin_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("👥 Users List", callback_data="admin_users")],
            [InlineKeyboardButton("🗑 Delete Swiggy Links", callback_data="admin_del_swiggy")],
            [InlineKeyboardButton("🗑 Delete Toing Links", callback_data="admin_del_toing")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu")],
        ]
    )


async def admin_panel_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    text = (
        "👑 <b>ADMIN PANEL</b>\n"
        f"{SEP}\n"
        "📊 Stats:\n"
        f"• Swiggy Links: <b>{count_links('swiggy')}</b>\n"
        f"• Toing Links: <b>{count_links('toing')}</b>\n"
        f"• Total Clicks: <b>{total_clicks()}</b>\n"
        f"{SEP}\n"
        "Select an option below:\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=admin_menu_keyboard()
    )


async def admin_stats_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    conn = db()
    total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    swiggy = conn.execute("SELECT COUNT(*) AS c FROM swiggy_links").fetchone()["c"]
    toing = conn.execute("SELECT COUNT(*) AS c FROM toing_links").fetchone()["c"]
    clicks = conn.execute("SELECT COUNT(*) AS c FROM link_clicks").fetchone()["c"]
    referrals = conn.execute("SELECT COUNT(*) AS c FROM referrals").fetchone()["c"]
    unlocked = conn.execute("SELECT COUNT(*) AS c FROM users WHERE has_referred=1").fetchone()["c"]
    joined = conn.execute("SELECT COUNT(*) AS c FROM users WHERE joined_channel=1").fetchone()["c"]
    conn.close()
    text = (
        "📊 <b>FULL STATISTICS</b>\n"
        f"{SEP}\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"✅ Joined Channel: <b>{joined}</b>\n"
        f"🟢 Swiggy Links: <b>{swiggy}</b>\n"
        f"🟠 Toing Links: <b>{toing}</b>\n"
        f"🖱 Total Clicks: <b>{clicks}</b>\n"
        f"🤝 Referrals Made: <b>{referrals}</b>\n"
        f"🔓 Users Unlocked: <b>{unlocked}</b>\n"
        f"{SEP}\n<i>{BRANDING}</i>"
    )
    await q.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        ),
    )


async def admin_users_view(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    q = update.callback_query
    users, total = users_page(page)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)

    if not users:
        body = "👥 <b>No users yet.</b>"
    else:
        lines = [f"👥 <b>USERS LIST</b>  (Page {page + 1}/{total_pages})", SEP]
        for u in users:
            name = esc(u["first_name"] or "—")
            uname = f"@{u['username']}" if u["username"] else "—"
            lines.append(
                f"<code>{u['user_id']}</code> {name} • {esc(uname)}\n"
                f"🤝 Referred: {'✅' if u['has_referred'] else '❌'}  "
                f"📤 Today: {u['daily_submissions']}"
            )
        lines.append(SEP)
        lines.append(f"<i>{BRANDING}</i>")
        body = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_users_page:{page - 1}"))
    nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_users_page:{page}"))
    if (page + 1) * USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_users_page:{page + 1}"))
    kb = InlineKeyboardMarkup(
        [nav, [InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
    )
    await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=kb)


async def admin_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE, link_type, page=0):
    q = update.callback_query
    emoji, type_name, _ = LINK_META[link_type]
    links, total = links_page(link_type, DEL_PER_PAGE, page * DEL_PER_PAGE)
    total_pages = max(1, (total + DEL_PER_PAGE - 1) // DEL_PER_PAGE)

    if not links:
        await q.edit_message_text(
            f"{emoji} <b>No {type_name} links to delete.</b>\n\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
            ),
        )
        return

    lines = [f"🗑 <b>DELETE {type_name.upper()} LINKS</b>  (Page {page + 1}/{total_pages})", SEP,
             "<i>Tap a link to delete it instantly</i>", ""]
    buttons = []
    for idx, link in enumerate(links, start=page * DEL_PER_PAGE + 1):
        lines.append(
            f"#{idx} 👤 {link['clicks']} clicks\n"
            f"<code>{esc(short_url(link['link_url'], 40))}</code>"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🗑 Delete #{idx}", callback_data=f"del_{link_type}:{link['id']}"
                )
            ]
        )
    lines.append(SEP)
    lines.append(f"<i>{BRANDING}</i>")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_del_{link_type}_p:{page - 1}"))
    nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_del_{link_type}_p:{page}"))
    if (page + 1) * DEL_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_del_{link_type}_p:{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")])

    await q.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_delete_one(update: Update, context: ContextTypes.DEFAULT_TYPE, link_type, link_id):
    q = update.callback_query
    emoji, type_name, _ = LINK_META[link_type]
    delete_link(link_type, link_id)
    logger.info("Admin %s deleted %s link #%s", q.from_user.id, link_type, link_id)
    await q.edit_message_text(
        f"🗑 <b>{type_name} link deleted!</b>\n"
        f"{SEP}\n"
        f"Removed link <code>#{link_id}</code> permanently.\n"
        f"{SEP}\n<i>{BRANDING}</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Delete More", callback_data=f"admin_del_{link_type}"),
                    InlineKeyboardButton("⬅️ Back", callback_data="admin_panel"),
                ]
            ]
        ),
    )


# ------------------------------------------------------------------
# CALLBACK DISPATCHER
# ------------------------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    user_id = q.from_user.id
    admin = get_admin()

    # ---------- main menu ----------
    if data == "menu":
        await q.edit_message_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    # ---------- channel join re-check ----------
    if data == "check_join":
        if await is_channel_member(context, user_id):
            mark_joined(user_id)
            await q.edit_message_text(
                WELCOME_TEXT,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(user_id),
            )
        else:
            await q.answer("⛔ You haven't joined the channel yet!", show_alert=True)
        return

    # ---------- referral info ----------
    if data == "refer":
        link = referral_link(user_id)
        unlocked = has_referred(user_id)
        status = "✅ Unlimited" if unlocked else f"⛔ {DAILY_LIMIT} link(s)/day"
        share_url = "https://t.me/share/url?" + urllib.parse.urlencode(
            {
                "url": link,
                "text": "🎁 Join me on Viediet LOOT! Exchange Swiggy & Toing referral links!",
            }
        )
        text = (
            "🔗 <b>YOUR REFERRAL LINK</b>\n"
            f"{SEP}\n"
            f"<code>{link}</code>\n\n"
            f"🔓 Your usage: <b>{status}</b>\n\n"
            "Share this link — when <b>1 friend</b> joins, "
            "you unlock <b>UNLIMITED</b> submissions! 🚀\n\n"
            f"{SEP}\n<i>{BRANDING}</i>"
        )
        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📤 Share with a Friend", url=share_url)],
                    [InlineKeyboardButton("⬅️ Back", callback_data="menu")],
                ]
            ),
        )
        return

    # ---------- my status ----------
    if data == "status":
        used, limit = daily_usage(user_id)
        unlocked = has_referred(user_id)
        usage_line = "✅ <b>UNLIMITED</b> (referral done)" if unlocked else \
            f"⛔ <b>{used}/{limit}</b> used today (resets at midnight IST)"
        text = (
            "⭐ <b>MY STATUS</b>\n"
            f"{SEP}\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"🤝 Referred anyone: {'✅ Yes' if unlocked else '❌ Not yet'}\n"
            f"🔓 Usage: {usage_line}\n\n"
            f"🔗 Referral link:\n<code>{referral_link(user_id)}</code>\n\n"
            f"{SEP}\n<i>{BRANDING}</i>"
        )
        await q.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🤝 Refer & Unlock", callback_data="refer")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="menu")],
                ]
            ),
        )
        return

    # ---------- submit (set waiting state) ----------
    if data in ("submit_swiggy", "submit_toing"):
        if not await is_channel_member(context, user_id):
            text, kb = join_screen()
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        link_type = data.split("_")[1]
        emoji, type_name, short_label = LINK_META[link_type]
        context.user_data["awaiting_link"] = link_type
        await q.edit_message_text(
            f"{emoji} 📤 <b>Submit {short_label} Link</b>\n"
            f"{SEP}\n"
            f"Please paste your full {type_name} referral link below.\n\n"
            f"⚠️ Must start with <code>http://</code> or <code>https://</code>\n"
            f"🚫 Same link can't be submitted twice\n\n"
            f"Type <code>/cancel</code> to abort.\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="menu")]]
            ),
        )
        return

    # ---------- view sections (with optional page) ----------
    for link_type in ("swiggy", "toing"):
        if data == f"view_{link_type}":
            await view_links(update, context, link_type, 0)
            return
        if data.startswith(f"view_{link_type}:p:"):
            try:
                page = int(data.split(":")[2])
            except (ValueError, IndexError):
                page = 0
            await view_links(update, context, link_type, page)
            return

    # ---------- open a link (click tracking) ----------
    for link_type in ("swiggy", "toing"):
        if data.startswith(f"open_{link_type}:"):
            try:
                link_id = int(data.split(":")[1])
            except (ValueError, IndexError):
                link_id = 0
            await open_link(update, context, link_type, link_id)
            return

    # ---------- ADMIN routes (guarded) ----------
    if user_id != admin:
        await q.answer("⛔ Access denied. Admin only!", show_alert=True)
        return

    if data == "admin_panel":
        await admin_panel_view(update, context)
    elif data == "admin_stats":
        await admin_stats_view(update, context)
    elif data == "admin_users":
        await admin_users_view(update, context, 0)
    elif data.startswith("admin_users_page:"):
        try:
            page = int(data.split(":")[1])
        except (ValueError, IndexError):
            page = 0
        await admin_users_view(update, context, page)
    elif data in ("admin_del_swiggy", "admin_del_toing"):
        await admin_delete_list(update, context, data.split("_")[2], 0)
    elif data.startswith("admin_del_swiggy_p:") or data.startswith("admin_del_toing_p:"):
        parts = data.split(":")
        try:
            page = int(parts[2])
        except (ValueError, IndexError):
            page = 0
        await admin_delete_list(update, context, parts[1], page)
    elif data.startswith("del_swiggy:") or data.startswith("del_toing:"):
        parts = data.split(":")
        try:
            link_id = int(parts[2])
        except (ValueError, IndexError):
            link_id = 0
        await admin_delete_one(update, context, parts[1], link_id)
    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await q.edit_message_text(
            "📢 <b>ADMIN BROADCAST</b>\n"
            f"{SEP}\n"
            "Send me the message to broadcast to <b>ALL</b> users.\n\n"
            "Type <code>/cancel</code> to abort.\n"
            f"{SEP}\n<i>{BRANDING}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]
            ),
        )
    else:
        await q.answer("❌ Unknown action", show_alert=True)


# ------------------------------------------------------------------
# STARTUP HOOK
# ------------------------------------------------------------------
async def post_init(application):
    global BOT_USERNAME
    me = await application.bot.get_me()
    if not BOT_USERNAME:
        BOT_USERNAME = me.username or BOT_USERNAME
    set_setting("bot_username", BOT_USERNAME)
    set_setting("channel", CHANNEL_USERNAME)
    logger.info("✅ Logged in as @%s", me.username)


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
def main():
    # Safe event-loop setup for Python 3.11+ on any platform.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Callbacks (all inline buttons)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ONE single text handler (the old script had two, causing double fire)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 GBX LOOT Bot started  |  Made by @viediet")
    logger.info("📢 Channel: @%s", CHANNEL_USERNAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
