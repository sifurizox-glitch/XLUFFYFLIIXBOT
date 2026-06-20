import asyncio
import json
import os
import logging
import uuid
import aiohttp
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, Awaitable
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command, CommandStart
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# 👇 IMPORTING YOUR AUTO-POST MODULE
from autopost import start_auto_poster, generate_random_card, get_local_bin_data

# ==========================================
# 1. CONFIGURATION & EXECUTIVE INFO
# ==========================================
BOT_TOKEN = "8962210629:AAFhB5oNooreoJRhIuG7Frc9kqRxpQ2NWHA" # ⚠️ Paste your actual bot token here
OWNER_ID = 8570832903
OWNER_USERNAME = "@theaadikoder"
EXECUTIVE_NAME = "Aditya Thakur"

USE_PYTHONANYWHERE_PROXY = True
redeem_cooldowns = {}
bg_tasks = set()

# ==========================================
# 2. ATOMIC JSON DATABASE ENGINE
# ==========================================
db_lock = asyncio.Lock()
FILES = {
    "users.json": {}, "stocks.json": [], "channels.json": [], "groups.json": [], "logs.json": [],
    "settings.json": {"auto_post_channel": None, "auto_post_interval": 120, "daily_limit": 50, "daily_post_count": 0, "last_post_date": "1970-01-01", "last_post_timestamp": 0}
}

def init_db():
    for filename, default_data in FILES.items():
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as f: json.dump(default_data, f)
        else:
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    if json.load(f) is None: raise json.JSONDecodeError("Null DB", "", 0)
            except json.JSONDecodeError:
                with open(filename, "w", encoding="utf-8") as f: json.dump(default_data, f)

async def read_db(filename: str):
    async with db_lock:
        try:
            with open(filename, "r", encoding="utf-8") as f: return json.load(f) or FILES.get(filename).copy()
        except: return FILES.get(filename).copy()

async def modify_db(filename: str, modifier_func: Callable):
    async with db_lock:
        try:
            with open(filename, "r", encoding="utf-8") as f: data = json.load(f) or FILES.get(filename).copy()
        except: data = FILES.get(filename).copy()
        updated_data = modifier_func(data)
        if updated_data is not None:
            with open(filename, "w", encoding="utf-8") as f: json.dump(updated_data, f, indent=4)
            return updated_data
        return None

async def log_action(action: str, user_id: int, details: str = ""):
    def add_log(logs):
        logs.append({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "action": action, "user_id": user_id, "details": details})
        return logs
    await modify_db("logs.json", add_log)

async def notify_owner(bot: Bot, message: str):
    try: await bot.send_message(chat_id=OWNER_ID, text=message)
    except: pass

async def ensure_user_registered(user_id: int, username: str, bot: Bot = None):
    uid_str = str(user_id)
    is_new = [False]
    safe_username = str(username).replace("_", "\\_") if username else "Unknown"

    def register_user(users):
        if uid_str not in users:
            users[uid_str] = {"username": safe_username, "role": "owner" if user_id == OWNER_ID else "user", "banned": False, "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "redeem_count": 0}
            is_new[0] = True
        else:
            if user_id == OWNER_ID: users[uid_str]["role"], users[uid_str]["banned"] = "owner", False
            if "join_date" not in users[uid_str]: users[uid_str]["join_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "redeem_count" not in users[uid_str]: users[uid_str]["redeem_count"] = 0
        return users

    await modify_db("users.json", register_user)
    if is_new[0] and bot and user_id != OWNER_ID: await notify_owner(bot, f"🔔 **System Alert**\n\n👤 **New User:** `{user_id}` (@{safe_username})")
    return is_new[0]

def parse_chat_id(cid: str):
    cid_str = str(cid).strip()
    if "t.me/" in cid_str: cid_str = "@" + cid_str.split("t.me/")[-1].split("/")[0]
    try: return int(cid_str)
    except ValueError:
        if not cid_str.startswith("@") and not cid_str.startswith("-100"): cid_str = "@" + cid_str
        return cid_str

# ==========================================
# 3. SECURITY MIDDLEWARE
# ==========================================
class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]], event: Message, data: Dict[str, Any]) -> Any:
        user_id = event.from_user.id
        bot: Bot = data['bot']
        await ensure_user_registered(user_id, event.from_user.username, bot)
        users = await read_db("users.json")

        if users.get(str(user_id), {}).get("banned", False):
            if isinstance(event, Message): await event.answer("🚫 **Access Revoked**")
            elif isinstance(event, CallbackQuery): await event.answer("🚫 Access Revoked", show_alert=True)
            return

        role = users.get(str(user_id), {}).get("role", "user")
        if user_id == OWNER_ID or role == "admin": return await handler(event, data)

        channels = await read_db("channels.json")
        not_joined = []
        for ch in channels:
            try:
                member = await bot.get_chat_member(parse_chat_id(ch['channel_id']), user_id)
                if member.status in ['left', 'kicked', 'banned']: not_joined.append(ch['link'])
            except TelegramBadRequest: pass

        if not_joined:
            text = "🛑 **Verification Required**\n\n> To maintain platform security, you must join our verified channels:\n"
            for link in not_joined: text += f"\n🔗 {link}"
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Verify Access", callback_data="verify_join")]])
            if isinstance(event, Message): await event.answer(text, disable_web_page_preview=True, reply_markup=markup)
            elif isinstance(event, CallbackQuery):
                await event.message.answer(text, disable_web_page_preview=True, reply_markup=markup)
                await event.answer("Verification required!", show_alert=True)
            return
        return await handler(event, data)

