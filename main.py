import asyncio
import json
import os
import logging
import uuid
import aiohttp
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command, CommandStart
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# ==========================================
# 1. CONFIGURATION & PERMANENT OWNER INFO
# ==========================================
BOT_TOKEN = "8962210629:AAFhB5oNooreoJRhIuG7Frc9kqRxpQ2NWHA" # Ensure your NEW token is here
OWNER_ID = 8570832903
OWNER_USERNAME = "@theaadikoder"

# Advanced System Memory (Anti-Spam & Rate Limiting)
redeem_cooldowns = {}

# ==========================================
# 2. JSON DATABASE ENGINE (Thread-Safe)
# ==========================================
db_lock = asyncio.Lock()

FILES = {
    "users.json": {},
    "stocks.json": [],
    "channels.json": [],
    "groups.json": [],
    "logs.json": []
}

def init_db():
    for filename, default_data in FILES.items():
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(default_data, f)

async def read_db(filename: str):
    async with db_lock:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

async def write_db(filename: str, data):
    async with db_lock:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

async def log_action(action: str, user_id: int, details: str = ""):
    logs = await read_db("logs.json")
    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "user_id": user_id,
        "details": details
    })
    await write_db("logs.json", logs)

async def notify_owner(bot: Bot, message: str):
    """Enterprise Notification Center: Alerts owner of critical events"""
    try:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=f"🔔 **System Alert**\n\n{message}"
        )
    except Exception as e:
        logging.error(f"Failed to notify owner: {e}")

async def ensure_user_registered(user_id: int, username: str, bot: Bot = None):
    users = await read_db("users.json")
    uid_str = str(user_id)
    is_new = False

    if uid_str not in users:
        users[uid_str] = {
            "username": username,
            "role": "owner" if user_id == OWNER_ID else "user",
            "banned": False,
            "join_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "redeem_count": 0
        }
        is_new = True
        if bot and user_id != OWNER_ID:
            await notify_owner(bot, f"👤 **New User Registration**\n\n**ID:** `{user_id}`\n**User:** @{username}")
    else:
        if user_id == OWNER_ID:
            users[uid_str]["role"] = "owner"
            users[uid_str]["banned"] = False

        if "join_date" not in users[uid_str]:
            users[uid_str]["join_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if "redeem_count" not in users[uid_str]:
            users[uid_str]["redeem_count"] = 0

    await write_db("users.json", users)
    return is_new

# ==========================================
# 3. SECURITY MIDDLEWARE (Mandatory Join)
# ==========================================
class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        bot: Bot = data['bot']

        await ensure_user_registered(user_id, event.from_user.username, bot)
        users = await read_db("users.json")

        if users.get(str(user_id), {}).get("banned", False):
            if isinstance(event, Message):
                await event.answer("🚫 **Access Revoked**\n\nYou are permanently banned from using this enterprise platform.")
            return

        role = users.get(str(user_id), {}).get("role", "user")
        if user_id == OWNER_ID or role == "admin":
            return await handler(event, data)

        channels = await read_db("channels.json")
        not_joined = []

        for ch in channels:
            try:
                member = await bot.get_chat_member(ch['channel_id'], user_id)
                if member.status in ['left', 'kicked', 'banned']:
                    not_joined.append(ch['link'])
            except TelegramBadRequest:
                pass

        if not_joined:
            text = (
                "🛑 **Verification Required**\n\n"
                "> To maintain platform security, you must join our verified channels before accessing the dashboard.\n\n"
                "**Required Channels:**"
            )
            for link in not_joined:
                text += f"\n🔗 {link}"

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Verify Access", callback_data="verify_join")]
            ])
            if isinstance(event, Message):
                await event.answer(text, disable_web_page_preview=True, reply_markup=markup)
            return

        return await handler(event, data)

# ==========================================
# 4. BOT INITIALIZATION
# ==========================================
dp = Dispatcher()
dp.message.middleware(ForceJoinMiddleware())
dp.callback_query.middleware(ForceJoinMiddleware())

