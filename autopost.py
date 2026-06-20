import asyncio
import random
import logging
from datetime import datetime

# ==========================================
# OFFLINE BIN ENGINE
# ==========================================
def get_local_bin_data(bin_str):
    """Generates realistic offline mock data based on card prefix."""
    prefix = bin_str[0]
    schemes = {'4': 'Visa', '5': 'Mastercard', '6': 'Discover', '2': 'Mastercard', '3': 'American Express'}
    scheme = schemes.get(prefix, "Unknown")
    c_type = random.choice(["Credit", "Debit", "Prepaid"])
    banks = ["JPMorgan Chase", "Bank of America", "Wells Fargo", "Citibank", "HSBC", "Barclays", "Capital One", "Standard Chartered"]
    bank_name = random.choice(banks) if scheme != 'American Express' else "American Express"
    countries = [("United States", "🇺🇸"), ("United Kingdom", "🇬🇧"), ("Canada", "🇨🇦"), ("Australia", "🇦🇺"), ("Germany", "🇩🇪"), ("France", "🇫🇷"), ("India", "🇮🇳")]
    country_name, flag = random.choice(countries)
    return scheme, c_type, bank_name, country_name, flag

def generate_random_card():
    prefixes = ['4', '5', '6', '2', '3']
    prefix = random.choice(prefixes)
    length = 15 if prefix == '3' else 16
    bin_str = prefix + ''.join([str(random.randint(0, 9)) for _ in range(5)])
    rest_of_card = ''.join([str(random.randint(0, 9)) for _ in range(length - 6)])
    card_number = bin_str + rest_of_card
    month = f"{random.randint(1, 12):02d}"
    year = str(random.randint(2025, 2032))
    cvv = ''.join([str(random.randint(0, 9)) for _ in range(4 if prefix == '3' else 3)])
    return bin_str, f"{card_number}|{month}|{year}|{cvv}"

# ==========================================
# AUTO POST BACKGROUND TASK
# ==========================================
async def start_auto_poster(bot, read_db, modify_db, parse_chat_id, notify_owner):
    await asyncio.sleep(5)
    logging.info("⚙️ Autopost Engine Started Successfully from autopost.py!")

    while True:
        try:
            # TRUE TICK SYSTEM: Checks clock every 5 seconds.
            await asyncio.sleep(5)

            settings = await read_db("settings.json")
            channel_id = settings.get("auto_post_channel")
            interval = settings.get("auto_post_interval", 120)
            daily_limit = settings.get("daily_limit", 50)
            last_ts = settings.get("last_post_timestamp", 0)

            if not channel_id or channel_id == "None":
                continue

            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            # Day Reset Logic
            if settings.get("last_post_date") != today:
                def reset_day(s):
                    s["last_post_date"] = today
                    s["daily_post_count"] = 0
                    return s
                settings = await modify_db("settings.json", reset_day)

            # Limit Check
            if settings.get("daily_post_count", 0) >= daily_limit:
                continue

            # Time Check
            current_ts = now.timestamp()
            if (current_ts - last_ts) < interval:
                continue

            # 🟢 TIME TO POST!
            bin_str, card_format = generate_random_card()
            scheme, c_type, bank_name, country_name, flag = get_local_bin_data(bin_str)

            text = (
                f"[💎] Card ➜ `{card_format}`\n"
                f"━━━━━━━━━━━\n"
                f"[ﾒ] Info ➜ {scheme} - {c_type}\n"
                f"[ﾒ] Bank ➜ {bank_name}\n"
                f"[ﾒ] Country ➜ {country_name} {flag}"
            )

            try:
                target_chat = parse_chat_id(channel_id)
                await bot.send_message(chat_id=target_chat, text=text)
                logging.info(f"✅ Auto-posted Mocked BIN {bin_str}")

                # Update timestamp after successful post
                def update_post_stats(s):
                    s["daily_post_count"] = s.get("daily_post_count", 0) + 1
                    s["last_post_timestamp"] = datetime.now().timestamp()
                    return s
                await modify_db("settings.json", update_post_stats)

            except Exception as e:
                err_msg = str(e)
                logging.error(f"❌ Failed to auto-post: {err_msg}")
                # Update timestamp anyway so we don't spam the owner every 5 seconds!
                def update_fail_ts(s):
                    s["last_post_timestamp"] = datetime.now().timestamp()
                    return s
                await modify_db("settings.json", update_fail_ts)

                await notify_owner(bot, f"⚠️ **Auto-Post Error!**\n\nFailed to post in `{channel_id}`.\n**Error:** {err_msg}\nEnsure bot is admin.")

        except Exception as e:
            logging.error(f"⚠️ Autopost Loop Error: {e}")