dp = Dispatcher()
dp.message.middleware(ForceJoinMiddleware())
dp.callback_query.middleware(ForceJoinMiddleware())

# ==========================================
# 4. USER DASHBOARD UI
# ==========================================
def get_user_dashboard_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Redeem Center", callback_data="user_redeem_center")],
        [InlineKeyboardButton(text="👤 My Profile", callback_data="user_profile"), InlineKeyboardButton(text="📊 Statistics", callback_data="user_stats")],
        [InlineKeyboardButton(text="📢 Official Links", callback_data="user_links"), InlineKeyboardButton(text="💬 Support", callback_data="user_support")]
    ])

@dp.message(CommandStart())
async def start_cmd(message: Message):
    text = f"✦ **Welcome to the Premium Management Platform** ✦\n\n> 🛡️ **Secured & Managed by:** {OWNER_USERNAME}\n> ⚡ **System Status:** Online & Optimized\n\nSelect an option below to access your dashboard services:"
    await message.answer(text, reply_markup=get_user_dashboard_kb())

@dp.callback_query(F.data == "verify_join")
async def verify_join_callback(call: CallbackQuery):
    await call.answer("Verifying your status...", show_alert=False)
    await call.message.delete()

@dp.callback_query(F.data == "user_home")
async def return_home(call: CallbackQuery):
    text = f"✦ **Premium Management Platform** ✦\n\n> 🛡️ **Secured & Managed by:** {OWNER_USERNAME}\n\nSelect an option below to access your dashboard services:"
    await call.message.edit_text(text, reply_markup=get_user_dashboard_kb())

@dp.callback_query(F.data == "user_profile")
async def user_profile(call: CallbackQuery):
    users = await read_db("users.json")
    user_data = users.get(str(call.from_user.id), {})
    text = f"👤 **User Profile Dashboard**\n\n**ID:** `{call.from_user.id}`\n**Username:** @{user_data.get('username', 'N/A')}\n**Role Level:** `{str(user_data.get('role', 'user')).upper()}`\n**Account Created:** `{user_data.get('join_date', 'Unknown')}`\n**Items Redeemed:** `{user_data.get('redeem_count', 0)}`"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="user_home")]]))

@dp.callback_query(F.data == "user_stats")
async def user_public_stats(call: CallbackQuery):
    stocks = await read_db("stocks.json")
    available_stocks = [s for s in stocks if not s.get("redeemed")]
    text = f"📊 **Platform Metrics**\n\n> Total Active Items: `{len(available_stocks):,}`\n> System Uptime: `99.9%`\n> Architecture: `Premium SaaS Engine`"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="user_home")]]))

@dp.callback_query(F.data == "user_links")
async def user_links(call: CallbackQuery):
    channels = await read_db("channels.json")
    text = "📢 **Official Platform Network**\n\n"
    for ch in channels: text += f"🔹 {ch['link']}\n"
    if not channels: text += "> No active networks currently published."
    await call.message.edit_text(text, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="user_home")]]))