# ==========================================
# 5. USER DASHBOARD UI & LOGIC
# ==========================================
def get_user_dashboard_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Redeem Center", callback_data="user_redeem_center")],
        [InlineKeyboardButton(text="👤 My Profile", callback_data="user_profile"),
         InlineKeyboardButton(text="📊 Statistics", callback_data="user_stats")],
        [InlineKeyboardButton(text="📢 Official Links", callback_data="user_links"),
         InlineKeyboardButton(text="💬 Support", callback_data="user_support")]
    ])

@dp.message(CommandStart())
async def start_cmd(message: Message):
    text = (
        f"✦ **Welcome to the Premium Management Platform** ✦\n\n"
        f"> 🛡️ **Secured & Managed by:** {OWNER_USERNAME}\n"
        f"> ⚡ **System Status:** Online & Optimized\n\n"
        f"Select an option below to access your dashboard services:"
    )
    await message.answer(text, reply_markup=get_user_dashboard_kb())

@dp.callback_query(F.data == "verify_join")
async def verify_join_callback(call: CallbackQuery):
    await call.answer("Verifying your status...", show_alert=False)
    await call.message.delete()

@dp.callback_query(F.data == "user_home")
async def return_home(call: CallbackQuery):
    text = (
        f"✦ **Premium Management Platform** ✦\n\n"
        f"> 🛡️ **Secured & Managed by:** {OWNER_USERNAME}\n\n"
        f"Select an option below to access your dashboard services:"
    )
    await call.message.edit_text(text, reply_markup=get_user_dashboard_kb())

@dp.callback_query(F.data == "user_profile")
async def user_profile(call: CallbackQuery):
    users = await read_db("users.json")
    user_data = users.get(str(call.from_user.id), {})

    text = (
        f"👤 **User Profile Dashboard**\n\n"
        f"**ID:** `{call.from_user.id}`\n"
        f"**Username:** @{user_data.get('username', 'N/A')}\n"
        f"**Role Level:** `{str(user_data.get('role', 'user')).upper()}`\n"
        f"**Account Created:** `{user_data.get('join_date', 'Unknown')}`\n"
        f"**Items Redeemed:** `{user_data.get('redeem_count', 0)}`"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="user_home")]])
    await call.message.edit_text(text, reply_markup=markup)

@dp.callback_query(F.data == "user_stats")
async def user_public_stats(call: CallbackQuery):
    stocks = await read_db("stocks.json")
    available_stocks = [s for s in stocks if not s["redeemed"]]

    text = (
        f"📊 **Platform Metrics**\n\n"
        f"> Total Active Items: `{len(available_stocks):,}`\n"
        f"> System Uptime: `99.9%`\n"
        f"> Architecture: `Premium SaaS Engine`"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="user_home")]])
    await call.message.edit_text(text, reply_markup=markup)

@dp.callback_query(F.data == "user_links")
async def user_links(call: CallbackQuery):
    channels = await read_db("channels.json")
    text = "📢 **Official Platform Network**\n\n"
    for ch in channels:
        text += f"🔹 {ch['link']}\n"
    if not channels:
        text += "> No active networks currently published."

    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="user_home")]])
    await call.message.edit_text(text, disable_web_page_preview=True, reply_markup=markup)

@dp.callback_query(F.data == "user_support")
async def user_support(call: CallbackQuery):
    text = (
        f"💬 **Support Center**\n\n"
        f"> For technical issues, stock inquiries, or business propositions, please contact the administrator.\n\n"
        f"**Primary Contact:** {OWNER_USERNAME}"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="user_home")]])
    await call.message.edit_text(text, reply_markup=markup)

