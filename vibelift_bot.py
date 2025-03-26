import os
import time
import logging
import asyncio
import json
import random
import string
from typing import Dict, Any
from datetime import datetime
import requests  # Added for payment API calls

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery  # Added CallbackQuery
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from asgiref.wsgi import WsgiToAsgi  # Updated import from previous fix
import uvicorn
import pymongo
from pymongo import MongoClient

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MONGODB_URI = os.getenv("MONGODB_URI")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")

# Define constants
WITHDRAWAL_LIMIT = 1000  # Added missing constant
PAYSTACK_HEADERS = {  # Added missing headers for Paystack API
    "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
    "Content-Type": "application/json"
}

# Flask app setup
app = Flask(__name__)

# MongoDB setup
client = MongoClient(MONGODB_URI)
db = client.get_default_database()
users_collection = db['users']

# Global variables
application = None
users: Dict[str, Any] = {}

# Rate limiting setup
RATE_LIMITS = {}
DEFAULT_COOLDOWN = 5  # 5 seconds for most actions
SIGNUP_COOLDOWN = 60  # 60 seconds for signup actions

# Package limits for different platforms
package_limits = {
    'followers': {
        'instagram': {'min': 10, 'max': 500},
        'facebook': {'min': 10, 'max': 500},
        'tiktok': {'min': 10, 'max': 500},
        'twitter': {'min': 10, 'max': 500}
    },
    'likes': {
        'instagram': {'min': 20, 'max': 500},
        'facebook': {'min': 20, 'max': 500},
        'tiktok': {'min': 20, 'max': 500},
        'twitter': {'min': 20, 'max': 500}
    },
    'comments': {
        'instagram': {'min': 5, 'max': 500},
        'facebook': {'min': 5, 'max': 500},
        'tiktok': {'min': 5, 'max': 500},
        'twitter': {'min': 5, 'max': 500}
    },
    'bundle': {
        'instagram': {
            'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 1890},
            'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 8640},
            'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 18900}
        },
        'facebook': {
            'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 2400},
            'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 10800},
            'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 24000}
        },
        'tiktok': {
            'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 2700},
            'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 14500},  # Updated from 12600 to 14500
            'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 27000}
        },
        'twitter': {
            'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 1600},
            'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 7200},
            'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 16000}
        }
    }
}

# Rate limiting function
def check_rate_limit(user_id: str, action: str, cooldown: int = DEFAULT_COOLDOWN, is_signup_action: bool = False) -> bool:
    key = f"{user_id}_{action}"
    current_time = time.time()
    cooldown = SIGNUP_COOLDOWN if is_signup_action else cooldown

    if key in RATE_LIMITS:
        last_time = RATE_LIMITS[key]
        if current_time - last_time < cooldown:
            return False
    RATE_LIMITS[key] = current_time
    return True

# MongoDB functions
async def load_users() -> Dict[str, Any]:
    try:
        users_data = users_collection.find_one({"_id": "users"})
        return users_data.get("data", {}) if users_data else {}
    except Exception as e:
        logger.error(f"Error loading users from MongoDB: {str(e)}")
        return {}

