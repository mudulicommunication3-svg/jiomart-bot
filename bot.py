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
from aiohttp import web
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
# ୧. CONFIG & FILE PATHS (RENDER/LINUX COMPATIBLE)
# ==========================================
VERSION = "v14.3.0 (RAM Optimized & Hang Fixed)"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TOKEN:
    raise ValueError("ଟୋକେନ୍ ମିଳିଲା ନାହିଁ! Environment Variable ଯାଞ୍ચ କରନ୍ତୁ।")

BASE_DIR = os.path.join(os.getcwd(), "JioMartBot")
os.makedirs(BASE_DIR, exist_ok=True)

SESSION_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

PRODUCT_DB = os.path.join(BASE_DIR, "product_library.json")
CART_DB = os.path.join(BASE_DIR, "cart_offline.json")
CONFIG_DB = os.path.join(BASE_DIR, "bot_database.json")

BATCH_STOP_FLAG = {}
STOP_UPCOMING_TASKS_FLAG = {}
ACTIVE_BATCH_INSTANCES = {}
SCHEDULED_ACTIONS = {}

FIXED_VIEWPORT = {'width': 360, 'height': 640}
MOBILE_USER_AGENT = "Mozilla/5.0 (Linux; Android 7.1.2; Redmi 5A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Mobile Safari/537.36"

# Low RAM Browser Launch Args for Render Free Tier
CHROMIUM_ARGS = [
    "--disable-notifications",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--single-process",
    "--disable-extensions",
    "--js-flags=--max-old-space-size=256"
]

# ==========================================
# 🛠️ HELPER FUNCTIONS
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

def load_configs():
    return load_json(CONFIG_DB, {})

def save_configs(data):
    save_json(CONFIG_DB, data)

def load_cart(chat_id):
    return load_json(CART_DB, {}).get(str(chat_id), {})

async def smart_location_popup_handler(page):
    try:
        modal_title = page.locator("text=Enable location Services, div:has-text('Enable location Services'), text=Location Permission").first
        enable_btn = page.locator("button:has-text('Enable Location'), button:has-text('Select Location Manually'), button:has-text('Allow')").first
        close_x = page.locator("div[class*='modal'] button[aria-label='Close'], button:has-text('✕')").first

        for _ in range(3):
            if await modal_title.is_visible() or await enable_btn.is_visible():
                if await enable_btn.is_visible(): await enable_btn.click(force=True)
                elif await close_x.is_visible(): await close_x.click(force=True)
                await page.wait_for_timeout(2000)
                return True
            await asyncio.sleep(1)
    except Exception:
        pass
    return False