# ==========================================
# 6. SMART REDEEM SYSTEM UI
# ==========================================
@dp.callback_query(F.data == "user_redeem_center")
async def redeem_center_ui(call: CallbackQuery):
    stocks = await read_db("stocks.json")
    available_stocks = [s for s in stocks if not s["redeemed"]]

    categories = {}
    for s in available_stocks:
        cat = s["category"].upper()
        categories[cat] = categories.get(cat, 0) + 1

    kb_rows = []
    text = "🎁 **Smart Redeem Center**\n\n> Select an available category below to securely redeem an item.\n\n"

    if not categories:
        text = "🎁 **Smart Redeem Center**\n\n❌ All stocks are currently depleted. Please check back later."
    else:
        cat_items = list(categories.items())
        for i in range(0, len(cat_items), 2):
            row = []
            cat1, count1 = cat_items[i]
            row.append(InlineKeyboardButton(text=f"{cat1} ({count1})", callback_data=f"ui_redeem_{cat1}"))
            if i + 1 < len(cat_items):
                cat2, count2 = cat_items[i+1]
                row.append(InlineKeyboardButton(text=f"{cat2} ({count2})", callback_data=f"ui_redeem_{cat2}"))
            kb_rows.append(row)

    kb_rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="user_redeem_center")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="user_home")])

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))

@dp.callback_query(F.data.startswith("ui_redeem_"))
async def process_ui_redeem(call: CallbackQuery, bot: Bot):
    user_id = call.from_user.id

    if user_id in redeem_cooldowns and datetime.now() < redeem_cooldowns[user_id]:
        return await call.answer("⏳ Rate Limited: Please wait a few seconds before trying again.", show_alert=True)
    redeem_cooldowns[user_id] = datetime.now() + timedelta(seconds=3)

    category = call.data.replace("ui_redeem_", "")

    async with db_lock:
        with open("stocks.json", "r", encoding="utf-8") as f:
            stocks = json.load(f)

        found_stock = None
        for stock in stocks:
            if stock["category"].upper() == category.upper() and not stock["redeemed"]:
                stock["redeemed"] = True
                stock["redeemed_by"] = user_id
                found_stock = stock
                break

        if found_stock:
            with open("stocks.json", "w", encoding="utf-8") as f:
                json.dump(stocks, f, indent=4)

            with open("users.json", "r", encoding="utf-8") as f:
                users = json.load(f)
            if str(user_id) in users:
                users[str(user_id)]["redeem_count"] = users[str(user_id)].get("redeem_count", 0) + 1
            with open("users.json", "w", encoding="utf-8") as f:
                json.dump(users, f, indent=4)

    if not found_stock:
        await call.answer(f"❌ {category} is out of stock or just grabbed by someone else!", show_alert=True)
        return await redeem_center_ui(call)

    await log_action("ui_redeem", user_id, f"Redeemed {category}: {found_stock['id']}")
    await notify_owner(bot, f"🎟️ **Stock Redeemed**\n\n**User ID:** `{user_id}`\n**Category:** `{category}`")

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ **Secure Redemption Successful**\n\n"
                f"> **Category:** `{found_stock['category']}`\n"
                f"> **Item Data:** `{found_stock['item']}`\n\n"
                f"⚠️ *Please secure this data immediately. For security purposes, it will not be displayed again.*"
            )
        )
        await call.answer("✅ Item successfully sent to your direct messages!", show_alert=True)
    except Exception:
        await call.answer("❌ Error: Please ensure you have started the bot directly to receive DMs.", show_alert=True)

    await redeem_center_ui(call)