@dp.callback_query(F.data == "user_support")
async def user_support(call: CallbackQuery):
    text = f"💬 **Support Center**\n\n> For technical issues, stock inquiries, or business propositions, please contact the administrator.\n\n**Primary Contact:** {OWNER_USERNAME}"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="user_home")]]))

# ==========================================
# 5. SMART REDEEM SYSTEM
# ==========================================
@dp.callback_query(F.data == "user_redeem_center")
async def redeem_center_ui(call: CallbackQuery):
    stocks = await read_db("stocks.json")
    available_stocks = [s for s in stocks if not s.get("redeemed")]
    categories = {}
    for s in available_stocks: categories[s["category"].upper()] = categories.get(s["category"].upper(), 0) + 1

    kb_rows = []
    text = "🎁 **Smart Redeem Center**\n\n> Select an available category below to securely redeem an item.\n\n"
    if not categories: text = "🎁 **Smart Redeem Center**\n\n❌ All stocks are currently depleted. Please check back later."
    else:
        cat_items = list(categories.items())
        for i in range(0, len(cat_items), 2):
            row = []
            row.append(InlineKeyboardButton(text=f"{cat_items[i][0]} ({cat_items[i][1]})", callback_data=f"ui_redeem_{cat_items[i][0]}"))
            if i + 1 < len(cat_items): row.append(InlineKeyboardButton(text=f"{cat_items[i+1][0]} ({cat_items[i+1][1]})", callback_data=f"ui_redeem_{cat_items[i+1][0]}"))
            kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="user_redeem_center")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="user_home")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@dp.callback_query(F.data.startswith("ui_redeem_"))
async def process_ui_redeem(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id
    if user_id in redeem_cooldowns and datetime.now() < redeem_cooldowns[user_id]: return await call.answer("⏳ Rate Limited: Please wait 3 seconds.", show_alert=True)
    redeem_cooldowns[user_id] = datetime.now() + timedelta(seconds=3)

    category = call.data.replace("ui_redeem_", "")
    found_stock = [None]

    def process_redemption(stocks):
        for stock in stocks:
            if stock["category"].upper() == category.upper() and not stock.get("redeemed"):
                stock["redeemed"], stock["redeemed_by"], found_stock[0] = True, user_id, stock
                break
        return stocks

    await modify_db("stocks.json", process_redemption)

    if found_stock[0]:
        def increment_user_stats(users):
            if str(user_id) in users: users[str(user_id)]["redeem_count"] = users[str(user_id)].get("redeem_count", 0) + 1
            return users
        await modify_db("users.json", increment_user_stats)
    else:
        await call.answer(f"❌ {category} is out of stock!", show_alert=True)
        return await redeem_center_ui(call)

    stock_data = found_stock[0]
    await log_action("ui_redeem", user_id, f"Redeemed {category}: {stock_data['id']}")
    try:
        await bot.send_message(chat_id=user_id, text=f"✅ **Secure Redemption Successful**\n\n> **Category:** `{stock_data['category']}`\n> **Item Data:** `{stock_data['item']}`\n\n⚠️ *Please secure this data immediately.*")
        await call.answer("✅ Item successfully sent to your direct messages!", show_alert=True)
    except Exception:
        await call.answer("❌ Error: Please ensure you have started the bot directly to receive DMs.", show_alert=True)
    await redeem_center_ui(call)

@dp.message(Command("redeem"))
async def redeem_stock_cmd(message: Message, bot: Bot):
    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Usage:** `/redeem [category]`")
    category, user_id = args[1], message.from_user.id
    if user_id in redeem_cooldowns and datetime.now() < redeem_cooldowns[user_id]: return await message.answer("⏳ **Rate Limited:** Wait 3 seconds.")
    redeem_cooldowns[user_id] = datetime.now() + timedelta(seconds=3)

    found_stock = [None]
    def process_redemption(stocks):
        for stock in stocks:
            if stock["category"].lower() == category.lower() and not stock.get("redeemed"):
                stock["redeemed"], stock["redeemed_by"], found_stock[0] = True, user_id, stock
                break
        return stocks

    await modify_db("stocks.json", process_redemption)

    if found_stock[0]:
        def increment_user_stats(users):
            if str(user_id) in users: users[str(user_id)]["redeem_count"] = users[str(user_id)].get("redeem_count", 0) + 1
            return users
        await modify_db("users.json", increment_user_stats)
    else: return await message.answer(f"❌ Sorry, `{category}` is currently out of stock.")

    stock_data = found_stock[0]
    await log_action("cmd_redeem", user_id, f"Redeemed {category}: {stock_data['id']}")
    await message.answer(f"✅ **Secure Redemption Successful**\n\n> **Category:** `{stock_data['category']}`\n> **Item Data:** `{stock_data['item']}`")

# ==========================================
# 6. OWNER CONTROL CENTER
# ==========================================
def get_owner_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Analytics Center", callback_data="panel_stats"), InlineKeyboardButton(text="📦 Advanced Stock", callback_data="panel_stock")],
        [InlineKeyboardButton(text="👥 User Management", callback_data="panel_users"), InlineKeyboardButton(text="🛡️ Admin Console", callback_data="panel_admins")],
        [InlineKeyboardButton(text="📢 Broadcast Hub", callback_data="panel_broadcast"), InlineKeyboardButton(text="📁 Database Export", callback_data="panel_export")],
        [InlineKeyboardButton(text="⚙️ Network & Settings", callback_data="panel_commands")]
    ])