# ==========================================
# 🛑 GLOBAL FORCE STOP EMERGENCY ENGINE
# ==========================================
async def global_force_stop_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    if query:
        await query.answer("🛑 Emergency Force Stop Executed!", show_alert=True)

    BATCH_STOP_FLAG[chat_id] = True
    STOP_UPCOMING_TASKS_FLAG[chat_id] = True

    instances = ACTIVE_BATCH_INSTANCES.get(chat_id, [])
    for inst in instances:
        try:
            await inst["page"].close()
            await inst["context"].close()
            await inst["browser"].close()
            await inst["playwright"].stop()
        except Exception:
            pass
    ACTIVE_BATCH_INSTANCES.pop(chat_id, None)
    SCHEDULED_ACTIONS.pop(chat_id, None)

    msg_text = "🛑 **GLOBAL FORCE STOP EXECUTED!**\n\nAll active browser sessions and batch tasks have been closed immediately."
    if query:
        try: await query.message.edit_text(msg_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back to Main Menu", callback_data="main_menu")]]), parse_mode="Markdown")
        except Exception: pass
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown")

async def stop_upcoming_continue_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    if query:
        await query.answer("⏸️ Stopping upcoming browser tasks. Completing active ones...", show_alert=True)
    STOP_UPCOMING_TASKS_FLAG[chat_id] = True

# ==========================================
# 🎛️ DYNAMIC INLINE KEYBOARD BUILDER
# ==========================================
async def build_payment_control_keyboard(chat_id, batch_instances):
    kb = []
    kb.append([
        InlineKeyboardButton("➕ Queue New Order (+1)", callback_data="queue_add_new_order_action"),
        InlineKeyboardButton("🛑 Cancel All", callback_data="global_force_stop_btn")
    ])

    kb.append([
        InlineKeyboardButton("💵 Wait Cash All", callback_data="global_pay_cod_all"),
        InlineKeyboardButton("📲 Wait QR All", callback_data="global_pay_qr_all")
    ])
    
    kb.append([InlineKeyboardButton("✋ Stop Upcoming Tasks & Continue Current", callback_data="stop_upcoming_continue_current")])

    user_sched = SCHEDULED_ACTIONS.get(chat_id, {})
    for idx, inst in enumerate(batch_instances):
        b_id = inst["id"]
        sched_val = user_sched.get(idx)
        
        cash_label = "💵 Cash (Scheduled)" if sched_val == "COD" else "💵 Cash"
        qr_label = "📲 QR (Scheduled)" if sched_val == "QR" else "📲 QR"

        if inst.get("done"):
            kb.append([InlineKeyboardButton(f"✅ {b_id}: Completed", callback_data="none")])
        else:
            kb.append([
                InlineKeyboardButton(f"{b_id}", callback_data="none"),
                InlineKeyboardButton(cash_label, callback_data=f"ind_pay_cod_{idx}"),
                InlineKeyboardButton(qr_label, callback_data=f"ind_pay_qr_{idx}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ind_pay_cancel_{idx}")
            ])

    return InlineKeyboardMarkup(kb)

async def refresh_live_dashboard(status_msg, chat_id, instances, extra_text=""):
    kb = await build_payment_control_keyboard(chat_id, instances)
    text = (
        f"💳 **Live Batch Engine Controller ({len(instances)} Browsers in Queue)**\n"
        f"{extra_text}\n\n"
        f"Select payment modes individually for each browser below, or schedule actions at top:"
    )
    try: await status_msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception: pass

# ==========================================
# ➕ ANYTIME DYNAMIC QUEUE ORDER HANDLER
# ==========================================
async def queue_add_new_order_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    await query.answer("➕ Adding New Order Browser to Queue...", show_alert=False)

    instances = ACTIVE_BATCH_INSTANCES.get(chat_id, [])
    cfg = load_configs().get(chat_id, {})
    active_key = cfg.get("active_key", "")
    auth_file = cfg.get("saved_keys", {}).get(active_key)

    if not auth_file or not os.path.exists(auth_file):
        return await query.message.reply_text("❌ No active session key found!")

    new_idx = len(instances) + 1
    inst_id = f"Browser #{new_idx}"

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
    ctx = await browser.new_context(
        storage_state=auth_file,
        viewport=FIXED_VIEWPORT,
        user_agent=MOBILE_USER_AGENT,
        is_mobile=True,
        has_touch=True
    )
    page = await ctx.new_page()

    new_inst = {
        "id": inst_id,
        "playwright": p,
        "browser": browser,
        "context": ctx,
        "page": page,
        "status": "In Cart",
        "done": False,
        "landed_payment": False
    }

    instances.append(new_inst)
    await refresh_live_dashboard(query.message, chat_id, instances, extra_text=f"➕ [{inst_id}] Dynamically Added to Queue!")

    try:
        await page.goto("https://www.jiomart.com/cart/bag", wait_until="domcontentloaded", timeout=45000)
        await smart_location_popup_handler(page)
    except Exception as ex:
        logger.warning(f"Queue Nav Warning {inst_id}: {ex}")

    preset_coupon = cfg.get("preset_coupon", "")
    context.application.create_task(process_single_queued_browser(query.message, context, chat_id, new_inst, preset_coupon, instances))

async def process_single_queued_browser(status_msg, context, chat_id, inst, coupon_code, instances):
    page = inst["page"]
    b_id = inst["id"]

    try:
        await page.bring_to_front()

        if coupon_code and coupon_code != "None ❌":
            try:
                coupon_input = page.locator("input[placeholder*='coupon'], input[id*='coupon'], input[class*='coupon']").first
                if not await coupon_input.is_visible():
                    apply_coupon_btn = page.get_by_text("Apply Coupon").or_(page.get_by_text("Apply")).first
                    if await apply_coupon_btn.is_visible():
                        await apply_coupon_btn.click()
                        await page.wait_for_timeout(2000)

                coupon_input = page.locator("input[placeholder*='coupon'], input[id*='coupon'], input[class*='coupon']").first
                if await coupon_input.is_visible():
                    await coupon_input.focus()
                    await coupon_input.fill(coupon_code)
                    await page.wait_for_timeout(1000)
                    
                    apply_click = page.locator("button:has-text('Apply'), div:has-text('Apply')").last
                    await apply_click.click(force=True)
                    await page.wait_for_timeout(4000)
            except Exception: pass

        cart_img_path = os.path.join(SESSION_DIR, f"cart_{b_id.replace('#', '').replace(' ', '')}.png")
        try:
            await page.screenshot(path=cart_img_path, full_page=True)
            if os.path.exists(cart_img_path):
                with open(cart_img_path, 'rb') as f:
                    await context.bot.send_photo(chat_id=chat_id, photo=f, caption=f"🧾 **[{b_id}] Queued Order Cart Billing**")
                os.remove(cart_img_path)
        except Exception: pass

        await asyncio.sleep(8)

        try:
            pay_now_btn = page.get_by_text("Pay Online").or_(page.get_by_text("Pay now")).or_(page.get_by_text("Proceed to Checkout")).first
            if await pay_now_btn.is_visible():
                await pay_now_btn.click(force=True)
        except Exception: pass

        await asyncio.sleep(10)

        pay_img_path = os.path.join(SESSION_DIR, f"payment_{b_id.replace('#', '').replace(' ', '')}.png")
        try:
            await page.screenshot(path=pay_img_path, full_page=True)
            if os.path.exists(pay_img_path):
                with open(pay_img_path, 'rb') as f:
                    await context.bot.send_photo(chat_id=chat_id, photo=f, caption=f"💳 **[{b_id}] Queued Order Payment Screen**")
                os.remove(pay_img_path)
        except Exception: pass

        inst["status"] = "Landed on Payment Page"
        inst["landed_payment"] = True
        await refresh_live_dashboard(status_msg, chat_id, instances, extra_text=f"✅ [{b_id}] Ready in Payment Page!")

        await auto_execute_scheduled_payments(status_msg, context, chat_id, instances)

    except Exception as e:
        logger.error(f"Error processing queued browser {b_id}: {e}")

# ==========================================
# 🔗 BATCH ORDER SECTION CONTROLLER
# ==========================================
async def multi_continue_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()

    if "batch_size" not in context.user_data: 
        context.user_data["batch_size"] = 1

    size = context.user_data["batch_size"]

    keyboard = [
        [InlineKeyboardButton("➖ 1", callback_data="batch_size_minus"), 
         InlineKeyboardButton(f"Qty: {size} Orders", callback_data="none"), 
         InlineKeyboardButton("➕ 1", callback_data="batch_size_plus")],
        [InlineKeyboardButton("🚀 Launch Sequential Batch Browsers", callback_data="start_advanced_batch_flow")],
        [InlineKeyboardButton("🛑 Force Stop All Operations", callback_data="global_force_stop_btn")],
        [InlineKeyboardButton("🔙 Back to Main Dashboard", callback_data="main_menu")]
    ]

    text = (
        f"📦 **Batch Order Controller [{VERSION}]**\n\n"
        f"🔢 **Initial Order Quantity:** `{size}` Browsers\n\n"
        f"⚡ **Key Features:**\n"
        f"1️⃣ **Anytime Queueing:** Use `➕ Queue New Order (+1)` at ANY TIME during execution to add more orders!\n"
        f"2️⃣ **Scheduled Auto-Execution:** Click Cash/QR for any browser to schedule execution upon Payment Page landing."
    )
    
    if query:
        try: await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception: pass

async def handle_batch_size_modifiers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "batch_size_minus" and context.user_data.get("batch_size", 1) > 1:
        context.user_data["batch_size"] -= 1
    elif query.data == "batch_size_plus" and context.user_data.get("batch_size", 1) < 10:
        context.user_data["batch_size"] += 1

    await multi_continue_menu_handler(update, context)

async def start_advanced_batch_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = str(update.effective_chat.id)
    cfg = load_configs().get(chat_id, {})
    active_key = cfg.get("active_key", "")
    auth_file = cfg.get("saved_keys", {}).get(active_key)

    if not active_key or not auth_file or not os.path.exists(auth_file):
        return await query.message.reply_text("❌ No active session key found. Please login first!")

    batch_size = context.user_data.get("batch_size", 1)
    preset_coupon = cfg.get("preset_coupon", "")

    BATCH_STOP_FLAG[chat_id] = False
    STOP_UPCOMING_TASKS_FLAG[chat_id] = False
    SCHEDULED_ACTIONS[chat_id] = {}

    status_msg = await query.message.reply_text(
        f"⏳ **Launching Batch Engine ({batch_size} Browsers)...**", 
        parse_mode="Markdown"
    )

    context.application.create_task(
        run_full_batch_coupon_engine(status_msg, context, chat_id, auth_file, active_key, batch_size, preset_coupon)
    )

async def run_full_batch_coupon_engine(status_msg, context, chat_id, auth_file, active_key, batch_size, coupon_code):
    batch_instances = []
    ACTIVE_BATCH_INSTANCES[chat_id] = batch_instances

    try:
        for i in range(batch_size):
            if BATCH_STOP_FLAG.get(chat_id) or STOP_UPCOMING_TASKS_FLAG.get(chat_id):
                break

            p = await async_playwright().start()
            browser = await p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
            ctx = await browser.new_context(
                storage_state=auth_file,
                viewport=FIXED_VIEWPORT,
                user_agent=MOBILE_USER_AGENT,
                is_mobile=True,
                has_touch=True
            )
            page = await ctx.new_page()

            inst_id = f"Browser #{i+1}"
            
            try:
                await page.goto("https://www.jiomart.com/cart/bag", wait_until="domcontentloaded", timeout=45000)
                await smart_location_popup_handler(page)
            except Exception as ex:
                logger.warning(f"Nav warning for {inst_id}: {ex}")

            batch_instances.append({
                "id": inst_id,
                "playwright": p,
                "browser": browser,
                "context": ctx,
                "page": page,
                "status": "In Cart",
                "done": False,
                "landed_payment": False
            })

            await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"🌐 [{inst_id}] Landed in Cart Page")
            await asyncio.sleep(2)

        if BATCH_STOP_FLAG.get(chat_id): return

        for idx, inst in enumerate(batch_instances):
            if BATCH_STOP_FLAG.get(chat_id): break

            if STOP_UPCOMING_TASKS_FLAG.get(chat_id) and idx > 0 and not inst.get("landed_payment"):
                inst["status"] = "Skipped"
                continue

            page = inst["page"]
            b_id = inst["id"]

            await page.bring_to_front()
            
            await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"🎟️ [{b_id}] Applying Coupon Code `{coupon_code}`...")
            if coupon_code and coupon_code != "None ❌":
                try:
                    coupon_input = page.locator("input[placeholder*='coupon'], input[id*='coupon'], input[class*='coupon']").first
                    if not await coupon_input.is_visible():
                        apply_coupon_btn = page.get_by_text("Apply Coupon").or_(page.get_by_text("Apply")).first
                        if await apply_coupon_btn.is_visible():
                            await apply_coupon_btn.click()
                            await page.wait_for_timeout(2000)

                    coupon_input = page.locator("input[placeholder*='coupon'], input[id*='coupon'], input[class*='coupon']").first
                    if await coupon_input.is_visible():
                        await coupon_input.focus()
                        await coupon_input.fill(coupon_code)
                        await page.wait_for_timeout(1000)
                        
                        apply_click = page.locator("button:has-text('Apply'), div:has-text('Apply')").last
                        await apply_click.click(force=True)
                        await page.wait_for_timeout(4000)
                except Exception as e:
                    logger.info(f"Coupon apply trace for {b_id}: {e}")

            await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"📸 [{b_id}] Capturing Cart Billing Photo...")
            cart_img_path = os.path.join(SESSION_DIR, f"cart_{b_id.replace('#', '').replace(' ', '')}.png")
            try:
                await page.screenshot(path=cart_img_path, full_page=True)
                if os.path.exists(cart_img_path):
                    with open(cart_img_path, 'rb') as f:
                        await context.bot.send_photo(
                            chat_id=chat_id, 
                            photo=f, 
                            caption=f"🧾 **[{b_id}] Cart Page Billing (Coupon Applied)**",
                            parse_mode="Markdown"
                        )
                    os.remove(cart_img_path)
            except Exception as e:
                logger.error(f"Cart screenshot error: {e}")

            for remaining_pre in range(8, 0, -1):
                if BATCH_STOP_FLAG.get(chat_id): break
                await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"⏳ [{b_id}] Coupon Applied! Waiting {remaining_pre}s before clicking Pay Online...")
                await asyncio.sleep(1)

            if BATCH_STOP_FLAG.get(chat_id): break

            await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"💳 [{b_id}] Clicking Pay Online...")
            try:
                pay_now_btn = page.get_by_text("Pay Online").or_(page.get_by_text("Pay now")).or_(page.get_by_text("Proceed to Checkout")).first
                if await pay_now_btn.is_visible():
                    await pay_now_btn.click(force=True)
            except Exception as ex:
                logger.warning(f"Pay online click error {b_id}: {ex}")

            for remaining_post in range(10, 0, -1):
                if BATCH_STOP_FLAG.get(chat_id): break
                await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"⏳ [{b_id}] Clicked Pay Online! Waiting {remaining_post}s before payment photo capture...")
                await asyncio.sleep(1)

            if BATCH_STOP_FLAG.get(chat_id): break

            await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"📸 [{b_id}] Capturing Payment Page Screenshot...")
            pay_img_path = os.path.join(SESSION_DIR, f"payment_{b_id.replace('#', '').replace(' ', '')}.png")
            try:
                await page.screenshot(path=pay_img_path, full_page=True)
                if os.path.exists(pay_img_path):
                    with open(pay_img_path, 'rb') as f:
                        await context.bot.send_photo(
                            chat_id=chat_id, 
                            photo=f, 
                            caption=f"💳 **[{b_id}] Payment Section Screen (Captured after 10s)**",
                            parse_mode="Markdown"
                        )
                    os.remove(pay_img_path)
            except Exception as e:
                logger.error(f"Payment screenshot error: {e}")

            inst["status"] = "Landed on Payment Page"
            inst["landed_payment"] = True
            await refresh_live_dashboard(status_msg, chat_id, batch_instances, extra_text=f"✅ [{b_id}] In Payment Page")
            await asyncio.sleep(1)

        if BATCH_STOP_FLAG.get(chat_id): return

        await auto_execute_scheduled_payments(status_msg, context, chat_id, batch_instances)

    except Exception as e:
        await status_msg.edit_text(f"❌ Batch Workflow Error: {str(e)}")

