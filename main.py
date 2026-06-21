import asyncio
import json
import os
import logging
import uuid
import random
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

# ==========================================
# 1. CONFIGURATION & EXECUTIVE INFO
# ==========================================
BOT_TOKEN = "8962210629:AAFhB5oNooreoJRhIuG7Frc9kqRxpQ2NWHA" # ⚠️ Paste your actual bot token here
OWNER_ID = 8570832903
OWNER_USERNAME = "@theaadikoder"
EXECUTIVE_NAME = "Aditya Thakur"
PLATFORM_NAME = "TheAdiCoder Premium Network"

# ⚠️ HOSTING SETTING: False = Optimized for Render/VPS/Heroku
USE_PYTHONANYWHERE_PROXY = False 

redeem_cooldowns = {}
bg_tasks = set()

# ==========================================
# 2. ATOMIC JSON DATABASE ENGINE
# ==========================================
db_lock = asyncio.Lock()
FILES = {
    "users.json": {}, "stocks.json": [], "channels.json": [], "groups.json": [], "logs.json": [],
    "settings.json": {
        "auto_post_enabled": False, # Master Switch
        "auto_post_channel": None, 
        "auto_post_interval": 300, 
        "daily_limit": 50, 
        "daily_post_count": 0, 
        "last_post_date": "1970-01-01", 
        "last_post_timestamp": 0
    }
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
# 4. PANELS (USER, ADMIN, OWNER)
# ==========================================
def get_user_dashboard_kb(role: str, user_id: int):
    kb = [
        [InlineKeyboardButton(text="🎁 Redeem Center", callback_data="user_redeem_center")],
        [InlineKeyboardButton(text="👤 My Profile", callback_data="user_profile"), InlineKeyboardButton(text="📊 Statistics", callback_data="user_stats")],
        [InlineKeyboardButton(text="📢 Official Links", callback_data="user_links"), InlineKeyboardButton(text="💬 Support", callback_data="user_support")]
    ]
    if user_id == OWNER_ID: kb.append([InlineKeyboardButton(text="👑 Enter Owner Panel", callback_data="panel_owner")])
    elif role == "admin": kb.append([InlineKeyboardButton(text="🛡️ Enter Admin Panel", callback_data="panel_admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Analytics", callback_data="admin_stats"), InlineKeyboardButton(text="📦 Manage Stock", callback_data="admin_stock")],
        [InlineKeyboardButton(text="👥 Manage Users", callback_data="admin_users"), InlineKeyboardButton(text="📢 Broadcast Hub", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📁 Database Export", callback_data="admin_export")],
        [InlineKeyboardButton(text="🔙 Back to User Panel", callback_data="user_home")]
    ])

def get_owner_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Auto-Post Manager", callback_data="owner_autopost")],
        [InlineKeyboardButton(text="📊 Analytics", callback_data="admin_stats"), InlineKeyboardButton(text="📦 Manage Stock", callback_data="admin_stock")],
        [InlineKeyboardButton(text="👥 Manage Users", callback_data="admin_users"), InlineKeyboardButton(text="📢 Broadcast Hub", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🛡️ Admin Controls", callback_data="owner_admins"), InlineKeyboardButton(text="📁 Export DB", callback_data="admin_export")],
        [InlineKeyboardButton(text="🔙 Back to User Panel", callback_data="user_home")]
    ])

@dp.message(CommandStart())
async def start_cmd(message: Message):
    users = await read_db("users.json")
    role = users.get(str(message.from_user.id), {}).get("role", "user")
    text = f"✦ **Welcome to the Premium Management Platform** ✦\n\n> 🛡️ **Secured & Managed by:** {OWNER_USERNAME}\n> ⚡ **System Status:** Online & Optimized\n\nSelect an option below to access your dashboard services:"
    await message.answer(text, reply_markup=get_user_dashboard_kb(role, message.from_user.id))

@dp.callback_query(F.data == "verify_join")
async def verify_join_callback(call: CallbackQuery):
    await call.answer("Verifying your status...", show_alert=False)
    await call.message.delete()

@dp.callback_query(F.data == "user_home")
async def return_home(call: CallbackQuery):
    users = await read_db("users.json")
    role = users.get(str(call.from_user.id), {}).get("role", "user")
    text = f"✦ **Premium Management Platform** ✦\n\n> 🛡️ **Secured & Managed by:** {OWNER_USERNAME}\n\nSelect an option below to access your dashboard services:"
    await call.message.edit_text(text, reply_markup=get_user_dashboard_kb(role, call.from_user.id))

# --- COMMANDS TO OPEN PANELS ---
@dp.message(Command("admin"))
async def admin_panel_cmd(message: Message):
    users = await read_db("users.json")
    role = users.get(str(message.from_user.id), {}).get("role", "user")
    if role not in ["admin", "owner"]: return await message.answer("⛔ **403 Forbidden:** You are not an Admin.")
    await message.answer(f"🛡️ **Admin Dashboard**\n\nSelect an interface below to manage the platform:", reply_markup=get_admin_keyboard())

@dp.message(Command("owner"))
async def owner_panel_cmd(message: Message):
    if message.from_user.id != OWNER_ID: return await message.answer("⛔ **403 Forbidden:** Owner credentials required.")
    await message.answer(f"👑 **Enterprise Owner Dashboard**\n\n> **Executive:** {EXECUTIVE_NAME}\n\nSelect an interface below to manage the platform:", reply_markup=get_owner_keyboard())

# --- CALLBACKS TO OPEN PANELS ---
@dp.callback_query(F.data == "panel_admin")
async def panel_admin_cb(call: CallbackQuery):
    users = await read_db("users.json")
    role = users.get(str(call.from_user.id), {}).get("role", "user")
    if role not in ["admin", "owner"]: return await call.answer("Forbidden", show_alert=True)
    await call.message.edit_text(f"🛡️ **Admin Dashboard**\n\nSelect an interface below to manage the platform:", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data == "panel_owner")
async def panel_owner_cb(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return await call.answer("Forbidden", show_alert=True)
    await call.message.edit_text(f"👑 **Enterprise Owner Dashboard**\n\n> **Executive:** {EXECUTIVE_NAME}\n\nSelect an interface below to manage the platform:", reply_markup=get_owner_keyboard())

# ==========================================
# 5. USER FEATURES
# ==========================================
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
    now = datetime.now()
    expired = [uid for uid, time in redeem_cooldowns.items() if time < now]
    for uid in expired: del redeem_cooldowns[uid]

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
    
    now = datetime.now()
    expired = [uid for uid, time in redeem_cooldowns.items() if time < now]
    for uid in expired: del redeem_cooldowns[uid]

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
# 6. SHARED ADMIN/OWNER FEATURES
# ==========================================
@dp.callback_query(F.data == "admin_stats")
async def show_statistics(call: CallbackQuery):
    users, stocks = await read_db("users.json"), await read_db("stocks.json")
    channels, groups = await read_db("channels.json"), await read_db("groups.json")
    banned_users = sum(1 for u in users.values() if u.get("banned"))
    admin_count = sum(1 for u in users.values() if u.get("role") in ["admin", "owner"])
    available_stock = sum(1 for s in stocks if not s.get("redeemed"))
    redeemed_stock = sum(1 for s in stocks if s.get("redeemed"))
    daily_joins = sum(1 for u in users.values() if u.get("join_date", "").startswith(datetime.now().strftime("%Y-%m-%d")))

    stats_text = f"📊 **Real-Time Analytics Core**\n\n**User Matrix**\n> 👥 Total Users: `{len(users):,}`\n> 📈 Today's Growth: `+{daily_joins}`\n> 🛡️ Active Admins: `{admin_count}`\n> 🚫 Banned Entites: `{banned_users}`\n\n**Inventory Health**\n> 📦 Available Assets: `{available_stock:,}`\n> 🎟️ Total Redeemed: `{redeemed_stock:,}`\n\n**Network Graph**\n> 📢 Force Channels: `{len(channels)}`\n> 🌐 Linked Groups: `{len(groups)}`"
    
    back_btn = "panel_owner" if call.from_user.id == OWNER_ID else "panel_admin"
    await call.message.edit_text(stats_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh Engine", callback_data="admin_stats")], [InlineKeyboardButton(text="🔙 Back", callback_data=back_btn)]]))

@dp.callback_query(F.data == "admin_stock")
async def show_advanced_stock_panel(call: CallbackQuery):
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
    
    back_btn = "panel_owner" if call.from_user.id == OWNER_ID else "panel_admin"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_stock")], [InlineKeyboardButton(text="🔙 Back", callback_data=back_btn)]]))

@dp.callback_query(F.data == "admin_users")
async def show_user_management(call: CallbackQuery):
    back_btn = "panel_owner" if call.from_user.id == OWNER_ID else "panel_admin"
    await call.message.edit_text("👥 **User Administration Hub**\n\n> **Available Commands:**\n\n🔨 `/ban [User_ID]`\n🕊️ `/unban [User_ID]`\n\n_Tip: Export the User Database via the Export Center to view all User IDs._", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data=back_btn)]]))