async def save_users() -> None:
    try:
        users_collection.update_one(
            {"_id": "users"},
            {"$set": {"data": users}},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving users to MongoDB: {str(e)}")

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("An error occurred. Please try again or contact support.")

# Generate admin code
async def generate_admin_code(user_id: int, action: str, action_data: Dict[str, Any]) -> str:
    code = ''.join(random.choices(string.digits, k=6))
    users['pending_admin_actions'][f"{action}_{user_id}"] = {
        'user_id': user_id,
        'action': action,
        'action_data': action_data,
        'code': code,
        'expiration': time.time() + 300
    }
    await application.bot.send_message(
        chat_id=user_id,
        text=f"Your 6-digit code for {action.replace('_', ' ')}: {code}\nEnter it in the admin group to confirm."
    )
    await save_users()
    return code

# Status command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /start command from user {user_id}")
    if not check_rate_limit(user_id, action='start', is_signup_action=True):
        logger.info(f"User {user_id} is rate-limited for /start")
        await update.message.reply_text("Please wait a moment before trying again!")
        return
    try:
        args = context.args
        if args and args[0].startswith("payment_success_"):
            order_id = args[0].split("payment_success_")[1]
            if order_id in users.get('active_orders', {}):
                await update.message.reply_text(
                    f"🎉 Payment successful! Your order (ID: {order_id}) is now active. Check progress with /status."
                )
            else:
                await update.message.reply_text(
                    "⚠️ Payment confirmation is still processing. Please wait a moment or use /status to check."
                )
            return
        keyboard = [
            [InlineKeyboardButton("Join as Client", callback_data='client')],
            [InlineKeyboardButton("Join as Engager", callback_data='engager')],
            [InlineKeyboardButton("Help", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Welcome to VibeLiftBot! 🚀\n"
            "Boost your social media or earn cash by engaging.\n"
            "Pick your role:", reply_markup=reply_markup
        )
        logger.info(f"Sent /start response to user {user_id}")
    except Exception as e:
        logger.error(f"Error in /start for user {user_id}: {str(e)}")
        await update.message.reply_text("An error occurred. Please try again or contact support.")

# Client command
async def client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /client command from user {user_id}")
    if not check_rate_limit(user_id, action='client', is_signup_action=True):
        logger.info(f"User {user_id} is rate-limited for /client")
        await update.message.reply_text("Please wait a moment before trying again!")
        return
    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        if client_data['step'] == 'awaiting_payment':
            await update.message.reply_text(
                f"You have an order pending payment!\n"
                f"Use /pay to proceed or /cancel to start over."
            )
            return
        elif client_data['step'] == 'awaiting_order':
            platform = client_data['platform']
            bundles = "\n".join(
                f"- {k.capitalize()}: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
                for k, v in package_limits['bundle'][platform].items()
            )
            await update.message.reply_text(
                f"You're ready to submit an order for {platform.capitalize()}!\n"
                f"Available bundles:\n{bundles}\n"
                "Options:\n"
                "1. Handle + Bundle: '@myhandle starter'\n"
                "2. URL + Bundle: 'https://instagram.com/username starter'\n"
                "3. Package + Screenshot: 'package pro' with photo\n"
                "4. Custom + Screenshot: 'username, 20 follows, 30 likes, 20 comments' with photo\n"
                "Custom limits: 10-500 per metric. Screenshot optional for options 1 and 2."
            )
            return
    users['clients'][user_id] = {'step': 'select_platform'}
    await save_users()
    keyboard = [
        [InlineKeyboardButton("Instagram", callback_data="platform_instagram")],
        [InlineKeyboardButton("Facebook", callback_data="platform_facebook")],
        [InlineKeyboardButton("TikTok", callback_data="platform_tiktok")],
        [InlineKeyboardButton("Twitter", callback_data="platform_twitter")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Select a platform to boost:",
        reply_markup=reply_markup
    )
    logger.info(f"Sent platform selection to user {user_id}")

# Engager command
async def engager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /engager command from user {user_id}")
    if not check_rate_limit(user_id, action='engager', is_signup_action=True):
        logger.info(f"User {user_id} is rate-limited for /engager")
        await update.message.reply_text("Please wait a moment before trying again!")
        return
    if user_id in users['engagers']:
        keyboard = [
            [InlineKeyboardButton("See Tasks", callback_data='tasks')],
            [InlineKeyboardButton("Check Balance", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "You're already an engager! Pick an action:",
            reply_markup=reply_markup
        )
        return
    users['engagers'][user_id] = {
        'earnings': 0,
        'signup_bonus': 500,
        'task_timers': {},
        'daily_tasks': {'count': 0, 'last_reset': time.time()},
        'claims': [],
        'awaiting_payout': False
    }
    keyboard = [
        [InlineKeyboardButton("See Tasks", callback_data='tasks')],
        [InlineKeyboardButton("Check Balance", callback_data='balance')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎉 Welcome, new engager! You’ve earned a ₦500 signup bonus!\nPick an action:",
        reply_markup=reply_markup
    )
    await save_users()
    logger.info(f"User {user_id} joined as engager")

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /help command from user {user_id}")
    if not check_rate_limit(user_id, action='help'):
        logger.info(f"User {user_id} is rate-limited for /help")
        await update.message.reply_text("Please wait a moment before trying again!")
        return
    await update.message.reply_text(
        "Need help?\n"
        "- Clients: Boost your social media with /client.\n"
        "- Engagers: Earn cash with /tasks.\n"
        "- Check your order status with /status.\n"
        "- Contact support: [Your Support Link]"
    )
    logger.info(f"Help sent to user {user_id}")

# Pay command
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /pay command from user {user_id}")
    if user_id not in users['clients']:
        await update.message.reply_text("Start an order with /client first!")
        return
    client_data = users['clients'][user_id]
    if client_data['step'] != 'awaiting_payment':
        await update.message.reply_text("You don't have an order awaiting payment! Start with /client.")
        return
    amount = client_data['amount']
    order_id = client_data['order_id']
    payment_link = await initiate_payment(user_id, amount, order_id)
    if payment_link:
        await update.message.reply_text(
            f"Please complete your payment of ₦{amount} here:\n{payment_link}\n"
            "After payment, you will be redirected back to the bot."
        )
    else:
        await update.message.reply_text("Failed to generate payment link. Please try again or contact support.")

# Payment initiation
async def initiate_payment(user_id: str, amount: int, order_id: str) -> str:
    try:
        url = "https://api.paystack.co/transaction/initialize"
        data = {
            "email": f"{user_id}@vibeliftbot.com",
            "amount": amount * 100,  # Convert to kobo
            "reference": order_id,
            "callback_url": f"{WEBHOOK_URL}/payment_callback"
        }
        response = requests.post(url, headers=PAYSTACK_HEADERS, json=data)
        response_data = response.json()
        if response_data.get("status"):
            return response_data['data']['authorization_url']
        else:
            logger.error(f"Payment initiation failed for user {user_id}: {response_data}")
            return None
    except Exception as e:
        logger.error(f"Error initiating payment for user {user_id}: {str(e)}")
        return None

# Payment callback endpoint
@app.route('/payment_callback', methods=['GET', 'POST'])
async def payment_callback():
    if request.method == 'POST':
        # Handle Paystack webhook (payment verification)
        try:
            data = request.get_json()
            if not data:
                logger.error("No JSON data received in payment callback")
                return jsonify({"status": "error", "message": "No data received"}), 400

            event = data.get('event')
            if event != 'charge.success':
                logger.info(f"Ignoring event: {event}")
                return jsonify({"status": "success"}), 200

            payment_data = data.get('data', {})
            reference = payment_data.get('reference')
            if not reference:
                logger.error("No reference found in payment callback data")
                return jsonify({"status": "error", "message": "No reference provided"}), 400

            # Verify payment with Paystack
            verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
            response = requests.get(verify_url, headers=PAYSTACK_HEADERS)
            verify_data = response.json()

            if verify_data.get('status') and verify_data['data']['status'] == 'success':
                order_id = reference
                if order_id in users.get('pending_payments', {}):
                    payment = users['pending_payments'][order_id]
                    client_id = payment['user_id']
                    order_details = payment['order_details']
                    users['active_orders'][order_id] = order_details
                    del users['pending_payments'][order_id]
                    if str(client_id) in users['clients']:
                        users['clients'][str(client_id)]['step'] = 'completed'
                    await save_users()
                    logger.info(f"Payment verified for order {order_id}, user {client_id}")
                    return jsonify({"status": "success"}), 200
                else:
                    logger.error(f"Order {order_id} not found in pending payments")
                    return jsonify({"status": "error", "message": "Order not found"}), 404
            else:
                logger.error(f"Payment verification failed for reference {reference}: {verify_data}")
                return jsonify({"status": "error", "message": "Payment verification failed"}), 400

        except Exception as e:
            logger.error(f"Error in payment callback: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

    elif request.method == 'GET':
        # Handle user redirect after payment
        reference = request.args.get('reference')
        if not reference:
            logger.error("No reference provided in GET payment callback")
            return "Payment reference missing", 400

        # Redirect to success.html with the order_id (reference) as a query parameter
        redirect_url = f"/static/success.html?order_id={reference}"
        return Response(
            f'<html><head><meta http-equiv="refresh" content="0;url={redirect_url}" /></head><body>Redirecting...</body></html>',
            status=302,
            mimetype='text/html'
        )

# Status command
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /status command from user {user_id}")
    if user_id not in users['clients']:
        await update.message.reply_text("You haven't placed an order yet! Start with /client.")
        return
    client_data = users['clients'][user_id]
    if client_data['step'] == 'awaiting_payment':
        await update.message.reply_text(
            f"Your order (ID: {client_data['order_id']}) is awaiting payment.\n"
            f"Total: ₦{client_data['amount']}. Use /pay to complete payment."
        )
        return
    elif client_data['step'] == 'completed':
        for order_id, order in users.get('active_orders', {}).items():
            if order['client_id'] == user_id:
                follows_left = order.get('follows_left', 0)
                likes_left = order.get('likes_left', 0)
                comments_left = order.get('comments_left', 0)
                await update.message.reply_text(
                    f"Order (ID: {order_id}) progress:\n"
                    f"Follows left: {follows_left}\n"
                    f"Likes left: {likes_left}\n"
                    f"Comments left: {comments_left}"
                )
                return
        await update.message.reply_text("No active orders found. Start a new one with /client!")
        return
    else:
        await update.message.reply_text("No active orders. Start a new one with /client!")

# Tasks command
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /tasks command from user {user_id}")
    if not check_rate_limit(user_id, action='tasks'):
        logger.info(f"User {user_id} is rate-limited for /tasks")
        await update.message.reply_text("Please wait a moment before trying again!")
        return
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    user_data = users['engagers'][user_id]
    daily_tasks = user_data['daily_tasks']
    current_time = time.time()
    if current_time - daily_tasks['last_reset'] > 86400:  # 24 hours
        daily_tasks['count'] = 0
        daily_tasks['last_reset'] = current_time
        await save_users()
    if daily_tasks['count'] >= 10:
        await update.message.reply_text("You've reached your daily task limit (10). Try again tomorrow!")
        return
    if not users.get('active_orders'):
        keyboard = [[InlineKeyboardButton("Check Balance", callback_data='balance')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("No tasks available right now. Check back later!", reply_markup=reply_markup)
        return
    for order_id, order in users['active_orders'].items():
        handle = order.get('handle', 'unknown')
        platform = order.get('platform', 'unknown')
        follows_left = order.get('follows_left', 0)
        likes_left = order.get('likes_left', 0)
        comments_left = order.get('comments_left', 0)
        keyboard = []
        if follows_left > 0:
            keyboard.append([InlineKeyboardButton(f"Follow ({follows_left}) - ₦50", callback_data=f'task_f_{order_id}')])
        if likes_left > 0:
            keyboard.append([InlineKeyboardButton(f"Like ({likes_left}) - ₦30", callback_data=f'task_l_{order_id}')])
        if comments_left > 0:
            keyboard.append([InlineKeyboardButton(f"Comment ({comments_left}) - ₦50", callback_data=f'task_c_{order_id}')])
        keyboard.append([InlineKeyboardButton("Check Balance", callback_data='balance')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = f"Task: {handle} on {platform}\n"
        if order.get('profile_url'):
            message += f"Profile URL: {order['profile_url']}\n"
        if order.get('profile_image_id'):
            await update.message.reply_photo(
                photo=order['profile_image_id'],
                caption=message,
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(message, reply_markup=reply_markup)

# Cancel command
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /cancel command from user {user_id}")
    if user_id not in users['clients']:
        await update.message.reply_text("You don't have an active order to cancel!")
        return
    if users['clients'][user_id]['step'] == 'completed':
        await update.message.reply_text("Your order is already completed or being processed. Contact support to cancel.")
        return
    order_id = users['clients'][user_id].get('order_id')
    if order_id in users.get('pending_payments', {}):
        del users['pending_payments'][order_id]
    del users['clients'][user_id]
    await update.message.reply_text("Order cancelled. Start a new one with /client!")
    await save_users()
    logger.info(f"User {user_id} cancelled their order")

# Order command (for debugging or manual order placement)
async def order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /order command from user {user_id}")
    if user_id != str(ADMIN_USER_ID):
        await update.message.reply_text("Admin only!")
        return
    args = context.args
    if len(args) < 5:
        await update.message.reply_text(
            "Usage: /order <client_id> <platform> <handle> <follows> <likes> <comments>\n"
            "Example: /order 123456789 instagram myhandle 10 20 5"
        )
        return
    client_id, platform, handle, follows, likes, comments = args[0], args[1].lower(), args[2], int(args[3]), int(args[4]), int(args[5])
    if platform not in ['instagram', 'facebook', 'tiktok', 'twitter']:
        await update.message.reply_text("Platform must be one of: instagram, facebook, tiktok, twitter")
        return
    order_id = f"{client_id}_{int(time.time())}"
    users['active_orders'][order_id] = {
        'client_id': client_id,
        'platform': platform,
        'handle': handle,
        'follows_left': follows,
        'likes_left': likes,
        'comments_left': comments,
        'priority': False
    }
    users['clients'][client_id] = {'step': 'completed'}
    await update.message.reply_text(f"Order {order_id} created for {client_id} on {platform}.")
    await save_users()
    logger.info(f"Admin created order {order_id} for client {client_id}")

# Admin command
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    logger.info(f"Admin command used by user {user_id} in chat {chat_id}, expected ADMIN_GROUP_ID: {ADMIN_GROUP_ID}")
    if str(chat_id) != str(ADMIN_GROUP_ID):
        logger.info(f"Chat ID {chat_id} does not match ADMIN_GROUP_ID {ADMIN_GROUP_ID}")
        await update.message.reply_text("This command can only be used in the admin group.")
        return
    if user_id != str(ADMIN_USER_ID):
        logger.info(f"User {user_id} does not match ADMIN_USER_ID {ADMIN_USER_ID}")
        await update.message.reply_text("Admin only!")
        return
    logger.info(f"Admin panel accessed by user {user_id}")
    keyboard = [
        [InlineKeyboardButton("📊 View Stats", callback_data='admin_stats')],
        [InlineKeyboardButton("🔍 Audit Task", callback_data='admin_audit')],
        [InlineKeyboardButton("💸 View Withdrawals", callback_data='admin_view_withdrawals')],
        [InlineKeyboardButton("💳 View Pending Payments", callback_data='admin_view_payments')],
        [InlineKeyboardButton("📋 Pending Actions", callback_data='admin_pending')],
        [InlineKeyboardButton("🗑️ Clear Pending Tasks", callback_data='admin_clear_pending')],
        [InlineKeyboardButton("📋 View Active Tasks", callback_data='admin_view_tasks')],
        [InlineKeyboardButton("🚀 Set Task Priority", callback_data='admin_set_priority')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Admin Panel:\n"
        f"Withdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\n"
        f"Pick an action:",
        reply_markup=reply_markup
    )
    logger.info(f"Admin panel sent to user {user_id}")

# Admin view tasks
async def admin_view_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.callback_query.from_user.id)
    if user_id != str(ADMIN_USER_ID):
        await update.callback_query.message.reply_text("Admin only!")
        return
    await update.callback_query.answer()
    if not users['active_orders']:
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.reply_text("No active tasks!", reply_markup=reply_markup)
        return
    for order_id, order in users['active_orders'].items():
        handle = order.get('handle', 'unknown')
        platform = order.get('platform', 'unknown')
        follows_left = order.get('follows_left', 0)
        likes_left = order.get('likes_left', 0)
        comments_left = order.get('comments_left', 0)
        priority = "Priority" if order.get('priority', False) else "Normal"
        message = (
            f"Order {order_id}: {handle} on {platform}\n"
            f"Follows: {follows_left}, Likes: {likes_left}, Comments: {comments_left} ({priority})\n"
        )
        if order.get('profile_url'):
            message += f"Profile URL: {order['profile_url']}\n"
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if order.get('profile_image_id'):
            await update.callback_query.message.reply_photo(
                photo=order['profile_image_id'],
                caption=message,
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.message.reply_text(message, reply_markup=reply_markup)

# Admin clear pending tasks
async def admin_clear_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.callback_query.from_user.id)
    if user_id != str(ADMIN_USER_ID):
        await update.callback_query.message.reply_text("Admin only!")
        return
    await update.callback_query.answer()

    message = "Clear Pending Tasks:\n"
    message += "Reply with the format:\n"
    message += "- `payment <payment_id>` to clear a pending payment\n"
    message += "- `payout <payout_id>` to clear a pending payout\n"
    message += "- `order <order_id>` to clear an active order\n"
    message += "Example: `payment 1234567890_1699999999`\n\n"

    if users['pending_payments'] or users['pending_payouts'] or users['active_orders']:
        message += "Current Pending Tasks and Active Orders:\n"
        
        for payment_id, payment in users['pending_payments'].items():
            client_id = payment['client_id']
            order_id = payment['order_id']
            amount = users['clients'][str(client_id)]['amount']
            message += f"- Payment {payment_id}: Client {client_id}, Order {order_id}, Amount: ₦{amount}\n"
        
        for payout_id, payout in users['pending_payouts'].items():
            engager_id = payout['engager_id']
            amount = payout['amount']
            account = payout['account']
            message += f"- Payout {payout_id}: Engager {engager_id}, Amount: ₦{amount}, Account: {account}\n"
        
        for order_id, order in users['active_orders'].items():
            handle = order.get('handle', 'unknown')
            platform = order.get('platform', 'unknown')
            message += f"- Order {order_id}: {handle} on {platform}\n"
    else:
        message += "No pending tasks or active orders currently.\n"

    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(message, reply_markup=reply_markup)

    users['pending_admin_actions'][f"clear_task_{user_id}"] = {
        'user_id': int(user_id),
        'action': 'awaiting_clear_task_input',
        'expiration': time.time() + 300
    }
    await save_users()

# Admin set priority
async def admin_set_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.callback_query.from_user.id)
    if user_id != str(ADMIN_USER_ID):
        await update.callback_query.message.reply_text("Admin only!")
        return
    await update.callback_query.answer()
    
    logger.info(f"Active orders before displaying priority menu: {users['active_orders']}")
    
    if not users['active_orders']:
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.reply_text("No active tasks to prioritize!", reply_markup=reply_markup)
        return

    message = "Select a Task to Set as Priority (or reply with: <order_id> <true/false>):\n"
    keyboard = []
    for order_id, order in users['active_orders'].items():
        handle = order.get('handle', 'unknown')
        platform = order.get('platform', 'unknown')
        priority = "Priority" if order.get('priority', False) else "Normal"
        message += f"\n- Order {order_id}: {handle} on {platform} (Current: {priority})\n"
        keyboard.append([
            InlineKeyboardButton(f"Set Priority: {order_id}", callback_data=f'set_priority_{order_id}')
        ])
    
    keyboard.append([InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(message, reply_markup=reply_markup)

    users['pending_admin_actions'][f"set_priority_{user_id}"] = {
        'user_id': int(user_id),
        'action': 'awaiting_priority_input',
        'expiration': time.time() + 300
    }
    await save_users()

# Button handlers
async def handle_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

async def handle_client_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await client(update, context)

async def handle_engager_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await engager(update, context)

async def handle_help_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await help_command(update, context)

async def handle_join_button(query, user_id_str: str) -> None:
    users['engagers'][user_id_str] = {
        'joined': True, 'earnings': 0, 'signup_bonus': 500, 'task_timers': {}, 'awaiting_payout': False,
        'daily_tasks': {'count': 0, 'last_reset': time.time()}, 'tasks_per_order': {}, 'claims': []
    }
    keyboard = [
        [InlineKeyboardButton("See Tasks", callback_data='tasks'), InlineKeyboardButton("Check Balance", callback_data='balance')],
        [InlineKeyboardButton("Back to Start", callback_data='start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("You’re in! Enjoy your ₦500 signup bonus. Start earning—withdraw at ₦1,000 earned!", reply_markup=reply_markup)
    await save_users()

async def handle_platform_button(query, user_id_str: str, data: str) -> None:
    platform = data.split('_')[1]
    users['clients'][user_id_str]['platform'] = platform
    users['clients'][user_id_str]['step'] = 'select_package'
    await save_users()
    keyboard = [
        [InlineKeyboardButton("Followers", callback_data='select_followers')],
        [InlineKeyboardButton("Likes", callback_data='select_likes')],
        [InlineKeyboardButton("Comments", callback_data='select_comments')],
        [InlineKeyboardButton("Bundle (All)", callback_data='select_bundle')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        f"Selected {platform.capitalize()}. Now pick a package:\n"
        f"Followers:\n- 10: ₦800-1,800\n- 50: ₦4,000-9,000\n- 100: ₦8,000-18,000\n"
        f"Likes:\n- 20: ₦600-1,800\n- 100: ₦3,000-9,000\n- 200: ₦6,000-18,000\n"
        f"Comments:\n- 5: ₦300-600\n- 10: ₦600-1,200\n- 50: ₦3,000-6,000\n"
        f"Bundles ({platform.capitalize()}):\n- Starter: 10F/20L/5C\n- Pro: 50F/100L/10C\n- Elite: 100F/200L/50C\n"
        f"Prices vary by platform. Reply with: handle package (e.g., @NaijaFashion 50).",
        reply_markup=reply_markup
    )

async def handle_select_button(query, user_id_str: str, data: str) -> None:
    package_type = data.split('_')[1]
    users['clients'][user_id_str]['order_type'] = package_type
    users['clients'][user_id_str]['step'] = 'awaiting_order'
    platform = users['clients'][user_id_str]['platform']
    example_url = {
        'instagram': 'https://instagram.com/yourusername',
        'facebook': 'https://facebook.com/yourusername',
        'tiktok': 'https://tiktok.com/@yourusername',
        'twitter': 'https://twitter.com/yourusername'
    }.get(platform, 'https://platform.com/yourusername')
    package_example = '10' if package_type == 'followers' else '20' if package_type == 'likes' else '5' if package_type == 'comments' else 'starter'
    await query.message.edit_text(
        f"Please provide the URL of your {platform.capitalize()} profile or a screenshot of your account.\n"
        f"Also include the package you want.\n"
        f"Example: `{example_url} {package_example}`\n"
        f"Or send a screenshot of your profile with the message: `package {package_example}`\n"
        f"Check /client for package options."
    )
    await save_users()

async def handle_task_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    task_parts = data.split('_', 2)
    task_type = task_parts[1]
    order_id = task_parts[2]
    logger.info(f"Task button clicked: user={user_id}, order_id={order_id}, active_orders={users['active_orders']}")
    if order_id not in users['active_orders']:
        await query.message.edit_text("Task no longer available!")
        return
    order = users['active_orders'][order_id]
    if task_type == 'f' and order.get('follows_left', 0) <= 0:
        await query.message.edit_text("Task no longer available!")
        return
    elif task_type == 'l' and order.get('likes_left', 0) <= 0:
        await query.message.edit_text("Task no longer available!")
        return
    elif task_type == 'c' and order.get('comments_left', 0) <= 0:
        await query.message.edit_text("Task no longer available!")
        return
    user_data = users['engagers'].get(user_id_str, {})
    claims = user_data.get('claims', [])
    for claim in claims:
        if claim['order_id'] == order_id and claim['task_type'] == task_type and claim['status'] == 'approved':
            task_name = {'f': 'follow', 'l': 'like', 'c': 'comment'}.get(task_type, 'task')
            await query.message.edit_text(f"You've already done the {task_name} task for this order!")
            return
    timer_key = f"{order_id}_{task_type}"
    if 'tasks_per_order' not in user_data:
        user_data['tasks_per_order'] = {}
    users['engagers'][user_id_str]['task_timers'][timer_key] = time.time()
    if task_type == 'f':
        await query.message.edit_text(f"Follow {order['handle']} on {order['platform']}. Send a screenshot here to earn! (Screenshot required)")
    elif task_type == 'l':
        if order.get('use_recent_posts'):
            await query.message.edit_text(f"Like the 3 latest posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each, then send a screenshot here! (Screenshot required)")
        else:
            await query.message.edit_text(f"Like this post: {order['like_url']}. Spend 60 seconds, then send a screenshot here! (Screenshot required)")
    elif task_type == 'c':
        if order.get('use_recent_posts'):
            await query.message.edit_text(f"Comment on the 3 latest posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each, then send a screenshot here! (Screenshot required)")
        else:
            await query.message.edit_text(f"Comment on the post: {order['comment_url']}. Spend 60 seconds, then send a screenshot here! (Screenshot required)")
    await save_users()

async def handle_tasks_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await tasks(update, context)

async def handle_balance_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await balance(update, context)

async def handle_withdraw_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await withdraw(update, context)

async def handle_admin_stats_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    num_clients = len(users['clients'])
    num_engagers = len(users['engagers'])
    pending_tasks = sum(order.get('follows_left', 0) + order.get('likes_left', 0) + order.get('comments_left', 0) for order in users['active_orders'].values())
    completed_tasks = sum(sum(claim.get('amount', 0) for claim in users['engagers'][engager].get('claims', []) if claim['status'] == 'approved') for engager in users['engagers']) // 20
    stats_text = (
        f"Admin Stats:\n"
        f"- Total Clients: {num_clients}\n"
        f"- Total Engagers: {num_engagers}\n"
        f"- Pending Tasks: {pending_tasks}\n"
        f"- Completed Tasks (approx.): {completed_tasks}"
    )
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(stats_text, reply_markup=reply_markup)

async def handle_admin_audit_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    users['pending_admin_actions'][f"audit_{user_id_str}"] = {'user_id': int(user_id_str), 'action': 'awaiting_audit_input', 'expiration': time.time() + 300}
    await save_users()
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("Reply with: <engager_id> <order_id> [reason]\nExample: 1518439839 1518439839_1742633918 Invalid proof", reply_markup=reply_markup)

async def handle_admin_view_withdrawals_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    if not users['pending_payouts']:
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("No pending withdrawals.", reply_markup=reply_markup)
        return
    message = "Pending Withdrawals:\n"
    keyboard = []
    for payout_id, payout in users['pending_payouts'].items():
        engager_id = payout['engager_id']
        amount = payout['amount']
        account = payout['account']
        timestamp = payout.get('timestamp', time.time())
        formatted_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
        message += f"\n- Engager: {engager_id}, Amount: ₦{amount}, Account: {account}, Requested: {formatted_time}\n"
        keyboard.append([
            InlineKeyboardButton(f"Approve (₦{amount})", callback_data=f'approve_payout_{payout_id}'),
            InlineKeyboardButton("Reject", callback_data=f'reject_payout_{payout_id}')
        ])
    keyboard.append([InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(message, reply_markup=reply_markup)

async def handle_admin_view_payments_button(query: CallbackQuery, user_id: int, user_id_str: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    if not users['pending_payments']:
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("No pending payments.", reply_markup=reply_markup)
        return
    for payment_id, payment in users['pending_payments'].items():
        client_id = payment['client_id']
        order_id = payment['order_id']
        amount = users['clients'][str(client_id)]['amount']
        keyboard = [
            [
                InlineKeyboardButton("Approve", callback_data=f'approve_payment_{payment_id}'),
                InlineKeyboardButton("Reject", callback_data=f'reject_payment_{payment_id}')
            ],
            [InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await application.bot.send_photo(
            chat_id=user_id,
            photo=payment['photo_id'],
            caption=f"Payment proof from {client_id} for order: {order_id}. Amount: ₦{amount}",
            reply_markup=reply_markup
        )

async def handle_admin_pending_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    message = "Pending Actions:\n"
    if users['pending_payments']:
        message += f"- Payments: {len(users['pending_payments'])}\n"
    if users['pending_payouts']:
        message += f"- Withdrawals: {len(users['pending_payouts'])}\n"
    if not (users['pending_payments'] or users['pending_payouts']):
        message += "None!"
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(message, reply_markup=reply_markup)

async def handle_admin_clear_pending_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_clear_pending(update, context)

async def handle_admin_view_tasks_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_view_tasks(update, context)

async def handle_admin_set_priority_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_set_priority(update, context)

async def handle_back_to_admin_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    keyboard = [
        [InlineKeyboardButton("📊 View Stats", callback_data='admin_stats')],
        [InlineKeyboardButton("🔍 Audit Task", callback_data='admin_audit')],
        [InlineKeyboardButton("💸 View Withdrawals", callback_data='admin_view_withdrawals')],
        [InlineKeyboardButton("💳 View Pending Payments", callback_data='admin_view_payments')],
        [InlineKeyboardButton("📋 Pending Actions", callback_data='admin_pending')],
        [InlineKeyboardButton("🗑️ Clear Pending Tasks", callback_data='admin_clear_pending')],
        [InlineKeyboardButton("📋 View Active Tasks", callback_data='admin_view_tasks')],
        [InlineKeyboardButton("🚀 Set Task Priority", callback_data='admin_set_priority')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        f"Admin Panel:\n"
        f"Withdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\n"
        f"Pick an action:",
        reply_markup=reply_markup
    )

async def handle_approve_payout_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Only admin can do this!")
        return
    payout_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'approve_payout', {'payout_id': payout_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to approve payout {payout_id}.", reply_markup=reply_markup)

async def handle_reject_payout_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Only admin can do this!")
        return
    payout_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'reject_payout', {'payout_id': payout_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to reject payout {payout_id}.", reply_markup=reply_markup)

async def handle_approve_payment_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Only admin can do this!")
        return
    payment_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'approve_payment', {'payment_id': payment_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to approve payment {payment_id}.", reply_markup=reply_markup)

async def handle_reject_payment_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Only admin can do this!")
        return
    payment_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'reject_payment', {'payment_id': payment_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to reject payment {payment_id}.", reply_markup=reply_markup)

async def handle_set_priority_button(query: CallbackQuery, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Only admin can do this!")
        return
    order_id = data.split('_')[2]
    logger.info(f"Attempting to set priority for order {order_id}, current active_orders: {users['active_orders']}")
    if order_id in users['active_orders']:
        current_priority = users['active_orders'][order_id].get('priority', False)
        users['active_orders'][order_id]['priority'] = not current_priority
        status = "Priority" if not current_priority else "Normal"
        await query.message.edit_text(f"Order {order_id} set to {status}!")
        await save_users()
    else:
        await query.message.edit_text(f"Order {order_id} no longer exists! It may have been completed or cleared.")

async def handle_cancel_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str in users['clients']:
        del users['clients'][user_id_str]
        await query.message.edit_text("Order canceled. Start over with /client!")
        await save_users()

# Main button handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    user_id_str = user_id
    data = query.data
    logger.info(f"Button clicked by user {user_id}: {data}")
    await query.answer()

    # Rate limit check for button actions
    if not check_rate_limit(user_id, action=data, is_signup_action=data in ['client', 'engager', 'start']):
        await query.message.edit_text("Please wait a moment before trying again!")
        return

    try:
        if data == "client":
            if user_id in users['clients']:
                client_data = users['clients'][user_id]
                if client_data['step'] == 'awaiting_order':
                    platform = client_data['platform']
                    bundles = "\n".join(
                        f"- {k.capitalize()}: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
                        for k, v in package_limits['bundle'][platform].items()
                    )
                    await query.edit_message_text(
                        f"You're ready to submit an order for {platform.capitalize()}!\n"
                        f"Available bundles:\n{bundles}\n"
                        "Options:\n"
                        "1. Handle + Bundle: '@myhandle starter'\n"
                        "2. URL + Bundle: 'https://instagram.com/username starter'\n"
                        "3. Package + Screenshot: 'package pro' with photo\n"
                        "4. Custom + Screenshot: 'username, 20 follows, 30 likes, 20 comments' with photo\n"
                        "Custom limits: 10-500 per metric. Screenshot optional for options 1 and 2."
                    )
                    logger.info(f"Prompted user {user_id} for order details on {platform}")
                    return
                elif client_data['step'] == 'awaiting_payment':
                    await query.edit_message_text(
                        f"You have an order pending payment!\n"
                        f"Use /pay to proceed or /cancel to start over."
                    )
                    return
            if not check_rate_limit(user_id, action='client'):
                await query.edit_message_text("Please wait a moment before trying again!")
                return
            users['clients'][user_id] = {'step': 'select_platform'}
            await save_users()
            keyboard = [
                [InlineKeyboardButton("Instagram", callback_data="platform_instagram")],
                [InlineKeyboardButton("Facebook", callback_data="platform_facebook")],
                [InlineKeyboardButton("TikTok", callback_data="platform_tiktok")],
                [InlineKeyboardButton("Twitter", callback_data="platform_twitter")]
            ]
            await query.edit_message_text(
                "Select a platform to boost:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            logger.info(f"Sent platform selection to user {user_id}")

        elif data == "engager":
            if not check_rate_limit(user_id, action='engager'):
                await query.edit_message_text("Please wait a moment before trying again!")
                return
            if user_id not in users['engagers']:
                users['engagers'][user_id] = {
                    'earnings': 0,
                    'daily_tasks': {'count': 0, 'last_reset': time.time()},
                    'task_timers': {},
                    'claims': [],
                    'awaiting_payout': False
                }
                await save_users()
                await query.edit_message_text(
                    "Welcome, Engager!\n"
                    "Earn cash by completing tasks.\n"
                    "Use /tasks to see available tasks or /balance to check earnings."
                )
                logger.info(f"User {user_id} joined as engager")
            else:
                await query.edit_message_text(
                    "You're already an engager!\n"
                    "Use /tasks to see available tasks or /balance to check earnings."
                )

        elif data == "help":
            if not check_rate_limit(user_id, action='help'):
                await query.edit_message_text("Please wait a moment before trying again!")
                return
            await query.edit_message_text(
                "Need help?\n"
                "- Clients: Boost your social media with /client.\n"
                "- Engagers: Earn cash with /tasks.\n"
                "- Contact support: [Your Support Link]"
            )
            logger.info(f"Help sent to user {user_id}")

        elif data.startswith("platform_"):
            platform = data.split("_")[1]
            if user_id not in users['clients']:
                users['clients'][user_id] = {}
            users['clients'][user_id]['platform'] = platform
            users['clients'][user_id]['step'] = 'awaiting_order'
            users['clients'][user_id]['order_type'] = 'bundle'  # Default, overridden if custom
            await save_users()
            example_url = {
                'instagram': 'https://instagram.com/myhandle',
                'facebook': 'https://facebook.com/myhandle',
                'tiktok': 'https://tiktok.com/@myhandle',
                'twitter': 'https://twitter.com/myhandle'
            }.get(platform, 'https://platform.com/myhandle')
            bundles = "\n".join(
                f"- {k.capitalize()}: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
                for k, v in package_limits['bundle'][platform].items()
            )
            await query.message.edit_text(
                f"Selected {platform.capitalize()}!\n"
                f"Available bundles:\n{bundles}\n"
                "Submit your order:\n"
                "1. Handle + Bundle: '@myhandle starter'\n"
                "2. URL + Bundle: '{example_url} starter'\n"
                "3. Package + Screenshot: 'package pro' with photo\n"
                "4. Custom + Screenshot: 'username, 20 follows, 30 likes, 20 comments' with photo\n"
                "Custom limits: 10-500 per metric. Screenshot optional for options 1 and 2."
            )
            logger.info(f"User {user_id} selected platform {platform}, prompted for order")

        elif data == 'tasks':
            await tasks(update, context)
        elif data == 'balance':
            await balance(update, context)
        elif data == 'withdraw':
            await withdraw(update, context)
        elif data == 'cancel':
            if user_id_str in users['clients']:
                del users['clients'][user_id_str]
                await query.message.edit_text("Order cancelled. Start a new one with /client!")
                await save_users()
        elif data == 'start':
            keyboard = [
                [InlineKeyboardButton("Join as Client", callback_data='client')],
                [InlineKeyboardButton("Join as Engager", callback_data='engager')],
                [InlineKeyboardButton("Help", callback_data='help')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                "Welcome to VibeLiftBot! 🚀\nBoost your social media or earn cash by engaging.\nPick your role:",
                reply_markup=reply_markup
            )
        elif data.startswith('task_'):
            await handle_task_button(query, int(user_id), user_id_str, data)
        elif data == 'admin_stats':
            await handle_admin_stats_button(query, user_id_str)
        elif data == 'admin_audit':
            await handle_admin_audit_button(query, user_id_str)
        elif data == 'admin_view_withdrawals':
            await handle_admin_view_withdrawals_button(query, user_id_str)
        elif data == 'admin_view_payments':
            await handle_admin_view_payments_button(query, int(user_id), user_id_str)
        elif data == 'admin_pending':
            await handle_admin_pending_button(query, user_id_str)
        elif data == 'admin_clear_pending':
            await handle_admin_clear_pending_button(update, context)
        elif data == 'admin_view_tasks':
            await handle_admin_view_tasks_button(update, context)
        elif data == 'admin_set_priority':
            await handle_admin_set_priority_button(update, context)
        elif data == 'back_to_admin':
            await handle_back_to_admin_button(query, user_id_str)
        elif data.startswith('approve_payout_'):
            await handle_approve_payout_button(query, int(user_id), user_id_str, data)
        elif data.startswith('reject_payout_'):
            await handle_reject_payout_button(query, int(user_id), user_id_str, data)
        elif data.startswith('approve_payment_'):
            await handle_approve_payment_button(query, int(user_id), user_id_str, data)
        elif data.startswith('reject_payment_'):
            await handle_reject_payment_button(query, int(user_id), user_id_str, data)
        elif data.startswith('set_priority_'):
            await handle_set_priority_button(query, user_id_str, data)
    except Exception as e:
        logger.error(f"Error in button handler for user {user_id}: {str(e)}")
        await query.message.edit_text("An error occurred. Please try again or contact support.")
# Balance command
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not check_rate_limit(user_id, action='balance'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    user_data = users['engagers'][user_id]
    earnings = user_data['earnings']
    signup_bonus = user_data['signup_bonus']
    total_balance = earnings + signup_bonus
    withdrawable = earnings if earnings >= WITHDRAWAL_LIMIT else 0
    message = (
        f"Your Balance:\n"
        f"Earnings: ₦{earnings}\n"
        f"Signup Bonus: ₦{signup_bonus}\n"
        f"Total: ₦{total_balance}\n"
        f"Withdrawable (excl. bonus): ₦{withdrawable}\n"
        f"Withdraw at ₦{WITHDRAWAL_LIMIT} earned (excl. bonus)."
    )
    keyboard = [[InlineKeyboardButton("Withdraw", callback_data='withdraw')]] if withdrawable >= WITHDRAWAL_LIMIT else []
    keyboard.append([InlineKeyboardButton("See Tasks", callback_data='tasks')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)

# Withdraw command
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not check_rate_limit(user_id, action='withdraw'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    user_data = users['engagers'][user_id]
    earnings = user_data['earnings']
    if earnings < WITHDRAWAL_LIMIT:
        await update.message.reply_text(f"You need at least ₦{WITHDRAWAL_LIMIT} earned (excl. bonus) to withdraw!")
        return
    if user_data.get('awaiting_payout', False):
        await update.message.reply_text("You already have a pending withdrawal. Wait for admin approval!")
        return
    user_data['awaiting_payout'] = True
    await update.message.reply_text("Reply with your 10-digit OPay account number to withdraw.")
    await save_users()

# Message handler (Completed with flexible ordering)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = (update.message.caption or update.message.text or "").lower().strip()  # Handle caption or text
    logger.info(f"Received message from user {user_id}: '{update.message.text or update.message.caption}' (normalized: '{text}')")
    
    # General message rate limit (5s default)
    if not check_rate_limit(user_id, action='message', cooldown=5, is_signup_action=False):
        logger.info(f"User {user_id} is rate-limited")
        await update.message.reply_text("Hang on a sec and try again!")
        return

    # Handle pending admin actions (e.g., audit, clear tasks, set priority, verification codes)
    pending_action = None
    action_id_to_remove = None
    for action_id, action_data in list(users.get('pending_admin_actions', {}).items()):
        if action_data['user_id'] == int(user_id) and time.time() < action_data['expiration']:
            pending_action = action_data
            action_id_to_remove = action_id
            break
        elif time.time() >= action_data['expiration']:
            del users['pending_admin_actions'][action_id]
            await save_users()

    # Admin group message handling
    if str(update.message.chat_id) == ADMIN_GROUP_ID and user_id == str(ADMIN_USER_ID):
        if pending_action and action_id_to_remove.startswith('audit_'):
            logger.info(f"Processing audit action for user {user_id}")
            if pending_action['action'] == 'awaiting_audit_input':
                parts = text.split(maxsplit=2)
                if len(parts) < 2:
                    await update.message.reply_text("Please provide: <engager_id> <order_id> [reason]\nExample: 1518439839 1518439839_1742633918 Invalid proof")
                    return
                engager_id, order_id = parts[0], parts[1]
                reason = parts[2] if len(parts) > 2 else "No reason provided"
                if engager_id not in users['engagers'] or 'claims' not in users['engagers'][engager_id]:
                    await update.message.reply_text("No claims found for this user.")
                    del users['pending_admin_actions'][action_id_to_remove]
                    await save_users()
                    return
                for claim in users['engagers'][engager_id]['claims']:
                    if claim['order_id'] == order_id and claim['status'] == 'approved':
                        claim['status'] = 'rejected'
                        claim['rejection_reason'] = reason
                        users['engagers'][engager_id]['earnings'] -= claim['amount']
                        await application.bot.send_message(
                            chat_id=engager_id,
                            text=f"Your task {order_id} was rejected after audit. Reason: {reason}. ₦{claim['amount']} removed."
                        )
                        await update.message.reply_text