# ==========================================
# 💳 PAYMENT SELECTION & EXECUTION
# ==========================================
async def handle_payment_inline_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = str(update.effective_chat.id)
    instances = ACTIVE_BATCH_INSTANCES.get(chat_id, [])

    if chat_id not in SCHEDULED_ACTIONS:
        SCHEDULED_ACTIONS[chat_id] = {}

    if data == "global_pay_cod_all":
        for i in range(len(instances)): SCHEDULED_ACTIONS[chat_id][i] = "COD"
        await query.message.reply_text("✅ Scheduled: Cash On Delivery (COD) for All Browsers!")
        await refresh_live_dashboard(query.message, chat_id, instances)
        await auto_execute_scheduled_payments(query.message, context, chat_id, instances)

    elif data == "global_pay_qr_all":
        for i in range(len(instances)): SCHEDULED_ACTIONS[chat_id][i] = "QR"
        await query.message.reply_text("✅ Scheduled: Pay via QR Code for All Browsers!")
        await refresh_live_dashboard(query.message, chat_id, instances)
        await auto_execute_scheduled_payments(query.message, context, chat_id, instances)

    elif data.startswith("ind_pay_cod_"):
        idx = int(data.split("_")[3])
        SCHEDULED_ACTIONS[chat_id][idx] = "COD"
        await refresh_live_dashboard(query.message, chat_id, instances)
        await auto_execute_scheduled_payments(query.message, context, chat_id, instances)

    elif data.startswith("ind_pay_qr_"):
        idx = int(data.split("_")[3])
        SCHEDULED_ACTIONS[chat_id][idx] = "QR"
        await refresh_live_dashboard(query.message, chat_id, instances)
        await auto_execute_scheduled_payments(query.message, context, chat_id, instances)

    elif data.startswith("ind_pay_cancel_"):
        idx = int(data.split("_")[3])
        if idx < len(instances):
            inst = instances[idx]
            try:
                await inst["page"].close()
                await inst["context"].close()
                await inst["browser"].close()
                await inst["playwright"].stop()
            except Exception: pass
            inst["done"] = True
            inst["status"] = "Cancelled"
            SCHEDULED_ACTIONS[chat_id].pop(idx, None)
            await refresh_live_dashboard(query.message, chat_id, instances)