# ==========================================
# 7. COMMAND REDEEM SYSTEM
# ==========================================
@dp.message(Command("redeem"))
async def redeem_stock_cmd(message: Message, bot: Bot):
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("⚠️ **Usage:** `/redeem [category]`\nExample: `/redeem Netflix`")

    category = args[1]
    user_id = message.from_user.id

    if user_id in redeem_cooldowns and datetime.now() < redeem_cooldowns[user_id]:
        return await message.answer("⏳ **Rate Limited:** Please wait 3 seconds before next request.")
    redeem_cooldowns[user_id] = datetime.now() + timedelta(seconds=3)

    async with db_lock:
        with open("stocks.json", "r", encoding="utf-8") as f:
            stocks = json.load(f)

        found_stock = None
        for stock in stocks:
            if stock["category"].lower() == category.lower() and not stock["redeemed"]:
                stock["redeemed"] = True
                stock["redeemed_by"] = user_id
                found_stock = stock
                break

        if found_stock:
            with open("stocks.json", "w", encoding="utf-8") as f:
                json.dump(stocks, f, indent=4)

            with open("users.json", "r", encoding="utf-8") as f:
                users = json.load(f)
            if str(user_id) in users:
                users[str(user_id)]["redeem_count"] = users[str(user_id)].get("redeem_count", 0) + 1
            with open("users.json", "w", encoding="utf-8") as f:
                json.dump(users, f, indent=4)

    if not found_stock:
        return await message.answer(f"❌ Sorry, `{category}` is currently out of stock.")

    await log_action("cmd_redeem", user_id, f"Redeemed {category}: {found_stock['id']}")
    await notify_owner(bot, f"🎟️ **Stock Redeemed**\n\n**User ID:** `{user_id}`\n**Category:** `{category}`")

    await message.answer(
        f"✅ **Secure Redemption Successful**\n\n"
        f"> **Category:** `{found_stock['category']}`\n"
        f"> **Item Data:** `{found_stock['item']}`\n\n"
        f"⚠️ *Please secure this data immediately.*"
    )

# ==========================================
# 8. OWNER CONTROL CENTER V2
# ==========================================
def get_owner_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Analytics Center", callback_data="panel_stats"),
         InlineKeyboardButton(text="📦 Advanced Stock", callback_data="panel_stock")],
        [InlineKeyboardButton(text="👥 User Management", callback_data="panel_users"),
         InlineKeyboardButton(text="🛡️ Admin Console", callback_data="panel_admins")],
        [InlineKeyboardButton(text="📢 Broadcast Hub", callback_data="panel_broadcast"),
         InlineKeyboardButton(text="📁 Database Export", callback_data="panel_export")],
        [InlineKeyboardButton(text="⚙️ Network & Settings", callback_data="panel_commands")]
    ])

def get_export_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Export Users (.txt)", callback_data="export_users"),
         InlineKeyboardButton(text="📄 Export Admins (.txt)", callback_data="export_admins")],
        [InlineKeyboardButton(text="📄 Export Channels", callback_data="export_channels"),
         InlineKeyboardButton(text="📄 Export Groups", callback_data="export_groups")],
        [InlineKeyboardButton(text="📈 Export Logs", callback_data="export_logs")],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])