def get_export_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Export Users", callback_data="export_users"), InlineKeyboardButton(text="📄 Export Admins", callback_data="export_admins")],
        [InlineKeyboardButton(text="📄 Export Channels", callback_data="export_channels"), InlineKeyboardButton(text="📄 Export Groups", callback_data="export_groups")],
        [InlineKeyboardButton(text="📈 Export Logs", callback_data="export_logs")],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])

@dp.message(Command("panel"))
async def owner_panel(message: Message):
    if message.from_user.id != OWNER_ID: return await message.answer("⛔ **403 Forbidden**")
    await message.answer(f"👑 **Enterprise Administration Dashboard**\n\n> **Executive:** {EXECUTIVE_NAME}\n> **Node Status:** `Active & Operational`\n\nSelect an interface below to manage the platform:", reply_markup=get_owner_keyboard())

@dp.callback_query(F.data == "back_to_panel")
async def back_to_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text(f"👑 **Enterprise Administration Dashboard**\n\n> **Executive:** {EXECUTIVE_NAME}\n> **Node Status:** `Active & Operational`\n\nSelect an interface below to manage the platform:", reply_markup=get_owner_keyboard())

@dp.callback_query(F.data == "panel_stats")
async def show_statistics(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    users, stocks = await read_db("users.json"), await read_db("stocks.json")
    channels, groups = await read_db("channels.json"), await read_db("groups.json")
    banned_users = sum(1 for u in users.values() if u.get("banned"))
    admin_count = sum(1 for u in users.values() if u.get("role") in ["admin", "owner"])
    available_stock = sum(1 for s in stocks if not s.get("redeemed"))
    redeemed_stock = sum(1 for s in stocks if s.get("redeemed"))
    daily_joins = sum(1 for u in users.values() if u.get("join_date", "").startswith(datetime.now().strftime("%Y-%m-%d")))

    stats_text = f"📊 **Real-Time Analytics Core**\n\n**User Matrix**\n> 👥 Total Users: `{len(users):,}`\n> 📈 Today's Growth: `+{daily_joins}`\n> 🛡️ Active Admins: `{admin_count}`\n> 🚫 Banned Entites: `{banned_users}`\n\n**Inventory Health**\n> 📦 Available Assets: `{available_stock:,}`\n> 🎟️ Total Redeemed: `{redeemed_stock:,}`\n\n**Network Graph**\n> 📢 Force Channels: `{len(channels)}`\n> 🌐 Linked Groups: `{len(groups)}`"
    await call.message.edit_text(stats_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh Engine", callback_data="panel_stats")], [InlineKeyboardButton(text="🔙 Back", callback_data="back_to_panel")]]))

@dp.callback_query(F.data == "panel_stock")
async def show_advanced_stock_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    stocks = await read_db("stocks.json")
    available_stocks = [s for s in stocks if not s.get("redeemed")]
    redeemed_stocks = [s for s in stocks if s.get("redeemed")]
    categories = {}
    for s in available_stocks: categories[s["category"].upper()] = categories.get(s["category"].upper(), 0) + 1

    text = f"📦 **Inventory Management System**\n\n**Global Stats:** `{len(available_stocks)}` Active | `{len(redeemed_stocks)}` Redeemed\n\n"
    if not categories: text += "> ⚠️ **Warning:** Master database is currently empty.\n> Use `/addstock` to initialize inventory."
    else:
        text += "**Category Distribution:**\n"
        for cat, count in categories.items(): text += f"{'🟢' if count > 10 else ('🟡' if count > 5 else '🔴')} **{cat}**: `{count}` units\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh", callback_data="panel_stock")], [InlineKeyboardButton(text="🔙 Back", callback_data="back_to_panel")]]))