async def auto_execute_scheduled_payments(status_msg, context, chat_id, instances):
    user_sched = SCHEDULED_ACTIONS.get(chat_id, {})

    for idx, inst in enumerate(instances):
        if inst.get("done") or not inst.get("landed_payment"):
            continue

        sched_action = user_sched.get(idx)
        if not sched_action:
            continue

        b_id = inst["id"]

        if sched_action == "COD":
            await refresh_live_dashboard(status_msg, chat_id, instances, extra_text=f"💵 Executing COD for [{b_id}]...")
            await execute_single_instance_cod(status_msg, context, chat_id, inst)
            inst["done"] = True
            user_sched.pop(idx, None)
            await refresh_live_dashboard(status_msg, chat_id, instances)

        elif sched_action == "QR":
            await refresh_live_dashboard(status_msg, chat_id, instances, extra_text=f"📲 Generating QR Code for [{b_id}]...")
            await execute_single_instance_qr(status_msg, context, chat_id, inst)
            inst["done"] = True
            user_sched.pop(idx, None)
            await refresh_live_dashboard(status_msg, chat_id, instances)

            for rem in range(5, 0, -1):
                if BATCH_STOP_FLAG.get(chat_id): break
                await asyncio.sleep(1)

    context.application.create_task(monitor_order_refresh_and_confirm(status_msg, context, chat_id, instances))