@dp.callback_query(F.data == "admin_broadcast")
async def show_broadcast_help(call: CallbackQuery):
    back_btn = "panel_owner" if call.from_user.id == OWNER_ID else "panel_admin"
    await call.message.edit_text("📢 **Super Broadcast Engine**\n\n**Execution Method:**\nReply to ANY media/text with `/broadcast` or type `/broadcast [Message]`\n\n**Network Controls:**\n➕ `/addchannel [ID] [Link]`\n➖ `/removechannel [ID]`\n➕ `/addgroup [ID]`\n➖ `/removegroup [ID]`", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data=back_btn)]]))

@dp.callback_query(F.data == "admin_export")
async def show_export_panel(call: CallbackQuery):
    back_btn = "panel_owner" if call.from_user.id == OWNER_ID else "panel_admin"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Export Users", callback_data="export_users"), InlineKeyboardButton(text="📄 Export Admins", callback_data="export_admins")],
        [InlineKeyboardButton(text="📄 Export Channels", callback_data="export_channels"), InlineKeyboardButton(text="📄 Export Groups", callback_data="export_groups")],
        [InlineKeyboardButton(text="📈 Export Logs", callback_data="export_logs")],
        [InlineKeyboardButton(text="🔙 Back", callback_data=back_btn)]
    ])
    await call.message.edit_text("📁 **Database Control Center**\n\n> Select a node below to generate a comprehensive local `.txt` dump.", reply_markup=markup)