@dp.callback_query(F.data == "panel_users")
async def show_user_management(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("👥 **User Administration Hub**\n\n> **Available Commands:**\n\n🔨 `/ban [User_ID]`\n🕊️ `/unban [User_ID]`\n\n_Tip: Export the User Database via the Export Center to view all User IDs._", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="back_to_panel")]]))

@dp.callback_query(F.data == "panel_admins")
async def show_admin_console(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    users = await read_db("users.json")
    admins = [uid for uid, data in users.items() if data.get("role") == "admin"]
    text = f"🛡️ **Security & Admin Console**\n\n> **Active Administrators:** `{len(admins)}`\n\n"
    for adm in admins: text += f"🔹 ID: `{adm}` (@{users.get(adm, {}).get('username', 'Unknown')})\n"
    text += "\n> **Hierarchy Controls:**\n➕ `/addadmin [ID]`\n➖ `/removeadmin [ID]`"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="back_to_panel")]]))

@dp.callback_query(F.data == "panel_broadcast")
async def show_broadcast_help(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("📢 **Super Broadcast Engine**\n\n**Execution Method:**\nReply to ANY media/text with `/broadcast` or type `/broadcast [Message]`\n\n**Network Controls:**\n➕ `/addchannel [ID] [Link]`\n➖ `/removechannel [ID]`\n➕ `/addgroup [ID]`\n➖ `/removegroup [ID]`", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="back_to_panel")]]))

@dp.callback_query(F.data == "panel_export")
async def show_export_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("📁 **Database Control Center**\n\n> Select a node below to generate a comprehensive local `.txt` dump.", reply_markup=get_export_keyboard())

@dp.callback_query(F.data.startswith("export_"))
async def handle_exports(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.answer("Compiling database node...", show_alert=False)
    action = call.data.split("_")[1]
    content, filename = "", f"DB_{action.capitalize()}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

    if action == "users":
        users = await read_db("users.json")
        content = "=== ENTERPRISE USER LEDGER ===\n\n"
        for uid, data in users.items(): content += f"ID: {uid} | User: {data.get('username')} | Role: {data.get('role')} | Banned: {data.get('banned')} | Joined: {data.get('join_date')} | Redeems: {data.get('redeem_count')}\n"
    elif action == "admins":
        users = await read_db("users.json")
        content = "=== SECURITY ADMINISTRATORS ===\n\n"
        for uid, data in users.items():
            if data.get("role") in ["admin", "owner"]: content += f"ID: {uid} | User: {data.get('username')} | Role: {data.get('role')}\n"
    elif action == "channels":
        channels = await read_db("channels.json")
        content = "=== SECURE NETWORK CHANNELS ===\n\n"
        for ch in channels: content += f"ID: {ch['channel_id']} | Link: {ch['link']}\n"
    elif action == "groups":
        groups = await read_db("groups.json")
        content = "=== BROADCAST GROUPS ===\n\n"
        for g in groups: content += f"Group ID: {g['group_id']}\n"
    elif action == "logs":
        logs = await read_db("logs.json")
        content = "=== SYSTEM ACTIVITY LOGS ===\n\n"
        for log in logs[-500:]: content += f"[{log['timestamp']}] User: {log['user_id']} | Action: {log['action']} | Details: {log['details']}\n"

    if not content.strip() or "===" not in content: content = "No data records found in this node."
    await call.message.answer_document(document=BufferedInputFile(content.encode('utf-8'), filename=filename), caption=f"✅ **Database Export Compiled:** `{filename}`")

# ==========================================
# 7. CLEAN ADMIN SETTINGS PANEL UI
# ==========================================
@dp.callback_query(F.data == "panel_commands")
async def show_commands(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    settings = await read_db("settings.json")

    interval_secs = settings.get('auto_post_interval', 120)
    if interval_secs >= 3600 and interval_secs % 3600 == 0: interval_display = f"{int(interval_secs/3600)} Hour(s)"
    else: interval_display = f"{int(interval_secs/60)} Minute(s)"

    text = (
        "⚙️ **System Architecture & Settings**\n\n"
        "🤖 **Auto-Post Settings:**\n"
        f"> Current Channel: `{settings.get('auto_post_channel', 'None')}`\n"
        f"> Post Interval: `{interval_display}`\n"
        f"> Daily Limit: `{settings.get('daily_limit', 50)} Posts/Day`\n\n"
        "**Modify Commands:**\n"
        "➕ `/setautochannel [ID / @username]`\n"
        "🕒 `/setinterval [Time]` _(e.g., 30m or 2h)_\n"
        "➕ `/setautolimit [Number]`\n"
        "🛠️ `/testpost` - Send a test card & check cooldown"
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="back_to_panel")]]))