async def execute_single_instance_cod(message_obj, context, chat_id, inst):
    page = inst["page"]
    b_id = inst["id"]
    await page.bring_to_front()

    try:
        for _ in range(2):
            await page.keyboard.press("PageDown")
            await page.wait_for_timeout(800)
    except Exception: pass

    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        js_cod = """() => {
            let elems = Array.from(document.querySelectorAll('*')).filter(el => el.innerText && el.innerText.includes('Cash on Delivery'));
            if (elems.length > 0) { elems[elems.length - 1].click(); return true; }
            return false;
        }"""
        await page.evaluate(js_cod)
    except Exception: pass

    await page.wait_for_timeout(3000)
    
    cod_img_path = os.path.join(SESSION_DIR, f"cod_{b_id.replace('#', '').replace(' ', '')}.png")
    try:
        await page.screenshot(path=cod_img_path, full_page=True)
        if os.path.exists(cod_img_path):
            with open(cod_img_path, 'rb') as f:
                await context.bot.send_photo(chat_id=chat_id, photo=f, caption=f"💵 **[{b_id}] Cash on Delivery Selected!**")
            os.remove(cod_img_path)
    except Exception: pass

async def execute_single_instance_qr(message_obj, context, chat_id, inst):
    page = inst["page"]
    b_id = inst["id"]
    await page.bring_to_front()

    try:
        qr_text_btn = page.get_by_text("QR Code").or_(page.get_by_text("Pay via QR Code")).or_(page.locator("text=QR")).first
        if await qr_text_btn.is_visible():
            await qr_text_btn.click(force=True)
            await page.wait_for_timeout(1500)

        gen_qr_btn = page.get_by_text("Generate QR Code").or_(page.get_by_text("Generate QR")).or_(page.locator("button:has-text('QR')")).first
        if await gen_qr_btn.is_visible():
            await gen_qr_btn.click(force=True)
            await page.wait_for_timeout(3000)
    except Exception as ex:
        logger.warning(f"QR click error {b_id}: {ex}")

    qr_img_path = os.path.join(SESSION_DIR, f"qr_{b_id.replace('#', '').replace(' ', '')}.png")
    try:
        await page.screenshot(path=qr_img_path, full_page=True)
        if os.path.exists(qr_img_path):
            with open(qr_img_path, 'rb') as f:
                await context.bot.send_photo(
                    chat_id=chat_id, 
                    photo=f, 
                    caption=f"📲 **[{b_id}] Payment QR Code (Generated)**",
                    parse_mode="Markdown"
                )
            os.remove(qr_img_path)
    except Exception as e:
        logger.error(f"QR screenshot error: {e}")

