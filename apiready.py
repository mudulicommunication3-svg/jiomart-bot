import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import time
import aiohttp
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)

# ==========================================
# ୧. CONFIG & FILE PATHS
# ==========================================
VERSION = "v9.0.0 (Merged - Original API + Login P2 + Payment Features)"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TOKEN:
    raise ValueError("ଟୋକେନ୍ ମିଳିଲା ନାହିଁ! .env ଫାଇଲ୍ ଯାଞ୍ଚ କରନ୍ତୁ।")

if os.name == "nt" and os.path.exists("D:\\"):
    BASE_DIR = os.path.join("D:\\", "JioMartBot")
else:
    BASE_DIR = os.path.join(os.path.expanduser("~"), "JioMartBot")

os.makedirs(BASE_DIR, exist_ok=True)
SESSION_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

PRODUCT_DB = os.path.join(BASE_DIR, "product_library.json")
CART_DB = os.path.join(BASE_DIR, "cart_offline.json")
CONFIG_DB = os.path.join(BASE_DIR, "bot_database.json")
ARTICLE_CACHE_DB = os.path.join(BASE_DIR, "article_cache.json")
ADDRESS_OFFLINE_DB = os.path.join(BASE_DIR, "address_offline.json")

GLOBAL_PRICES = {}
USER_HEADERS_CACHE = {}
ACTIVE_GUI_SESSIONS = {}
LIVE_CART_BILLING_CACHE = {}

# Force stop tracking flags and task references
FORCE_STOP_FLAG = {}
RUNNING_TASKS = {}

# ==========================================
# LOGIN P2 MODULE CONFIGURATION
# ==========================================
# --- STATES ---
MOBILE, OTP, NEW_NAME_STATE = range(3)

# 📱 Fixed Screen Viewport for Redmi 5A (360x640 px)
FIXED_VIEWPORT = {'width': 360, 'height': 640}
MOBILE_USER_AGENT = "Mozilla/5.0 (Linux; Android 7.1.2; Redmi 5A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36"

# 🔥 Date-Based Session Naming: Jio<Mobile>T<HH.MM>D<DD.MM.YY>
def generate_date_session_key(mobile):
    now = datetime.now()
    time_str = now.strftime("%H.%M")
    date_str = now.strftime("%d.%m.%y")
    return f"Jio{mobile}T{time_str}D{date_str}"

# ==========================================
# 🛠️ SYSTEM UTILITIES
# ==========================================
async def smart_location_popup_handler(page, chat_id=None, context=None):
    try:
        modal_title = page.locator("text=Enable location Services, div:has-text('Enable location Services'), text=Location Permission").first
        enable_btn = page.locator("button:has-text('Enable Location'), button:has-text('Select Location Manually'), button:has-text('Allow')").first
        close_x = page.locator("div[class*='modal'] button[aria-label='Close'], button:has-text('✕')").first

        for _ in range(4):
            if await modal_title.is_visible() or await enable_btn.is_visible():
                if chat_id and context:
                    try: await context.bot.send_message(chat_id=chat_id, text="⚠️ Location Popup detected! Bypassing...")
                    except: pass
                if await enable_btn.is_visible(): await enable_btn.click(force=True)
                elif await close_x.is_visible(): await close_x.click(force=True)
                await page.wait_for_timeout(3000)
                return True
            await asyncio.sleep(1)
    except Exception as ex:
        logging.info(f"Location bypass trace: {str(ex)}")
    return False

async def purge_otp_inputs_right_to_left(page):
    try:
        await page.evaluate("""() => {
            const inputs = Array.from(document.querySelectorAll('input[type="tel"], input[class*="otp"], input'));
            if(inputs.length > 0) {
                for(let i = inputs.length - 1; i >= 0; i--) {
                    inputs[i].focus();
                    inputs[i].value = '';
                    inputs[i].dispatchEvent(new Event('input', { bubbles: true }));
                }
            }
        }""")
        inputs_count = await page.locator('input').count()
        for idx in reversed(range(inputs_count)):
            try:
                current_input = page.locator('input').nth(idx)
                await current_input.click()
                await page.keyboard.press("Backspace")
                await page.keyboard.press("Delete")
            except: pass
        await page.wait_for_timeout(300)
    except Exception as e:
        logging.info(f"OTP Purge Error: {str(e)}")

# ==========================================
# 🔑 LOGIN P2 MODULE (Mi 5A Mode: 360x640)
# ==========================================
async def start_customer_login_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    str_id = str(update.effective_chat.id); cfg = load_configs().get(str_id, {})
    user_lat = float(cfg.get("latitude", 21.4676))
    user_lon = float(cfg.get("longitude", 86.9333))

    status_msg = await query.message.reply_text("🚀 **PART 1: LOGIN P2 MATRIX STARTED (Mi 5A Mode - 360x640)**", parse_mode="Markdown")
    
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=False, args=["--disable-notifications"])
    
    ctx = await browser.new_context(
        permissions=["geolocation"], 
        geolocation={"latitude": user_lat, "longitude": user_lon}, 
        viewport=FIXED_VIEWPORT,
        user_agent=MOBILE_USER_AGENT,
        is_mobile=True,
        has_touch=True
    )
    page = await ctx.new_page()
    
    context.user_data["playwright_p"] = p
    context.user_data["browser"] = browser
    context.user_data["ctx"] = ctx
    context.user_data["page"] = page
    
    try:
        await page.goto("https://www.jiomart.com/profile", wait_until="networkidle", timeout=60000)
        await asyncio.sleep(4)
    except Exception as e:
        await status_msg.edit_text(f"❌ Initial Load Error: {str(e)}")
        if browser: await browser.close()
        if p: await p.stop()
        return ConversationHandler.END
    
    keyboard = [[InlineKeyboardButton("❌ Cancel Login", callback_data="login_cancel_action")]]
    await status_msg.edit_text("📱 Please send the 10-digit customer **Mobile Number**:", reply_markup=InlineKeyboardMarkup(keyboard))
    return MOBILE

async def customer_mobile_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    num = update.message.text.strip(); page = context.user_data.get("page")
    
    if num.lower() in ['cancel', 'stop', 'exit']:
        return await cancel_login_flow(update, context)
    
    if not num.isdigit() or len(num) != 10:
        keyboard = [[InlineKeyboardButton("❌ Cancel Login", callback_data="login_cancel_action")]]
        await update.message.reply_text("❌ Please enter a valid 10-digit number:", reply_markup=InlineKeyboardMarkup(keyboard))
        return MOBILE
    
    secret_key = generate_date_session_key(num)
    context.user_data["login_mobile"] = num
    context.user_data["login_secret_key"] = secret_key
    
    proc_msg = await update.message.reply_text(f"📱 Typing mobile number `{num}`...\n🔑 Session Key: `{secret_key}`", parse_mode="Markdown")
    
    try:
        await page.locator("input[type='tel']").first.fill(num)
        await page.locator("button:has-text('Sign In'), button:has-text('Sign in')").first.click()
        await page.wait_for_timeout(3000)
    except Exception as e:
        await proc_msg.edit_text(f"❌ Submission Error: {str(e)}")
        return MOBILE
    
    keyboard = [
        [InlineKeyboardButton("🔄 Resend OTP", callback_data="login_resend_otp_action")],
        [InlineKeyboardButton("❌ Cancel Login", callback_data="login_cancel_action")]
    ]
    await proc_msg.edit_text("📥 Send the 6-digit customer **OTP** here:", reply_markup=InlineKeyboardMarkup(keyboard))
    return OTP

async def login_resend_otp_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    page = context.user_data.get("page")
    if page:
        try:
            resend_lnk = page.locator("text=Resend").first or page.locator("a:has-text('Resend')").first
            if await resend_lnk.is_visible(timeout=3000):
                await resend_lnk.click(force=True)
                await query.message.reply_text("✅ 'Resend OTP' clicked!")
        except Exception: pass

async def cancel_login_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel login flow and cleanup browser resources"""
    chat_id = str(update.effective_chat.id)
    
    try:
        browser = context.user_data.get("browser")
        p = context.user_data.get("playwright_p")
        ctx = context.user_data.get("ctx")
        
        if browser:
            try:
                await browser.close()
            except:
                pass
        if p:
            try:
                await p.stop()
            except:
                pass
        if ctx:
            try:
                await ctx.close()
            except:
                pass
    except Exception as e:
        logging.info(f"Browser cleanup error: {str(e)}")
    
    keys_to_remove = [
        "playwright_p", "browser", "ctx", "page", 
        "login_mobile", "login_secret_key",
        "conversation_state", "current_conversation"
    ]
    
    for key in keys_to_remove:
        context.user_data.pop(key, None)
    
    if update.callback_query:
        try:
            await update.callback_query.answer("Login cancelled!")
            await update.callback_query.message.edit_text("❌ **Login P2 Flow Cancelled**\n\nBrowser closed and resources cleaned up.\n\nYou can now start a new login.", parse_mode="Markdown")
        except:
            try:
                await update.callback_query.message.reply_text("❌ **Login P2 Flow Cancelled**\n\nBrowser closed and resources cleaned up.\n\nYou can now start a new login.", parse_mode="Markdown")
            except:
                pass
    elif update.message:
        try:
            await update.message.reply_text("❌ **Login P2 Flow Cancelled**\n\nBrowser closed and resources cleaned up.\n\nYou can now start a new login.", parse_mode="Markdown")
        except:
            pass
    
    return ConversationHandler.END

async def login_cancel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button callback"""
    return await cancel_login_flow(update, context)