# ==========================================
# 8. ADMIN COMMANDS (Ban, Broadcast, Setup)
# ==========================================
@dp.message(Command("addadmin"))
async def add_admin(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/addadmin [User ID]`")

    def promote_user(users):
        if args[1] in users:
            users[args[1]]["role"] = "admin"
            return users
        return None
    if await modify_db("users.json", promote_user): await message.answer(f"✅ User `{args[1]}` is now an Admin.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("removeadmin"))
async def remove_admin(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/removeadmin [User ID]`")

    def demote_user(users):
        if args[1] in users:
            users[args[1]]["role"] = "user"
            return users
        return None
    if await modify_db("users.json", demote_user): await message.answer(f"⬇️ User `{args[1]}` demoted.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("addgroup"))
async def add_group(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax Error:** `/addgroup [Group ID]`")
    def append_group(groups):
        groups.append({"group_id": args[1]})
        return groups
    await modify_db("groups.json", append_group)
    await message.answer(f"✅ **Network Updated:** Group `{args[1]}` linked.")

@dp.message(Command("removegroup"))
async def remove_group(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax Error:** `/removegroup [Group ID]`")
    def drop_group(groups): return [g for g in groups if str(g["group_id"]) != args[1]]
    await modify_db("groups.json", drop_group)
    await message.answer(f"🗑️ Group `{args[1]}` unlinked.")

@dp.message(Command("addchannel"))
async def add_force_channel(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 3: return await message.answer("⚠️ **Syntax Error:** `/addchannel [Channel ID] [Invite Link]`")
    def append_channel(channels):
        channels.append({"channel_id": args[1], "link": args[2]})
        return channels
    await modify_db("channels.json", append_channel)
    await message.answer(f"✅ Force channel linked.\n> ID: `{args[1]}`\n> Link: {args[2]}")

@dp.message(Command("removechannel"))
async def remove_force_channel(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax Error:** `/removechannel [Channel ID]`")
    def drop_channel(channels): return [ch for ch in channels if str(ch["channel_id"]) != args[1]]
    await modify_db("channels.json", drop_channel)
    await message.answer(f"🗑️ Channel `{args[1]}` unlinked.")

@dp.message(Command("ban"))
async def ban_user(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/ban [User ID]`")
    def restrict_user(users):
        if args[1] in users:
            users[args[1]]["banned"] = True
            return users
        return None
    if await modify_db("users.json", restrict_user): await message.answer(f"🔨 `{args[1]}` banned globally.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("unban"))
async def unban_user(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/unban [User ID]`")
    def restore_user(users):
        if args[1] in users:
            users[args[1]]["banned"] = False
            return users
        return None
    if await modify_db("users.json", restore_user): await message.answer(f"🕊️ `{args[1]}` restored.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("broadcast"))
async def broadcast_message(message: Message, bot: Bot):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return

    broadcast_text = message.text.replace("/broadcast", "").strip()
    if not broadcast_text and not message.reply_to_message: return await message.answer("⚠️ `/broadcast [Message]` or reply to media.")

    channels, groups = await read_db("channels.json"), await read_db("groups.json")
    all_targets = list(set(list(users.keys()) + [str(ch["channel_id"]) for ch in channels] + [str(g["group_id"]) for g in groups]))

    status_msg = await message.answer(f"🔄 **Initializing Super Broadcast** to `{len(all_targets)}` endpoints...")
    success, failed = 0, 0
    for target_id in all_targets:
        try:
            target = parse_chat_id(target_id)
            if message.reply_to_message: await message.reply_to_message.copy_to(chat_id=target)
            else: await bot.send_message(chat_id=target, text=broadcast_text)
            success += 1
        except Exception: failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(f"✅ **Broadcast Complete**\n\n> 🟢 Delivered: `{success}`\n> 🔴 Dropped: `{failed}`")

@dp.message(Command("addstock"))
async def add_stock(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return

    args = message.text.split(maxsplit=2)
    if len(args) < 3: return await message.answer("⚠️ `/addstock [Category] [Details]`")

    def insert_stock(stocks):
        stocks.append({"id": str(uuid.uuid4()), "category": args[1], "item": args[2], "redeemed": False, "redeemed_by": None, "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return stocks

    await modify_db("stocks.json", insert_stock)
    await message.answer(f"✅ **Inventory Updated:** New asset deployed to `{args[1]}`.")

# --- DYNAMIC AUTO-POST SETTINGS CONTROL ---
@dp.message(Command("setautochannel"))
async def cmd_set_auto_channel(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return

    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax:** `/setautochannel [Channel ID / @username]`")

    def update_channel(s):
        s["auto_post_channel"] = args[1]
        return s
    await modify_db("settings.json", update_channel)
    await message.answer(f"✅ **Auto-Post Channel Updated:** `{args[1]}`\n\n_Tip: Use `/testpost` to verify if the bot can send messages there._")

@dp.message(Command("setinterval"))
async def cmd_set_auto_interval(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return

    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax:** `/setinterval [Time]`\n\n**Examples:**\n`/setinterval 30m` *(30 Minutes)*\n`/setinterval 2h` *(2 Hours)*")

    time_str = args[1].lower()
    try:
        if time_str.endswith('m'):
            val = int(time_str[:-1])
            seconds = val * 60
            display = f"{val} Minutes"
        elif time_str.endswith('h'):
            val = int(time_str[:-1])
            seconds = val * 3600
            display = f"{val} Hours"
        else:
            return await message.answer("❌ Invalid format! Use 'm' for minutes or 'h' for hours (e.g., `10m` or `1h`).")

        if seconds < 60: return await message.answer("❌ Minimum allowed interval is 1 minute (`1m`).")

        def update_interval(s):
            s["auto_post_interval"] = seconds
            return s
        await modify_db("settings.json", update_interval)
        await message.answer(f"✅ **Auto-Post Interval Updated:** The bot will now post every `{display}`.")
    except ValueError:
        await message.answer("❌ Invalid number format.")

@dp.message(Command("setautolimit"))
async def cmd_set_auto_limit(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return

    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax:** `/setautolimit [Number]`\nExample: `/setautolimit 50`")

    try:
        limit = int(args[1])
        def update_limit(s):
            s["daily_limit"] = limit
            return s
        await modify_db("settings.json", update_limit)
        await message.answer(f"✅ **Auto-Post Daily Limit Updated:** Max `{limit}` posts per day.")
    except ValueError:
        await message.answer("❌ Error: Limit must be a whole number.")

@dp.message(Command("testpost"))
async def cmd_test_post(message: Message, bot: Bot):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return

    settings = await read_db("settings.json")
    channel_id = settings.get("auto_post_channel")

    if not channel_id or channel_id == "None":
        return await message.answer("❌ **No Channel Set:** Please set a channel first using `/setautochannel [ID]`")

    last_ts = settings.get("last_post_timestamp", 0)
    interval = settings.get("auto_post_interval", 120)
    current_ts = datetime.now().timestamp()
    remaining = int(interval - (current_ts - last_ts))

    if remaining > 0:
        if remaining > 3600: time_msg = f"⏳ Auto-Post Cooldown: `{int(remaining/3600)}h {int((remaining%3600)/60)}m` remaining."
        else: time_msg = f"⏳ Auto-Post Cooldown: `{int(remaining/60)}m {remaining%60}s` remaining."
    else:
        time_msg = "🟢 Auto-Post is Ready to trigger instantly."

    status_msg = await message.answer(f"🔄 **Testing Auto-Post...**\nSending a test card to `{channel_id}`\n\n{time_msg}")

    try:
        bin_str, card_format = generate_random_card()
        scheme, c_type, bank_name, country_name, flag = get_local_bin_data(bin_str)

        text = (
            f"🛠 **[TEST POST]** 🛠\n\n"
            f"[💎] Card ➜ `{card_format}`\n"
            f"━━━━━━━━━━━\n"
            f"[ﾒ] Info ➜ {scheme} - {c_type}\n"
            f"[ﾒ] Bank ➜ {bank_name}\n"
            f"[ﾒ] Country ➜ {country_name} {flag}"
        )

        target_chat = parse_chat_id(channel_id)
        await bot.send_message(chat_id=target_chat, text=text)
        await status_msg.edit_text(f"✅ **Test Successful!**\nThe message was successfully posted to your channel.\n\n{time_msg}")

    except TelegramBadRequest as e:
        await status_msg.edit_text(f"❌ **Test Failed!**\n\n**Telegram Error:** `{e}`\n\n_Make sure the bot is an ADMIN in the channel and the ID is correct._")
    except Exception as e:
        await status_msg.edit_text(f"❌ **Test Failed!**\n\n**System Error:** `{e}`")

# ==========================================
# 10. KEEP-ALIVE WEB SERVER
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def run_dummy_server():
    if USE_PYTHONANYWHERE_PROXY:
        logging.info("🌐 Keep-alive server is disabled on PythonAnywhere to prevent port crash.")
        return
    try:
        app = web.Application()
        app.router.add_get('/', handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logging.info(f"🌐 Keep-alive web server running on port {port}")
    except Exception as e:
        logging.error(f"🌐 Dummy Server Skipped: {e}")

# ==========================================
# 11. ENGINE IGNITION (WITH ANTI-CRASH)
# ==========================================
class PythonAnywhereSession(AiohttpSession):
    async def create_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(trust_env=True)

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    init_db()
    logging.info("✅ Database Multi-Node Architecture Initialized")

    await ensure_user_registered(OWNER_ID, OWNER_USERNAME.replace("@", ""), None)

    if USE_PYTHONANYWHERE_PROXY:
        os.environ["http_proxy"] = "http://proxy.server:3128"
        os.environ["https_proxy"] = "http://proxy.server:3128"
        os.environ["HTTP_PROXY"] = "http://proxy.server:3128"
        os.environ["HTTPS_PROXY"] = "http://proxy.server:3128"
        session = PythonAnywhereSession()
        print("🔄 Connecting via PythonAnywhere Proxy...")
    else:
        session = AiohttpSession()
        print("🔄 Connecting directly (Render/VPS mode)...")

    bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="Markdown"))

    # 🌐 Launch Background Web Server securely
    task1 = asyncio.create_task(run_dummy_server())
    bg_tasks.add(task1)
    task1.add_done_callback(bg_tasks.discard)

    # 🌟 Launch Smart Auto-Poster securely from autopost.py!
    task2 = asyncio.create_task(start_auto_poster(bot, read_db, modify_db, parse_chat_id, notify_owner))
    bg_tasks.add(task2)
    task2.add_done_callback(bg_tasks.discard)

    print(f"🚀 Premium Enterprise Engine Online! Executive Access: {OWNER_USERNAME}")
    await notify_owner(bot, "🟢 **System Online:** Platform Reboot Successful.")

    # 🛡️ INFINITE ANTI-CRASH RESURRECTION LOOP
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"⚠️ Proxy/Network Error (503): {e}")
            logging.info("🔄 Auto-Reconnecting in 5 seconds to bypass proxy limits...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Manual Termination Sequence Initiated.")