@dp.callback_query(F.data.startswith("export_"))
async def handle_exports(call: CallbackQuery):
    users = await read_db("users.json")
    if users.get(str(call.from_user.id), {}).get("role") not in ["admin", "owner"]: return
    await call.answer("Compiling database node...", show_alert=False)
    action = call.data.split("_")[1]
    content, filename = "", f"DB_{action.capitalize()}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

    if action == "users":
        content = "=== ENTERPRISE USER LEDGER ===\n\n"
        for uid, data in users.items(): content += f"ID: {uid} | User: {data.get('username')} | Role: {data.get('role')} | Banned: {data.get('banned')} | Joined: {data.get('join_date')} | Redeems: {data.get('redeem_count')}\n"
    elif action == "admins":
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
# 7. OWNER EXCLUSIVE FEATURES (Admins & Auto-Post)
# ==========================================
@dp.callback_query(F.data == "owner_admins")
async def show_admin_console(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    users = await read_db("users.json")
    admins = [uid for uid, data in users.items() if data.get("role") == "admin"]
    text = f"🛡️ **Admin Hierarchy Console**\n\n> **Active Administrators:** `{len(admins)}`\n\n"
    for adm in admins: text += f"🔹 ID: `{adm}` (@{users.get(adm, {}).get('username', 'Unknown')})\n"
    text += "\n> **Hierarchy Controls:**\n➕ `/addadmin [ID]`\n➖ `/removeadmin [ID]`"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="panel_owner")]]))

