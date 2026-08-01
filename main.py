import asyncio
import html
import logging

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

import config
from database import Database
from swiggy_api import SwiggyClient, extract_campaign_id, parse_amount, parse_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("buzzbot")

db = Database()

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
    return user_id in config.ADMIN_IDS


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
            InlineKeyboardButton("🆘 Help", callback_data="btn_help"),
        ],
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


async def send_plain(update, text):
    cid = chat_id(update)
    if not cid:
        return None
    bot = update.get_bot()
    return await bot.send_message(cid, text, parse_mode=ParseMode.HTML)


async def start(update, context):
    if not tg_id(update):
        return
    text = (
        "<b>🤖 Swiggy Buzz Auto-Collector</b>\n\n"
        "Collect all your Swiggy Buzz rewards automatically — no manual clicking!\n\n"
        f"{config.BRAND}"
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
    await answer(
        update,
        "📱 <b>Login to Swiggy Buzz</b>\n\n"
        "Enter your phone number with country code.\n\n"
        "Example: <code>919876543210</code>\n\n"
        "Send /cancel to abort.",
    )
    return PHONE


async def phone_received(update, context):
    user_id = tg_id(update)
    if not user_id:
        return ConversationHandler.END
    phone = (update.message.text or "").strip()
    if not phone.isdigit() or not (10 <= len(phone) <= 13):
        await update.message.reply_text(
            "❌ Invalid phone number. Use digits only, e.g. <code>919876543210</code>",
            parse_mode=ParseMode.HTML,
        )
        return PHONE
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
        await update.message.reply_text(
            f"❌ OTP request failed:\n{html.escape(str(status.get('message', 'unknown error'))[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return PHONE
    login_sessions[user_id] = {"phone": phone, "client": client}
    await update.message.reply_text(
        "✅ <b>OTP sent!</b>\n\nEnter the 6-digit OTP you received on your phone:",
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
    try:
        data = await asyncio.to_thread(client.verify_otp, session["phone"], otp)
    except Exception as exc:
        await update.message.reply_text(
            f"❌ OTP verification failed: {html.escape(str(exc)[:200])}\n\nTry again:",
            parse_mode=ParseMode.HTML,
        )
        return OTP
    login_info = parse_session(data)
    if not login_info.get("token"):
        await update.message.reply_text("❌ Login failed. Check the OTP and try again.", parse_mode=ParseMode.HTML)
        return OTP
    account_id = db.add_account(
        user_id,
        session["phone"],
        client.device_id,
        client.swuid,
        login_info["token"],
        login_info["tid"],
        login_info["sid"],
        login_info["customer_id"],
    )
    login_sessions.pop(user_id, None)
    account = db.get_account(account_id)
    if not account:
        await update.message.reply_text("❌ Could not save account. Try again.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    await update.message.reply_text(
        f"✅ <b>Logged in as +{html.escape(session['phone'])}</b>\n\n🎁 Auto-collection started...",
        parse_mode=ParseMode.HTML,
    )
    start_collection(update, account)
    await update.message.reply_text("🔹 <b>Main Menu</b>\n\n" + config.BRAND, reply_markup=main_menu(update), parse_mode=ParseMode.HTML)
    return ConversationHandler.END


def start_collection(update, account):
    cid = chat_id(update)
    if not cid:
        return
    existing = collecting_tasks.get(cid)
    if existing and not existing.done():
        log.info("collection already running for chat %s", cid)
        return
    collecting_tasks[cid] = asyncio.create_task(run_collection(update, account))


def progress_text(done, total, earned, last_ok):
    bar_len = 12
    filled = min(bar_len, int(bar_len * done / max(total, 1)))
    bar = "🟩" * filled + "⬜" * (bar_len - filled)
    mark = "✅" if last_ok else "❌"
    return (
        f"🎁 <b>Collecting... [{done}/{total}]</b>\n"
        f"{bar}\n"
        f"💰 Earned: ₹{earned:.2f}\n"
        f"Last result: {mark}"
    )


def final_text(done, total, earned, account_total):
    return (
        f"✅ <b>Collection finished! [{done}/{total}]</b>\n\n"
        f"💰 This run: ₹{earned:.2f}\n"
        f"🏆 Account total: ₹{account_total:.2f}\n\n"
        f"{config.BRAND}"
    )


async def edit_progress(cid, update, text):
    msg_id = progress_messages.get(cid)
    if not msg_id:
        return
    bot = update.get_bot()
    try:
        await bot.edit_message_text(text, chat_id=cid, message_id=msg_id, parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "message is not modified" not in str(exc):
            log.warning("progress edit failed: %s", exc)
    except Exception as exc:
        log.warning("progress edit failed: %s", exc)


async def run_collection(update, account):
    cid = chat_id(update)
    client = SwiggyClient(device_id=account["device_id"], swuid=account["swuid"])
    links = db.get_all_links()
    total_new = 0.0
    done = 0
    last_ok = True
    try:
        if not links:
            await send_plain(update, "📭 No buzz links added yet. Ask an admin to add links first.")
            return
        first = await send_plain(update, "🎁 <b>Collecting... [0/0]</b>\n\nStarting...")
        if first is not None:
            progress_messages[cid] = first.message_id
        for index, link in enumerate(links, 1):
            row = db.get_account(account["id"])
            if not row:
                break
            if row["total_earned"] >= config.MAX_EARN_PER_ACCOUNT:
                await edit_progress(
                    cid,
                    update,
                    f"🏆 <b>Max limit ₹{config.MAX_EARN_PER_ACCOUNT:g} reached!</b>\n\n"
                    f"Total earned: ₹{row['total_earned']:.2f}\n\n{config.BRAND}",
                )
                return
            gained = 0.0
            try:
                result = await asyncio.to_thread(client.collect_campaign, row, link["campaign_id"], "web")
                gained += parse_amount(result)
                db.log(row["id"], link["id"], "open", parse_amount(result), "ok")
                last_ok = True
            except Exception as exc:
                db.log(row["id"], link["id"], "open", 0, "failed")
                last_ok = False
                log.warning("open failed for %s: %s", link["campaign_id"], exc)
            try:
                back = await asyncio.to_thread(client.buzz_back, row, link["campaign_id"])
                back_amt = parse_amount(back)
                gained += back_amt
                db.log(row["id"], link["id"], "buzz_back", back_amt, "ok")
            except Exception as exc:
                db.log(row["id"], link["id"], "buzz_back", 0, "failed")
                log.warning("buzz_back failed for %s: %s", link["campaign_id"], exc)
            db.add_earned(row["id"], gained)
            total_new += gained
            done += 1
            await edit_progress(cid, update, progress_text(done, len(links), total_new, last_ok))
            await asyncio.sleep(config.REQUEST_DELAY)
        row = db.get_account(account["id"])
        final_total = row["total_earned"] if row else total_new
        await edit_progress(cid, update, final_text(done, len(links), total_new, final_total))
    except Exception as exc:
        log.exception("collection crashed")
        try:
            await edit_progress(cid, update, f"❌ <b>Collection stopped:</b> {html.escape(str(exc)[:200])}")
        except Exception:
            pass
    finally:
        progress_messages.pop(cid, None)
        collecting_tasks.pop(cid, None)


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
    text = "👤 <b>Your Accounts</b>\n\n" + "\n".join(lines) + "\n\nTap to switch active account. 🗑️ removes it."
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


async def collect_menu(update):
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
        start_collection(update, account)
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
    lines = [
        f"📱 +{html.escape(a['phone'])} → ₹{a['total_earned']:.2f}{' 🟢' if a['active'] else ''}"
        for a in accounts
    ]
    text = (
        "📊 <b>Your Stats</b>\n\n"
        + "\n".join(lines)
        + f"\n\n💰 <b>Total earned: ₹{total:.2f}</b>\n\n{config.BRAND}"
    )
    await answer(update, text, main_menu(update))


async def help_menu(update):
    text = (
        "<b>🤖 How to use Swiggy Buzz Auto-Collector</b>\n\n"
        "1️⃣ Tap <b>🔐 Login Account</b>\n"
        "2️⃣ Enter your phone number with country code\n"
        "3️⃣ Enter the OTP you receive\n"
        "4️⃣ Buzz rewards are collected automatically\n\n"
        "💰 Opening a link + buzz-back = ₹2-10 per link\n"
        f"🏆 Max ₹{config.MAX_EARN_PER_ACCOUNT:g} per account\n\n"
        f"{config.BRAND}"
    )
    await answer(update, text, main_menu(update))


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
            await answer(update, "🔹 <b>Main Menu</b>\n\n" + config.BRAND, main_menu(update))
        elif data == "btn_accounts":
            await accounts_menu(update)
        elif data == "btn_collect":
            await collect_menu(update)
        elif data == "btn_stats":
            await stats_menu(update)
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
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

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