async def force_stop_login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global force stop command for login flow"""
    chat_id = str(update.effective_chat.id)
    
    if context.user_data.get("browser") or context.user_data.get("playwright_p"):
        return await cancel_login_flow(update, context)
    else:
        await update.message.reply_text("ℹ️ No active login session to stop.")
        return ConversationHandler.END

async def customer_otp_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    page = context.user_data.get("page")
    otp = update.message.text.strip()
    
    if otp.lower() in ['cancel', 'stop', 'exit']:
        return await cancel_login_flow(update, context)
    
    if not otp.isdigit() or len(otp) != 6:
        keyboard = [
            [InlineKeyboardButton("🔄 Resend OTP", callback_data="login_resend_otp_action")],
            [InlineKeyboardButton("❌ Cancel Login", callback_data="login_cancel_action")]
        ]
        await update.message.reply_text("❌ Please enter a valid 6-digit OTP:", reply_markup=InlineKeyboardMarkup(keyboard))
        return OTP
    
    proc_msg = await update.message.reply_text("⏳ Purging old inputs and auto-typing OTP...")
    
    try:
        await purge_otp_inputs_right_to_left(page)
        await page.locator("input").first.click()
        await page.keyboard.type(otp, delay=100)
        
        try:
            verify_btn = page.locator("button:has-text('Verify OTP'), button[type='submit']").first
            await verify_btn.click(timeout=3000, force=True)
        except Exception:
            pass 
        
        await proc_msg.edit_text("⏳ OTP typed! Waiting 6 seconds for JioMart Auto-Verification...")
        await asyncio.sleep(6)
    except Exception as e:
        await proc_msg.edit_text(f"❌ OTP UI error: {str(e)}")
        return OTP

    is_wrong_otp = await page.evaluate("""() => {
        const toast = document.querySelector('div[class*="toast"], div[class*="error"], div[class*="alert"]');
        if (toast && toast.innerText && toast.innerText.toLowerCase().includes('wrong otp')) return true;
        const textNodes = Array.from(document.querySelectorAll('div, p, span'));
        return !!textNodes.find(el => el.innerText && el.innerText.includes('Wrong OTP'));
    }""")

    if is_wrong_otp:
        await proc_msg.edit_text("❌ **Wrong OTP detected!** Please send the correct OTP:")
        await purge_otp_inputs_right_to_left(page)
        return OTP

    return await handle_possible_name_setup_screen(update, context, chat_id, page, proc_msg)

async def handle_possible_name_setup_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str, page, status_msg=None):
    is_new_account = await page.evaluate("""() => {
        const textNodes = Array.from(document.querySelectorAll('div, h2, h1, p, span'));
        return !!textNodes.find(el => el.innerText && (el.innerText.includes('Instant account setup') || el.innerText.includes('All we need is your name')));
    }""")

    if is_new_account:
        if status_msg: await status_msg.edit_text("🆕 **Instant account setup screen detected!**")
        else: await context.bot.send_message(chat_id=chat_id, text="🆕 **Instant account setup screen detected!**")
        
        cfg = load_configs().get(chat_id, {})
        preset_name = cfg.get("preset_name", "JEMS").strip()

        if preset_name:
            await context.bot.send_message(chat_id=chat_id, text=f"📝 Auto-typing Preset Name `{preset_name}`...", parse_mode="Markdown")
            try:
                await page.evaluate("""() => {
                    const setupContainer = Array.from(document.querySelectorAll('div')).find(el => el.innerText && el.innerText.includes('Instant account setup'));
                    if (setupContainer) {
                        const inputField = setupContainer.querySelector('input');
                        if (inputField) {
                            inputField.focus(); inputField.value = '';
                            inputField.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                    } else {
                        const inputField = document.querySelector('input[placeholder*="name"], input[name*="firstName"], input[id*="name"]');
                        if (inputField) {
                            inputField.focus(); inputField.value = '';
                            inputField.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                    }
                }""")
                await page.wait_for_timeout(1000)

                await page.keyboard.type(preset_name, delay=100)
                await page.wait_for_timeout(1500)
                
                get_started_btn = page.locator("button:has-text('Get Started'), button:has-text('Continue'), button[type='submit']").first
                await get_started_btn.click(force=True)
                await asyncio.sleep(4)
                context.application.create_task(execute_direct_profile_stay_sequence(update, context, chat_id, is_new_reg=True))
                return ConversationHandler.END
            except Exception as e:
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Auto-fill failed: {str(e)}")
        
        keyboard = [[InlineKeyboardButton("❌ Cancel Login", callback_data="login_cancel_action")]]
        await context.bot.send_message(chat_id=chat_id, text="✍️ Please type and send the customer's **Full Name**:", reply_markup=InlineKeyboardMarkup(keyboard))
        return NEW_NAME_STATE
    
    context.application.create_task(execute_direct_profile_stay_sequence(update, context, chat_id, is_new_reg=False))
    return ConversationHandler.END

async def new_name_received_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = context.user_data.get("page")
    customer_name = update.message.text.strip()
    chat_id = str(update.effective_chat.id)
    
    if customer_name.lower() in ['cancel', 'stop', 'exit']:
        return await cancel_login_flow(update, context)
    
    try:
        await page.evaluate("""() => {
            const inputField = document.querySelector('input[placeholder*="name"], input[name*="firstName"], input[id*="name"]');
            if (inputField) {
                inputField.focus(); inputField.value = '';
                inputField.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }""")
        await page.wait_for_timeout(1000)
        await page.keyboard.type(customer_name, delay=100)
        await page.wait_for_timeout(1500)
        get_started_btn = page.locator("button:has-text('Get Started'), button:has-text('Continue'), button[type='submit']").first
        await get_started_btn.click(force=True)
        await asyncio.sleep(4)
    except Exception as e:
        keyboard = [[InlineKeyboardButton("❌ Cancel Login", callback_data="login_cancel_action")]]
        await update.message.reply_text(f"❌ Name Entry Fail: {str(e)}", reply_markup=InlineKeyboardMarkup(keyboard))

    context.application.create_task(execute_direct_profile_stay_sequence(update, context, chat_id, is_new_reg=True))
    return ConversationHandler.END

async def execute_direct_profile_stay_sequence(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str, is_new_reg: bool = False, is_view_mode: bool = False):
    page = context.user_data.get("page")
    ctx = context.user_data.get("ctx")
    browser = context.user_data.get("browser")
    p = context.user_data.get("playwright_p")
    
    secret_key = context.user_data.get("login_secret_key")
    if not secret_key or "reauth" in secret_key:
        secret_key = load_configs().get(chat_id, {}).get("active_key", "")

    status_msg = await context.bot.send_message(chat_id=chat_id, text="⚡ **JioMart Auth Complete!** Scanning profile data...", parse_mode="Markdown")
    try:
        await page.goto("https://www.jiomart.com/profile", wait_until="networkidle", timeout=45000)
        await asyncio.sleep(4)
    except Exception as e: logging.info(f"Redirection info: {e}")

    await smart_location_popup_handler(page, chat_id=chat_id, context=context)

    profile_location = await page.evaluate("""() => {
        const headerLoc = document.querySelector('div[id*="pincode"], div[class*="location"], [class*="delivery-pincode"], div.css-11a2v8e');
        if (headerLoc && headerLoc.innerText) return headerLoc.innerText.trim().replace(/\\n/g, ' ');
        return "Unknown";
    }""")

    profile_name = await page.evaluate("""() => {
        const nameEl = document.querySelector('div[class*="user-name"], h2[class*="name"], span[class*="name"]');
        if (nameEl && nameEl.innerText) return nameEl.innerText.trim();
        return "User";
    }""")

    await status_msg.edit_text(f"👤 **Profile Data Captured:**\n📍 Location: `{profile_location}`\n👤 Name: `{profile_name}`", parse_mode="Markdown")

    session_file_path = os.path.join(SESSION_DIR, f"{secret_key}.json")
    await ctx.storage_state(path=session_file_path)

    configs = load_configs()
    if chat_id not in configs: configs[chat_id] = {"saved_keys": {}, "active_key": ""}
    if "saved_keys" not in configs[chat_id]: configs[chat_id]["saved_keys"] = {}
    configs[chat_id]["saved_keys"][secret_key] = session_file_path
    configs[chat_id]["active_key"] = secret_key
    save_configs(configs)

    await status_msg.edit_text(f"✅ **Session Saved Successfully!**\n🔑 Key: `{secret_key}`\n📁 Path: `{session_file_path}`", parse_mode="Markdown")

    try:
        await browser.close()
        await p.stop()
    except Exception: pass

    context.user_data.pop("playwright_p", None)
    context.user_data.pop("browser", None)
    context.user_data.pop("ctx", None)
    context.user_data.pop("page", None)

    await context.bot.send_message(chat_id=chat_id, text="🎉 **Login P2 Flow Complete!** You can now use the bot features.", parse_mode="Markdown")

# ==========================================
# 💳 PAY NOW (BROWSER) CONTROLLER
# ==========================================
async def continue_old_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    str_id = str(update.effective_chat.id); cfg = load_configs().get(str_id, {})
    active_key = cfg.get("active_key", "")
    
    if not active_key: return await query.message.reply_text("❌ No active key selected!")
        
    auth_file = cfg.get("saved_keys", {}).get(active_key)
    if auth_file and os.path.exists(auth_file):
        msg_obj = await query.message.reply_text(f"🚀 Running Pay Now (Browser) via Key: `{active_key}`...")
        task_id = str(int(time.time() * 1000))[-6:]
        context.application.create_task(run_classic_order_engine_execution(msg_obj, context, cfg, auth_file, active_key, task_id, batch_mode=False))

# ==========================================================
# 🛒 ORDER EXECUTION ENGINE (CART -> PAY ONLINE -> COD)
# ==========================================================
async def run_classic_order_engine_execution(message_obj, context, data, auth_file, key_used, task_id, batch_mode=False):
    chat_id = message_obj.chat_id
    str_id = str(chat_id)
    headless_mode = False
    
    track_msg = await message_obj.reply_text(f"🔄 **[ID: {task_id}]** Setting up browser...")
    
    async def update_track(text):
        if track_msg:
            try: await track_msg.edit_text(f"🔄 **[ID: {task_id}]** {text}")
            except: pass

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=headless_mode, args=["--disable-notifications"])
    if "running_tasks" not in context.user_data: context.user_data["running_tasks"] = {}
    
    try:
        ctx = await browser.new_context(
            storage_state=auth_file,
            viewport=FIXED_VIEWPORT,
            user_agent=MOBILE_USER_AGENT,
            is_mobile=True,
            has_touch=True
        )
        page = await ctx.new_page()
        context.user_data["running_tasks"][task_id] = {"playwright_p": p, "browser": browser, "page": page}

        await update_track("Opening Cart Bag page...")
        try: await page.goto("https://www.jiomart.com/cart/bag", wait_until="domcontentloaded", timeout=30000)
        except Exception: pass
            
        await page.wait_for_timeout(3000)
        await smart_location_popup_handler(page)

        cart_img_path = os.path.join(SESSION_DIR, f"cart_{task_id}.png")
        try:
            await page.screenshot(path=cart_img_path, full_page=True)
            if os.path.exists(cart_img_path):
                with open(cart_img_path, 'rb') as f:
                    await context.bot.send_photo(chat_id=chat_id, photo=f, caption=f"🛒 **[ID: {task_id}] Cart Items**")
                os.remove(cart_img_path)
        except Exception: pass

        billing_data = await page.evaluate(r"""() => {
            let res = { final_billing: "₹0.00" };
            try {
                let match = document.body.innerText.match(/\u20b9[\d,.]+/g);
                if (match && match.length > 0) res.final_billing = match[match.length - 1];
            } catch(e) {}
            return res;
        }""")

        await message_obj.reply_text(f"✅ Order Stage Ready [ID: {task_id}]\n💳 Billing Amount: {billing_data.get('final_billing', '₹0.00')}")
        try: await track_msg.delete()
        except: pass

        process_msg = await message_obj.reply_text("🔄 Clicking Pay Online / Pay Now...")
        return await execute_payment_step(task_id, chat_id, process_msg, context, batch_mode)

    except Exception as e:
        await message_obj.reply_text(f"❌ Error: {str(e)[:50]}")
        if task_id in context.user_data.get("running_tasks", {}): context.user_data["running_tasks"].pop(task_id)
        try: await browser.close(); await p.stop()
        except: pass
        return False

async def execute_payment_step(task_id, chat_id, message_obj, context, batch_mode):
    task_env = context.user_data.get("running_tasks", {}).get(task_id)
    if not task_env: return False
    page = task_env["page"]
    browser = task_env["browser"]
    p = task_env["playwright_p"]

    try:
        pay_now_btn = page.get_by_text("Pay Online").or_(page.get_by_text("Pay now")).or_(page.get_by_text("Proceed to Checkout")).first
        try: await pay_now_btn.wait_for(state="visible", timeout=15000)
        except: pass

        if await pay_now_btn.is_visible():
            try: await pay_now_btn.click(timeout=5000)
            except: await pay_now_btn.evaluate("el => el.click()")
            
            await page.wait_for_timeout(8000)

            # SCROLL 2 TIMES & SELECT COD
            try:
                for _ in range(2):
                    await page.keyboard.press("PageDown")
                    await page.wait_for_timeout(800)
            except: pass

            cod_clicked = False
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                js_cod = """() => {
                    let elems = Array.from(document.querySelectorAll('*')).filter(el => el.innerText && el.innerText.includes('Cash on Delivery'));
                    if (elems.length > 0) { elems[elems.length - 1].click(); return true; }
                    return false;
                }"""
                cod_clicked = await page.evaluate(js_cod)
            except Exception: pass

            await page.wait_for_timeout(8000)

            payment_img_path = os.path.join(SESSION_DIR, f"payment_{task_id}.png")
            try:
                await page.screenshot(path=payment_img_path, full_page=True)
                if os.path.exists(payment_img_path):
                    with open(payment_img_path, 'rb') as f:
                        await context.bot.send_photo(chat_id=chat_id, photo=f, caption=f"💳 **[ID: {task_id}] Payment Screen (COD Selected)**")
                    os.remove(payment_img_path)
            except Exception: pass

            if batch_mode: return True
            else:
                keyboard = [
                    [InlineKeyboardButton("✅ Confirm Proceed Click", callback_data=f"fast_confirm_m2_{task_id}")],
                    [InlineKeyboardButton("❌ Cancel Order", callback_data=f"fast_confirm_cancel_{task_id}")]
                ]
                await message_obj.reply_text(f"⚠️ **CONFIRM YOUR ORDER READY [ID: {task_id}]**", reply_markup=InlineKeyboardMarkup(keyboard))
                return True
        else:
            await message_obj.reply_text("❌ Error: Pay Online / Pay Now button not found.")
            context.user_data.get("running_tasks", {}).pop(task_id, None)
            await browser.close(); await p.stop()
            return False

    except Exception as e:
        await message_obj.reply_text(f"❌ Payment Error: {str(e)[:50]}")
        context.user_data.get("running_tasks", {}).pop(task_id, None)
        try: await browser.close(); await p.stop()
        except: pass
        return False

async def process_single_fast_run_button_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    parts = query.data.split("_")
    action_type, task_id = parts[2], parts[3]
    
    running_tasks = context.user_data.get("running_tasks", {})
    task_env = running_tasks.get(task_id)
    
    if not task_env:
        return await query.message.reply_text("❌ Task expired or not found.")
    
    page = task_env["page"]
    browser = task_env["browser"]
    p = task_env["playwright_p"]
    
    if action_type == "cancel":
        try: await browser.close(); await p.stop()
        except: pass
        running_tasks.pop(task_id, None)
        return await query.message.reply_text("❌ Order Cancelled.")
    
    try:
        js_code = """() => {
            let btns = Array.from(document.querySelectorAll('button, div[role="button"]')).filter(b => b.innerText.trim() === 'Proceed');
            if (btns.length > 0) btns[btns.length - 1].click();
        }"""
        await page.evaluate(js_code)
        
        await asyncio.sleep(5)

        ss_path = os.path.join(SESSION_DIR, f"success_{task_id}.png")
        try: await page.screenshot(path=ss_path, full_page=True)
        except: pass

        if os.path.exists(ss_path):
            with open(ss_path, 'rb') as f:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f, caption=f"✅ **[ID: {task_id}] Order Placed Successfully!**")
            os.remove(ss_path)
            
        running_tasks.pop(task_id, None)
        try: await task_env["browser"].close(); await task_env["playwright_p"].stop()
        except: pass

    except Exception as e:
        await update.effective_chat.send_message(f"❌ Error during Proceed click: {str(e)[:50]}")

# ==========================================
# 🔗 MULTI PAY NOW (ORIGINAL FROM LOGIN.PY)
# ==========================================
def get_dynamic_batch_keyboard(batch_id, tasks_dict, status):
    keyboard = []
    if status in ["preparing", "wait_confirm"]: 
        keyboard.append([InlineKeyboardButton("⏳ Wait & Confirm All", callback_data=f"bdyn_waitconfirm_{batch_id}"), InlineKeyboardButton("❌ Cancel All", callback_data=f"bdyn_cancel_{batch_id}")])
    elif status in ["complete", "paying"]: 
        keyboard.append([InlineKeyboardButton("⚡ CONFIRM ALL READY (3s Gap)", callback_data=f"bdyn_payall_{batch_id}"), InlineKeyboardButton("❌ CANCEL ALL", callback_data=f"bdyn_cancel_{batch_id}")])
    elif status == "done":
        keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_to_main_dashboard")])
        
    for idx, (t_id, t_status) in enumerate(tasks_dict.items()):
        if t_status == "ready": keyboard.append([InlineKeyboardButton(f"✅ Confirm #{idx+1}", callback_data=f"bdyn_paysin_{batch_id}_{t_id}"), InlineKeyboardButton(f"❌ Cancel #{idx+1}", callback_data=f"bdyn_cancelsin_{batch_id}_{t_id}")])
        elif t_status == "paid": keyboard.append([InlineKeyboardButton(f"🟢 Order #{idx+1} Placed Successfully", callback_data="none")])
        elif t_status == "cancelled": keyboard.append([InlineKeyboardButton(f"🚫 Order #{idx+1} Cancelled", callback_data="none")])
        elif t_status == "failed": keyboard.append([InlineKeyboardButton(f"⚠️ Order #{idx+1} Failed", callback_data="none")])
    return InlineKeyboardMarkup(keyboard)

async def multi_continue_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if "batch_size" not in context.user_data: context.user_data["batch_size"] = 2
    size = context.user_data["batch_size"]
    keyboard = [
        [InlineKeyboardButton("➖ 1", callback_data="batch_size_minus"), InlineKeyboardButton(f"Count: {size} Orders", callback_data="none"), InlineKeyboardButton("➕ 1", callback_data="batch_size_plus")],
        [InlineKeyboardButton("🚀 Start Batch Orders", callback_data="start_dynamic_batch")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main_dashboard")]
    ]
    text = "🔗 **Multi Pay Now Mode**\nSelect the number of orders to process simultaneously:"
    try: await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except: pass

async def handle_batch_size_modifiers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == "batch_size_minus" and context.user_data.get("batch_size", 2) > 1: context.user_data["batch_size"] -= 1
    elif query.data == "batch_size_plus" and context.user_data.get("batch_size", 2) < 999: context.user_data["batch_size"] += 1
    await multi_continue_menu_handler(update, context)

async def start_dynamic_batch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    count = context.user_data.get("batch_size", 2)
    str_id = str(update.effective_chat.id); cfg = load_configs().get(str_id, {})
    active_key = cfg.get("active_key", "")
    
    if not active_key: return await query.message.reply_text("❌ No active key selected!")
        
    auth_file = cfg.get("saved_keys", {}).get(active_key)
    if auth_file and os.path.exists(auth_file):
        batch_id = str(int(time.time() * 1000))[-4:]
        if "batches" not in context.user_data: context.user_data["batches"] = {}
        batch_tasks = {}
        context.user_data["batches"][batch_id] = {"status": "preparing", "auto_confirm": False, "tasks": batch_tasks}
        
        status_msg = await query.message.reply_text(f"⏳ **Live Batch Engine Started ({count} Orders)...**", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, "preparing"))
        context.application.create_task(execute_batch_orders_dynamic(status_msg, context, cfg, auth_file, active_key, count, batch_id))

async def execute_batch_orders_dynamic(status_msg, context, data, auth_file, key_used, count, batch_id):
    batch_info = context.user_data["batches"].get(batch_id)
    if not batch_info: return
    batch_tasks = batch_info["tasks"]
    
    for i in range(count):
        if context.user_data["batches"].get(batch_id, {}).get("status") not in ["preparing", "wait_confirm"]: break
        task_id = f"b{batch_id}_{i+1}"
        
        try:
            await status_msg.edit_text(f"⏳ **Live Batch Engine Running...**\nPreparing Order {i+1} of {count}...", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, batch_info["status"]))
        except Exception: pass
        
        success = await run_classic_order_engine_execution(status_msg, context, data, auth_file, key_used, task_id, batch_mode=True)
        batch_tasks[task_id] = "ready" if success else "failed"
        
        try:
            await status_msg.edit_text(f"⏳ **Live Batch Engine Running...**\nOrder {i+1} Ready!", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, batch_info["status"]))
        except Exception: pass
    
    final_info = context.user_data["batches"].get(batch_id)
    if final_info:
        final_info["status"] = "complete"
        if final_info.get("auto_confirm", False):
            await trigger_batch_pay_all(status_msg, context, batch_id)
        else:
            try: await status_msg.edit_text(f"🎉 **Batch Preparation Complete!**\nAll {count} orders processed.", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, "complete"))
            except Exception: pass

async def trigger_batch_pay_all(status_msg, context, batch_id):
    batch_info = context.user_data["batches"].get(batch_id)
    running_tasks = context.user_data.get("running_tasks", {})
    if not batch_info: return
    batch_tasks = batch_info["tasks"]

    batch_info["status"] = "paying"
    try: await status_msg.edit_text("⚡ **Auto-Confirm Triggered!** Executing Proceed clicks with 3-second gaps...", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, "paying"))
    except Exception: pass
    
    for t_id, t_status in list(batch_tasks.items()):
        if t_status == "ready":
            task_env = running_tasks.get(t_id)
            if task_env:
                try:
                    page = task_env["page"]
                    await page.bring_to_front()
                    await page.evaluate("""() => {
                        let btns = Array.from(document.querySelectorAll('button, div[role="button"]')).filter(b => b.innerText.trim() === 'Proceed');
                        if (btns.length > 0) btns[btns.length - 1].click();
                    }""")
                    
                    await asyncio.sleep(5)
                    ss_path = os.path.join(SESSION_DIR, f"success_{t_id}.png")
                    try:
                        await page.screenshot(path=ss_path, full_page=True)
                        if os.path.exists(ss_path):
                            with open(ss_path, 'rb') as f:
                                await context.bot.send_photo(chat_id=status_msg.chat_id, photo=f, caption=f"✅ **[ID: {t_id}] Batch Order Placed Successfully!**")
                            os.remove(ss_path)
                    except: pass
                    batch_tasks[t_id] = "paid"
                except: batch_tasks[t_id] = "failed"
                
                try: await status_msg.edit_reply_markup(reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, "paying"))
                except Exception: pass
                
                try: await task_env["browser"].close(); await task_env["playwright_p"].stop()
                except: pass
                running_tasks.pop(t_id, None)
                await asyncio.sleep(3)

    batch_info["status"] = "done"
    try: await status_msg.edit_text("🎊 **Batch Execution Complete!** All eligible orders processed.", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, "done"))
    except Exception: pass

async def handle_dynamic_batch_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    parts = query.data.split("_")
    action, batch_id = parts[1], parts[2]
    
    batch_info = context.user_data["batches"].get(batch_id)
    running_tasks = context.user_data.get("running_tasks", {})
    if not batch_info: return await query.message.reply_text("❌ Batch expired or already completed.")

    if action == "waitconfirm":
        batch_info["auto_confirm"] = True
        if batch_info["status"] == "complete":
            await trigger_batch_pay_all(query.message, context, batch_id)
        else:
            batch_info["status"] = "wait_confirm"
            try: await query.message.edit_text("⏳ **Wait & Confirm Activated!**", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_info["tasks"], "wait_confirm"))
            except Exception: pass

    elif action == "payall":
        await trigger_batch_pay_all(query.message, context, batch_id)
        
    elif action == "cancel":
        batch_info["status"] = "cancelled"
        for t_id in list(running_tasks.keys()):
            if t_id.startswith(f"b{batch_id}_"):
                task_env = running_tasks.pop(t_id, None)
                if task_env:
                    try: await task_env["browser"].close(); await task_env["playwright_p"].stop()
                    except: pass
        for t_id, t_status in list(batch_tasks.items()):
            if t_status != "paid": batch_tasks[t_id] = "cancelled"
        try: await query.message.edit_text("❌ **Batch Cancelled!**", reply_markup=get_dynamic_batch_keyboard(batch_id, batch_tasks, "done"))
        except Exception: pass

    elif action in ["paysin", "cancelsin"]:
        t_id = f"{parts[3]}_{parts[4]}"
        task_env = running_tasks.pop(t_id, None)
        if action == "paysin" and task_env:
            try:
                page = task_env["page"]
                await page.bring_to_front()
                await page.evaluate("""() => {
                    let btns = Array.from(document.querySelectorAll('button, div[role="button"]')).filter(b => b.innerText.trim() === 'Proceed');
                    if (btns.length > 0) btns[btns.length - 1].click();
                }""")
                await asyncio.sleep(5)
                ss_path = os.path.join(SESSION_DIR, f"success_{t_id}.png")
                try:
                    await page.screenshot(path=ss_path, full_page=True)
                    if os.path.exists(ss_path):
                        with open(ss_path, 'rb') as f:
                            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f, caption=f"✅ **[ID: {t_id}] Order Placed Successfully!**")
                        os.remove(ss_path)
                except: pass
                batch_info["tasks"][t_id] = "paid"
            except: batch_info["tasks"][t_id] = "failed"
        else: batch_info["tasks"][t_id] = "cancelled"
        if task_env:
            try: await task_env["browser"].close(); await task_env["playwright_p"].stop()
            except: pass
        try: await query.message.edit_reply_markup(reply_markup=get_dynamic_batch_keyboard(batch_id, batch_info["tasks"], batch_info["status"]))
        except Exception: pass

async def back_to_main_dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await start_command(update, context)

# ==========================================
# ୨. DATABASE & FILE HELPERS
# ==========================================
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_products():
    return load_json(PRODUCT_DB, {})

def save_products(data):
    save_json(PRODUCT_DB, data)

def load_configs():
    return load_json(CONFIG_DB, {})

def save_configs(data):
    save_json(CONFIG_DB, data)

def load_cart(chat_id):
    return load_json(CART_DB, {}).get(str(chat_id), {})

def save_cart(chat_id, cart_data):
    data = load_json(CART_DB, {})
    data[str(chat_id)] = cart_data
    save_json(CART_DB, data)

def load_offline_addresses(chat_id):
    return load_json(ADDRESS_OFFLINE_DB, {}).get(str(chat_id), [])

def save_offline_addresses(chat_id, addr_list):
    all_data = load_json(ADDRESS_OFFLINE_DB, {})
    all_data[str(chat_id)] = addr_list
    save_json(ADDRESS_OFFLINE_DB, all_data)

def save_article_cache(chat_id, cache_map):
    all_cache = load_json(ARTICLE_CACHE_DB, {})
    all_cache[str(chat_id)] = cache_map
    save_json(ARTICLE_CACHE_DB, all_cache)

def get_cached_item_details(chat_id, item_id):
    user_cache = load_json(ARTICLE_CACHE_DB, {}).get(str(chat_id), {})
    return user_cache.get(str(item_id), {})

def get_sync_mode(chat_id):
    configs = load_configs().get(str(chat_id), {})
    return configs.get("sync_mode", "DIRECT")

def set_sync_mode(chat_id, mode):
    configs = load_configs()
    chat_str = str(chat_id)
    if chat_str not in configs:
        configs[chat_str] = {}
    configs[chat_str]["sync_mode"] = mode
    save_configs(configs)

def backup_session_file(auth_file):
    if auth_file and os.path.exists(auth_file):
        try:
            backup_path = auth_file + ".tmp"
            shutil.copy2(auth_file, backup_path)
            logger.info(f"Temporary session copy created at {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Backup failed: {e}")
    return None

def is_valid_cart_id_format(cid):
    if not cid or str(cid).strip() in ["None", "", "null", "undefined"]:
        return False
    return True

def get_saved_cart_id(chat_id):
    configs = load_configs().get(str(chat_id), {})
    active_key = configs.get("active_key", "")
    if not active_key:
        return ""
    cid = configs.get("saved_cart_ids", {}).get(active_key, "")
    if is_valid_cart_id_format(cid):
        return str(cid)
    return ""

def set_saved_cart_id(chat_id, cart_id):
    if not is_valid_cart_id_format(cart_id):
        return
    configs = load_configs()
    chat_str = str(chat_id)
    if chat_str not in configs:
        configs[chat_str] = {}
    active_key = configs[chat_str].get("active_key", "")
    if not active_key:
        return
    if "saved_cart_ids" not in configs[chat_str]:
        configs[chat_str]["saved_cart_ids"] = {}
    configs[chat_str]["saved_cart_ids"][active_key] = str(cart_id)
    save_configs(configs)

def clear_saved_cart_id(chat_id):
    configs = load_configs()
    chat_str = str(chat_id)
    active_key = configs.get(chat_str, {}).get("active_key", "")
    if active_key and "saved_cart_ids" in configs.get(chat_str, {}):
        configs[chat_str]["saved_cart_ids"].pop(active_key, None)
        save_configs(configs)

def get_active_session_auth_and_pincode(chat_id):
    configs = load_configs().get(str(chat_id), {})
    pincode = configs.get("pincode", "754011")
    active_key = configs.get("active_key", "")
    auth_file = configs.get("saved_keys", {}).get(active_key) if active_key else None

    cookie_dict = {}
    access_token = configs.get("active_token", "")

    if auth_file and os.path.exists(auth_file):
        try:
            with open(auth_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                for c in state.get("cookies", []):
                    cookie_dict[c["name"]] = c["value"]
                    if c["name"] == "cra_access_token" and not access_token:
                        access_token = c["value"]
        except Exception as e:
            logger.error(f"Error loading auth file: {e}")

    return auth_file, pincode, active_key, cookie_dict, access_token

def update_session_file_token_and_pincode(auth_file, new_token="", new_pin="", address_id=""):
    if auth_file and os.path.exists(auth_file):
        try:
            with open(auth_file, "r", encoding="utf-8") as f:
                state = json.load(f)

            cookies = state.get("cookies", [])
            existing_names = [c.get("name") for c in cookies]

            if new_pin:
                for c in cookies:
                    if c.get("name") in ["pincode", "x-pincode", "glo_pincode", "delivery_pincode"]:
                        c["value"] = str(new_pin)

            if address_id:
                for c in cookies:
                    if c.get("name") in ["default_address_id", "selected_address_id"]:
                        c["value"] = str(address_id)

            if new_token:
                clean_tok = new_token.replace("Bearer ", "")
                if "cra_access_token" in existing_names:
                    for c in cookies:
                        if c.get("name") == "cra_access_token":
                            c["value"] = clean_tok
                else:
                    cookies.append({
                        "name": "cra_access_token",
                        "value": clean_tok,
                        "domain": ".jiomart.com",
                        "path": "/",
                        "expires": 253402300799,
                        "httpOnly": False,
                        "secure": False,
                        "sameSite": "Lax"
                    })

            state["cookies"] = cookies
            with open(auth_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            logger.error(f"Error updating session file token/pincode: {e}")

def generate_secret_key(phone):
    return f"key_{phone[-4:]}_{int(time.time())}"

def has_access(chat_id):
    return int(chat_id) == ADMIN_ID or ADMIN_ID == 0

def has_key_sharing_permission(chat_id):
    """Check if user has permission to share keys"""
    if int(chat_id) == ADMIN_ID or ADMIN_ID == 0:
        return True
    
    configs = load_configs()
    user_data = configs.get(str(chat_id), {})
    return user_data.get("can_share_keys", False)

# ==========================================
# ୩. HELPER & FORMATTING FUNCTIONS
# ==========================================
def extract_item_id_from_url(url):
    if not url:
        return None
    match = re.search(r"(?:-|\/)(\d{5,10})/?$", url)
    if match:
        return int(match.group(1))
    match_fallback = re.search(r"(\d{5,10})", url)
    return int(match_fallback.group(1)) if match_fallback else None

def extract_product_name_from_url(url):
    match = re.search(r"/(?:product|item)/([^/?]+)", url)
    if not match:
        return "Custom_Product_" + str(len(url))
    slug = match.group(1)
    parts = slug.rsplit("-", 2)
    if len(parts) > 1:
        return parts[0]
    return slug

def format_jiomart_url(url):
    url = url.strip()
    url = re.sub(r"https?://[^/]*jiomartjcp[^/]*", "https://www.jiomart.com", url)
    url = url.replace(".com.com", ".com")
    prod_path_match = re.search(r"/(product/[a-zA-Z0-9\-_]+(?:-\d+)?)", url)
    if prod_path_match:
        return f"https://www.jiomart.com/{prod_path_match.group(1)}"
    if not url.startswith("http"):
        return (
            f"https://www.jiomart.com{url}"
            if url.startswith("/")
            else f"https://www.jiomart.com/{url}"
        )
    return url

def get_smart_name(slug):
    clean_name = re.sub(r"-[a-z0-9]{5,}-\d{5,}$", "", slug)
    clean_name = re.sub(r"-\d{5,}$", "", clean_name)
    words = clean_name.split("-")
    res = " ".join([w.capitalize() for w in words if w])
    if len(res) > 18:
        return res[:16] + ".."
    return res

def extract_unit_price_from_item_node(itm):
    qty = int(itm.get("quantity", 1))
    if qty <= 0:
        qty = 1

    price_info = itm.get("price", {})
    total_price = None

    if isinstance(price_info, dict):
        base = price_info.get("base", {})
        if isinstance(base, dict) and base:
            total_price = (
                base.get("selling") or base.get("effective") or base.get("marked")
            )

        if total_price is None:
            total_price = (
                price_info.get("selling_price")
                or price_info.get("selling")
                or price_info.get("effective_price")
                or price_info.get("effective")
                or price_info.get("mrp")
            )

    if total_price is not None:
        try:
            total_p_float = float(total_price)
            return round(total_p_float / qty, 2)
        except Exception:
            pass

    return None

def build_dynamic_location_headers(pincode, access_token=""):
    loc_detail = {
        "country": "INDIA",
        "country_iso_code": "IN",
        "pincode": str(pincode),
    }
    geolocation = {"polygon_ids": ["U1QE_QC_11b47dae"]}
    visitor_id = "v2_jiomart_session_visitor"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.jiomart.com",
        "Referer": "https://www.jiomart.com/",
        "Host": "www.jiomart.com",
        "pincode": str(pincode),
        "x-pincode": str(pincode),
        "glo_pincode": str(pincode),
        "x-glo-pincode": str(pincode),
        "visitor-id": visitor_id,
        "x-visitor-id": visitor_id,
        "X-Location-Detail": json.dumps(loc_detail),
        "X-Geolocation": json.dumps(geolocation),
    }
    if access_token:
        headers["authorization"] = (
            access_token if access_token.startswith("Bearer ") else f"Bearer {access_token}"
        )
    return headers

def update_product_library_smart(new_name, new_url):
    products = load_products()
    new_item_id = extract_item_id_from_url(new_url)

    if new_item_id:
        keys_to_delete = []
        for name, url in products.items():
            existing_id = extract_item_id_from_url(url)
            if existing_id and existing_id == new_item_id:
                if name != new_name:
                    keys_to_delete.append(name)

        for k in keys_to_delete:
            del products[k]
            GLOBAL_PRICES.pop(k, None)

    products[new_name] = new_url
    save_products(products)

async def interruptible_sleep(chat_id, delay_seconds):
    steps = int(delay_seconds * 10)
    for _ in range(steps):
        if FORCE_STOP_FLAG.get(str(chat_id)):
            raise asyncio.CancelledError("Force stop triggered during sleep.")
        await asyncio.sleep(0.1)

async def purge_otp_inputs_right_to_left(page):
    try:
        inputs = page.locator("input")
        count = await inputs.count()
        for i in range(count - 1, -1, -1):
            await inputs.nth(i).click()
            await page.keyboard.press("Backspace")
    except Exception:
        pass

async def execute_global_emergency_kill(chat_id, context):
    try:
        if "browser" in context.user_data and context.user_data["browser"]:
            await context.user_data["browser"].close()
        if "playwright_p" in context.user_data and context.user_data["playwright_p"]:
            await context.user_data["playwright_p"].stop()
    except Exception:
        pass
    try:
        if os.name == 'nt':
            subprocess.run("taskkill /f /im chrome.exe", shell=True, stdout=subprocess.DEVNULL)
        else:
            subprocess.run("pkill -f chrome", shell=True, stdout=subprocess.DEVNULL)
    except Exception:
        pass


# ==========================================
# 🧾 MY ORDERS HISTORY MODULE
# ==========================================
async def check_order_history_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    chat_id = update.effective_chat.id
    str_id = str(chat_id)
    cfg = load_configs().get(str_id, {})
    auth_file = cfg.get("saved_keys", {}).get(cfg.get("active_key", ""))
    
    msg_func = query.message.reply_text if query else update.message.reply_text
    if not auth_file or not os.path.exists(auth_file): 
        return await msg_func("❌ No valid active session key found. Please login first.")
    
    status_msg = await msg_func("🧾 Fetching My Orders Page & taking screenshot... Please wait ⏳")
    
    async def run_history_reader():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(storage_state=auth_file, viewport={'width': 1280, 'height': 1500})
                page = await ctx.new_page()
                await page.goto("https://www.jiomart.com/profile/orders", wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(8000)
                img_path = os.path.join(SESSION_DIR, f"orders_{chat_id}.png")
                await page.screenshot(path=img_path, full_page=True)
                
                with open(img_path, 'rb') as f:
                    await context.bot.send_photo(chat_id=chat_id, photo=f, caption="🧾 **Recent Orders History:**", parse_mode="Markdown")
                
                await status_msg.delete()
                if os.path.exists(img_path): os.remove(img_path)
            except Exception as e: 
                await status_msg.edit_text(f"❌ Screenshot Error: {str(e)}")
            finally: 
                await browser.close()
                
    asyncio.create_task(run_history_reader())

# ==========================================
# 👥 ADMIN VIEW ALL USERS KEYS MODULE
# ==========================================
async def admin_view_all_keys_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    chat_id = update.effective_chat.id
    if not has_access(chat_id): 
        msg_func = query.message.reply_text if query else update.message.reply_text
        return await msg_func("❌ ଏହି ଅପ୍ସନ୍ ପାଇଁ ଆପଣଙ୍କ ପାଖରେ ଅନୁମତି ନାହିଁ।")
    
    configs = load_configs()
    keyboard = []
    found_any = False
    
    for u_id, config in configs.items():
        if isinstance(config, dict) and "saved_keys" in config:
            for secret_key in config["saved_keys"].keys():
                found_any = True
                keyboard.append([InlineKeyboardButton(f"🔑 Key: {secret_key} (User: {u_id})", callback_data=f"switch_to_shared_{u_id}_{secret_key}")])
                
    if not found_any:
        msg_func = query.message.reply_text if query else update.message.reply_text
        await msg_func("❌ ଡାଟାବେସ୍‌ରେ କୌଣସି Saved Session Key ମିଳିଲା ନାହିଁ।")
        return await start_command(update, context)
    
    keyboard.append([InlineKeyboardButton("🏠 Back to Menu", callback_data="main_menu")])
    if query:
        await query.message.edit_text("👥 **ସବୁ ୟୁଜର୍‌ଙ୍କ Session Keys ଲିଷ୍ଟ୍:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text("👥 **ସବୁ ୟୁଜର୍‌ଙ୍କ Session Keys ଲିଷ୍ଟ୍:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def handle_admin_switch_shared_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_parts = query.data.split("_")
    target_uid = data_parts[3]
    target_key = "_".join(data_parts[4:])
    
    configs = load_configs()
    chat_str = str(update.effective_chat.id)
    if chat_str not in configs: configs[chat_str] = {}
    if "saved_keys" not in configs[chat_str]: configs[chat_str]["saved_keys"] = {}
    
    configs[chat_str]["active_key"] = target_key
    configs[chat_str]["saved_keys"][target_key] = configs[target_uid]["saved_keys"][target_key]
    save_configs(configs)
    
    status_msg = await query.message.reply_text(f"👑 Admin Override: Switched to Shared Key `{target_key}` from User `{target_uid}`.\n⏳ Auto-syncing account...", parse_mode="Markdown")
    
    # Auto sync account after switching
    success, report = await sync_account_full_engine(chat_str, status_msg=status_msg)
    await status_msg.edit_text(report, parse_mode="HTML")
    
    await start_command(update, context)

# ==========================================
# ୪. ACCOUNT & SESSION ATOMIC REPLACE ENGINE
# ==========================================
async def api_delete_all_addresses(chat_id):
    chat_id_str = str(chat_id)
    logger.info(f"🧹 Deleting ALL addresses for {chat_id}")
    
    addrs = await get_user_saved_addresses(chat_id)
    if not addrs:
        logger.info("No addresses found to delete.")
        return {"success": True, "deleted_count": 0}

    deleted_count = 0
    for addr in addrs:
        if FORCE_STOP_FLAG.get(chat_id_str):
            break
        a_id = str(addr.get("id", ""))
        logger.info(f"🗑️ Deleting Address ID: {a_id}")
        try:
            result = await api_delete_jiomart_address(chat_id, a_id)
            if result.get("success"):
                deleted_count += 1
                logger.info(f"✅ Deleted {a_id}")
            else:
                logger.warning(f"⚠️ Failed to delete {a_id}: {result.get('error')}")
            await interruptible_sleep(chat_id_str, 0.5)
        except Exception as e:
            logger.error(f"Delete error for {a_id}: {e}")

    logger.info(f"✅ Deleted {deleted_count} addresses.")
    return {"success": True, "deleted_count": deleted_count}

async def execute_locked_address_click_and_save(chat_id, status_msg=None, bot=None):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, _, _ = get_active_session_auth_and_pincode(chat_id_str)

    if not auth_file or not os.path.exists(auth_file):
        return False

    backup_session_file(auth_file)
    configs = load_configs().get(chat_id_str, {})
    lat_val = float(configs.get("latitude", 20.060583))
    long_val = float(configs.get("longitude", 86.004619))
    is_visible = configs.get("browser_visible", False)

    logs = []
    async def add_log(text):
        logs.append(text)
        logger.info(text)
        if status_msg:
            try:
                msg_text = f"🖥️ <b>Auto Sync [{VERSION}]:</b>\n\n" + "\n".join(logs[-7:])
                await status_msg.edit_text(msg_text, parse_mode="HTML")
            except Exception:
                pass

    browser = None
    try:
        await add_log("🔒 Temporary Session copy created.")
        await add_log("📱 Opening Cart Page in Mobile View...")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not is_visible)
            ctx = await browser.new_context(
                viewport={"width": 393, "height": 852},
                is_mobile=True,
                has_touch=True,
                permissions=["geolocation"],
                geolocation={"latitude": lat_val, "longitude": long_val},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                storage_state=auth_file
            )
            page = await ctx.new_page()

            await page.goto("https://www.jiomart.com/cart/bag", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(15000)

            await add_log("🖱️ [Click 1] Executing Header Tap...")
            header_selectors = ["text=India", "text=ODISHA", "#btn-delivery-pincode", ".rel-header-deliver-to", "header"]
            for sel in header_selectors:
                try:
                    if await page.is_visible(sel):
                        await page.tap(sel)
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(5000)
            await add_log("🖱️ [Click 2] Executing Ultra-Native Home Click...")

            home_locators = [
                page.get_by_text("Home", exact=True),
                page.locator("div:has-text('Home')").last,
                page.locator("[class*='home']").first,
                page.locator(".saved-address-card").first
            ]

            for loc in home_locators:
                try:
                    if await loc.is_visible(timeout=2000):
                        await loc.tap(force=True)
                        await loc.dispatch_event("touchstart")
                        await loc.dispatch_event("touchend")
                        await loc.click(force=True)
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(15000)
            screenshot_bytes = await page.screenshot(full_page=True)

            if auth_file and os.path.exists(auth_file):
                try:
                    os.remove(auth_file)
                except Exception:
                    pass
            
            await ctx.storage_state(path=auth_file)
            await add_log("💾 Saved updated session state.")

            if bot:
                caption_text = "🎉 <b>Address Switch Automation Complete!</b>"
                await bot.send_photo(chat_id=chat_id, photo=screenshot_bytes, caption=caption_text, parse_mode="HTML")

            await browser.close()
            browser = None
            return True

    except Exception as e:
        await add_log(f"❌ Error: {str(e)[:50]}")
        return False
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass

async def clean_inject_click_save(chat_id, addr_dict, target_hex_id="", status_msg=None, bot=None):
    if status_msg:
        try:
            await status_msg.edit_text(f"🖥️ <b>Auto Sync [{VERSION}]:</b>\n\n🧹 Step 1: Deleting ALL old addresses...", parse_mode="HTML")
        except Exception:
            pass

    await api_delete_all_addresses(chat_id)
    
    if status_msg:
        try:
            await status_msg.edit_text(f"🖥️ <b>Auto Sync [{VERSION}]:</b>\n\n📦 Step 2: Injecting new address via API...", parse_mode="HTML")
        except Exception:
            pass

    inject_res = await api_add_jiomart_address(chat_id, addr_dict)
    if not inject_res.get("success"):
        if status_msg:
            try:
                await status_msg.edit_text(f"❌ API Injection Failed: {html.escape(str(inject_res.get('error')))}", parse_mode="HTML")
            except Exception:
                pass
        return False

    return await execute_locked_address_click_and_save(chat_id, status_msg=status_msg, bot=bot)

# =========================================================================
# 🔄 SYNC ACCOUNT FULL ENGINE
# =========================================================================
async def sync_account_full_engine(chat_id, status_msg=None, session_key_override=None):
    chat_id_str = str(chat_id)
    configs = load_configs().get(chat_id_str, {})
    active_key = session_key_override or configs.get("active_key", "")
    auth_file = configs.get("saved_keys", {}).get(active_key) if active_key else None
    pincode = configs.get("pincode", "754011")

    if not active_key or not auth_file:
        return False, "❌ No Active Session Found. Please Login in Manage Sessions."

    temp_auth_file = backup_session_file(auth_file)
    file_to_load = temp_auth_file if temp_auth_file and os.path.exists(temp_auth_file) else auth_file

    cart_id_found = ""
    address_id_found = ""
    address_name_found = ""
    address_phone_found = ""
    address_str_found = ""
    lat_found = ""
    long_found = ""
    pincode_found = ""
    auth_token_found = ""
    captured_items = []
    fresh_article_cache = {}
    logs = []

    async def add_log(msg_text):
        logs.append(msg_text)
        if status_msg:
            try:
                progress_text = (
                    f"⏳ <b>Syncing Account [{VERSION}]...</b>\n\n"
                    + "\n".join(logs[-8:])
                )
                await status_msg.edit_text(progress_text, parse_mode="HTML")
            except Exception:
                pass

    await add_log(f"🌐 <b>Opening Headless Browser with Temp Session:</b> <code>{active_key}</code>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = (
            await browser.new_context(storage_state=file_to_load, user_agent="Mozilla/5.0")
            if file_to_load and os.path.exists(file_to_load)
            else await browser.new_context(user_agent="Mozilla/5.0")
        )

        page = await ctx.new_page()
        
        # Wait 5 seconds for browser to stabilize before starting network interception
        await asyncio.sleep(5)

        async def intercept_network(response):
            nonlocal cart_id_found, address_id_found, address_name_found, address_phone_found, address_str_found, lat_found, long_found, pincode_found, auth_token_found, captured_items, fresh_article_cache
            req = response.request
            url = req.url
            headers = req.headers

            if "authorization" in headers and headers["authorization"].startswith("Bearer") and not auth_token_found:
                auth_token_found = headers["authorization"].replace("Bearer ", "").strip()
                await add_log("🔑 <b>Fresh Bearer Auth Token Intercepted!</b>")

            if "cart/v1.0/address" in url and response.status == 200:
                try:
                    addr_data = await response.json()
                    addr_list = addr_data.get("address", [])
                    if isinstance(addr_list, list) and len(addr_list) > 0:
                        saved_def_id = configs.get("default_address_id", "")

                        for addr in addr_list:
                            hex_id = str(addr.get("id", ""))
                            if hex_id == saved_def_id or addr.get("is_default_address") is True or not address_id_found:
                                address_id_found = hex_id
                                address_name_found = str(addr.get("name") or addr.get("contact_person") or "User")
                                address_phone_found = str(addr.get("phone", ""))
                                address_str_found = str(addr.get("address") or f"{addr.get('address1', '')}, {addr.get('city', '')}")
                                pincode_found = str(addr.get("area_code", "") or addr.get("pincode", ""))
                                geo = addr.get("geo_location", {})
                                lat_found = str(geo.get("latitude", ""))
                                long_found = str(geo.get("longitude", ""))
                        if address_id_found:
                            await add_log(f"📍 <b>Default Address Captured:</b> <code>{address_name_found} ({pincode_found})</code>")
                except Exception:
                    pass

            if "get_cart" in url and response.status == 200:
                try:
                    data = await response.json()
                    cid = data.get("cart_id")
                    if is_valid_cart_id_format(cid):
                        cart_id_found = str(cid)

                    items_list = data.get("items", [])
                    captured_items.clear()
                    fresh_article_cache.clear()

                    for idx, itm in enumerate(items_list):
                        prod = itm.get("product", {})
                        art = itm.get("article", {})
                        identifiers = itm.get("identifiers", {})

                        iid = prod.get("uid") or prod.get("item_code")
                        aid = art.get("uid") or art.get("seller_identifier")
                        seller_id = itm.get("seller_id") or 1
                        qty = itm.get("quantity", 1)
                        pname = prod.get("name", "")
                        item_size = itm.get("item_size") or "OS"
                        identifier_val = identifiers.get("identifier", "")
                        unit_price_val = extract_unit_price_from_item_node(itm)

                        if iid:
                            item_id_str = str(iid)
                            captured_items.append({
                                "item_id": int(iid) if item_id_str.isdigit() else iid,
                                "article_id": str(aid) if aid else "",
                                "seller_id": seller_id,
                                "quantity": int(qty),
                                "name": pname,
                                "item_size": item_size,
                                "identifier": identifier_val,
                                "item_index": idx,
                                "price": unit_price_val,
                            })

                            fresh_article_cache[item_id_str] = {
                                "article_id": str(aid) if aid else "",
                                "seller_id": seller_id,
                                "quantity": int(qty),
                                "item_size": item_size,
                                "identifier": identifier_val,
                                "item_index": idx,
                                "price": unit_price_val,
                            }

                            if pname and unit_price_val is not None:
                                GLOBAL_PRICES[pname] = unit_price_val
                                GLOBAL_PRICES[item_id_str] = unit_price_val

                    await add_log(f"📦 <b>Captured {len(captured_items)} Live Cart Items!</b>")
                except Exception:
                    pass

        page.on("response", intercept_network)

        try:
            await add_log("⏳ <i>Navigating to Profile Address Page...</i>")
            await page.goto("https://www.jiomart.com/profile/address", wait_until="domcontentloaded", timeout=60000)
            
            # Wait 5 seconds after page load for browser to stabilize
            await asyncio.sleep(5)

            for cycle in range(15):
                if FORCE_STOP_FLAG.get(chat_id_str):
                    break
                if cart_id_found and auth_token_found and address_id_found:
                    break
                await asyncio.sleep(3)

        except Exception as e:
            await add_log(f"⚠️ <i>Browser Warning: {str(e)[:50]}</i>")

        # If cart ID not found, try navigating to cart page
        if not cart_id_found:
            await add_log("🛒 <i>Cart ID not found, navigating to Cart Page...</i>")
            try:
                await page.goto("https://www.jiomart.com/cart/bag", wait_until="domcontentloaded", timeout=45000)
                # Wait 5 seconds for cart page to stabilize
                await asyncio.sleep(5)
                for cycle in range(15):
                    if cart_id_found:
                        await add_log("✅ <i>Cart ID captured from Cart Page!</i>")
                        break
                    await asyncio.sleep(3)
            except Exception as cart_error:
                await add_log(f"⚠️ <i>Cart Page Navigation Error: {str(cart_error)[:50]}</i>")

        await add_log("🔍 <b>Verifying Extracted Session IDs...</b>")
        
        if auth_file and os.path.exists(auth_file):
            try:
                os.remove(auth_file)
                await add_log("🗑️ <b>Deleted Old Session File!</b>")
            except Exception as e:
                logger.error(f"Error removing old session file: {e}")

        await add_log("⏳ <b>Waiting 5 Seconds before saving fresh session...</b>")
        await asyncio.sleep(5)

        try:
            await ctx.storage_state(path=auth_file)
            await add_log("💾 <b>Saved Fresh New Session File & Cookies!</b>")
        except Exception as e:
            logger.error(f"Error saving new session state: {e}")

        await browser.close()

    if temp_auth_file and os.path.exists(temp_auth_file):
        try: os.remove(temp_auth_file)
        except Exception: pass

    configs = load_configs()
    if chat_id_str not in configs: configs[chat_id_str] = {}

    if address_id_found:
        configs[chat_id_str]["default_address_id"] = address_id_found
        configs[chat_id_str]["default_address_name"] = address_name_found
        configs[chat_id_str]["default_address_phone"] = address_phone_found
        configs[chat_id_str]["default_address_str"] = address_str_found
        configs[chat_id_str]["latitude"] = lat_found or "20.060583"
        configs[chat_id_str]["longitude"] = long_found or "86.004619"

    if pincode_found:
        configs[chat_id_str]["pincode"] = pincode_found

    if auth_token_found:
        configs[chat_id_str]["active_token"] = auth_token_found

    if is_valid_cart_id_format(cart_id_found):
        set_saved_cart_id(chat_id, cart_id_found)

    save_configs(configs)

    if auth_token_found:
        update_session_file_token_and_pincode(auth_file, new_token=auth_token_found, new_pin=pincode_found or pincode, address_id=address_id_found)
        headers = build_dynamic_location_headers(pincode_found or pincode, auth_token_found)
        USER_HEADERS_CACHE[chat_id_str] = headers

    save_article_cache(chat_id, fresh_article_cache)

    synced_cart = {}
    for itm in captured_items:
        pname = itm["name"] or f"Product_{itm['item_id']}"
        synced_cart[pname] = itm["quantity"]
        p_url = f"https://www.jiomart.com/p/groceries/item/{itm['item_id']}"
        update_product_library_smart(pname, p_url)

    save_cart(chat_id, synced_cart)

    any_id_found = bool(cart_id_found or address_id_found or auth_token_found)

    summary = f"🎉 <b>Account & Address Sync Completed! [{VERSION}]</b>\n\n"
    summary += f"🔑 <b>Active Session:</b> <code>{active_key}</code>\n"
    summary += f"🆔 <b>Cart ID:</b> <code>{cart_id_found or 'None ❌'}</code>\n"
    summary += f"🔑 <b>Auth Token:</b> <code>{'Found ✅' if auth_token_found else 'None ❌'}</code>\n"
    summary += f"📌 <b>Address Hex ID:</b> <code>{address_id_found or 'None ❌'}</code>\n"
    summary += f"📍 <b>Pincode:</b> <code>{pincode_found or 'None ❌'}</code>\n"
    summary += f"📦 <b>Items Captured:</b> <code>{len(captured_items)} ✅</code>\n"
    summary += f"⏱️ <b>Browser Stabilization:</b> <code>5 seconds added</code>\n"
    
    if not any_id_found:
        summary += f"\n⚠️ <b>No Session IDs were captured. This might be due to:</b>\n"
        summary += f"• Session expired or invalid\n"
        summary += f"• Network timeout\n"
        summary += f"• Login required\n"
        summary += f"• Please try Login P2 to create a fresh session."

    return any_id_found, summary

# ==========================================
# ୫. PURE API PRICE FETCH ENGINE & LIB ACTIONS
# ==========================================
async def fetch_product_price(full_product_slug, chat_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)

    raw_slug = str(full_product_slug).strip()
    match = re.search(r"/(?:product|item|p)/([^/?]+)", raw_slug)
    clean_slug = match.group(1) if match else raw_slug

    headers = build_dynamic_location_headers(pincode, access_token)
    headers["Content-Type"] = "application/json"

    url_v1 = "https://www.jiomart.com/api/service/application/catalog/v1.0/products/sizes/price"
    payload_v1 = {
        "items": [
            {"slug": clean_slug, "size": "OS", "is_tradein_opted": False}
        ]
    }

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.post(url_v1, json=payload_v1, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    item_data = items[0] if isinstance(items, list) and len(items) > 0 else items.get(clean_slug, {})
                    
                    if item_data.get("is_out_of_stock") is True or item_data.get("error") == "Out of Stock":
                        return "OOS"

                    effective = item_data.get("price", {}).get("effective")
                    res_val = effective.get("min") if isinstance(effective, dict) else effective
                    if res_val is None:
                        res_val = item_data.get("price", {}).get("selling_price")

                    if res_val is not None:
                        return float(res_val)
                elif resp.status in [400, 401, 403]:
                    return {"error": "Session Auth Expired. Please click '🔄 Sync Account'."}
    except Exception as e:
        logger.warning(f"Price Fetch Warning: {e}")

    item_id_match = re.search(r"(\d{5,12})", clean_slug)
    if item_id_match:
        item_id_str = item_id_match.group(1)
        url_v2 = f"https://www.jiomart.com/api/service/catalog/product/get-product-detail?item_id={item_id_str}"
        guest_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "pincode": str(pincode),
            "x-pincode": str(pincode),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url_v2, headers=guest_headers, timeout=8) as resp2:
                    if resp2.status == 200:
                        data2 = await resp2.json()
                        prod_data = data2.get("data", {}) or data2
                        if prod_data.get("is_out_of_stock") is True:
                            return "OOS"
                        price_node = prod_data.get("price", {}) or prod_data.get("price_info", {})
                        p_val = price_node.get("selling_price") or price_node.get("effective_price") or price_node.get("mrp")
                        if p_val is not None:
                            return float(p_val)
        except Exception:
            pass

    return {"error": "Price Fetch Failed. Please check product link in Library."}

async def refresh_lib_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Prices fetching...", show_alert=False)
    chat_id = update.effective_chat.id
    products = load_products()

    sem = asyncio.Semaphore(2)

    async def fetch_and_store(name):
        url = products.get(name)
        if not url: return
        async with sem:
            res = await fetch_product_price(url, chat_id)
            if res is not None and not isinstance(res, dict):
                GLOBAL_PRICES[name] = res

    item_list = list(products.keys())
    chunk_size = 5
    for i in range(0, len(item_list), chunk_size):
        chunk = item_list[i : i + chunk_size]
        await asyncio.gather(*[fetch_and_store(n) for n in chunk])
        await asyncio.sleep(0.3)

    context.user_data["lib_page"] = 0
    await show_library(update, context)

async def lib_single_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.split("_")[1])
    products = load_products()
    names = list(products.keys())
    chat_id = update.effective_chat.id
    if idx < len(names):
        name = names[idx]
        await query.answer("Price updating...", show_alert=False)
        original_url = products.get(name)
        if original_url:
            res = await fetch_product_price(original_url, chat_id)
            if isinstance(res, dict) and "error" in res:
                await context.bot.send_message(chat_id=chat_id, text=f"⚠️ **Price Error:**\n{res.get('error')}", parse_mode="Markdown")
            elif res is not None:
                GLOBAL_PRICES[name] = res
        await show_library(update, context)

async def refresh_cart_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer("Prices refreshing...", show_alert=False)

    captured_items, _ = await get_online_cart_details(chat_id)
    cart = load_cart(str(chat_id))
    products = load_products()

    for name in cart.keys():
        url = products.get(name, "")
        if url:
            res = await fetch_product_price(url, chat_id)
            if res is not None and not isinstance(res, dict):
                GLOBAL_PRICES[name] = res

    await show_cart(update, context)

async def lib_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.split("_")[1])
    products = load_products()
    names = list(products.keys())
    if idx < len(names):
        name = names[idx]
        chat_id = str(update.effective_chat.id)
        cart = load_cart(chat_id)
        cart[name] = cart.get(name, 0) + 1
        save_cart(chat_id, cart)

        sync_mode = get_sync_mode(chat_id)
        if sync_mode == "DIRECT":
            url = products.get(name, "")
            if url:
                await api_push_to_jiomart_cart(url, chat_id, quantity=1)
                await get_online_cart_details(chat_id)

        await query.answer("✅ Cart ରେ ଯୋଡ଼ାଗଲା!", show_alert=False)

async def lib_remove_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = context.user_data.get("del_page", 0)
    if query.data.startswith("delpage_"):
        page = int(query.data.split("_")[1])
        context.user_data["del_page"] = page

    products = load_products()
    product_names = list(products.keys())
    total_products = len(product_names)
    ITEMS_PER_PAGE = 10
    total_pages = max(1, (total_products + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_items = product_names[start_idx:end_idx]

    keyboard = []
    for name in current_items:
        idx = product_names.index(name)
        keyboard.append([
            InlineKeyboardButton(get_smart_name(name), callback_data="ignore"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"dellib_{idx}"),
        ])

    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"delpage_{page-1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"delpage_{page+1}"))
    if total_products > ITEMS_PER_PAGE: keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Back to Library", callback_data="lib")])
    try:
        await query.edit_message_text("🗑️ **Permanently Remove କରିବା ପାଇଁ Items ବାଛନ୍ତୁ:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        pass

async def delete_from_lib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    idx = int(query.data.split("_")[1])
    products = load_products()
    names = list(products.keys())
    if idx < len(names):
        name = names[idx]
        del products[name]
        save_products(products)
        if name in GLOBAL_PRICES: del GLOBAL_PRICES[name]
        await query.answer(f"✅ {get_smart_name(name)} Delete ହୋଇଗଲା", show_alert=False)
    await lib_remove_menu(update, context)

async def get_online_cart_details(chat_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, _, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)
    headers = build_dynamic_location_headers(pincode, access_token)

    get_url = f"https://www.jiomart.com/ext/jmshipmentfee/cart/v1.0/get_cart?area_code={pincode}&b=true&i=true"

    captured_items = []
    real_cart_id = ""
    fresh_cache = {}

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.get(get_url, headers=headers, timeout=12) as response:
                if response.status == 200:
                    data = await response.json()

                    real_cart_id = str(data.get("cart_id") or "")
                    if is_valid_cart_id_format(real_cart_id):
                        set_saved_cart_id(chat_id_str, real_cart_id)

                    breakup = data.get("breakup_values", {})
                    display_list = breakup.get("display", [])
                    disp_map = {}
                    if isinstance(display_list, list):
                        for d in display_list:
                            if isinstance(d, dict) and "key" in d:
                                disp_map[d["key"]] = d.get("value")

                    mrp_total = disp_map.get("mrp_total") or disp_map.get("mrp") or breakup.get("mrp", {}).get("value", 0)
                    discount_val = disp_map.get("discount") or disp_map.get("mrp_discount") or breakup.get("discount", {}).get("value", 0)
                    subtotal_val = disp_map.get("subtotal") or breakup.get("subtotal", {}).get("value", 0)

                    coupon_obj = breakup.get("coupon", {}) or {}
                    coupon_val = 0
                    coupon_code = ""
                    if isinstance(coupon_obj, dict) and coupon_obj.get("is_applied"):
                        coupon_val = coupon_obj.get("value", 0)
                        coupon_code = coupon_obj.get("code", "")

                    delivery_val = None
                    for del_key in ["delivery_charge", "delivery_fee", "shipping_fee", "delivery_amount"]:
                        if del_key in disp_map and disp_map[del_key] is not None:
                            delivery_val = disp_map[del_key]
                            break

                    if delivery_val is None:
                        del_node = breakup.get("delivery_charge") or breakup.get("delivery_fee") or {}
                        if isinstance(del_node, dict):
                            delivery_val = del_node.get("value", 0)
                        elif isinstance(del_node, (int, float)):
                            delivery_val = del_node
                        else:
                            delivery_val = 0

                    total_payable = disp_map.get("net_price") or disp_map.get("total") or breakup.get("net_price", {}).get("value") or data.get("net_price", 0)

                    net_cart_value = float(subtotal_val or 0) - float(coupon_val or 0)
                    if float(total_payable or 0) > net_cart_value and net_cart_value > 0:
                        calc_delivery = float(total_payable) - net_cart_value
                        if float(delivery_val or 0) == 0:
                            delivery_val = calc_delivery

                    you_saved = disp_map.get("you_saved") or breakup.get("you_saved", {}).get("value") or (
                        float(mrp_total or 0) - float(total_payable or 0) if float(mrp_total or 0) > float(total_payable or 0) else 0
                    )

                    LIVE_CART_BILLING_CACHE[chat_id_str] = {
                        "mrp": float(mrp_total or 0),
                        "discount": float(discount_val or 0),
                        "subtotal": float(subtotal_val or 0),
                        "coupon_val": float(coupon_val or 0),
                        "coupon_code": str(coupon_code),
                        "delivery_fee": float(delivery_val or 0),
                        "total_payable": float(total_payable or 0),
                        "you_saved": float(you_saved or 0),
                    }

                    items_list = data.get("items", [])
                    for idx, itm in enumerate(items_list):
                        prod = itm.get("product", {})
                        art = itm.get("article", {})
                        identifiers = itm.get("identifiers", {})

                        iid = prod.get("uid") or prod.get("item_code")
                        aid = art.get("uid") or art.get("seller_identifier")
                        seller_id = itm.get("seller_id") or 1
                        qty = itm.get("quantity", 1)
                        pname = prod.get("name", "")
                        item_size = itm.get("item_size") or "OS"
                        identifier_val = identifiers.get("identifier", "")
                        unit_price_val = extract_unit_price_from_item_node(itm)

                        if iid:
                            item_id_str = str(iid)
                            captured_items.append({
                                "item_id": int(iid) if item_id_str.isdigit() else iid,
                                "article_id": str(aid) if aid else "",
                                "seller_id": seller_id,
                                "quantity": int(qty),
                                "name": pname,
                                "item_size": item_size,
                                "identifier": identifier_val,
                                "item_index": idx,
                                "price": unit_price_val,
                            })
                            fresh_cache[item_id_str] = {
                                "article_id": str(aid) if aid else "",
                                "seller_id": seller_id,
                                "quantity": int(qty),
                                "item_size": item_size,
                                "identifier": identifier_val,
                                "item_index": idx,
                                "price": unit_price_val,
                            }
                            if pname and unit_price_val is not None:
                                GLOBAL_PRICES[pname] = unit_price_val
                                GLOBAL_PRICES[item_id_str] = unit_price_val

                    if fresh_cache:
                        save_article_cache(chat_id_str, fresh_cache)

    except Exception as e:
        logger.error(f"Error in get_online_cart_details: {e}")

    return captured_items, real_cart_id

async def api_update_jiomart_cart_item(chat_id, target_item_id, new_qty, target_article_id="", cart_id_param="", current_qty=1):
    chat_id_str = str(chat_id)
    captured_items, live_cart_id = await get_online_cart_details(chat_id_str)
    cart_id = live_cart_id or cart_id_param or get_saved_cart_id(chat_id_str)

    item_id_str = str(target_item_id)
    article_id = target_article_id
    seller_id = 1
    item_size = "OS"
    identifier_val = ""
    item_index = 0

    for idx, itm in enumerate(captured_items):
        if str(itm.get("item_id")) == item_id_str:
            article_id = itm.get("article_id") or article_id
            seller_id = itm.get("seller_id") or 1
            item_size = itm.get("item_size") or "OS"
            identifier_val = itm.get("identifier") or ""
            item_index = itm.get("item_index", idx)
            break

    if not article_id or not identifier_val:
        cached_info = get_cached_item_details(chat_id_str, item_id_str)
        article_id = cached_info.get("article_id", article_id)
        seller_id = cached_info.get("seller_id", seller_id)
        item_size = cached_info.get("item_size", item_size)
        identifier_val = cached_info.get("identifier", identifier_val)
        item_index = cached_info.get("item_index", item_index)

    auth_file, pincode, _, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)
    headers = build_dynamic_location_headers(pincode, access_token)

    update_url = "https://www.jiomart.com/ext/jmshipmentfee/cart/v2.0/update_cart"
    if is_valid_cart_id_format(cart_id):
        update_url += f"?id={cart_id}"

    item_id_val = int(item_id_str) if item_id_str.isdigit() else target_item_id
    op_type = "remove_item" if new_qty <= 0 else "update_item"

    item_payload = {
        "article_id": str(article_id),
        "item_id": item_id_val,
        "identifiers": {"identifier": str(identifier_val)},
        "item_size": str(item_size),
        "quantity": 0 if new_qty <= 0 else int(new_qty),
        "parent_item_identifiers": {
            "identifier": None,
            "parent_item_size": None,
            "parent_item_id": None,
        },
        "meta": {"vertical_code": "GROCERIES", "compute_delivery_fee": True},
        "item_index": int(item_index),
        "extra_meta": {"is_tradein_opted": False},
    }

    payload = {"operation": op_type, "item": item_payload}

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.put(update_url, json=payload, headers=headers, timeout=10) as response:
                resp_text = await response.text()
                if response.status == 200:
                    try:
                        res_json = json.loads(resp_text)
                        if res_json.get("success") is True or res_json.get("code") == 200:
                            return {"success": True, "debug": "OK 200"}
                    except Exception:
                        return {"success": True, "debug": "OK 200"}

                return {"success": False, "debug": f"HTTP {response.status}: {resp_text[:50]}"}
    except Exception as e:
        return {"success": False, "debug": f"Err: {str(e)}"}

async def api_push_to_jiomart_cart(full_url, chat_id, quantity=1):
    auth_file, pincode, _, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id)

    product_item_id = extract_item_id_from_url(full_url)
    if not product_item_id:
        return {"error": "Link ରେ Item ID ମିଳିଲା ନାହିଁ।"}

    captured_items, live_cart_id = await get_online_cart_details(chat_id)
    cart_id = live_cart_id or get_saved_cart_id(chat_id)

    headers = build_dynamic_location_headers(pincode, access_token)
    add_url = f"https://www.jiomart.com/ext/jmshipmentfee/cart/v2.0/add_items?area_code={pincode}"
    if is_valid_cart_id_format(cart_id):
        add_url += f"&id={cart_id}"

    payload = {
        "extra_meta": {"is_tradein_opted": False},
        "item_id": int(product_item_id),
        "item_size": "OS",
        "meta": {"compute_delivery_fee": True, "vertical_code": "GROCERIES"},
        "quantity": int(quantity),
        "seller_id": 1,
    }

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.post(add_url, json=payload, headers=headers, timeout=10) as response:
                if response.status == 200:
                    rdata = await response.json()
                    if rdata.get("success") is True or "cart_id" in rdata:
                        return {"success": True}
                    return {"error": rdata.get("message", "Add failed")}
                return {"error": f"HTTP {response.status}"}
    except Exception as e:
        return {"error": str(e)}

async def apply_jiomart_coupon(chat_id, coupon_code):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id)

    captured_items, live_cart_id = await get_online_cart_details(chat_id_str)
    cart_id = live_cart_id or get_saved_cart_id(chat_id_str)

    if chat_id_str in USER_HEADERS_CACHE and USER_HEADERS_CACHE[chat_id_str]:
        headers = USER_HEADERS_CACHE[chat_id_str].copy()
    else:
        headers = build_dynamic_location_headers(pincode, access_token)

    headers["Content-Type"] = "application/json"
    coupon_url = "https://www.jiomart.com/api/service/application/cart/v1.0/coupon"
    if is_valid_cart_id_format(cart_id):
        coupon_url += f"?id={cart_id}"

    payload = {"coupon_code": str(coupon_code).strip().upper()}

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.post(coupon_url, json=payload, headers=headers, timeout=12) as response:
                if response.status in [401, 403]:
                    return {"success": False, "message": "HTTP 401: Session Expired. Manage Sessions ରୁ Re-login/Sync କରନ୍ତୁ।"}

                if response.status == 200:
                    res_data = await response.json()
                    breakup = res_data.get("breakup_values", {}).get("coupon", {})
                    if breakup.get("is_applied") is True:
                        val = breakup.get("value", 0)
                        title = breakup.get("title", "")
                        msg = breakup.get("message", "Coupon applied successfully")

                        await get_online_cart_details(chat_id)
                        return {"success": True, "value": val, "title": title, "message": msg}
                    else:
                        msg = res_data.get("message") or breakup.get("message") or "Coupon Apply Failed"
                        return {"success": False, "message": msg}
                else:
                    resp_text = await response.text()
                    return {"success": False, "message": f"HTTP {response.status}: {resp_text[:60]}"}
    except Exception as e:
        return {"success": False, "message": f"Connection error: {str(e)}"}

async def remove_jiomart_coupon(chat_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id)
    captured_items, live_cart_id = await get_online_cart_details(chat_id_str)
    cart_id = live_cart_id or get_saved_cart_id(chat_id_str)

    if chat_id_str in USER_HEADERS_CACHE and USER_HEADERS_CACHE[chat_id_str]:
        headers = USER_HEADERS_CACHE[chat_id_str].copy()
    else:
        headers = build_dynamic_location_headers(pincode, access_token)

    coupon_url = "https://www.jiomart.com/api/service/application/cart/v1.0/coupon"
    if is_valid_cart_id_format(cart_id):
        coupon_url += f"?id={cart_id}"

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.delete(coupon_url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    await get_online_cart_details(chat_id)
                    return {"success": True, "message": "Coupon Removed!"}
                return {"success": False, "message": f"HTTP {response.status}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

# ==========================================
# ୬. ADDRESS API OPERATIONS
# ==========================================
async def get_user_saved_addresses(chat_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)
    headers = build_dynamic_location_headers(pincode, access_token)
    address_url = "https://www.jiomart.com/api/service/application/cart/v1.0/address?checkout_mode=self"

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.get(address_url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict) and isinstance(data.get("address"), list):
                        addrs = data.get("address", [])
                        if addrs:
                            configs = load_configs()
                            if chat_id_str not in configs: configs[chat_id_str] = {}
                            configs[chat_id_str]["cached_online_addresses"] = addrs
                            save_configs(configs)
                            return addrs
    except Exception as e:
        logger.error(f"Error fetching address: {e}")

    configs = load_configs()
    return configs.get(chat_id_str, {}).get("cached_online_addresses", [])

async def api_verify_logistics_pincode(chat_id, pincode):
    chat_id_str = str(chat_id)
    auth_file, _, _, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)
    headers = build_dynamic_location_headers(pincode, access_token)

    url = f"https://www.jiomart.com/api/service/application/logistics/v1.0/pincode/{pincode}"
    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.get(url, headers=headers, timeout=8) as resp:
                if resp.status == 200:
                    return True
    except Exception:
        pass
    return False

async def api_delete_jiomart_address(chat_id, address_hex_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)

    headers = build_dynamic_location_headers(pincode, access_token)
    del_url = f"https://www.jiomart.com/api/service/application/cart/v1.0/address/{address_hex_id}"

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.delete(del_url, headers=headers, timeout=10) as response:
                if response.status in [200, 204]:
                    return {"success": True}
                else:
                    return {"success": False, "error": f"HTTP {response.status}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def api_select_jiomart_address(chat_id, address_hex_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)
    
    cart_id = get_saved_cart_id(chat_id_str)
    configs = load_configs()
    
    cached_addresses = configs.get(chat_id_str, {}).get("cached_online_addresses", [])
    target_addr = next((a for a in cached_addresses if str(a.get("id")) == str(address_hex_id)), {})
    
    if not target_addr:
        saved_addresses = await get_user_saved_addresses(chat_id) or []
        target_addr = next((a for a in saved_addresses if str(a.get("id")) == str(address_hex_id)), {})

    target_pin = str(target_addr.get("area_code") or target_addr.get("pincode") or pincode)

    headers = build_dynamic_location_headers(target_pin, access_token)
    headers["Content-Type"] = "application/json"

    select_url = "https://www.jiomart.com/ext/jmshipmentfee/cart/v1.0/select_address"
    
    payload = {
        "id": str(address_hex_id),
        "billing_address_id": str(address_hex_id),
        "cart_id": str(cart_id) if cart_id else ""
    }

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.post(select_url, json=payload, headers=headers, timeout=12) as response:
                if response.status == 200:
                    data = await response.json()
                    is_valid = data.get("is_valid", True)
                    message = data.get("message", "")
                    if is_valid is False:
                        return {"success": False, "error": f"⚠️ JioMart Note: {message}"}
                    return {"success": True, "data": data}
                else:
                    return {"success": False, "error": f"HTTP {response.status}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def api_add_jiomart_address(chat_id, addr_dict, is_retry=False):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)

    pin = str(addr_dict.get("pincode", "754011"))
    await api_verify_logistics_pincode(chat_id, pin)

    await asyncio.sleep(2.5)

    headers = build_dynamic_location_headers(pin, access_token)
    add_url = "https://www.jiomart.com/api/service/application/cart/v1.0/address"

    phone_num = str(addr_dict.get("phone", "9933701234"))
    name_val = str(addr_dict.get("name", "JEMS MARQ"))
    addr1 = str(addr_dict.get("address1", "KASARDA GALI"))
    addr2 = str(addr_dict.get("address2", "Locality"))
    city_val = str(addr_dict.get("city", "CUTTACK")).upper()
    state_val = str(addr_dict.get("state", "ODISHA")).upper()
    landmark_val = str(addr_dict.get("landmark", "Near Tower"))
    lat_val = float(addr_dict.get("latitude", "20.060583"))
    long_val = float(addr_dict.get("longitude", "86.004619"))

    payload = {
        "is_default_address": True,
        "name": name_val,
        "contact_person": name_val,
        "phone": phone_num,
        "email": f"{phone_num}@nomail.jiomart.com",
        "address1": addr1,
        "address2": addr2,
        "landmark": landmark_val,
        "area": addr1,
        "city": city_val,
        "state": state_val,
        "country": "India",
        "country_phone_code": "91",
        "pincode": pin,
        "area_code": pin,
        "address_type": "Home",
        "address": f"{addr1}, {addr2}, {city_val}, {state_val} {pin}, India",
        "display_address": f"{addr2}, {addr1}, {landmark_val}",
        "_custom_json": {
            "flat_or_house_no": addr2,
            "floor_no": "",
            "tower_no": "",
            "address_line": "",
            "input_mode": "MAP_POLY",
        },
        "geo_location": {
            "latitude": lat_val,
            "longitude": long_val,
        },
    }

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.post(add_url, json=payload, headers=headers, timeout=12) as response:
                if response.status == 200:
                    res_json = await response.json()
                    new_hex_id = str(res_json.get("id", ""))

                    if new_hex_id:
                        await api_select_jiomart_address(chat_id, new_hex_id)

                        configs = load_configs()
                        if chat_id_str not in configs: configs[chat_id_str] = {}
                        configs[chat_id_str]["default_address_id"] = new_hex_id
                        configs[chat_id_str]["default_address_name"] = name_val
                        configs[chat_id_str]["default_address_phone"] = phone_num
                        configs[chat_id_str]["default_address_str"] = f"{addr1}, {city_val}"
                        configs[chat_id_str]["latitude"] = str(lat_val)
                        configs[chat_id_str]["longitude"] = str(long_val)
                        configs[chat_id_str]["pincode"] = pin
                        save_configs(configs)

                        update_session_file_token_and_pincode(auth_file, new_token=access_token, new_pin=pin, address_id=new_hex_id)
                        USER_HEADERS_CACHE[chat_id_str] = build_dynamic_location_headers(pin, access_token)

                    return {"success": True, "hex_id": new_hex_id, "data": res_json}
                else:
                    txt = await response.text()
                    clean_txt = html.escape(txt[:100])
                    return {"success": False, "error": f"HTTP {response.status}: {clean_txt}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ==========================================
# ୭. PURE API COD CHECKOUT ENGINE
# ==========================================
async def api_place_cod_order(chat_id, address_id):
    chat_id_str = str(chat_id)
    auth_file, pincode, active_key, cookie_dict, access_token = get_active_session_auth_and_pincode(chat_id_str)

    captured_items, live_cart_id = await get_online_cart_details(chat_id_str)
    cart_id = live_cart_id or get_saved_cart_id(chat_id_str)

    configs = load_configs().get(chat_id_str, {})
    address_id = configs.get("default_address_id", address_id)

    headers = build_dynamic_location_headers(pincode, access_token)
    headers["Content-Type"] = "application/json"

    if is_valid_cart_id_format(cart_id):
        cookie_dict["cart_id"] = str(cart_id)
        cookie_dict["jm_cart_id"] = str(cart_id)
        headers["x-cart-id"] = str(cart_id)

    checkout_url = "https://www.jiomart.com/ext/jmshipmentfee/cart/v2.0/checkout"

    checkout_payload = {
        "address_id": str(address_id),
        "aggregator": "JioOnePay",
        "merchant_code": "JIOPP",
        "payment_mode": "JIOPP",
        "callback_url": "https://www.jiomart.com/cart/order-status",
        "payment_methods": [{"mode": "JIOPP", "name": "JioOnePay"}],
    }

    try:
        async with aiohttp.ClientSession(cookies=cookie_dict) as session:
            async with session.post(checkout_url, json=checkout_payload, headers=headers, timeout=15) as res1:
                resp_text1 = await res1.text()

                try:
                    data1 = json.loads(resp_text1)
                except Exception:
                    return {"success": False, "message": "Invalid JSON in Checkout Response"}

                if data1.get("success") is False:
                    err = data1.get("message") or data1.get("error") or "Checkout Failed"
                    return {"success": False, "message": str(err)}

                order_id = None
                if isinstance(data1, dict):
                    order_id = data1.get("order_id")
                    data_node = data1.get("data")
                    if isinstance(data_node, dict):
                        order_id = order_id or data_node.get("merchant_order_id") or data_node.get("order_id")

                if not order_id:
                    return {"success": False, "message": "Order ID Not Found in API Response"}

            confirm_url = "https://payments.jio.com/jop/v1/points-confirmpayment"
            confirm_payload = {
                "isCodSelected": "true",
                "isStoreCreditSelected": "false",
                "isRoneSelected": "false",
                "isEmpGVSelected": "false",
                "isGiftCardSelected": "false",
                "isJopWalletSelected": "false",
                "isVouchagramSelected": "false",
                "isSapCreditSelected": "false",
                "isSuperWalletSelected": "false",
            }

            async with session.post(confirm_url, json=confirm_payload, headers=headers, timeout=15) as res2:
                resp_text2 = await res2.text()
                if res2.status != 200:
                    return {"success": False, "message": f"COD Confirm Failed (HTTP {res2.status})"}

                try:
                    data2 = json.loads(resp_text2)
                except Exception:
                    return {"success": False, "message": "Invalid JSON in Points-Confirm Response"}

                html_form = data2.get("htmlForm", "")
                match = re.search(r'name=["\']jioResponseMsg["\']\s+value=["\']([^"\']+)["\']', html_form)

                if not match:
                    return {"success": False, "message": "COD Response Token (jioResponseMsg) missing"}

                jio_msg = match.group(1)

            callback_url = "https://payments.jio.com/jop/v1/b2b/paymentCallBackB2B"
            callback_payload = {"jioResponseMsg": jio_msg}
            cb_headers = headers.copy()
            cb_headers["Content-Type"] = "application/x-www-form-urlencoded"

            async with session.post(callback_url, data=callback_payload, headers=cb_headers, timeout=15) as res3:
                if res3.status in [200, 302]:
                    clear_saved_cart_id(chat_id_str)
                    LIVE_CART_BILLING_CACHE.pop(chat_id_str, None)
                    return {"success": True, "order_id": order_id}
                else:
                    return {"success": False, "message": f"Payment Callback Failed (HTTP {res3.status})"}

    except Exception as e:
        return {"success": False, "message": f"Execution Exception: {str(e)}"}

# ==========================================
# 🛑 FORCE STOP HANDLER & AUTO SYNC
# ==========================================
async def force_stop_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    FORCE_STOP_FLAG[chat_id] = True

    if chat_id in RUNNING_TASKS and not RUNNING_TASKS[chat_id].done():
        RUNNING_TASKS[chat_id].cancel()

    await execute_global_emergency_kill(update.effective_chat.id, context)

    await query.answer("🛑 Operations Force-Stopped Instantly!", show_alert=True)
    await start_command(update, context)

async def auto_sync_sessions_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Starting Auto Sync for ALL Saved Sessions...", show_alert=False)
    chat_id = str(update.effective_chat.id)
    FORCE_STOP_FLAG[chat_id] = False

    configs = load_configs()
    user_conf = configs.get(chat_id, {})
    saved_keys = user_conf.get("saved_keys", {})

    if not saved_keys:
        return await query.message.reply_text("❌ No saved sessions found to sync.")

    status_msg = await query.message.reply_text(
        f"⏳ <b>Auto Sync Engine Started [{VERSION}]...</b>\nTotal Sessions to process: <b>{len(saved_keys)}</b>",
        parse_mode="HTML"
    )

    async def run_sync_all():
        keys_list = list(saved_keys.keys())
        synced_count = 0
        deleted_count = 0
        report_lines = []

        try:
            for idx, key_name in enumerate(keys_list, 1):
                if FORCE_STOP_FLAG.get(chat_id):
                    report_lines.append("🛑 Auto Sync Interrupted by User Force Stop.")
                    break

                await status_msg.edit_text(
                    f"🔄 <b>Processing Session ({idx}/{len(keys_list)}):</b> <code>{key_name}</code>...",
                    parse_mode="HTML"
                )

                any_found, summary = await sync_account_full_engine(chat_id, status_msg=None, session_key_override=key_name)

                if any_found:
                    synced_count += 1
                    report_lines.append(f"✅ <code>{key_name}</code>: Synced & Updated")
                else:
                    auth_file = saved_keys.get(key_name)
                    if auth_file and os.path.exists(auth_file):
                        try: os.remove(auth_file)
                        except Exception: pass

                    configs = load_configs()
                    if chat_id in configs and "saved_keys" in configs[chat_id]:
                        configs[chat_id]["saved_keys"].pop(key_name, None)
                        if configs[chat_id].get("active_key") == key_name:
                            configs[chat_id]["active_key"] = ""
                        save_configs(configs)

                    deleted_count += 1
                    report_lines.append(f"🗑️ <code>{key_name}</code>: No ID Found → Deleted")

                await interruptible_sleep(chat_id, 1)

        except asyncio.CancelledError:
            await status_msg.edit_text("🛑 <b>Auto Sync Task Cancelled by Force Stop.</b>", parse_mode="HTML")
            return

        final_report = (
            f"🎉 <b>Auto Sync & Clean Complete [{VERSION}]!</b>\n\n"
            f"✅ <b>Synced Sessions:</b> {synced_count}\n"
            f"🗑️ <b>Deleted Expired Sessions:</b> {deleted_count}\n\n"
            f"<b>Details:</b>\n" + "\n".join(report_lines)
        )
        await status_msg.edit_text(final_report, parse_mode="HTML")

    task = asyncio.create_task(run_sync_all())
    RUNNING_TASKS[chat_id] = task

# ==========================================
# ⚙️ SETTINGS MENU & BUTTON ORGANIZERS
# ==========================================
async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try: await query.answer()
        except Exception: pass

    kb = [
        [InlineKeyboardButton("⚙️ Preset Settings", callback_data="preset_menu")],
        [InlineKeyboardButton("🎛️ Home Screen Organizer", callback_data="button_organizer")],
        [InlineKeyboardButton("🛒 Cart Screen Organizer", callback_data="cart_button_organizer")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
    ]

    msg = f"⚙️ <b>UI & Application Settings [{VERSION}]</b>\nSelect an option below to customize your interface:"
    if query:
        try: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        except Exception: await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def button_organizer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)

    configs = load_configs()
    user_btns = configs.get(chat_id, {}).get("organized_buttons", {
        "lib": True, "cart": True, "old_cart": True, "address": True, "preset": True, "auto_order": True, "sync": True, "all_keys": True
    })

    kb = [
        [InlineKeyboardButton(f"{'✅' if user_btns.get('lib', True) else '❌'} Product Library", callback_data="toggle_org_lib")],
        [InlineKeyboardButton(f"{'✅' if user_btns.get('cart', True) else '❌'} My Cart", callback_data="toggle_org_cart")],
        [InlineKeyboardButton(f"{'✅' if user_btns.get('old_cart', True) else '❌'} Pay Now Browser", callback_data="toggle_org_old_cart")],
        [InlineKeyboardButton(f"{'✅' if user_btns.get('address', True) else '❌'} Delivery Address Menu", callback_data="toggle_org_address")],
        [InlineKeyboardButton(f"{'✅' if user_btns.get('auto_order', True) else '❌'} Auto Order Engine", callback_data="toggle_org_auto_order")],
        [InlineKeyboardButton(f"{'✅' if user_btns.get('sync', True) else '❌'} Sync Account & Sessions", callback_data="toggle_org_sync")],
        [InlineKeyboardButton(f"{'✅' if user_btns.get('all_keys', True) else '❌'} All Keys Section", callback_data="toggle_org_all_keys")],
        [InlineKeyboardButton("🔙 Settings", callback_data="settings_menu")],
    ]

    await query.edit_message_text("🎛️ <b>Home Screen Button Organizer:</b>\nToggle which buttons appear on your Main Dashboard:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def cart_button_organizer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)

    configs = load_configs()
    user_c_btns = configs.get(chat_id, {}).get("cart_organized_buttons", {
        "pay_api": True, "pay_browser": True, "select_addr": True, "add_addr": True,
        "coupon": True, "read_online": True, "sync": True, "empty_cart": True, "home_screen": True
    })

    kb = [
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('pay_api', True) else '❌'} ⚡ Pay Now API", callback_data="toggle_corg_pay_api")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('pay_browser', True) else '❌'} 🌐 Pay Now Browser", callback_data="toggle_corg_pay_browser")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('select_addr', True) else '❌'} 📍 Select Address", callback_data="toggle_corg_select_addr")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('add_addr', True) else '❌'} ➕ Add Address", callback_data="toggle_corg_add_addr")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('coupon', True) else '❌'} 🎟️ Apply Coupon", callback_data="toggle_corg_coupon")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('read_online', True) else '❌'} 📥 Read Online Cart", callback_data="toggle_corg_read_online")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('sync', True) else '❌'} 🔄 Offline/Online Sync", callback_data="toggle_corg_sync")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('empty_cart', True) else '❌'} 🧹 Empty Cart", callback_data="toggle_corg_empty_cart")],
        [InlineKeyboardButton(f"{'✅' if user_c_btns.get('home_screen', True) else '❌'} 🏠 Home Screen Button", callback_data="toggle_corg_home_screen")],
        [InlineKeyboardButton("🔙 Settings", callback_data="settings_menu")],
    ]

    await query.edit_message_text("🛒 <b>Cart Page Button Organizer:</b>\nToggle buttons shown in Cart Summary:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def show_all_keys_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    configs = load_configs()
    user_conf = configs.get(chat_id, {})
    
    saved_keys = user_conf.get("saved_keys", {})
    active_key = user_conf.get("active_key", "")
    
    kb = []
    for key_name, key_path in saved_keys.items():
        is_active = "✅" if key_name == active_key else ""
        kb.append([InlineKeyboardButton(f"{is_active} {key_name}", callback_data=f"switch_key_{key_name}")])
    
    kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    
    await query.edit_message_text(f"🔑 <b>All Keys Section</b>\n\nActive: <code>{active_key or 'None'}</code>\nTotal Keys: {len(saved_keys)}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def switch_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    target_key = data.replace("switch_key_", "")
    chat_id = str(update.effective_chat.id)
    
    configs = load_configs()
    if chat_id not in configs: configs[chat_id] = {}
    configs[chat_id]["active_key"] = target_key
    save_configs(configs)
    
    status_msg = await query.message.reply_text(f"🔄 Switched to session: `{target_key}`\n⏳ Auto-syncing account...", parse_mode="Markdown")
    
    # Auto sync account after switching
    success, report = await sync_account_full_engine(chat_id, status_msg=status_msg)
    await status_msg.edit_text(report, parse_mode="HTML")
    
    await start_command(update, context)

async def toggle_org_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    btn_key = query.data.replace("toggle_org_", "")
    chat_id = str(update.effective_chat.id)

    configs = load_configs()
    if chat_id not in configs: configs[chat_id] = {}
    if "organized_buttons" not in configs[chat_id]:
        configs[chat_id]["organized_buttons"] = {"lib": True, "cart": True, "old_cart": True, "address": True, "preset": True, "auto_order": True, "sync": True, "all_keys": True}

    configs[chat_id]["organized_buttons"][btn_key] = not configs[chat_id]["organized_buttons"].get(btn_key, True)
    save_configs(configs)

    await query.answer(f"Updated Home {btn_key} visibility!")
    await button_organizer(update, context)

async def toggle_cart_org_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    btn_key = query.data.replace("toggle_corg_", "")
    chat_id = str(update.effective_chat.id)

    configs = load_configs()
    if chat_id not in configs: configs[chat_id] = {}
    if "cart_organized_buttons" not in configs[chat_id]:
        configs[chat_id]["cart_organized_buttons"] = {
            "pay_api": True, "pay_browser": True, "select_addr": True, "add_addr": True,
            "coupon": True, "read_online": True, "sync": True, "empty_cart": True, "home_screen": True
        }

    configs[chat_id]["cart_organized_buttons"][btn_key] = not configs[chat_id]["cart_organized_buttons"].get(btn_key, True)
    save_configs(configs)

    await query.answer(f"Updated Cart {btn_key} visibility!")
    await cart_button_organizer(update, context)

# ==========================================
# ⚙️ PRESET MENU & CONFIGS
# ==========================================
async def preset_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try: await query.answer()
        except Exception: pass

    chat_id = str(update.effective_chat.id)
    configs = load_configs().get(chat_id, {})
    
    preset_coupon = configs.get("preset_coupon", "None ❌")
    preset_delay = configs.get("preset_delay", "2s")
    preset_limit = configs.get("preset_qty_limit", "5")

    kb = [
        [InlineKeyboardButton(f"🎟️ Set Preset Coupon: {preset_coupon}", callback_data="set_preset_coupon")],
        [InlineKeyboardButton(f"⏱️ Auto-Order Delay: {preset_delay}", callback_data="set_preset_delay")],
        [InlineKeyboardButton(f"📦 Order Qty Limit/ID: {preset_limit}", callback_data="set_preset_limit")],
        [InlineKeyboardButton("🔙 Back to Settings", callback_data="settings_menu")],
    ]

    msg = (
        f"⚙️ <b>Preset & Auto Engine Settings [{VERSION}]:</b>\n\n"
        f"🎟️ <b>Preset Coupon Code:</b> <code>{preset_coupon}</code>\n"
        f"⏱️ <b>Auto Order Click Delay:</b> <code>{preset_delay}</code>\n"
        f"📦 <b>Quantity Limit Per Account:</b> <code>{preset_limit}</code>"
    )

    if query:
        try: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        except Exception: await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def set_preset_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["bot_state"] = "WAITING_FOR_PRESET_COUPON"
    await query.message.reply_text("🎟️ <b>Enter Coupon Code to Save as Preset:</b>", parse_mode="HTML")

async def apply_preset_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    configs = load_configs().get(chat_id, {})
    preset_coupon = configs.get("preset_coupon", "")

    if not preset_coupon or preset_coupon == "None ❌":
        await query.answer("⚠️ No Preset Coupon found. Directing to manual input...", show_alert=True)
        return await prompt_apply_coupon_handler(update, context)

    await query.answer(f"Applying Preset Coupon '{preset_coupon}'...", show_alert=False)
    status_msg = await query.message.reply_text(f"⏳ <b>Applying Preset Coupon <code>{preset_coupon}</code>...</b>", parse_mode="HTML")
    
    res = await apply_jiomart_coupon(chat_id, preset_coupon)
    if res.get("success"):
        await status_msg.edit_text(f"🎉 <b>Preset Coupon Applied!</b> Savings: ₹{res.get('value')} Off", parse_mode="HTML")
    else:
        await status_msg.edit_text(f"❌ <b>Preset Coupon Failed:</b> {res.get('message')}", parse_mode="HTML")
    
    await show_cart(update, context)

# ==========================================
# ⚡ LOCATION CHECK MODE SWITCH & MANUAL PINCODE INPUT
# ==========================================
async def toggle_location_check_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    configs = load_configs()
    if chat_id not in configs: configs[chat_id] = {}

    curr_mode = configs[chat_id].get("location_check_mode", "PINCODE")
    new_mode = "ADDRESS" if curr_mode == "PINCODE" else "PINCODE"
    configs[chat_id]["location_check_mode"] = new_mode
    save_configs(configs)

    await query.answer(f"Location Check Mode: {new_mode}", show_alert=True)
    await show_cart(update, context)

async def prompt_manual_pincode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["bot_state"] = "WAITING_FOR_MANUAL_PINCODE"
    await query.message.reply_text("📌 <b> Enter New Pincode for Location Price Discovery:</b>\n*(e.g., 754011)*", parse_mode="HTML")

# ==========================================
# 🛒 OFFLINE ↔ ONLINE CART SYNC HANDLERS
# ==========================================
async def sync_offline_to_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Syncing Offline Cart → Online Cart...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    status_msg = await query.message.reply_text("⏳ <b>Clearing existing online cart and pushing offline items...</b>", parse_mode="HTML")

    online_items, cart_id = await get_online_cart_details(chat_id)
    for itm in online_items:
        if FORCE_STOP_FLAG.get(chat_id): break
        await api_update_jiomart_cart_item(chat_id, target_item_id=itm["item_id"], new_qty=0, target_article_id=itm.get("article_id", ""), cart_id_param=cart_id)

    offline_cart = load_cart(chat_id)
    products = load_products()
    pushed_count = 0

    for pname, qty in offline_cart.items():
        if FORCE_STOP_FLAG.get(chat_id): break
        url = products.get(pname, "")
        if url:
            await api_push_to_jiomart_cart(url, chat_id, quantity=qty)
            pushed_count += 1
            await asyncio.sleep(0.3)

    await get_online_cart_details(chat_id)
    await status_msg.edit_text(f"✅ <b>Offline → Online Cart Sync Complete!</b>\nPushed <b>{pushed_count}</b> items.", parse_mode="HTML")
    await show_cart(update, context)

async def sync_online_to_offline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Syncing Online Cart → Offline Cart...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    status_msg = await query.message.reply_text("⏳ <b>Fetching Online Cart Items to save offline...</b>", parse_mode="HTML")

    captured_items, _ = await get_online_cart_details(chat_id)
    new_offline_cart = {}

    for itm in captured_items:
        pname = itm.get("name") or f"Product_{itm['item_id']}"
        new_offline_cart[pname] = itm["quantity"]
        p_url = f"https://www.jiomart.com/p/groceries/item/{itm['item_id']}"
        update_product_library_smart(pname, p_url)

    save_cart(chat_id, new_offline_cart)
    await status_msg.edit_text(f"✅ <b>Online → Offline Cart Sync Complete!</b>\nSaved <b>{len(new_offline_cart)}</b> items offline.", parse_mode="HTML")
    await show_cart(update, context)

# ==========================================
# 👑 ADMIN CONTROL PANEL
# ==========================================
async def admin_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id

    if not has_access(chat_id):
        return await query.answer("❌ Admin Permissions Required!", show_alert=True)

    configs = load_configs()
    total_users = len(configs)

    kb = [
        [InlineKeyboardButton("🔑 View All User Keys & Sessions", callback_data="admin_view_keys")],
        [InlineKeyboardButton("👥 User Management", callback_data="admin_user_management")],
        [InlineKeyboardButton("🔑 Key Sharing Permissions", callback_data="admin_key_permissions")],
        [InlineKeyboardButton("👁️ Toggle User UI Mode", callback_data="admin_toggle_user_ui")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="main_menu")],
    ]

    msg = f"👑 <b>Admin Control Panel [{VERSION}]</b>\n\n📊 Total Configured Users: <b>{total_users}</b>"
    if query:
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def admin_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not has_access(update.effective_chat.id):
        return await query.answer("❌ Access Denied", show_alert=True)
    
    configs = load_configs()
    kb = []
    
    for user_id, user_data in configs.items():
        if user_id == "global_ui_mode": continue
        kb.append([InlineKeyboardButton(f"👤 User {user_id}", callback_data=f"admin_user_detail_{user_id}")])
    
    kb.append([InlineKeyboardButton("➕ Add New User", callback_data="admin_add_user")])
    kb.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")])
    
    await query.edit_message_text("👥 <b>User Management</b>\n\nSelect a user to manage:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def admin_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not has_access(update.effective_chat.id):
        return await query.answer("❌ Access Denied", show_alert=True)
    
    context.user_data["bot_state"] = "WAITING_FOR_ADMIN_ADD_USER"
    await query.edit_message_text("➕ <b>Add New User</b>\n\nPlease send the Telegram User ID (numeric):", parse_mode="HTML")

async def admin_key_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not has_access(update.effective_chat.id):
        return await query.answer("❌ Access Denied", show_alert=True)
    
    configs = load_configs()
    kb = []
    
    for user_id, user_data in configs.items():
        if user_id == "global_ui_mode": continue
        can_share = user_data.get("can_share_keys", False)
        status = "✅ Allowed" if can_share else "❌ Denied"
        kb.append([InlineKeyboardButton(f"👤 User {user_id}: {status}", callback_data=f"admin_toggle_share_{user_id}")])
    
    kb.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")])
    
    await query.edit_message_text("🔑 <b>Key Sharing Permissions</b>\n\nToggle which users can share their keys:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def admin_toggle_share_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not has_access(update.effective_chat.id):
        return await query.answer("❌ Access Denied", show_alert=True)
    
    target_user = query.data.replace("admin_toggle_share_", "")
    configs = load_configs()
    
    if target_user in configs:
        current = configs[target_user].get("can_share_keys", False)
        configs[target_user]["can_share_keys"] = not current
        save_configs(configs)
        await query.answer(f"Updated sharing permission for User {target_user}", show_alert=True)
    
    await admin_key_permissions(update, context)

async def admin_toggle_user_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not has_access(update.effective_chat.id):
        return await query.answer("❌ Access Denied", show_alert=True)

    configs = load_configs()
    curr_view = configs.get("global_ui_mode", "FULL")
    new_view = "SIMPLIFIED" if curr_view == "FULL" else "FULL"
    configs["global_ui_mode"] = new_view
    save_configs(configs)

    await query.answer(f"User UI Mode set to: {new_view}", show_alert=True)
    await admin_control_panel(update, context)

# ==========================================
# 🤖 AUTO CLICKER / WORKFLOW AUTO ORDER ENGINE
# ==========================================
async def auto_order_engine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Starting Automated Multi-Account Order Workflow...", show_alert=False)
    chat_id = str(update.effective_chat.id)
    FORCE_STOP_FLAG[chat_id] = False

    configs = load_configs()
    user_conf = configs.get(chat_id, {})
    saved_keys = user_conf.get("saved_keys", {})
    preset_coupon = user_conf.get("preset_coupon", "")
    preset_delay = int(user_conf.get("preset_delay", "2").replace("s", ""))

    if not saved_keys:
        return await query.message.reply_text("❌ No saved account sessions available for Auto Order.")

    status_msg = await query.message.reply_text(
        f"🤖 <b>Auto Clicker Order Engine Activated [{VERSION}]</b>\n\nTotal Accounts queued: <b>{len(saved_keys)}</b>",
        parse_mode="HTML"
    )

    async def run_auto_order():
        keys_list = list(saved_keys.keys())

        try:
            for idx, key_name in enumerate(keys_list, 1):
                if FORCE_STOP_FLAG.get(chat_id):
                    await status_msg.edit_text("🛑 <b>Auto Order Workflow Stopped by User.</b>", parse_mode="HTML")
                    return

                await status_msg.edit_text(f"🔄 <b>Step 1/5:</b> Switching Session to <code>{key_name}</code> ({idx}/{len(keys_list)})...", parse_mode="HTML")
                
                configs = load_configs()
                configs[chat_id]["active_key"] = key_name
                save_configs(configs)
                await interruptible_sleep(chat_id, preset_delay)

                # Step 2: Push Address
                if FORCE_STOP_FLAG.get(chat_id): return
                await status_msg.edit_text(f"📍 <b>Step 2/5:</b> Pushing Delivery Address for <code>{key_name}</code>...", parse_mode="HTML")
                off_addrs = load_offline_addresses(chat_id)
                if off_addrs:
                    await api_add_jiomart_address(chat_id, off_addrs[0])
                await interruptible_sleep(chat_id, preset_delay)

                # Step 3: Push Offline Cart Items
                if FORCE_STOP_FLAG.get(chat_id): return
                await status_msg.edit_text(f"📦 <b>Step 3/5:</b> Pushing Cart Items for <code>{key_name}</code>...", parse_mode="HTML")
                offline_cart = load_cart(chat_id)
                products = load_products()
                for pname in offline_cart.keys():
                    if FORCE_STOP_FLAG.get(chat_id): return
                    p_url = products.get(pname, "")
                    if p_url:
                        await api_push_to_jiomart_cart(p_url, chat_id, quantity=offline_cart[pname])
                await interruptible_sleep(chat_id, preset_delay)

                # Step 4: Apply Coupon
                if FORCE_STOP_FLAG.get(chat_id): return
                if preset_coupon and preset_coupon != "None ❌":
                    await status_msg.edit_text(f"🎟️ <b>Step 4/5:</b> Applying Preset Coupon <code>{preset_coupon}</code>...", parse_mode="HTML")
                    await apply_jiomart_coupon(chat_id, preset_coupon)
                    await interruptible_sleep(chat_id, preset_delay)

                # Step 5: Place COD Order
                if FORCE_STOP_FLAG.get(chat_id): return
                await status_msg.edit_text(f"💵 <b>Step 5/5:</b> Executing Pure API COD Order for <code>{key_name}</code>...", parse_mode="HTML")
                address_id = configs.get(chat_id, {}).get("default_address_id", "")
                order_res = await api_place_cod_order(chat_id, address_id)

                if order_res.get("success"):
                    await context.bot.send_message(chat_id=chat_id, text=f"🎉 <b>Order Placed Successfully for {key_name}!</b>\nOrder ID: <code>{order_res.get('order_id')}</code>", parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ <b>Order Failed for {key_name}:</b> {order_res.get('message')}", parse_mode="HTML")

                await interruptible_sleep(chat_id, preset_delay)

            await status_msg.edit_text(f"🎉 <b>Auto Order Workflow Completed for ALL Accounts [{VERSION}]!</b>", parse_mode="HTML")

        except asyncio.CancelledError:
            await status_msg.edit_text("🛑 <b>Auto Order Task Force-Stopped Immediately.</b>", parse_mode="HTML")

    task = asyncio.create_task(run_auto_order())
    RUNNING_TASKS[chat_id] = task

# ==========================================
# Ⅸ. UI DISPLAY HANDLERS (LIBRARY & CART)
# ==========================================
async def show_library(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    chat_id = str(update.effective_chat.id)
    configs = load_configs().get(chat_id, {})
    current_pin = configs.get("pincode", "754011")
    active_session = configs.get("active_key") or "Default 🔘"

    page = context.user_data.get("lib_page", 0)
    if query and query.data.startswith("libpage_"):
        page = int(query.data.split("_")[1])
        context.user_data["lib_page"] = page

    products = load_products()
    product_names = list(products.keys())
    search_q = context.user_data.get("search_query", "").lower()
    if search_q:
        product_names = [
            n for n in product_names
            if search_q in get_smart_name(n).lower() or search_q in n.lower()
        ]
    product_names.sort(
        key=lambda n: (
            1 if str(GLOBAL_PRICES.get(n)) == "OOS" else 0,
            get_smart_name(n),
        )
    )

    total_products = len(product_names)
    ITEMS_PER_PAGE = 10
    total_pages = max(1, (total_products + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_items = product_names[start_idx:end_idx]

    keyboard = []
    for name in current_items:
        idx = list(products.keys()).index(name)
        short_name = get_smart_name(name)
        price = GLOBAL_PRICES.get(name)
        p_text = "🔴 OOS" if str(price) == "OOS" else (f"₹{price}" if price else "🔄")
        keyboard.append([
            InlineKeyboardButton(short_name, callback_data="ignore"),
            InlineKeyboardButton(p_text, callback_data=f"libref_{idx}"),
            InlineKeyboardButton("➕", callback_data=f"lib2cart_{idx}"),
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"libpage_{page-1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"libpage_{page+1}"))
    if total_products > ITEMS_PER_PAGE:
        keyboard.append(nav_row)

    if search_q:
        keyboard.append([InlineKeyboardButton(f"✖️ Clear Search ('{search_q}')", callback_data="clear_search")])
    else:
        keyboard.append([InlineKeyboardButton("🔍 Search Product", callback_data="search_lib")])

    keyboard.append([InlineKeyboardButton("🔄 Bulk Read ALL (API Fast Engine)", callback_data="lib_refresh")])
    keyboard.append([InlineKeyboardButton("🧹 Clean Library & Duplicates", callback_data="clean_lib")])
    keyboard.append([
        InlineKeyboardButton("➕ Add New Link", callback_data="add_to_library"),
        InlineKeyboardButton("🗑️ Manage", callback_data="lib_remove"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])

    msg_title = "📚 **Search Results**" if search_q else "📚 **Global Product Library**"
    msg = f"{msg_title} (Total: {total_products})\n📢 *Hint:* Tracking Pin `{current_pin}` & Session `{active_session}`."

    try:
        if query:
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        pass

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    chat_id = str(update.effective_chat.id)
    cart = load_cart(chat_id)
    configs = load_configs().get(chat_id, {})
    current_pin = configs.get("pincode", "754011")
    sync_mode = get_sync_mode(chat_id)
    location_mode = configs.get("location_check_mode", "PINCODE")

    c_org = configs.get("cart_organized_buttons", {
        "pay_api": True, "pay_browser": True, "select_addr": True, "add_addr": True,
        "coupon": True, "read_online": True, "sync": True, "empty_cart": True, "home_screen": True
    })

    item_coupon_map = {}
    if sync_mode == "DIRECT" and cart:
        captured_items, _ = await get_online_cart_details(chat_id)
        for c_itm in captured_items:
            iid_str = str(c_itm.get("item_id", ""))
            p_name = str(c_itm.get("name", ""))
            s_name = get_smart_name(p_name).lower()

            item_coupon_map[iid_str] = c_itm
            item_coupon_map[p_name] = c_itm
            item_coupon_map[p_name.lower()] = c_itm
            item_coupon_map[s_name] = c_itm

    total_items = total_price = 0
    keyboard = []
    has_oos = False

    mode_btn_text = "⚡ Mode: Direct Sync (Online Real Prices)" if sync_mode == "DIRECT" else "📁 Mode: Offline Only (Approx Prices)"
    loc_btn_text = f"📍 Price Loc Mode: {location_mode}"
    keyboard.append([InlineKeyboardButton(mode_btn_text, callback_data="toggle_sync_mode")])
    keyboard.append([
        InlineKeyboardButton(loc_btn_text, callback_data="toggle_location_check_mode"),
        InlineKeyboardButton("✏️ Set Pin", callback_data="prompt_manual_pincode")
    ])

    bill_text = f"📊 *Your Cart Summary [{VERSION}] (Pin: {current_pin} | Mode: {location_mode})*\n\n```text\n"
    bill_text += f"{'Item Name':<16} {'Qty x Price':>10} = {'Total':>7}\n"
    bill_text += "-" * 38 + "\n"

    products = load_products()

    if cart:
        for idx, name in enumerate(cart.keys()):
            qty = cart[name]
            total_items += qty
            short_name = get_smart_name(name)

            item_price = GLOBAL_PRICES.get(name)
            url = products.get(name, "")
            iid = extract_item_id_from_url(url)
            if item_price is None and iid:
                item_price = GLOBAL_PRICES.get(str(iid))

            itm_info = (
                item_coupon_map.get(str(iid))
                or item_coupon_map.get(name)
                or item_coupon_map.get(name.lower())
                or item_coupon_map.get(short_name.lower())
                or {}
            )
            c_disc = itm_info.get("coupon_discount", 0)
            is_c_applied = itm_info.get("is_coupon_applied", False)

            name_str = short_name[:15]

            if str(item_price) == "OOS":
                has_oos = True
                bill_text += f"{name_str:<16} {qty:>2}x{'OOS':>6} = {'OOS':>7}\n"
                p_text = "🔴 OOS"
            elif item_price is not None:
                try:
                    p_float = float(str(item_price).replace(",", ""))
                    row_tot = p_float * qty
                    total_price += row_tot

                    c_tag = " 🎟️" if is_c_applied else ""
                    bill_text += f"{name_str:<16}{c_tag} {qty:>2}x{p_float:>6g} = {row_tot:>7g}\n"
                    if c_disc > 0:
                        bill_text += f" └ Coupon Save: -₹{c_disc}\n"

                    p_text = f"₹{item_price}"
                except Exception:
                    bill_text += f"{name_str:<16} {qty:>2}x{'Err':>6} = {'Err':>7}\n"
                    p_text = "🔄"
            else:
                bill_text += f"{name_str:<16} {qty:>2}x{'?':>6} = {'?':>7}\n"
                p_text = "🔄"

            btn_price_text = f"{p_text} 🎟️" if is_c_applied else p_text

            keyboard.append([
                InlineKeyboardButton("➖", callback_data=f"cdec_{idx}"),
                InlineKeyboardButton(short_name, callback_data="ignore"),
                InlineKeyboardButton(btn_price_text, callback_data=f"cref_{idx}"),
                InlineKeyboardButton("➕", callback_data=f"cinc_{idx}"),
                InlineKeyboardButton("🗑️", callback_data=f"crem_{idx}"),
            ])
    else:
        bill_text += "🛒 Cart is empty.\n"

    bill_text += "-" * 38 + "\n"
    bill_text += f"{'Total Items:':<20} {total_items:>17}\n"

    live_bill = LIVE_CART_BILLING_CACHE.get(chat_id, {})
    if sync_mode == "DIRECT" and live_bill and (live_bill.get("mrp", 0) > 0 or live_bill.get("total_payable", 0) > 0):
        mrp = live_bill.get("mrp", 0)
        disc = live_bill.get("discount", 0)
        subtotal = live_bill.get("subtotal", 0)
        coupon_val = live_bill.get("coupon_val", 0)
        coupon_code = live_bill.get("coupon_code", "")
        delivery = live_bill.get("delivery_fee", 0)
        final_pay = live_bill.get("total_payable", total_price)
        saved = live_bill.get("you_saved", 0)

        bill_text += "\nPayment details:\n"
        bill_text += "-" * 38 + "\n"

        if mrp > 0: bill_text += f"{'Total MRP:':<20} {mrp:>17g}\n"
        if disc > 0: bill_text += f"{'Discount:':<20} {-disc:>17g}\n"
        elif mrp > subtotal and subtotal > 0:
            calc_disc = mrp - subtotal
            bill_text += f"{'Discount:':<20} {-calc_disc:>17g}\n"

        if subtotal > 0: bill_text += f"{'Subtotal:':<20} {subtotal:>17g}\n"

        if coupon_val > 0:
            lbl = f"Coupon ({coupon_code[:6]}):" if coupon_code else "Coupon:"
            bill_text += f"{lbl:<20} {-coupon_val:>17g}\n"

        if delivery > 0: bill_text += f"{'Delivery Fee:':<20} {delivery:>17g}\n"
        else: bill_text += f"{'Delivery Fee:':<20} {'FREE':>17}\n"

        bill_text += "=" * 38 + "\n"
        bill_text += f"{'Total Payable:':<20} {round(final_pay, 2):>17g}\n"
        if saved > 0: bill_text += f"{'🎉 You Saved:':<20} ₹{round(saved, 2):>15g}\n"
    else:
        if total_price > 0:
            bill_text += f"{'Total Price:':<20} {round(total_price, 2):>17g}\n"

    bill_text += "```"

    if has_oos:
        keyboard.append([InlineKeyboardButton("🧹 Out of Stock Items ହଟାନ୍ତୁ", callback_data="remove_oos")])

    keyboard.append([InlineKeyboardButton("🔄 Refresh All Prices", callback_data="refresh_prices")])

    # Cart Action Buttons (Respecting Organizer)
    pay_row = []
    if c_org.get("pay_api", True):
        pay_row.append(InlineKeyboardButton("⚡ Pay Now API", callback_data="btn_pay_now_cod"))
    if c_org.get("pay_browser", True):
        pay_row.append(InlineKeyboardButton("🌐 Pay Now Browser", callback_data="continue_old_cart_action"))
    if pay_row:
        keyboard.append(pay_row)

    addr_row = []
    if c_org.get("select_addr", True):
        addr_row.append(InlineKeyboardButton("📍 Select Address", callback_data="btn_select_address"))
    if c_org.get("add_addr", True):
        addr_row.append(InlineKeyboardButton("➕ Add Address", callback_data="btn_prompt_choose_add_mode"))
    if addr_row:
        keyboard.append(addr_row)

    if c_org.get("coupon", True):
        keyboard.append([
            InlineKeyboardButton("🎟️ Apply Coupon", callback_data="apply_preset_coupon_click"),
            InlineKeyboardButton("❌ Remove Coupon", callback_data="remove_coupon"),
        ])

    if c_org.get("read_online", True):
        keyboard.append([InlineKeyboardButton("📥 Read Online Cart", callback_data="read_online_cart")])

    if c_org.get("sync", True):
        keyboard.append([
            InlineKeyboardButton("🔄 Offline → Online Sync", callback_data="sync_off_to_on"),
            InlineKeyboardButton("📥 Online → Offline Sync", callback_data="sync_on_to_off"),
        ])

    if c_org.get("empty_cart", True):
        if sync_mode == "DIRECT":
            keyboard.append([
                InlineKeyboardButton("🗑️ Remove Selected", callback_data="remove_selected_cart"),
                InlineKeyboardButton("🧹 Empty Cart", callback_data="empty_online_cart"),
            ])
        else:
            if cart:
                keyboard.append([InlineKeyboardButton("🧹 Empty Offline Cart", callback_data="empty_offline_cart")])

    keyboard.append([
        InlineKeyboardButton("📚 Add from Library", callback_data="lib"),
        InlineKeyboardButton("🔗 Add Link", callback_data="add_to_cart"),
    ])
    
    # Home Screen button (if enabled in cart organizer)
    if c_org.get("home_screen", True):
        keyboard.append([InlineKeyboardButton("🏠 Home Screen", callback_data="main_menu")])
    else:
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")])

    try:
        if query:
            await query.edit_message_text(bill_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await update.message.reply_text(bill_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            logger.warning(f"Telegram Edit Warning: {e}")
    except Exception as e:
        logger.error(f"Cart UI Error: {e}")

# ==========================================
# ୧୦. COMMAND & CALLBACK ACTION HANDLERS
# ==========================================
async def pay_now_cod_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Initiating COD Checkout...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    configs = load_configs().get(chat_id, {})
    address_id = configs.get("default_address_id", "")

    if not address_id:
        return await query.message.reply_text(
            "❌ <b>Default Address ID ମିଳିଲା ନାହିଁ!</b>\nଦୟାକରି Cart ରେ '📍 Select Delivery Address' କ୍ଲିକ୍ କରି ଆପଣଙ୍କ Address ସିଲେକ୍ଟ କରନ୍ତୁ।",
            parse_mode="HTML",
        )

    status_msg = await query.message.reply_text(
        "⏳ <b>JioMart COD Checkout Engine Execution...</b>\n"
        "1️⃣ Resolving Session Auth & Address ID...\n"
        "2️⃣ Submitting Clean Checkout Endpoint...\n"
        "3️⃣ Confirming Cash on Delivery (COD)...",
        parse_mode="HTML",
    )

    result = await api_place_cod_order(chat_id, address_id)

    if result.get("success"):
        order_id = result.get("order_id", "N/A")
        msg = (
            "🎉 <b>Order Successfully Placed & Confirmed!</b>\n\n"
            f"📦 <b>Order ID:</b> <code>{order_id}</code>\n"
            "💵 <b>Payment Mode:</b> Cash on Delivery (COD)\n"
            "✨ <b>Status:</b> ✅ Confirmed"
        )
        await status_msg.edit_text(msg, parse_mode="HTML")
    else:
        err_msg = result.get("message", "COD Order Placing Fail ହେଲା।")

        out_text = (
            f"❌ <b>COD Order Failed:</b>\n<code>{html.escape(err_msg)}</code>"
        )
        await status_msg.edit_text(out_text, parse_mode="HTML")

async def sync_account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Sync Account Engine ଚାଲୁହେଉଛି...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    status_msg = await query.message.reply_text(
        f"⏳ <b>Headless Browser ଖୋଲାଯାଉଛି [{VERSION}]...</b>\nJioMart Page Load, Token, Cart ID, Address ID, Article IDs & Live Items Sync ହେଉଛି...",
        parse_mode="HTML",
    )

    success, report = await sync_account_full_engine(chat_id, status_msg=status_msg)
    await status_msg.edit_text(report, parse_mode="HTML")

async def choose_add_address_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except Exception: pass

    kb = [
        [InlineKeyboardButton("☁️ Direct Add to JioMart Online", callback_data="add_addr_online")],
        [InlineKeyboardButton("📁 Add to Offline Database", callback_data="add_addr_offline")],
        [InlineKeyboardButton("🔙 Back to Address Menu", callback_data="btn_select_address")],
    ]

    await query.message.reply_text(
        "<b>➕ Choose New Address Target Mode:</b>\n\n"
        "1️⃣ <b>Direct Add to JioMart Online:</b> Add & Select directly.\n"
        "2️⃣ <b>Add to Offline Database:</b> Save locally.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML",
    )

async def prompt_add_address_gps_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    mode = "ONLINE" if query.data == "add_addr_online" else "OFFLINE"
    try: await query.answer()
    except Exception: pass

    context.user_data["add_addr_target_mode"] = mode
    context.user_data["new_addr_data"] = {}
    context.user_data["bot_state"] = "WAITING_FOR_ADD_ADDRESS_PINCODE"

    guide_msg = (
        f"📍 <b>Add New Delivery Address ({mode} Mode) - Step 1/5</b>\n\n"
        "<b>ଦୟାକରି ଆପଣଙ୍କର 6-ଅଙ୍କିଆ Pincode ଟାଇପ୍ କରନ୍ତୁ:</b>\n\n"
        "*(ଉଦାହରଣ: 754011)*"
    )

    await query.message.reply_text(guide_msg, parse_mode="HTML")

async def select_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try: await query.answer()
        except Exception: pass

    chat_id = str(update.effective_chat.id)
    online_addresses = await get_user_saved_addresses(chat_id) or []
    offline_addresses = load_offline_addresses(chat_id) or []

    configs = load_configs()
    current_addr_id = configs.get(chat_id, {}).get("default_address_id", "")

    keyboard = []
    msg = "<b>📍 Select Your Delivery Address:</b>\n\n"
    counter = 1

    if online_addresses:
        msg += f"🌐 <b>--- JioMart Online Addresses ({len(online_addresses)}) ---</b>\n"
        for addr in online_addresses:
            hex_id = str(addr.get("id", ""))
            name = str(addr.get("name") or addr.get("area") or "Address")
            pincode = str(addr.get("area_code") or addr.get("pincode", ""))
            city = str(addr.get("city", ""))

            is_def = (hex_id == current_addr_id)

            label = ("✅ " if is_def else "☁️ ") + f"{name} ({pincode}, {city})"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"setaddr_{hex_id}")])

            msg += f"<b>{counter}. {html.escape(name)}</b> ({html.escape(city)} - {pincode}) [ONLINE]\n   └ ID: <code>{hex_id}</code>\n"
            counter += 1

    if offline_addresses:
        msg += f"\n📁 <b>--- Offline Addresses ({len(offline_addresses)}) ---</b>\n"
        for idx, addr in enumerate(offline_addresses):
            name = addr.get("name", "Offline Address")
            pincode = addr.get("pincode", "")
            label = f"📁 [Offline] {name} ({pincode})"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"setoffaddr_{idx}")])

    keyboard.append([
        InlineKeyboardButton("➕ Add New Address", callback_data="btn_prompt_choose_add_mode"),
        InlineKeyboardButton("🗑️ Delete Address", callback_data="btn_prompt_delete_address"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    if query:
        try: await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception: await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def set_default_address_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = str(update.effective_chat.id)

    try: await query.answer("Initiating Address Switch...", show_alert=False)
    except Exception: pass

    try: await query.edit_message_text("⚙️ <b>Address Change Initiated.</b>", parse_mode="HTML")
    except Exception: pass

    configs = load_configs()

    async def run_sync_workflow(target_addr_dict, del_hex_id, is_offline):
        status_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ <b>Address Change Started...</b>", parse_mode="HTML")
        
        success = await clean_inject_click_save(chat_id, target_addr_dict, target_hex_id=del_hex_id, status_msg=status_msg, bot=context.bot)

        if success:
            await status_msg.edit_text("✅ <b>Address Switch Completed!</b>\n⏳ Auto-syncing account...", parse_mode="HTML")
            
            # Auto sync account after address switch
            sync_success, sync_report = await sync_account_full_engine(chat_id, status_msg=status_msg)
            await status_msg.edit_text(sync_report, parse_mode="HTML")
            
            kb = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
            await context.bot.send_message(chat_id=chat_id, text="🎉 <b>Sync Finished!</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        else:
            await status_msg.edit_text("🛑 <b>Address Switch Cancelled.</b>", parse_mode="HTML")

    if data.startswith("setaddr_"):
        selected_id = data.split("_")[1]
        cached_addresses = configs.get(chat_id, {}).get("cached_online_addresses", [])
        
        target_addr = next((a for a in cached_addresses if str(a.get("id")) == str(selected_id)), {})
        if not target_addr:
            saved_addresses = await get_user_saved_addresses(chat_id) or []
            target_addr = next((a for a in saved_addresses if str(a.get("id")) == str(selected_id)), {})
            
        if not target_addr:
            await context.bot.send_message(chat_id=chat_id, text="❌ <b>Address Data Not Found.</b>", parse_mode="HTML")
            return

        geo = target_addr.get("geo_location", {})
        addr_dict = {
            "name": target_addr.get("name") or target_addr.get("contact_person") or "User",
            "phone": target_addr.get("phone") or "",
            "pincode": target_addr.get("area_code") or target_addr.get("pincode") or "754011",
            "address1": target_addr.get("address1") or target_addr.get("area") or "",
            "address2": target_addr.get("address2") or "",
            "city": target_addr.get("city") or "CUTTACK",
            "state": target_addr.get("state") or "ODISHA",
            "landmark": target_addr.get("landmark") or "Near Tower",
            "latitude": geo.get("latitude", 20.060583),
            "longitude": geo.get("longitude", 86.004619)
        }

        asyncio.create_task(run_sync_workflow(addr_dict, selected_id, False))

    elif data.startswith("setoffaddr_"):
        idx = int(data.split("_")[1])
        off_addrs = load_offline_addresses(chat_id)
        if idx < len(off_addrs):
            addr_dict = off_addrs[idx]
            asyncio.create_task(run_sync_workflow(addr_dict, None, True))

async def prompt_delete_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        try: await query.answer()
        except Exception: pass
    chat_id = str(update.effective_chat.id)

    online_addresses = await get_user_saved_addresses(chat_id) or []
    offline_addresses = load_offline_addresses(chat_id) or []

    keyboard = []
    for addr in online_addresses:
        hex_id = str(addr.get("id", ""))
        name = str(addr.get("name") or addr.get("area") or "Address")
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete Online: {name}", callback_data=f"delonaddr_{hex_id}")])

    for idx, addr in enumerate(offline_addresses):
        name = str(addr.get("name", "Offline Address"))
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete Offline: {name}", callback_data=f"deloffaddr_{idx}")])

    keyboard.append([InlineKeyboardButton("🔙 Back to Address Menu", callback_data="btn_select_address")])

    msg_text = "<b>🗑️ Select Address to Delete:</b>"

    if query:
        try: await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception: await query.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def delete_address_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = str(update.effective_chat.id)

    if data.startswith("delonaddr_"):
        hex_id = data.split("_")[1]
        try: await query.answer("Deleting Online Address...", show_alert=False)
        except Exception: pass
        res = await api_delete_jiomart_address(chat_id, hex_id)
        if res.get("success"):
            try: await query.answer("✅ Online Address Deleted!", show_alert=True)
            except Exception: pass
        else:
            safe_err = html.escape(str(res.get('error')))
            try: await query.answer(f"❌ Delete Failed: {safe_err}", show_alert=True)
            except Exception: pass

    elif data.startswith("deloffaddr_"):
        idx = int(data.split("_")[1])
        off_addresses = load_offline_addresses(chat_id)
        if idx < len(off_addresses):
            removed = off_addresses.pop(idx)
            save_offline_addresses(chat_id, off_addresses)
            try: await query.answer(f"✅ Offline Address '{removed.get('name')}' Deleted!", show_alert=True)
            except Exception: pass

    await prompt_delete_address_handler(update, context)

async def toggle_browser_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    
    configs = load_configs()
    if chat_id not in configs:
        configs[chat_id] = {}
        
    current_state = configs[chat_id].get("browser_visible", False)
    configs[chat_id]["browser_visible"] = not current_state
    save_configs(configs)
    
    try: await query.answer(f"Browser: {'VISIBLE' if not current_state else 'HIDDEN'}", show_alert=False)
    except Exception: pass
        
    await start_command(update, context)

async def read_online_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("JioMart Live Cart Read ହେଉଛି...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    status_msg = await query.message.reply_text("⏳ <b>JioMart API ରୁ Active Online Cart Items ଅଣାଯାଉଛି...</b>", parse_mode="HTML")

    captured_items, cart_id = await get_online_cart_details(chat_id)

    synced_cart = {}
    for itm in captured_items:
        pname = itm["name"] or f"Product_{itm['item_id']}"
        synced_cart[pname] = itm["quantity"]

        if itm.get("price") is not None:
            GLOBAL_PRICES[pname] = itm["price"]
            GLOBAL_PRICES[str(itm["item_id"])] = itm["price"]

        p_url = f"https://www.jiomart.com/p/groceries/item/{itm['item_id']}"
        update_product_library_smart(pname, p_url)

    save_cart(chat_id, synced_cart)

    await status_msg.edit_text(
        f"✅ <b>Live Online Cart Read Complete!</b>\n📦 Total Items: <b>{len(captured_items)}</b>\n🆔 Active Cart ID: <code>{cart_id or 'Session Active'}</code>",
        parse_mode="HTML",
    )
    await show_cart(update, context)

async def clean_library_duplicates_and_errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Library Clean + Deduplication ଚାଲୁଛି...", show_alert=False)
    chat_id = update.effective_chat.id

    status_msg = await query.message.reply_text("⏳ <b>Product Library ରେ Duplicate ଏବଂ Broken Links Clean ହେଉଛି...</b>", parse_mode="HTML")

    products = load_products()
    if not products:
        return await status_msg.edit_text("📚 Product Library ଖାଲି ଅଛି।")

    sem = asyncio.Semaphore(2)

    async def check_price(name):
        url = products.get(name)
        if not url: return
        async with sem:
            res = await fetch_product_price(url, chat_id)
            if res is not None and not isinstance(res, dict):
                GLOBAL_PRICES[name] = res

    await asyncio.gather(*[check_price(n) for n in list(products.keys())])

    grouped_items = {}
    for name, url in products.items():
        item_id = extract_item_id_from_url(url) or name
        smart_title = get_smart_name(name).strip().lower()
        key = (smart_title, str(item_id))
        if key not in grouped_items: grouped_items[key] = []
        grouped_items[key].append(name)

    removed_duplicates = removed_errors = 0
    cleaned_products = {}

    for (smart_title, item_id), name_list in grouped_items.items():
        if len(name_list) == 1:
            pname = name_list[0]
            price_val = GLOBAL_PRICES.get(pname)
            if price_val is None or (isinstance(price_val, dict) and "error" in price_val):
                removed_errors += 1
            else:
                cleaned_products[pname] = products[pname]
        else:
            best_name = None
            for pname in name_list:
                p_val = GLOBAL_PRICES.get(pname)
                if p_val is not None and not isinstance(p_val, dict) and p_val != "OOS":
                    best_name = pname
                    break

            if not best_name: best_name = name_list[0]
            cleaned_products[best_name] = products[best_name]
            removed_duplicates += len(name_list) - 1

    save_products(cleaned_products)

    msg_out = f"✅ <b>Library Clean & Deduplication ସମ୍ପୂର୍ଣ୍ଣ ହେଲା!</b>\n\n"
    msg_out += f"🧹 <b>Removed Duplicate Items:</b> {removed_duplicates}\n"
    msg_out += f"❌ <b>Removed Broken/Error Items:</b> {removed_errors}\n"
    msg_out += f"📚 <b>Total Active Products Left:</b> {len(cleaned_products)}"

    await status_msg.edit_text(msg_out, parse_mode="HTML")
    await show_library(update, context)

async def empty_online_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Live cart data ଅଣାଯାଉଛି...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    status_msg = await query.message.reply_text("⏳ <b>JioMart API ରୁ Live Cart Items ଯାଞ୍ଚ ହେଉଛି...</b>", parse_mode="HTML")

    captured_items, cart_id = await get_online_cart_details(chat_id)

    if not captured_items:
        return await status_msg.edit_text("🛒 <b>JioMart Online Cart ରେ କୌଣସି Item ନାହିଁ!</b>", parse_mode="HTML")

    context.user_data["temp_empty_items"] = captured_items
    context.user_data["temp_cart_id"] = cart_id

    item_summary = f"🛒 <b>JioMart Live Cart (ID: `{cart_id or 'Session Active'}`):</b>\n\n"
    total_qty = 0
    for idx, itm in enumerate(captured_items, 1):
        total_qty += itm["quantity"]
        item_summary += f"{idx}. <b>{itm['name']}</b>\n   └ Qty: <b>{itm['quantity']}</b> | Item ID: `{itm['item_id']}`\n"

    item_summary += f"\n📦 <b>Total Items: {total_qty}</b>\n\n⚡ <b>Delete କରିବା ପାଇଁ Deletion Formula (Method) ସିଲେକ୍ଟ କରନ୍ତୁ:</b>"

    keyboard = [
        [InlineKeyboardButton("1️⃣ Formula 1: Sequential API Delete (1-by-1)", callback_data="empty_method_f1")],
        [InlineKeyboardButton("2️⃣ Formula 2: Parallel Bulk API Delete (Fast)", callback_data="empty_method_f2")],
        [InlineKeyboardButton("3️⃣ Formula 3: Strict Article-ID Sync Delete", callback_data="empty_method_f3")],
        [InlineKeyboardButton("❌ Cancel Deletion", callback_data="empty_method_cancel")],
    ]

    await status_msg.edit_text(item_summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def confirm_empty_cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    data = query.data

    if data == "empty_method_cancel":
        context.user_data.pop("temp_empty_items", None)
        context.user_data.pop("temp_cart_id", None)
        await query.answer("Cancelled", show_alert=False)
        return await query.edit_message_text("❌ <b>Cart Deletion ବାତିଲ୍ କରାଗଲା।</b>", parse_mode="HTML")

    captured_items = context.user_data.get("temp_empty_items", [])
    cart_id = context.user_data.get("temp_cart_id", "")

    if not captured_items:
        captured_items, cart_id = await get_online_cart_details(chat_id)

    if not captured_items:
        return await query.edit_message_text("❌ Cart Data ମିଳିଲା ନାହିଁ। ଦୟାକରି ପୁଣି ଚେଷ୍ଟା କରନ୍ତୁ।")

    if data == "empty_method_f1":
        await query.answer("Formula 1 ଚାଲୁଛି (1-by-1)...")
        status_msg = await query.message.edit_text("⏳ <b>Formula 1 [Sequential API 1-by-1] ଚାଲୁଛି...</b>", parse_mode="HTML")

        for idx, itm in enumerate(captured_items, 1):
            if FORCE_STOP_FLAG.get(chat_id): break
            iid = itm["item_id"]
            aid = itm.get("article_id", "")
            await status_msg.edit_text(f"⏳ <b>Formula 1: Deleting Item {idx}/{len(captured_items)}</b> (`{iid}`)...", parse_mode="HTML")
            await api_update_jiomart_cart_item(chat_id, target_item_id=iid, new_qty=0, target_article_id=aid, cart_id_param=cart_id)
            await asyncio.sleep(0.3)

    elif data == "empty_method_f2":
        await query.answer("Formula 2 ଚାଲୁଛି (Parallel Bulk)...")
        status_msg = await query.message.edit_text(f"⏳ <b>Formula 2 [Parallel Bulk API] ଚାଲୁଛି ({len(captured_items)} Items Bulk Delete)...</b>", parse_mode="HTML")

        async def delete_task(itm):
            return await api_update_jiomart_cart_item(chat_id, target_item_id=itm["item_id"], new_qty=0, target_article_id=itm.get("article_id", ""), cart_id_param=cart_id)

        await asyncio.gather(*[delete_task(itm) for itm in captured_items])

    elif data == "empty_method_f3":
        await query.answer("Formula 3 ଚାଲୁଛି (Strict Article Sync)...")
        status_msg = await query.message.edit_text("⏳ <b>Formula 3 [Strict Article Sync] ଚାଲୁଛି...</b>", parse_mode="HTML")

        fresh_items, cart_id = await get_online_cart_details(chat_id)
        target_list = fresh_items if fresh_items else captured_items

        for idx, itm in enumerate(target_list, 1):
            if FORCE_STOP_FLAG.get(chat_id): break
            iid = itm["item_id"]
            aid = itm.get("article_id", "")
            await status_msg.edit_text(f"⏳ <b>Formula 3: Deleting Item {idx}/{len(target_list)}</b> (`{iid}`)...", parse_mode="HTML")
            await api_update_jiomart_cart_item(chat_id, target_item_id=iid, new_qty=0, target_article_id=aid, cart_id_param=cart_id)
            await asyncio.sleep(0.4)

    await status_msg.edit_text("🔍 <b>Cart Empty ସ୍ଥିତି ଯାଞ୍ଛ ହେଉଛି...</b>", parse_mode="HTML")
    await asyncio.sleep(1)

    remaining_items, _ = await get_online_cart_details(chat_id)

    if not remaining_items:
        context.user_data.pop("temp_empty_items", None)
        context.user_data.pop("temp_cart_id", None)
        save_cart(chat_id, {})
        LIVE_CART_BILLING_CACHE.pop(chat_id, None)

        await status_msg.edit_text("✅ <b>Cart Deletion ସଫଳତାପୂର୍ବକ ସମ୍ପୂର୍ଣ୍ଣ ହେଲା!</b>\n🎉 JioMart Active Online Cart ସମ୍ପୂର୍ଣ୍ଣ Empty ହୋଇଗଲା।", parse_mode="HTML")
    else:
        context.user_data["temp_empty_items"] = remaining_items
        fail_msg = f"⚠️ <b>Deletion କାର୍ଯ୍ୟ ସମ୍ପୂର୍ଣ୍ଣ ହୋଇପାରିଲା ନାହିଁ!</b>\nବାକି <b>{len(remaining_items)} ଟି Item</b> Delete ହୋଇପାରିଲା ନାହିଁ।"

        keyboard = [
            [InlineKeyboardButton("1️⃣ Try Formula 1", callback_data="empty_method_f1"), InlineKeyboardButton("2️⃣ Try Formula 2", callback_data="empty_method_f2")],
            [InlineKeyboardButton("❌ Stop Deletion", callback_data="empty_method_cancel")],
        ]

        await status_msg.edit_text(fail_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def prompt_apply_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["bot_state"] = "WAITING_FOR_COUPON_CODE"
    await query.message.reply_text("🎟️ <b>ଦୟାକରି ଆପଣଙ୍କ JioMart Coupon Code ଟାଇପ୍ କରନ୍ତୁ:</b>\n*(ଉଦାହରଣ: `R2A5V1E4HOT`)*", parse_mode="HTML")

async def remove_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Coupon Remove କରାଯାଉଛି...", show_alert=False)
    chat_id = str(update.effective_chat.id)

    res = await remove_jiomart_coupon(chat_id)
    if res.get("success"):
        await query.message.reply_text("✅ <b>Coupon Successfully Removed!</b>", parse_mode="HTML")
    else:
        await query.message.reply_text(f"⚠️ {res.get('message')}", parse_mode="HTML")

    await show_cart(update, context)

async def toggle_sync_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    current_mode = get_sync_mode(chat_id)

    new_mode = "OFFLINE" if current_mode == "DIRECT" else "DIRECT"
    set_sync_mode(chat_id, new_mode)

    mode_msg = (
        "📁 <b>Offline Cart Mode ON!</b>\nଆପଣ ବର୍ତ୍ତମାନ ଯାହା Add/Change କରିବେ, ତାହା କେବଳ Offline Cart ରେ ସେଭ୍ ହେବ।"
        if new_mode == "OFFLINE"
        else "⚡ <b>Direct Sync Mode ON!</b>\nସମସ୍ତ Add/Change ସିଧାସଳଖ JioMart Online Cart ସହିତ Sync ହେବ।"
    )

    await query.answer(f"Switched to {new_mode} Mode", show_alert=True)
    await query.message.reply_text(mode_msg, parse_mode="HTML")
    await show_cart(update, context)

async def switch_session_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    saved_keys = load_configs().get(chat_id, {}).get("saved_keys", {})
    active_key = load_configs().get(chat_id, {}).get("active_key", "")

    kb = [[InlineKeyboardButton(("✅ " if key == active_key else "🔘 ") + key, callback_data=f"setkey_{key}")] for key in saved_keys.keys()]
    kb.append([InlineKeyboardButton("🔄 Auto Sync ALL Sessions", callback_data="auto_sync_all_btn")])
    kb.append([InlineKeyboardButton("👁️ ପ୍ରୋଫାଇଲ୍ ଦେଖନ୍ତୁ / ଅପଡେଟ୍ (GUI)", callback_data="view_gui_session")])
    kb.append([InlineKeyboardButton("➕ Login & Add New Session", callback_data="add_new_session")])
    kb.append([InlineKeyboardButton("🛑 ଫୋର୍ସ-କିଲ୍ ବ୍ରାଉଜର୍ (Force Kill)", callback_data="force_kill_browser")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

    msg = f"🔄 **ମ୍ୟାନେଜ୍ ସେସନ୍ [{VERSION}]:**\n\n*(ଯଦି ଆପଣ ନୂଆ ଅଟନ୍ତି, ତେବେ ତଳେ ଥିବା ➕ ବଟନ୍ ଦବାଇ ନିଜ JioMart ରେ ଲଗଇନ୍ କରନ୍ତୁ)*"
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def set_active_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    key = query.data.split("_")[1]
    chat_id = str(update.effective_chat.id)
    configs = load_configs()
    configs[chat_id]["active_key"] = key
    save_configs(configs)

    clear_saved_cart_id(chat_id)
    LIVE_CART_BILLING_CACHE.pop(chat_id, None)
    USER_HEADERS_CACHE.pop(chat_id, None)

    await query.answer(f"✅ Switched to {key} (Cart ID Reset)", show_alert=False)
    await start_command(update, context)

async def add_new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["bot_state"] = "WAITING_FOR_SESSION_NAME"
    await query.message.reply_text("🔑 **ଦୟାକରି ଏହି ନୂଆ Session ର ଏକ ନାମ ଦିଅନ୍ତୁ:**\n*(ଉଦାହରଣ: MyAccount1)*", parse_mode="Markdown")

async def view_gui_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    active_key = load_configs().get(chat_id, {}).get("active_key", "")

    if not active_key:
        return await query.answer("❌ ଦୟାକରି ପ୍ରଥମେ ଉପରୁ ଏକ ସେସନ୍ ସିଲେକ୍ଟ କରନ୍ତୁ।", show_alert=True)

    await query.answer("ବ୍ରାଉଜର୍ ଖୋଲୁଛି...", show_alert=False)
    msg = await query.message.reply_text("🌐 ଆପଣଙ୍କର ପୁରୁଣା ସେସନ୍ ବ୍ରାଉଜର୍ ଲୋଡ୍ ହେଉଛି...")
    asyncio.create_task(launch_gui_browser(chat_id, active_key, is_new=False, message=msg, bot=context.bot))

async def launch_gui_browser(chat_id, session_name, is_new, message, bot):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            auth_file = load_configs().get(chat_id, {}).get("saved_keys", {}).get(session_name)

            if not is_new and auth_file and os.path.exists(auth_file):
                ctx = await browser.new_context(storage_state=auth_file, user_agent="Mozilla/5.0")
            else:
                ctx = await browser.new_context(user_agent="Mozilla/5.0")

            page = await ctx.new_page()
            await page.goto("https://www.jiomart.com/profile/address", wait_until="domcontentloaded")

            event = asyncio.Event()
            ACTIVE_GUI_SESSIONS[chat_id] = {
                "browser": browser,
                "context": ctx,
                "session_name": session_name,
                "event": event,
            }

            kb = [
                [InlineKeyboardButton("✅ Update & Save Session", callback_data="save_gui_session")],
                [InlineKeyboardButton("🛑 ଫୋର୍ସ-କିଲ୍ (Force Kill)", callback_data="force_kill_browser")],
            ]
            await message.edit_text(
                f"🖥️ **ବ୍ରାଉଜର୍ ଖୋଲିଯାଇଛି! (Session: {session_name})**\n\nଦୟାକରି ଲଗଇନ୍ କରନ୍ତୁ। କାମ ସରିବା ପରେ ତଳେ ଥିବା **Save** ବଟନ୍ ଦବାନ୍ତୁ 👇",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown",
            )
            await event.wait()
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Browser Error: {str(e)}")
    finally:
        ACTIVE_GUI_SESSIONS.pop(chat_id, None)

async def save_gui_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    if chat_id in ACTIVE_GUI_SESSIONS:
        await query.answer("💾 ଡାଟା ସେଭ୍ ହେଉଛି...", show_alert=False)
        session_data = ACTIVE_GUI_SESSIONS[chat_id]
        ctx = session_data["context"]
        session_name = session_data["session_name"]
        file_path = os.path.join(SESSION_DIR, f"session_{session_name}.json")
        await ctx.storage_state(path=file_path)

        configs = load_configs()
        if chat_id not in configs: configs[chat_id] = {"saved_keys": {}}
        if "saved_keys" not in configs[chat_id]: configs[chat_id]["saved_keys"] = {}
        configs[chat_id]["saved_keys"][session_name] = file_path
        configs[chat_id]["active_key"] = session_name
        save_configs(configs)

        clear_saved_cart_id(chat_id)
        LIVE_CART_BILLING_CACHE.pop(chat_id, None)
        USER_HEADERS_CACHE.pop(chat_id, None)

        await query.edit_message_text(f"✅ **Session '{session_name}' ସଫଳତାର ସହ ସେଭ୍ ହୋଇ ବ୍ରାଉଜର୍ ବନ୍ଦ ହୋଇଗଲା!**", parse_mode="Markdown")
        session_data["event"].set()
    else:
        await query.answer("❌ କୌଣସି ଆକ୍ଟିଭ୍ ବ୍ରାଉଜର୍ ମିଳିଲା ନାହିଁ।", show_alert=True)

async def force_kill_browser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    if chat_id in ACTIVE_GUI_SESSIONS:
        await query.answer("🛑 ବ୍ରାଉଜର୍ ବନ୍ଦ କରାଯାଉଛି...", show_alert=False)
        ACTIVE_GUI_SESSIONS[chat_id]["event"].set()
        await query.edit_message_text("🛑 **ବ୍ରାଉଜର୍ କୁ ଫୋର୍ସ-ଷ୍ଟପ୍ କରାଗଲା!**")
    else:
        await query.answer("❌ ବର୍ତ୍ତମାନ କୌଣସି ବ୍ରାଉଜର୍ ଅନ୍ ନାହିଁ।", show_alert=True)

async def trigger_search_lib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["bot_state"] = "WAITING_FOR_SEARCH"
    await update.callback_query.message.reply_text("🔍 **ଦୟାକରି ଆପଣ ଯାହା ଖୋଜିବାକୁ ଚାହୁଁଛନ୍ତି ଟାଇପ୍ କରନ୍ତୁ:**", parse_mode="Markdown")

async def clear_search_lib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("search_query", None)
    context.user_data["lib_page"] = 0
    await update.callback_query.answer("Search Cleared!", show_alert=False)
    await show_library(update, context)

async def trigger_add_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = "CART" if query.data == "add_to_cart" else "LIBRARY"
    context.user_data["add_target"] = target
    context.user_data["bot_state"] = "WAITING_FOR_LINKS"
    await query.message.reply_text(f"🔗 <b>ଦୟାକରି {target} ରେ Add କରିବାକୁ ଚାହୁଁଥିବା JioMart Link(s) ପଠାନ୍ତୁ:</b>", parse_mode="HTML")

async def complete_adding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Adding completed!")
    target = context.user_data.get("add_target", "LIBRARY")
    context.user_data.pop("bot_state", None)
    if target == "CART":
        await show_cart(update, context)
    else:
        await show_library(update, context)

async def cart_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⚡ Request Processing...", show_alert=False)

    data = query.data
    chat_id = str(update.effective_chat.id)
    cart = load_cart(chat_id)
    items = list(cart.keys())
    sync_mode = get_sync_mode(chat_id)

    try:
        idx = int(data.split("_")[1])
        name = items[idx]
        products = load_products()
        url = products.get(name, "")
        item_id = extract_item_id_from_url(url)

        if data.startswith("cinc_"):
            cart[name] += 1
            save_cart(chat_id, cart)
            if sync_mode == "DIRECT" and url:
                api_res = await api_push_to_jiomart_cart(url, chat_id, quantity=1)
                if "error" in api_res:
                    cart[name] -= 1
                    save_cart(chat_id, cart)
                    await query.answer(f"⚠️ {api_res['error']}", show_alert=True)
                else:
                    await get_online_cart_details(chat_id)

        elif data.startswith("cdec_"):
            if cart[name] > 1:
                new_qty = cart[name] - 1
                if sync_mode == "DIRECT":
                    res = await api_update_jiomart_cart_item(chat_id, target_item_id=item_id or name, new_qty=new_qty)
                    if res.get("success"):
                        cart[name] = new_qty
                        save_cart(chat_id, cart)
                        await get_online_cart_details(chat_id)
                    else:
                        await query.answer(f"⚠️ {res.get('debug', 'Update Failed')}", show_alert=True)
                else:
                    cart[name] = new_qty
                    save_cart(chat_id, cart)
            else:
                return await query.answer("❌ Minimum Qty 1। Delete କରିବା ପାଇଁ 🗑️ ବ୍ୟବହାର କରନ୍ତୁ।", show_alert=True)

        elif data.startswith("crem_"):
            if sync_mode == "DIRECT":
                res = await api_update_jiomart_cart_item(chat_id, target_item_id=item_id or name, new_qty=0)
                if res.get("success"):
                    del cart[name]
                    save_cart(chat_id, cart)
                    await get_online_cart_details(chat_id)
                else:
                    await query.answer(f"⚠️ {res.get('debug', 'Delete Failed')}", show_alert=True)
            else:
                del cart[name]
                save_cart(chat_id, cart)

        await show_cart(update, context)
    except Exception as e:
        logger.error(f"Error in Cart Adjust: {e}")

# ==========================================
# ୧୧. GLOBAL MESSAGE & TEXT HANDLER
# ==========================================
async def global_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    bot_state = context.user_data.get("bot_state", "")

    if bot_state == "WAITING_FOR_MANUAL_PINCODE" and update.message.text:
        new_pin = update.message.text.strip()
        context.user_data.pop("bot_state", None)

        if not new_pin.isdigit() or len(new_pin) != 6:
            return await update.message.reply_text("❌ Invalid Pincode format. Must be 6 digits.")

        configs = load_configs()
        if chat_id not in configs: configs[chat_id] = {}
        configs[chat_id]["pincode"] = new_pin
        save_configs(configs)

        await update.message.reply_text(f"✅ <b>Pincode set to:</b> <code>{new_pin}</code>", parse_mode="HTML")
        return await show_cart(update, context)

    if bot_state == "WAITING_FOR_PRESET_COUPON" and update.message.text:
        coupon_code = update.message.text.strip().upper()
        context.user_data.pop("bot_state", None)
        configs = load_configs()
        if chat_id not in configs: configs[chat_id] = {}
        configs[chat_id]["preset_coupon"] = coupon_code
        save_configs(configs)
        await update.message.reply_text(f"✅ <b>Preset Coupon set to:</b> <code>{coupon_code}</code>", parse_mode="HTML")
        return await preset_menu(update, context)

    if bot_state == "WAITING_FOR_ADD_ADDRESS_PINCODE" and update.message.text:
        pincode = update.message.text.strip()
        if not pincode.isdigit() or len(pincode) != 6:
            return await update.message.reply_text("❌ ଦୟାକରି କେବଳ 6-ଅଙ୍କିଆ Pincode ଦିଅନ୍ତୁ।")

        context.user_data["new_addr_data"]["pincode"] = pincode
        context.user_data["bot_state"] = "WAITING_FOR_ADD_ADDRESS_GPS"

        kb = [[KeyboardButton("📍 Send Current Location", request_location=True)]]
        inline_kb = [
            [InlineKeyboardButton("🗺️ Open Google Maps", url="https://maps.google.com")],
            [InlineKeyboardButton("🔙 Cancel", callback_data="btn_select_address")],
        ]

        guide_msg = (
            f"📍 <b>Add New Delivery Address - Step 2/5</b>\n\n"
            "<b>Send your Location using:</b>\n\n"
            "1️⃣ <b>Telegram Location:</b> Press `📍 Send Current Location`.\n"
            "2️⃣ <b>Google Maps Link:</b> Open Maps, Share Link here.\n"
            "3️⃣ <b>Type Lat/Long:</b> e.g. <code>20.060583, 86.004619</code>."
        )

        await update.message.reply_text(guide_msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True), parse_mode="HTML")
        await update.message.reply_text("👇 Other Options:", reply_markup=InlineKeyboardMarkup(inline_kb), parse_mode="HTML")

    if bot_state == "WAITING_FOR_ADD_ADDRESS_GPS" and update.message.location:
        lat_str = str(update.message.location.latitude)
        long_str = str(update.message.location.longitude)

        context.user_data["new_addr_data"]["latitude"] = lat_str
        context.user_data["new_addr_data"]["longitude"] = long_str
        context.user_data["bot_state"] = "WAITING_FOR_ADD_ADDRESS_PHONE"

        return await update.message.reply_text(
            f"✅ <b>GPS Captured:</b> <code>{lat_str}, {long_str}</code>\n\n"
            "📱 <b>Step 3/5:</b> ୧୦-ଅଙ୍କିଆ <b>ମୋବାଇଲ୍ ନମ୍ବର</b> ଟାଇପ୍ କରନ୍ତୁ:\n*(e.g. 9933701234)*",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )

    if bot_state == "WAITING_FOR_ADD_ADDRESS_GPS" and update.message.text:
        coords = update.message.text.strip().split(",")
        if len(coords) == 2 and coords[0].replace(".", "").replace("-", "").isdigit():
            lat_str, long_str = coords[0].strip(), coords[1].strip()
        else:
            lat_str, long_str = "20.060583", "86.004619"

        context.user_data["new_addr_data"]["latitude"] = lat_str
        context.user_data["new_addr_data"]["longitude"] = long_str
        context.user_data["bot_state"] = "WAITING_FOR_ADD_ADDRESS_PHONE"

        return await update.message.reply_text(
            f"✅ <b>GPS Captured:</b> <code>{lat_str}, {long_str}</code>\n\n"
            "📱 <b>Step 3/5:</b> ୧୦-ଅଙ୍କିଆ <b>ମୋବାଇଲ୍ ନମ୍ବର</b> ଟାଇପ୍ କରନ୍ତୁ:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )

    if bot_state == "WAITING_FOR_ADD_ADDRESS_PHONE" and update.message.text:
        phone = update.message.text.strip()
        if not phone.isdigit() or len(phone) < 10:
            return await update.message.reply_text("❌ ଦୟାକରି କେବଳ ୧୦-ଅଙ୍କିଆ ମୋବାଇଲ୍ ନମ୍ବର ଦିଅନ୍ତୁ।")

        context.user_data["new_addr_data"]["phone"] = phone
        context.user_data["bot_state"] = "WAITING_FOR_ADD_ADDRESS_NAME"
        return await update.message.reply_text("👤 <b>Step 4/5:</b> <b>ପ୍ରାପ୍ତକର୍ତ୍ତାଙ୍କ ନାମ</b> ଟାଇପ୍ କରନ୍ତୁ:\n*(e.g. JEMS MARQ)*", parse_mode="HTML")

    if bot_state == "WAITING_FOR_ADD_ADDRESS_NAME" and update.message.text:
        context.user_data["new_addr_data"]["name"] = update.message.text.strip()
        context.user_data["bot_state"] = "WAITING_FOR_ADD_ADDRESS_HOUSE"
        return await update.message.reply_text("🏠 <b>Step 5/5:</b> <b>House / Flat / Street Details</b> ଟାଇପ୍ କରନ୍ତୁ:\n*(e.g. KASARDA GALI)*", parse_mode="HTML")

    if bot_state == "WAITING_FOR_ADD_ADDRESS_HOUSE" and update.message.text:
        context.user_data["new_addr_data"]["address1"] = update.message.text.strip()
        context.user_data["new_addr_data"]["address2"] = update.message.text.strip()
        context.user_data.pop("bot_state", None)

        mode = context.user_data.get("add_addr_target_mode", "ONLINE")
        addr_dict = context.user_data.get("new_addr_data", {})

        if mode == "OFFLINE":
            off_addrs = load_offline_addresses(chat_id)
            off_addrs.append(addr_dict)
            save_offline_addresses(chat_id, off_addrs)
            await update.message.reply_text("🎉 <b>Address Saved to Offline Database!</b>", parse_mode="HTML")
        else:
            status_msg = await update.message.reply_text("⏳ <b>JioMart Account ରେ Address Add & Sync ହେଉଛି...</b>", parse_mode="HTML")
            res = await api_add_jiomart_address(chat_id, addr_dict)
            if res.get("success"):
                await status_msg.edit_text("🎉 <b>Address Added to JioMart & Set Default!</b>", parse_mode="HTML")
            else:
                await status_msg.edit_text(f"❌ <b>Add Address Failed:</b> {html.escape(str(res.get('error')))}", parse_mode="HTML")

        return await show_cart(update, context)

    if bot_state == "WAITING_FOR_COUPON_CODE" and update.message.text:
        coupon_code = update.message.text.strip().upper()
        context.user_data.pop("bot_state", None)
        status_msg = await update.message.reply_text(f"⏳ <b>Applying Coupon `{coupon_code}`...</b>", parse_mode="HTML")
        res = await apply_jiomart_coupon(chat_id, coupon_code)
        if res.get("success"):
            await status_msg.edit_text(f"🎉 <b>Coupon Applied!</b> Savings: ₹{res.get('value')} Off", parse_mode="HTML")
        else:
            await status_msg.edit_text(f"❌ <b>Coupon Apply Failed:</b> {res.get('message')}", parse_mode="HTML")
        return await show_cart(update, context)

    if bot_state == "WAITING_FOR_SESSION_NAME" and update.message.text:
        session_name = update.message.text.strip().replace(" ", "_")
        context.user_data.pop("bot_state", None)
        msg = await update.message.reply_text("🌐 ନୂଆ ବ୍ରାଉଜର୍ ଖୋଲୁଛି... ଅପେକ୍ଷା କରନ୍ତୁ...")
        asyncio.create_task(launch_gui_browser(chat_id, session_name, is_new=True, message=msg, bot=context.bot))
        return

    if bot_state == "WAITING_FOR_SEARCH" and update.message.text:
        context.user_data["search_query"] = update.message.text.strip()
        context.user_data["lib_page"] = 0
        context.user_data.pop("bot_state", None)
        return await show_library(update, context)

    if bot_state == "WAITING_FOR_ADMIN_ADD_USER" and update.message.text:
        new_user_id = update.message.text.strip()
        context.user_data.pop("bot_state", None)
        
        if not new_user_id.isdigit():
            return await update.message.reply_text("❌ Invalid User ID. Must be numeric.")
        
        configs = load_configs()
        if new_user_id not in configs:
            configs[new_user_id] = {"pincode": "754011", "active_key": "", "saved_keys": {}}
            save_configs(configs)
            await update.message.reply_text(f"✅ <b>User {new_user_id} added successfully!</b>", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ <b>User {new_user_id} already exists!</b>", parse_mode="HTML")
        return

    if bot_state == "WAITING_FOR_LINKS" and update.message.text:
        urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', update.message.text.strip())
        if not urls: return
        processing_msg = await update.message.reply_text("⏳ ଲିଙ୍କ୍ ପ୍ରୋସେସ୍ ହେଉଛି...")
        target = context.user_data.get("add_target", "LIBRARY")
        sync_mode = get_sync_mode(chat_id)
        results = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context(user_agent="Mozilla/5.0")
                page = await ctx.new_page()
                for raw_url in urls:
                    url_part = format_jiomart_url(raw_url)
                    last_seg = url_part.split("?")[0].rstrip("/").split("/")[-1]
                    if len(last_seg) <= 10 or "/l/" in url_part or "/product/" not in url_part:
                        try:
                            await page.goto(url_part, wait_until="domcontentloaded", timeout=30000)
                            for _ in range(15):
                                if "/product/" in page.url:
                                    url_part = page.url
                                    break
                                await asyncio.sleep(0.5)
                        except Exception: pass

                    final_clean_link = format_jiomart_url(url_part)
                    product_name = extract_product_name_from_url(final_clean_link)
                    update_product_library_smart(product_name, final_clean_link)

                    api_status = ""
                    if target == "CART":
                        cart = load_cart(chat_id)
                        cart[product_name] = cart.get(product_name, 0) + 1
                        save_cart(chat_id, cart)

                        if sync_mode == "DIRECT":
                            api_res = await api_push_to_jiomart_cart(final_clean_link, chat_id, quantity=1)
                            api_status = "☁️ ଲାଇଭ୍ ଏକାଉଣ୍ଟରେ ଯୋଡାଗଲା!" if "success" in api_res else f"⚠️ API Error: {api_res.get('error')}"
                        else:
                            api_status = "📁 Offline Cart ରେ ଯୋଡ଼ାଗଲା!"

                    results.append(f"✅ **{get_smart_name(product_name)}**\n🔗 `{final_clean_link}`\n{api_status}\n")
                await browser.close()
        except Exception: pass

        kb = [[InlineKeyboardButton("✅ Complete Adding Product", callback_data="done_adding")]]
        await processing_msg.edit_text(f"🎯 **Processed:**\n\n" + "\n".join(results), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# ==========================================
# ୧୨. MAIN SCREEN / START COMMAND HANDLER
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_configs()
    if chat_id not in configs:
        configs[chat_id] = {"pincode": "754011", "active_key": "", "saved_keys": {}}
        save_configs(configs)

    context.user_data.pop("bot_state", None)
    context.user_data.pop("search_query", None)

    user_conf = configs.get(chat_id, {})
    current_pin = user_conf.get("pincode", "754011")
    active_session = user_conf.get("active_key") or "None ❌"
    saved_cid = get_saved_cart_id(chat_id) or "Session Active"
    
    addr_name = html.escape(str(user_conf.get("default_address_name") or "Not Set"))
    addr_phone = html.escape(str(user_conf.get("default_address_phone") or "Not Set"))
    addr_str = html.escape(str(user_conf.get("default_address_str") or "Not Set"))
    addr_hex_id = html.escape(str(user_conf.get("default_address_id") or "N/A"))
    auth_token = html.escape(str(user_conf.get("active_token") or "Active"))
    lat_val = user_conf.get("latitude", "20.060583")
    long_val = user_conf.get("longitude", "86.004619")

    is_visible = user_conf.get("browser_visible", False)
    btn_vis_text = "👁️ Show Browser: ON" if is_visible else "👁️ Show Browser: OFF"

    org_btns = user_conf.get("organized_buttons", {
        "lib": True, "cart": True, "old_cart": True, "address": True, "preset": True, "auto_order": True, "sync": True, "all_keys": True
    })

    keyboard = []
    # Add Login P2 Flow button at the top
    keyboard.append([InlineKeyboardButton("🔑 Start Login P2 Flow", callback_data="start_cust_login")])
    
    if org_btns.get("lib", True):
        keyboard.append([InlineKeyboardButton(f"📚 Product Library ({len(load_products())})", callback_data="lib")])
    if org_btns.get("cart", True):
        keyboard.append([InlineKeyboardButton(f"🛒 My Cart ({len(load_cart(chat_id))} items)", callback_data="cart")])
    
    # Add Payment buttons
    keyboard.append([
        InlineKeyboardButton("💳 Pay Now (Browser)", callback_data="continue_old_cart_action"),
        InlineKeyboardButton("🔗 Multi Pay Now", callback_data="multi_continue_menu")
    ])
    
    keyboard.append([
        InlineKeyboardButton("🧾 My Orders", callback_data="my_orders_history_btn"),
    ])
    
    # Admin-only section
    if str(update.effective_chat.id) == str(ADMIN_ID):
        keyboard.append([InlineKeyboardButton("👥 View All Users Keys", callback_data="admin_view_keys")])
        keyboard.append([InlineKeyboardButton("🔧 Admin Control Panel", callback_data="admin_panel")])
    
    # All Keys section (if enabled in organizer)
    if org_btns.get("all_keys", True):
        keyboard.append([InlineKeyboardButton("🔑 All Keys Section", callback_data="show_all_keys_section")])

    if org_btns.get("address", True):
        keyboard.append([InlineKeyboardButton("📍 Delivery Address Menu", callback_data="btn_select_address")])
    if org_btns.get("auto_order", True):
        keyboard.append([InlineKeyboardButton("🤖 Auto Order Engine", callback_data="auto_order_btn")])
    
    keyboard.append([InlineKeyboardButton(btn_vis_text, callback_data="toggle_browser_mode")])
    
    if org_btns.get("sync", True):
        keyboard.append([
            InlineKeyboardButton("🔄 Sync Account", callback_data="sync_account_btn"),
            InlineKeyboardButton("🔑 Manage Sessions", callback_data="switch_session"),
        ])

    keyboard.append([InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")])
    keyboard.append([InlineKeyboardButton("🛑 Force Stop All Operations", callback_data="force_stop_btn")])

    if int(chat_id) == ADMIN_ID or ADMIN_ID == 0:
        keyboard.append([InlineKeyboardButton("👑 Admin Control Panel", callback_data="admin_panel")])

    msg = (
        f"👋 <b>Welcome to JioMart Bot [{VERSION}]!</b>\n\n"
        f"🔑 <b>Active Session:</b> <code>{active_session}</code>\n"
        f"🆔 <b>Cart ID:</b> <code>{saved_cid}</code>\n"
        f"🔑 <b>Auth Token:</b> <code>{auth_token}</code>\n"
        f"📌 <b>Address Hex ID:</b> <code>{addr_hex_id}</code>\n"
        f"🏠 <b>Full Address:</b> <code>{addr_str}</code>\n"
        f"👤 <b>Recipient Name:</b> <code>{addr_name}</code>\n"
        f"📱 <b>Delivery Mobile:</b> <code>{addr_phone}</code>\n"
        f"📍 <b>Pincode & GPS:</b> <code>{current_pin}</code> (<code>{lat_val}, {long_val}</code>)"
    )

    if update.message:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        try:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

# ==========================================
# ୧୩. MAIN APPLICATION BUILDER
# ==========================================
def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(start_command, pattern="^main_menu$"))

    # Login P2 Conversation Handler
    login_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_customer_login_flow, pattern="^start_cust_login$")],
        states={
            MOBILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, customer_mobile_received)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, customer_otp_received)],
            NEW_NAME_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_name_received_handler)]
        },
        fallbacks=[
            CallbackQueryHandler(login_cancel_callback_handler, pattern="^login_cancel_action$"),
            CommandHandler("cancel", cancel_login_flow)
        ],
        per_message=False,
        allow_reentry=True,
        conversation_timeout=300
    )

    # Login P2 Handlers
    application.add_handler(login_conv_handler)
    application.add_handler(CallbackQueryHandler(login_resend_otp_callback_handler, pattern="^login_resend_otp_action$"))
    application.add_handler(CallbackQueryHandler(login_cancel_callback_handler, pattern="^login_cancel_action$"))
    application.add_handler(CommandHandler("cancel", force_stop_login_command))

    # Payment Handlers
    application.add_handler(CallbackQueryHandler(continue_old_cart_handler, pattern="^continue_old_cart_action$"))
    application.add_handler(CallbackQueryHandler(multi_continue_menu_handler, pattern="^multi_continue_menu$"))
    application.add_handler(CallbackQueryHandler(handle_batch_size_modifiers, pattern="^batch_size_"))
    application.add_handler(CallbackQueryHandler(start_dynamic_batch_handler, pattern="^start_dynamic_batch$"))
    application.add_handler(CallbackQueryHandler(handle_dynamic_batch_callbacks, pattern="^bdyn_"))
    application.add_handler(CallbackQueryHandler(back_to_main_dashboard_handler, pattern="^back_to_main_dashboard$"))
    application.add_handler(CallbackQueryHandler(process_single_fast_run_button_clicks, pattern="^fast_confirm_"))

    # Force Stop
    application.add_handler(CallbackQueryHandler(force_stop_btn_handler, pattern="^force_stop_btn$"))

    application.add_handler(CallbackQueryHandler(check_order_history_flow, pattern="^my_orders_history_btn$"))

    # Settings & Organizers
    application.add_handler(CallbackQueryHandler(settings_menu, pattern="^settings_menu$"))
    application.add_handler(CallbackQueryHandler(button_organizer, pattern="^button_organizer$"))
    application.add_handler(CallbackQueryHandler(cart_button_organizer, pattern="^cart_button_organizer$"))
    application.add_handler(CallbackQueryHandler(toggle_org_button, pattern="^toggle_org_.*"))
    application.add_handler(CallbackQueryHandler(toggle_cart_org_button, pattern="^toggle_corg_.*"))

    # Preset & Auto Engines
    application.add_handler(CallbackQueryHandler(preset_menu, pattern="^preset_menu$"))
    application.add_handler(CallbackQueryHandler(set_preset_coupon_handler, pattern="^set_preset_coupon$"))
    application.add_handler(CallbackQueryHandler(auto_order_engine, pattern="^auto_order_btn$"))
    application.add_handler(CallbackQueryHandler(auto_sync_sessions_all, pattern="^auto_sync_all_btn$"))
    application.add_handler(CallbackQueryHandler(apply_preset_coupon_handler, pattern="^apply_preset_coupon_click$"))
    application.add_handler(CallbackQueryHandler(toggle_location_check_mode, pattern="^toggle_location_check_mode$"))
    application.add_handler(CallbackQueryHandler(prompt_manual_pincode, pattern="^prompt_manual_pincode$"))

    # Offline ↔ Online Cart Sync
    application.add_handler(CallbackQueryHandler(sync_offline_to_online, pattern="^sync_off_to_on$"))
    application.add_handler(CallbackQueryHandler(sync_online_to_offline, pattern="^sync_on_to_off$"))

    # Admin Handlers
    application.add_handler(CallbackQueryHandler(admin_control_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_view_all_keys_handler, pattern="^admin_view_keys$"))
    application.add_handler(CallbackQueryHandler(handle_admin_switch_shared_key, pattern="^switch_to_shared_.*"))
    application.add_handler(CallbackQueryHandler(admin_toggle_user_ui, pattern="^admin_toggle_user_ui$"))
    application.add_handler(CallbackQueryHandler(admin_user_management, pattern="^admin_user_management$"))
    application.add_handler(CallbackQueryHandler(admin_add_user, pattern="^admin_add_user$"))
    application.add_handler(CallbackQueryHandler(admin_key_permissions, pattern="^admin_key_permissions$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_share_permission, pattern="^admin_toggle_share_.*"))
    
    # All Keys Section Handler
    application.add_handler(CallbackQueryHandler(show_all_keys_section, pattern="^show_all_keys_section$"))
    application.add_handler(CallbackQueryHandler(switch_key_handler, pattern="^switch_key_.*"))

    application.add_handler(CallbackQueryHandler(sync_account_handler, pattern="^sync_account_btn$"))
    application.add_handler(CallbackQueryHandler(toggle_sync_mode_handler, pattern="^toggle_sync_mode$"))
    application.add_handler(CallbackQueryHandler(toggle_browser_mode, pattern="^toggle_browser_mode$"))

    # Product Library Handlers
    application.add_handler(CallbackQueryHandler(show_library, pattern="^lib$|^libpage_.*"))
    application.add_handler(CallbackQueryHandler(refresh_lib_prices, pattern="^lib_refresh$"))
    application.add_handler(CallbackQueryHandler(lib_single_refresh, pattern="^libref_.*"))
    application.add_handler(CallbackQueryHandler(lib_to_cart, pattern="^lib2cart_.*"))
    application.add_handler(CallbackQueryHandler(trigger_search_lib, pattern="^search_lib$"))
    application.add_handler(CallbackQueryHandler(clear_search_lib, pattern="^clear_search$"))
    application.add_handler(CallbackQueryHandler(clean_library_duplicates_and_errors, pattern="^clean_lib$"))
    application.add_handler(CallbackQueryHandler(lib_remove_menu, pattern="^lib_remove$|^delpage_.*"))
    application.add_handler(CallbackQueryHandler(delete_from_lib, pattern="^dellib_.*"))

    # Cart Handlers
    application.add_handler(CallbackQueryHandler(show_cart, pattern="^cart$"))
    application.add_handler(CallbackQueryHandler(refresh_cart_prices, pattern="^refresh_prices$"))
    application.add_handler(CallbackQueryHandler(read_online_cart_handler, pattern="^read_online_cart$"))
    application.add_handler(CallbackQueryHandler(empty_online_cart_handler, pattern="^empty_online_cart$"))
    application.add_handler(CallbackQueryHandler(confirm_empty_cart_action, pattern="^empty_method_.*"))
    application.add_handler(CallbackQueryHandler(pay_now_cod_handler, pattern="^btn_pay_now_cod$"))

    # Address Handlers
    application.add_handler(CallbackQueryHandler(select_address_handler, pattern="^btn_select_address$"))
    application.add_handler(CallbackQueryHandler(choose_add_address_mode, pattern="^btn_prompt_choose_add_mode$"))
    application.add_handler(CallbackQueryHandler(prompt_add_address_gps_options, pattern="^add_addr_.*"))
    application.add_handler(CallbackQueryHandler(set_default_address_action, pattern="^setaddr_.*|^setoffaddr_.*"))
    application.add_handler(CallbackQueryHandler(prompt_delete_address_handler, pattern="^btn_prompt_delete_address$"))
    application.add_handler(CallbackQueryHandler(delete_address_action, pattern="^delonaddr_.*|^deloffaddr_.*"))

    # Coupon Handlers
    application.add_handler(CallbackQueryHandler(prompt_apply_coupon_handler, pattern="^prompt_apply_coupon$"))
    application.add_handler(CallbackQueryHandler(remove_coupon_handler, pattern="^remove_coupon$"))

    # Session Management
    application.add_handler(CallbackQueryHandler(switch_session_menu, pattern="^switch_session$"))
    application.add_handler(CallbackQueryHandler(set_active_key, pattern="^setkey_.*"))
    application.add_handler(CallbackQueryHandler(add_new_session, pattern="^add_new_session$"))
    application.add_handler(CallbackQueryHandler(view_gui_session, pattern="^view_gui_session$"))
    application.add_handler(CallbackQueryHandler(save_gui_session, pattern="^save_gui_session$"))
    application.add_handler(CallbackQueryHandler(force_kill_browser, pattern="^force_kill_browser$"))

    # Add Product/Cart Items
    application.add_handler(CallbackQueryHandler(trigger_add_mode, pattern="^add_to_cart$|^add_to_library$"))
    application.add_handler(CallbackQueryHandler(complete_adding, pattern="^done_adding$"))
    application.add_handler(CallbackQueryHandler(cart_adjust, pattern="^c(inc|dec|rem|ref)_.*"))

    application.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^ignore$"))
    application.add_handler(MessageHandler(filters.TEXT | filters.LOCATION, global_message_handler))

    logger.info(f"🚀 JioMart Bot [{VERSION}] Online & Ready!")
    application.run_polling()

if __name__ == "__main__":
    main()