@dp.message(Command("panel"))
async def owner_panel(message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.answer("⛔ **403 Forbidden:** Administrative credentials required.")

    text = (
        f"👑 **Enterprise Administration Dashboard**\n\n"
        f"> **Executive:** {OWNER_USERNAME}\n"
        f"> **Node Status:** `Active & Operational`\n"
        f"> **Engine:** `Advanced JSON DB`\n\n"
        f"Select an interface below to manage the platform:"
    )
    await message.answer(text, reply_markup=get_owner_keyboard())

@dp.callback_query(F.data == "back_to_panel")
async def back_to_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    text = (
        f"👑 **Enterprise Administration Dashboard**\n\n"
        f"> **Executive:** {OWNER_USERNAME}\n"
        f"> **Node Status:** `Active & Operational`\n\n"
        f"Select an interface below to manage the platform:"
    )
    await call.message.edit_text(text, reply_markup=get_owner_keyboard())

# --- ADVANCED ANALYTICS CENTER ---
@dp.callback_query(F.data == "panel_stats")
async def show_statistics(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return

    users = await read_db("users.json")
    stocks = await read_db("stocks.json")
    channels = await read_db("channels.json")
    groups = await read_db("groups.json")

    total_users = len(users)
    banned_users = sum(1 for u in users.values() if u.get("banned"))
    admin_count = sum(1 for u in users.values() if u.get("role") in ["admin", "owner"])

    available_stock = sum(1 for s in stocks if not s.get("redeemed"))
    redeemed_stock = sum(1 for s in stocks if s.get("redeemed"))

    today = datetime.now().strftime("%Y-%m-%d")
    daily_joins = sum(1 for u in users.values() if u.get("join_date", "").startswith(today))

    stats_text = (
        "📊 **Real-Time Analytics Core**\n\n"
        f"**User Matrix**\n"
        f"> 👥 Total Users: `{total_users:,}`\n"
        f"> 📈 Today's Growth: `+{daily_joins}`\n"
        f"> 🛡️ Active Admins: `{admin_count}`\n"
        f"> 🚫 Banned Entites: `{banned_users}`\n\n"
        f"**Inventory Health**\n"
        f"> 📦 Available Assets: `{available_stock:,}`\n"
        f"> 🎟️ Total Redeemed: `{redeemed_stock:,}`\n\n"
        f"**Network Graph**\n"
        f"> 📢 Force Channels: `{len(channels)}`\n"
        f"> 🌐 Linked Groups: `{len(groups)}`"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh Engine", callback_data="panel_stats")],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])
    await call.message.edit_text(stats_text, reply_markup=markup)

# --- ADVANCED STOCK MANAGEMENT ---
@dp.callback_query(F.data == "panel_stock")
async def show_advanced_stock_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return

    stocks = await read_db("stocks.json")
    available_stocks = [s for s in stocks if not s["redeemed"]]
    redeemed_stocks = [s for s in stocks if s["redeemed"]]

    categories = {}
    for s in available_stocks:
        cat = s["category"].upper()
        categories[cat] = categories.get(cat, 0) + 1

    text = "📦 **Inventory Management System**\n\n"
    text += f"**Global Stats:** `{len(available_stocks)}` Active | `{len(redeemed_stocks)}` Redeemed\n\n"

    if not categories:
        text += "> ⚠️ **Warning:** Master database is currently empty.\n> Use `/addstock` to initialize inventory."
    else:
        text += "**Category Distribution:**\n"
        for cat, count in categories.items():
            indicator = "🟢" if count > 10 else ("🟡" if count > 5 else "🔴")
            text += f"{indicator} **{cat}**: `{count}` units\n"

        text += "\n_Tip: Use `/addstock [Category] [Data]` to add inventory._"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh Inventory", callback_data="panel_stock")],
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])
    await call.message.edit_text(text, reply_markup=markup)

# --- USER MANAGEMENT HUB ---
@dp.callback_query(F.data == "panel_users")
async def show_user_management(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return

    text = (
        "👥 **User Administration Hub**\n\n"
        "> **Available Commands:**\n\n"
        "🔨 `/ban [User_ID]` - Terminate access\n"
        "🕊️ `/unban [User_ID]` - Restore access\n\n"
        "_Tip: Export the User Database via the Export Center to view all User IDs and status metrics._"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])
    await call.message.edit_text(text, reply_markup=markup)

# --- ADMIN CONSOLE ---
@dp.callback_query(F.data == "panel_admins")
async def show_admin_console(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return

    users = await read_db("users.json")
    admins = [uid for uid, data in users.items() if data.get("role") == "admin"]

    text = "🛡️ **Security & Admin Console**\n\n"
    text += f"> **Active Administrators:** `{len(admins)}`\n\n"

    if admins:
        for adm in admins:
            text += f"🔹 ID: `{adm}` (@{users[adm].get('username', 'Unknown')})\n"
    else:
        text += "No active administrators assigned.\n"

    text += (
        "\n> **Hierarchy Controls:**\n"
        "➕ `/addadmin [ID]` - Grant elevated access\n"
        "➖ `/removeadmin [ID]` - Revoke access\n"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])
    await call.message.edit_text(text, reply_markup=markup)

# --- BROADCAST HUB V2 ---
@dp.callback_query(F.data == "panel_broadcast")
async def show_broadcast_help(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return

    text = (
        "📢 **Super Broadcast Engine**\n\n"
        "> Execute mass-distribution protocols across all connected nodes.\n\n"
        "**Execution Method:**\n"
        "Reply to ANY media/text with `/broadcast` or type `/broadcast [Message]`\n"
        "_(Payload targets: Users, Channels, & Groups)_\n\n"
        "**Network Controls:**\n"
        "➕ `/addchannel [ID] [Link]`\n"
        "➖ `/removechannel [ID]`\n"
        "➕ `/addgroup [ID]`\n"
        "➖ `/removegroup [ID]`\n"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])
    await call.message.edit_text(text, reply_markup=markup)

# --- DATABASE CENTER (EXPORT) ---
@dp.callback_query(F.data == "panel_export")
async def show_export_panel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    text = (
        "📁 **Database Control Center**\n\n"
        "> Select a node below to generate a comprehensive local `.txt` dump of the requested ledger."
    )
    await call.message.edit_text(text, reply_markup=get_export_keyboard())

@dp.callback_query(F.data.startswith("export_"))
async def handle_exports(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.answer("Compiling database node...", show_alert=False)

    action = call.data.split("_")[1]
    content = ""
    filename = ""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    if action == "users":
        users = await read_db("users.json")
        content = "=== ENTERPRISE USER LEDGER ===\n\n"
        for uid, data in users.items():
            content += f"ID: {uid} | User: {data.get('username')} | Role: {data.get('role')} | Banned: {data.get('banned')} | Joined: {data.get('join_date')} | Redeems: {data.get('redeem_count')}\n"
        filename = f"DB_Users_{timestamp}.txt"

    elif action == "admins":
        users = await read_db("users.json")
        content = "=== SECURITY ADMINISTRATORS ===\n\n"
        for uid, data in users.items():
            if data.get("role") in ["admin", "owner"]:
                content += f"ID: {uid} | User: {data.get('username')} | Role: {data.get('role')}\n"
        filename = f"DB_Admins_{timestamp}.txt"

    elif action == "channels":
        channels = await read_db("channels.json")
        content = "=== SECURE NETWORK CHANNELS ===\n\n"
        for ch in channels:
            content += f"ID: {ch['channel_id']} | Link: {ch['link']}\n"
        filename = f"DB_Channels_{timestamp}.txt"

    elif action == "groups":
        groups = await read_db("groups.json")
        content = "=== BROADCAST GROUPS ===\n\n"
        for g in groups:
            content += f"Group ID: {g['group_id']}\n"
        filename = f"DB_Groups_{timestamp}.txt"

    elif action == "logs":
        logs = await read_db("logs.json")
        content = "=== SYSTEM ACTIVITY LOGS ===\n\n"
        for log in logs[-500:]:
            content += f"[{log['timestamp']}] User: {log['user_id']} | Action: {log['action']} | Details: {log['details']}\n"
        filename = f"DB_Logs_{timestamp}.txt"

    if not content.strip() or "===" not in content:
        content = "No data records found in this node."

    file = BufferedInputFile(content.encode('utf-8'), filename=filename)
    await call.message.answer_document(document=file, caption=f"✅ **Database Export Compiled:** `{filename}`")

# --- NETWORK & SETTINGS ---
@dp.callback_query(F.data == "panel_commands")
async def show_commands(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    text = (
        "⚙️ **System Architecture & Settings**\n\n"
        "**Core Configuration**\n"
        "> Platform: Aiogram v3 Async\n"
        "> UI State: Advanced Buttons\n"
        "> DB Engine: JSON Multi-Lock\n\n"
        "All configuration modifications must be done via direct repository injection."
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="back_to_panel")]
    ])
    await call.message.edit_text(text, reply_markup=markup)