# --- DYNAMIC AUTO-POST MANAGER (OWNER ONLY) ---
@dp.callback_query(F.data == "owner_autopost")
async def owner_autopost_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    settings = await read_db("settings.json")
    
    interval_sec = settings.get('auto_post_interval', 300)
    if interval_sec >= 3600 and interval_sec % 3600 == 0: interval_display = f"{int(interval_sec/3600)} Hour(s)"
    else: interval_display = f"{int(interval_sec/60)} Minute(s)"

    status_icon = "🟢 RUNNING" if settings.get('auto_post_enabled') else "🔴 STOPPED"

    text = (
        "🤖 **Enterprise Auto-Post Engine**\n\n"
        f"**Engine Status:** `{status_icon}`\n"
        f"**Target Channel:** `{settings.get('auto_post_channel', 'Not Set')}`\n"
        f"**Post Interval:** `{interval_display}`\n"
        f"**Daily Limit:** `{settings.get('daily_limit', 50)} Posts/Day`\n"
        f"**Posted Today:** `{settings.get('daily_post_count', 0)}`\n\n"
        "⚙️ **Engine Controls:**"
    )
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Start", callback_data="ap_start"),
         InlineKeyboardButton(text="⏸️ Stop", callback_data="ap_stop"),
         InlineKeyboardButton(text="🔄 Restart", callback_data="ap_restart")],
        [InlineKeyboardButton(text="⏱️ Set Interval", callback_data="ap_set_time"),
         InlineKeyboardButton(text="📊 Set Limit", callback_data="ap_set_limit")],
        [InlineKeyboardButton(text="📺 Setup Channel", callback_data="ap_set_channel"),
         InlineKeyboardButton(text="🛠️ Force Test", callback_data="ap_testpost")],
        [InlineKeyboardButton(text="🔙 Back to Owner Panel", callback_data="panel_owner")]
    ])
    await call.message.edit_text(text, reply_markup=markup)