# ==========================================
# 🔍 MONITOR PAGE REFRESH & CLOSE
# ==========================================
async def monitor_order_refresh_and_confirm(status_msg, context, chat_id, instances):
    completed = 0
    total = len(instances)

    while completed < total:
        if BATCH_STOP_FLAG.get(chat_id): break

        for inst in instances:
            if inst.get("done_confirmed"): continue
            page = inst["page"]
            b_id = inst["id"]

            try:
                current_url = page.url
                is_refreshed = "order-status" in current_url or "success" in current_url or await page.get_by_text("Order Confirmed").is_visible()
                
                if is_refreshed:
                    await asyncio.sleep(5)

                    conf_img_path = os.path.join(SESSION_DIR, f"confirm_{b_id.replace('#', '').replace(' ', '')}.png")
                    await page.screenshot(path=conf_img_path, full_page=True)
                    
                    if os.path.exists(conf_img_path):
                        with open(conf_img_path, 'rb') as f:
                            await context.bot.send_photo(
                                chat_id=chat_id, 
                                photo=f, 
                                caption=f"🎉 **[{b_id}] Order Confirmed Successfully!**",
                                parse_mode="Markdown"
                            )
                        os.remove(conf_img_path)

                    await page.close()
                    await inst["context"].close()
                    await inst["browser"].close()
                    await inst["playwright"].stop()
                    inst["done"] = True
                    inst["done_confirmed"] = True
                    completed += 1
            except Exception:
                pass

        await asyncio.sleep(3)

    ACTIVE_BATCH_INSTANCES.pop(chat_id, None)
    SCHEDULED_ACTIONS.pop(chat_id, None)
    kb = [[InlineKeyboardButton("🔙 Back to Main Dashboard", callback_data="main_menu")]]
    await status_msg.edit_text("🎊 **All Batch Orders Processed & Browsers Closed Successfully!**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# ==========================================
# 💾 DATABASE EXPORT COMMAND (/getdb)
# ==========================================
async def get_database_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    files = [CONFIG_DB, PRODUCT_DB, CART_DB]
    
    found = False
    for fpath in files:
        if os.path.exists(fpath):
            found = True
            with open(fpath, "rb") as f:
                await context.bot.send_document(chat_id=chat_id, document=f, caption=f"📁 Backup: `{os.path.basename(fpath)}`", parse_mode="Markdown")

    if not found:
        await update.message.reply_text("❌ No database files found on server yet.")

# ==========================================
# 🌐 DUMMY WEB SERVER FOR RENDER PORT BINDING
# ==========================================
async def handle_ping(request):
    return web.Response(text="JioMart Bot Web Server is Active & Healthy!")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# ==========================================
# 🤖 MAIN BOT APPLICATION BUILDER
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    configs = load_configs()
    if chat_id not in configs:
        configs[chat_id] = {"pincode": "754011", "active_key": "", "saved_keys": {}}
        save_configs(configs)

    user_conf = configs.get(chat_id, {})
    active_session = user_conf.get("active_key") or "None ❌"

    keyboard = [
        [InlineKeyboardButton("📦 Batch Order Section", callback_data="multi_continue_menu")],
        [InlineKeyboardButton(f"📚 Product Library ({len(load_products())})", callback_data="lib")],
        [InlineKeyboardButton(f"🛒 My Cart ({len(load_cart(chat_id))} items)", callback_data="cart")],
        [InlineKeyboardButton("🔑 Manage Sessions", callback_data="switch_session")],
        [InlineKeyboardButton("🛑 Force Stop All Operations", callback_data="global_force_stop_btn")]
    ]

    msg = (
        f"👋 **JioMart Automation Bot [{VERSION}]**\n\n"
        f"🔑 **Active Session:** `{active_session}`\n"
        f"📌 **Pincode:** `{user_conf.get('pincode', '754011')}`\n\n"
        f"💡 *Tip:* Use `/getdb` command to download database files."
    )

    if update.message:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        try: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception: pass

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("getdb", get_database_files_command))
    application.add_handler(CallbackQueryHandler(start_command, pattern="^main_menu$"))

    application.add_handler(CallbackQueryHandler(global_force_stop_action, pattern="^global_force_stop_btn$"))
    application.add_handler(CallbackQueryHandler(stop_upcoming_continue_current, pattern="^stop_upcoming_continue_current$"))

    application.add_handler(CallbackQueryHandler(multi_continue_menu_handler, pattern="^multi_continue_menu$"))
    application.add_handler(CallbackQueryHandler(handle_batch_size_modifiers, pattern="^batch_size_"))
    application.add_handler(CallbackQueryHandler(start_advanced_batch_flow_handler, pattern="^start_advanced_batch_flow$"))
    application.add_handler(CallbackQueryHandler(queue_add_new_order_handler, pattern="^queue_add_new_order_action$"))
    application.add_handler(CallbackQueryHandler(handle_payment_inline_actions, pattern="^(global_pay_|ind_pay_).*"))

    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    
    async def start_web_server():
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_web_server())

    logger.info(f"🚀 JioMart Bot [{VERSION}] Online & Ready on Port {port}!")
    application.run_polling()

if __name__ == "__main__":
    main()