# ==========================================
# 9. HANDLERS: ADMIN, GROUPS & CHANNELS
# ==========================================
@dp.message(Command("addadmin"))
async def add_admin(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit():
        return await message.answer("⚠️ **Syntax Error:** `/addadmin [User ID Number]`")

    target_id = args[1]
    users = await read_db("users.json")
    if target_id in users:
        users[target_id]["role"] = "admin"
        await write_db("users.json", users)
        await message.answer(f"✅ **Privilege Escalation:** User `{target_id}` is now an Admin.")
        await notify_owner(bot, f"🛡️ **Admin Added:** `{target_id}`")
    else:
        await message.answer("❌ **Error:** Identity not found. User must initialize the platform first.")

@dp.message(Command("removeadmin"))
async def remove_admin(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit():
        return await message.answer("⚠️ **Syntax Error:** `/removeadmin [User ID Number]`")

    target_id = args[1]
    users = await read_db("users.json")
    if target_id in users:
        users[target_id]["role"] = "user"
        await write_db("users.json", users)
        await message.answer(f"⬇️ **Privilege Revoked:** User `{target_id}` demoted.")
        await notify_owner(bot, f"🛡️ **Admin Removed:** `{target_id}`")
    else:
        await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("addgroup"))
async def add_group(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("⚠️ **Syntax Error:** `/addgroup [Group ID]`")

    group_id = args[1]
    groups = await read_db("groups.json")
    groups.append({"group_id": group_id})
    await write_db("groups.json", groups)
    await message.answer(f"✅ **Network Updated:** Group `{group_id}` linked to broadcast engine.")

@dp.message(Command("removegroup"))
async def remove_group(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("⚠️ **Syntax Error:** `/removegroup [Group ID]`")

    group_id = args[1]
    groups = await read_db("groups.json")
    new_groups = [g for g in groups if str(g["group_id"]) != group_id]
    await write_db("groups.json", new_groups)
    await message.answer(f"🗑️ **Network Updated:** Group `{group_id}` unlinked.")

@dp.message(Command("addchannel"))
async def add_force_channel(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 3:
        return await message.answer("⚠️ **Syntax Error:** `/addchannel [Channel ID] [Invite Link]`")

    channel_id = args[1]
    link = args[2]
    channels = await read_db("channels.json")
    channels.append({"channel_id": channel_id, "link": link})
    await write_db("channels.json", channels)
    await message.answer(f"✅ **Security Updated:** Force channel linked.\n> ID: `{channel_id}`\n> Link: {link}")

@dp.message(Command("removechannel"))
async def remove_force_channel(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2:
        return await message.answer("⚠️ **Syntax Error:** `/removechannel [Channel ID]`")

    channel_id = args[1]
    channels = await read_db("channels.json")
    new_channels = [ch for ch in channels if str(ch["channel_id"]) != channel_id]
    await write_db("channels.json", new_channels)
    await message.answer(f"🗑️ **Security Updated:** Channel `{channel_id}` unlinked from force requirements.")

@dp.message(Command("ban"))
async def ban_user(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit():
        return await message.answer("⚠️ **Syntax Error:** `/ban [User ID Number]`")

    target_id = args[1]
    users = await read_db("users.json")
    if target_id in users:
        users[target_id]["banned"] = True
        await write_db("users.json", users)
        await message.answer(f"🔨 **Protocol Executed:** Entity `{target_id}` banned globally.")
        await notify_owner(bot, f"🔨 **User Banned:** `{target_id}`")
    else:
        await message.answer("❌ **Error:** Identity not found in master node.")

@dp.message(Command("unban"))
async def unban_user(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit():
        return await message.answer("⚠️ **Syntax Error:** `/unban [User ID Number]`")

    target_id = args[1]
    users = await read_db("users.json")
    if target_id in users:
        users[target_id]["banned"] = False
        await write_db("users.json", users)
        await message.answer(f"🕊️ **Protocol Executed:** Entity `{target_id}` restored.")
        await notify_owner(bot, f"🕊️ **User Unbanned:** `{target_id}`")
    else:
        await message.answer("❌ **Error:** Identity not found.")

# ==========================================
# 10. ADVANCED BROADCAST ENGINE
# ==========================================
@dp.message(Command("broadcast"))
async def broadcast_message(message: Message, bot: Bot):
    users = await read_db("users.json")
    user_role = users.get(str(message.from_user.id), {}).get("role", "user")
    if message.from_user.id != OWNER_ID and user_role != "admin": return

    broadcast_text = message.text.replace("/broadcast", "").strip()
    if not broadcast_text and not message.reply_to_message:
        return await message.answer("⚠️ **Syntax Error:** `/broadcast [Message]` or reply to media.")

    channels = await read_db("channels.json")
    groups = await read_db("groups.json")

    user_ids = list(users.keys())
    channel_ids = [str(ch["channel_id"]) for ch in channels]
    group_ids = [str(g["group_id"]) for g in groups]

    all_targets = list(set(user_ids + channel_ids + group_ids))

    status_msg = await message.answer(f"🔄 **Initializing Super Broadcast** to `{len(all_targets)}` endpoints...")

    success, failed = 0, 0
    for target_id in all_targets:
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy_to(chat_id=int(target_id))
            else:
                await bot.send_message(chat_id=int(target_id), text=broadcast_text)
            success += 1
        except TelegramForbiddenError:
            failed += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ **Broadcast Sequence Complete**\n\n"
        f"> 🟢 Delivered: `{success}`\n"
        f"> 🔴 Dropped: `{failed}`\n"
        f"> 📊 Efficiency: `{round((success/len(all_targets))*100, 1) if all_targets else 0}%`"
    )
    await notify_owner(bot, f"📢 **Broadcast Finished**\nSuccess: {success} | Failed: {failed}")

# ==========================================
# 11. INVENTORY SYSTEM ENTRY
# ==========================================
@dp.message(Command("addstock"))
async def add_stock(message: Message):
    users = await read_db("users.json")
    user_role = users.get(str(message.from_user.id), {}).get("role", "user")
    if message.from_user.id != OWNER_ID and user_role != "admin": return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        return await message.answer("⚠️ **Syntax Error:** `/addstock [Category] [Details]`\nExample: `/addstock Netflix PremiumUser:Pass`")

    category = args[1]
    item_data = args[2]

    stocks = await read_db("stocks.json")
    stocks.append({
        "id": str(uuid.uuid4()),
        "category": category,
        "item": item_data,
        "redeemed": False,
        "redeemed_by": None,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    await write_db("stocks.json", stocks)
    await message.answer(f"✅ **Inventory Updated:** New asset deployed to `{category}`.")

# ==========================================
# 12. MAIN EXECUTION (PYTHONANYWHERE FIX V3)
# ==========================================

# Using the Official Aiogram Session Override to fix 503 proxy errors completely
class PythonAnywhereSession(AiohttpSession):
    async def create_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(trust_env=True)

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    init_db()
    logging.info("✅ Database Multi-Node Architecture Initialized")

    bot_dummy = None
    await ensure_user_registered(OWNER_ID, OWNER_USERNAME.replace("@", ""), bot_dummy)

    # 1. Provide Proxies directly to the OS Environment
    os.environ["http_proxy"] = "http://proxy.server:3128"
    os.environ["https_proxy"] = "http://proxy.server:3128"
    os.environ["HTTP_PROXY"] = "http://proxy.server:3128"
    os.environ["HTTPS_PROXY"] = "http://proxy.server:3128"

    # 2. Use the safe, non-patching session class
    session = PythonAnywhereSession()

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode="Markdown")
    )

    # 3. Auto-Retry Loop (Prevents 503 Crashes on Startup!)
    print("🔄 Connecting to Telegram via PythonAnywhere Proxy...")
    connected = False
    for attempt in range(1, 6):
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            connected = True
            break
        except Exception as e:
            print(f"⚠️ Proxy hiccup (Attempt {attempt}/5). Retrying in 3 seconds...")
            await asyncio.sleep(3)

    if not connected:
        print("❌ Critical Error: PythonAnywhere proxy is down right now. Try running the script again.")
        return

    print(f"🚀 Premium Enterprise Engine Online! Executive Access: {OWNER_USERNAME}")
    await notify_owner(bot, "🟢 **System Online:** Platform Reboot Successful.")

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Manual Termination Sequence Initiated.")