@dp.callback_query(F.data.in_(["ap_start", "ap_stop", "ap_restart"]))
async def ap_state_controls(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    action = call.data

    def toggle_state(s):
        if action == "ap_start": s["auto_post_enabled"] = True
        elif action == "ap_stop": s["auto_post_enabled"] = False
        elif action == "ap_restart":
            s["auto_post_enabled"] = True
            s["daily_post_count"] = 0
            s["last_post_timestamp"] = 0 # Forces immediate post
        return s

    await modify_db("settings.json", toggle_state)
    await call.answer(f"Engine {'Started' if action != 'ap_stop' else 'Stopped'} Successfully!", show_alert=False)
    await owner_autopost_menu(call) # Refresh UI

@dp.callback_query(F.data == "ap_set_channel")
async def ap_set_channel(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    text = "📺 **Set Auto-Post Channel**\n\nTo connect your channel, type this command in the chat:\n\n👉 `/setautochannel [Channel ID or @username]`\n_Example: `/setautochannel -100123456789`_"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_autopost")]]))

@dp.callback_query(F.data == "ap_set_limit")
async def ap_set_limit_menu(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    limits = [10, 25, 50, 100, 500, 1000]
    kb = []
    # 2 items per row
    for i in range(0, len(limits), 2):
        row = [InlineKeyboardButton(text=f"{limits[i]} Posts", callback_data=f"aplim_{limits[i]}")]
        if i + 1 < len(limits): row.append(InlineKeyboardButton(text=f"{limits[i+1]} Posts", callback_data=f"aplim_{limits[i+1]}"))
        kb.append(row)
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="owner_autopost")])
    await call.message.edit_text("📊 **Set Daily Post Limit**\n\nSelect maximum posts per day:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("aplim_"))
async def ap_save_limit(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    limit = int(call.data.split("_")[1])
    def update_limit(s):
        s["daily_limit"] = limit
        return s
    await modify_db("settings.json", update_limit)
    await call.answer(f"Daily limit set to {limit}!", show_alert=True)
    await owner_autopost_menu(call)

@dp.callback_query(F.data == "ap_set_time")
async def ap_set_time_main(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Minutes", callback_data="ap_time_min"), InlineKeyboardButton(text="⌛ Hours", callback_data="ap_time_hr")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="owner_autopost")]
    ])
    await call.message.edit_text("⏱️ **Auto-Post Time Setup**\n\nChoose time unit:", reply_markup=markup)

@dp.callback_query(F.data.startswith("ap_time_"))
async def ap_show_numbers(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    t_type = call.data.split("_")[2] # 'min' or 'hr'
    
    if t_type == "min":
        options = [1, 2, 3, 5, 10, 15, 30, 45, 60]
        title = "MINUTES"
    else:
        options = [1, 2, 3, 4, 6, 8, 12, 24, 48]
        title = "HOURS"
    
    kb = []
    for i in range(0, len(options), 3):
        row = [InlineKeyboardButton(text=str(opt), callback_data=f"apsave_{t_type}_{opt}") for opt in options[i:i+3]]
        kb.append(row)
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="ap_set_time")])
    
    await call.message.edit_text(f"⏱️ **Set Interval in {title}**\n\nSelect a number below:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("apsave_"))
async def ap_save_time(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    parts = call.data.split("_")
    t_type, num = parts[1], int(parts[2])
    
    interval_sec = num * 60 if t_type == "min" else num * 3600
        
    def update_interval(s):
        s["auto_post_interval"] = interval_sec
        return s
    await modify_db("settings.json", update_interval)
    await call.answer("Interval Saved Successfully!", show_alert=True)
    await owner_autopost_menu(call)

@dp.callback_query(F.data == "ap_testpost")
async def ap_testpost_cb(call: CallbackQuery, bot: Bot):
    if call.from_user.id != OWNER_ID: return
    settings = await read_db("settings.json")
    channel_id = settings.get("auto_post_channel")

    if not channel_id or channel_id == "None":
        return await call.message.edit_text("❌ **No Channel Set:** Please set a channel first via `📺 Setup Channel`.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_autopost")]]))

    await call.message.edit_text(f"🔄 **Testing Auto-Post...**\nSending a test card to `{channel_id}`")

    try:
        bin_str, card_format = generate_random_card()
        scheme, c_type, bank_name, country_name, flag = get_local_bin_data(bin_str)
        text = f"🛠 **[TEST POST]** 🛠\n\n[💎] Card ➜ `{card_format}`\n━━━━━━━━━━━\n[ﾒ] Info ➜ {scheme} - {c_type}\n[ﾒ] Bank ➜ {bank_name}\n[ﾒ] Country ➜ {country_name} {flag}"

        target_chat = parse_chat_id(channel_id)
        await bot.send_message(chat_id=target_chat, text=text)
        await call.message.edit_text("✅ **Test Successful!**\nThe message was successfully posted to your channel.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Manager", callback_data="owner_autopost")]]))
    except TelegramBadRequest as e:
        await call.message.edit_text(f"❌ **Test Failed!**\n\n**Telegram Error:** `{e}`\n\n_Make sure the bot is an ADMIN in the channel and the ID is correct._", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_autopost")]]))
    except Exception as e:
        await call.message.edit_text(f"❌ **Test Failed!**\n\n**System Error:** `{e}`", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_autopost")]]))


# ==========================================
# 8. ADMIN/OWNER TEXT COMMANDS
# ==========================================
@dp.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/addadmin [User ID]`")
    def promote(users):
        if args[1] in users: users[args[1]]["role"] = "admin"; return users
        return None
    if await modify_db("users.json", promote): await message.answer(f"✅ User `{args[1]}` is now an Admin.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("removeadmin"))
async def cmd_removeadmin(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/removeadmin [User ID]`")
    def demote(users):
        if args[1] in users: users[args[1]]["role"] = "user"; return users
        return None
    if await modify_db("users.json", demote): await message.answer(f"⬇️ User `{args[1]}` demoted.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("addstock"))
async def cmd_addstock(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return
    args = message.text.split(maxsplit=2)
    if len(args) < 3: return await message.answer("⚠️ `/addstock [Category] [Details]`")
    def insert(stocks):
        stocks.append({"id": str(uuid.uuid4()), "category": args[1], "item": args[2], "redeemed": False, "redeemed_by": None, "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return stocks
    await modify_db("stocks.json", insert)
    await message.answer(f"✅ **Inventory Updated:** New asset deployed to `{args[1]}`.")

@dp.message(Command("addchannel"))
async def cmd_addchannel(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return
    args = message.text.split()
    if len(args) < 3: return await message.answer("⚠️ **Syntax Error:** `/addchannel [Channel ID] [Invite Link]`")
    def append_ch(channels):
        channels.append({"channel_id": args[1], "link": args[2]})
        return channels
    await modify_db("channels.json", append_ch)
    await message.answer(f"✅ Force channel linked.\n> ID: `{args[1]}`")

@dp.message(Command("removechannel"))
async def cmd_removechannel(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax Error:** `/removechannel [Channel ID]`")
    def drop_ch(channels): return [ch for ch in channels if str(ch["channel_id"]) != args[1]]
    await modify_db("channels.json", drop_ch)
    await message.answer(f"🗑️ Channel `{args[1]}` unlinked.")

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/ban [User ID]`")
    def restrict(u):
        if args[1] in u: u[args[1]]["banned"] = True; return u
        return None
    if await modify_db("users.json", restrict): await message.answer(f"🔨 `{args[1]}` banned globally.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    users = await read_db("users.json")
    if users.get(str(message.from_user.id), {}).get("role") not in ["admin", "owner"]: return
    args = message.text.split()
    if len(args) < 2 or not args[1].lstrip('-').isdigit(): return await message.answer("⚠️ **Syntax Error:** `/unban [User ID]`")
    def restore(u):
        if args[1] in u: u[args[1]]["banned"] = False; return u
        return None
    if await modify_db("users.json", restore): await message.answer(f"🕊️ `{args[1]}` restored.")
    else: await message.answer("❌ **Error:** Identity not found.")

@dp.message(Command("setautochannel"))
async def cmd_set_auto_channel(message: Message):
    if message.from_user.id != OWNER_ID: return
    args = message.text.split()
    if len(args) < 2: return await message.answer("⚠️ **Syntax:** `/setautochannel [Channel ID / @username]`")
    def update_ch(s):
        s["auto_post_channel"] = args[1]
        return s
    await modify_db("settings.json", update_ch)
    await message.answer(f"✅ **Auto-Post Channel Updated:** `{args[1]}`")

# ==========================================
# 9. INTERNAL OFFLINE BIN ENGINE
# ==========================================
def get_local_bin_data(bin_str):
    prefix = bin_str[0]
    schemes = {'4': 'Visa', '5': 'Mastercard', '6': 'Discover', '2': 'Mastercard', '3': 'American Express'}
    scheme = schemes.get(prefix, "Unknown")
    c_type = random.choice(["Credit", "Debit", "Prepaid"])
    banks = ["JPMorgan Chase", "Bank of America", "Wells Fargo", "Citibank", "HSBC", "Barclays", "Capital One", "American Express"]
    bank_name = random.choice(banks) if scheme != 'American Express' else "American Express"
    countries = [("United States", "🇺🇸"), ("United Kingdom", "🇬🇧"), ("Canada", "🇨🇦"), ("Australia", "🇦🇺"), ("India", "🇮🇳")]
    country_name, flag = random.choice(countries)
    return scheme, c_type, bank_name, country_name, flag

def generate_random_card():
    prefixes = ['4', '5', '6', '2', '3']
    prefix = random.choice(prefixes)
    length = 15 if prefix == '3' else 16
    bin_str = prefix + ''.join([str(random.randint(0, 9)) for _ in range(5)])
    rest_of_card = ''.join([str(random.randint(0, 9)) for _ in range(length - 6)])
    month = f"{random.randint(1, 12):02d}"
    year = str(random.randint(2025, 2032))
    cvv = ''.join([str(random.randint(0, 9)) for _ in range(4 if prefix == '3' else 3)])
    return bin_str, f"{bin_str}{rest_of_card}|{month}|{year}|{cvv}"

async def auto_post_task(bot: Bot):
    await asyncio.sleep(5)
    logging.info("⚙️ Smart Auto-Post Task Started...")
    
    while True:
        try:
            await asyncio.sleep(5) # Tick Engine runs every 5 secs
            settings = await read_db("settings.json")
            
            # 🟢 Check Master Switch!
            if not settings.get("auto_post_enabled", False):
                continue
                
            channel_id = settings.get("auto_post_channel")
            interval = settings.get("auto_post_interval", 300) 
            daily_limit = settings.get("daily_limit", 50)
            last_ts = settings.get("last_post_timestamp", 0)
            
            if not channel_id or channel_id == "None": continue

            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            if settings.get("last_post_date") != today:
                def reset_day(s):
                    s["last_post_date"] = today
                    s["daily_post_count"] = 0
                    return s
                settings = await modify_db("settings.json", reset_day)

            if settings.get("daily_post_count", 0) >= daily_limit: continue
            
            # Time gap check
            if (now.timestamp() - last_ts) < interval: continue 

            bin_str, card_format = generate_random_card()
            scheme, c_type, bank_name, country_name, flag = get_local_bin_data(bin_str)
            text = f"[💎] Card ➜ `{card_format}`\n━━━━━━━━━━━\n[ﾒ] Info ➜ {scheme} - {c_type}\n[ﾒ] Bank ➜ {bank_name}\n[ﾒ] Country ➜ {country_name} {flag}"

            try:
                target_chat = parse_chat_id(channel_id)
                await bot.send_message(chat_id=target_chat, text=text)
                logging.info(f"✅ Auto-posted Mocked BIN {bin_str}")
                
                def update_post_stats(s):
                    s["daily_post_count"] = s.get("daily_post_count", 0) + 1
                    s["last_post_timestamp"] = datetime.now().timestamp()
                    return s
                await modify_db("settings.json", update_post_stats)
                
            except Exception as e:
                err_msg = str(e)
                logging.error(f"❌ Failed to post: {err_msg}")
                # Turn OFF Engine automatically on fail to prevent spam
                def turn_off_engine(s):
                    s["auto_post_enabled"] = False
                    return s
                await modify_db("settings.json", turn_off_engine)
                await notify_owner(bot, f"⚠️ **Auto-Post Engine Stopped!**\n\nBot couldn't post to the channel.\n**Reason:** `{err_msg}`\n\n_Engine has been turned OFF. Please fix the issue and turn it ON from the Owner Panel._")
            
        except Exception as e:
            logging.error(f"⚠️ Loop Error: {e}")

# ==========================================
# 10. KEEP-ALIVE WEB SERVER
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def run_dummy_server():
    if USE_PYTHONANYWHERE_PROXY: return
    try:
        app = web.Application()
        app.router.add_get('/', handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logging.info(f"🌐 Keep-alive web server running on port {port}")
    except Exception as e: logging.error(f"🌐 Dummy Server Skipped: {e}")

# ==========================================
# 11. ENGINE IGNITION
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

    # Background Tasks
    asyncio.create_task(run_dummy_server())
    asyncio.create_task(auto_post_task(bot))

    print(f"🚀 Premium Enterprise Engine Online! Executive Access: {OWNER_USERNAME}")
    await notify_owner(bot, "🟢 **System Online:** Platform Reboot Successful.")

    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"⚠️ Network Error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Manual Termination Sequence Initiated.")
