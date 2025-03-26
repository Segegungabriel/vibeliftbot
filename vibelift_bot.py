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

from flask import Flask, request, jsonify, Response  # Added Response for payment callback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery  # Added CallbackQuery
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from asgiref.wsgi import WsgiToAsgi  # Updated import from wsgi_to_asgi
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

# Start command
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
    logger.info(f"Received /admin command from user {user_id}")
    if user_id != str(ADMIN_USER_ID):
        await update.message.reply_text("Admin only!")
        return
    keyboard = [
        [InlineKeyboardButton("Set Priority", callback_data='admin_set_priority')],
        [InlineKeyboardButton("Approve Payment", callback_data='admin_approve_payment')],
        [InlineKeyboardButton("Reject Payment", callback_data='admin_reject_payment')],
        [InlineKeyboardButton("Approve Payout", callback_data='admin_approve_payout')],
        [InlineKeyboardButton("Reject Payout", callback_data='admin_reject_payout')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Admin actions:", reply_markup=reply_markup)

# Balance command
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /balance command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    user_data = users['engagers'][user_id]
    earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
    keyboard = [[InlineKeyboardButton("Withdraw", callback_data='withdraw')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Your balance: ₦{earnings}\n"
        f"Minimum withdrawal: ₦{WITHDRAWAL_LIMIT}",
        reply_markup=reply_markup
    )

# Withdraw command
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /withdraw command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    user_data = users['engagers'][user_id]
    if user_data.get('awaiting_payout', False):
        await update.message.reply_text("You already have a pending withdrawal. Please wait for admin approval.")
        return
    earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
    if earnings < WITHDRAWAL_LIMIT:
        await update.message.reply_text(
            f"Your balance (₦{earnings}) is below the minimum withdrawal limit (₦{WITHDRAWAL_LIMIT}). Keep earning!"
        )
        return
    await update.message.reply_text(
        "Please provide your bank details in this format:\n"
        "Account Number, Bank Name, Account Name\n"
        "Example: 1234567890, GTBank, John Doe"
    )
    user_data['awaiting_payout'] = True
    await save_users()

# Button handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_id_str = str(user_id)
    data = query.data
    logger.info(f"Button clicked by user {user_id}: {data}")

    if data == 'client':
        await client(update, context)
    elif data == 'engager':
        await engager(update, context)
    elif data == 'help':
        await help_command(update, context)
    elif data == 'tasks':
        await tasks(update, context)
    elif data == 'balance':
        await balance(update, context)
    elif data == 'withdraw':
        await withdraw(update, context)
    elif data.startswith('platform_'):
        platform = data.split('_')[1]
        users['clients'][user_id_str] = {
            'step': 'awaiting_order',
            'platform': platform
        }
        await save_users()
        bundles = "\n".join(
            f"- {k.capitalize()}: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
            for k, v in package_limits['bundle'][platform].items()
        )
        await query.message.edit_text(
            f"Selected {platform.capitalize()}!\n"
            f"Available bundles:\n{bundles}\n"
            "Options:\n"
            "1. Handle + Bundle: '@myhandle starter'\n"
            "2. URL + Bundle: 'https://instagram.com/username starter'\n"
            "3. Package + Screenshot: 'package pro' with photo\n"
            "4. Custom + Screenshot: 'username, 20 follows, 30 likes, 20 comments' with photo\n"
            "Custom limits: 10-500 per metric. Screenshot optional for options 1 and 2."
        )
    elif data.startswith('task_'):
        await handle_task_button(query, user_id, user_id_str, data)
    elif data.startswith('admin_'):
        await handle_admin_button(query, user_id, user_id_str, data)

# Handle task button
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

# Handle admin button
async def handle_admin_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    action = data.split('_', 2)[-1]
    if action == 'set_priority':
        if not users.get('active_orders'):
            await query.message.edit_text("No active orders to prioritize!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'priority_{order_id}')]
            for order_id in users['active_orders'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select an order to prioritize:", reply_markup=reply_markup)
    elif action == 'approve_payment':
        if not users.get('pending_payments'):
            await query.message.edit_text("No pending payments to approve!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'approve_payment_{order_id}')]
            for order_id in users['pending_payments'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a payment to approve:", reply_markup=reply_markup)
    elif action == 'reject_payment':
        if not users.get('pending_payments'):
            await query.message.edit_text("No pending payments to reject!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'reject_payment_{order_id}')]
            for order_id in users['pending_payments'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a payment to reject:", reply_markup=reply_markup)
    elif action == 'approve_payout':
        pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
        if not pending_payouts:
            await query.message.edit_text("No pending payouts to approve!")
            return
        keyboard = [
            [InlineKeyboardButton(f"User {user_id}", callback_data=f'approve_payout_{user_id}')]
            for user_id in pending_payouts.keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a payout to approve:", reply_markup=reply_markup)
    elif action == 'reject_payout':
        pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
        if not pending_payouts:
            await query.message.edit_text("No pending payouts to reject!")
            return
        keyboard = [
            [InlineKeyboardButton(f"User {user_id}", callback_data=f'reject_payout_{user_id}')]
            for user_id in pending_payouts.keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a payout to reject:", reply_markup=reply_markup)
    elif data.startswith('priority_'):
        order_id = data.split('_', 1)[1]
        if order_id in users['active_orders']:
            users['active_orders'][order_id]['priority'] = True
            await save_users()
            await query.message.edit_text(f"Order {order_id} prioritized!")
        else:
            await query.message.edit_text("Order not found!")
    elif data.startswith('approve_payment_'):
        order_id = data.split('_', 2)[2]
        if order_id in users['pending_payments']:
            payment = users['pending_payments'][order_id]
            client_id = payment['user_id']
            order_details = payment['order_details']
            users['active_orders'][order_id] = order_details
            del users['pending_payments'][order_id]
            if str(client_id) in users['clients']:
                users['clients'][str(client_id)]['step'] = 'completed'
            await save_users()
            await query.message.edit_text(f"Payment for order {order_id} approved!")
            await application.bot.send_message(
                chat_id=client_id,
                text=f"Your payment for order {order_id} has been approved! Check progress with /status."
            )
        else:
            await query.message.edit_text("Payment not found!")
    elif data.startswith('reject_payment_'):
        order_id = data.split('_', 2)[2]
        if order_id in users['pending_payments']:
            payment = users['pending_payments'][order_id]
            client_id = payment['user_id']
            del users['pending_payments'][order_id]
            if str(client_id) in users['clients']:
                del users['clients'][str(client_id)]
            await save_users()
            await query.message.edit_text(f"Payment for order {order_id} rejected!")
            await application.bot.send_message(
                chat_id=client_id,
                text=f"Your payment for order {order_id} was rejected. Please contact support or start a new order with /client."
            )
        else:
            await query.message.edit_text("Payment not found!")
    elif data.startswith('approve_payout_'):
        target_user_id = data.split('_', 2)[2]
        if target_user_id in users['engagers']:
            user_data = users['engagers'][target_user_id]
            if not user_data.get('awaiting_payout'):
                await query.message.edit_text("No pending payout for this user!")
                return
            user_data['earnings'] = 0
            user_data['signup_bonus'] = 0
            user_data['awaiting_payout'] = False
            await save_users()
            await query.message.edit_text(f"Payout for user {target_user_id} approved!")
            await application.bot.send_message(
                chat_id=target_user_id,
                text="Your payout has been approved and processed! Check your bank account."
            )
        else:
            await query.message.edit_text("User not found!")
    elif data.startswith('reject_payout_'):
        target_user_id = data.split('_', 2)[2]
        if target_user_id in users['engagers']:
            user_data = users['engagers'][target_user_id]
            if not user_data.get('awaiting_payout'):
                await query.message.edit_text("No pending payout for this user!")
                return
            user_data['awaiting_payout'] = False
            await save_users()
            await query.message.edit_text(f"Payout for user {target_user_id} rejected!")
            await application.bot.send_message(
                chat_id=target_user_id,
                text="Your payout request was rejected. Please contact support for more details."
            )
        else:
            await query.message.edit_text("User not found!")

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received message from user {user_id}")
    if not check_rate_limit(user_id, action='message'):
        logger.info(f"User {user_id} is rate-limited for messages")
        await update.message.reply_text("Please wait a moment before sending another message!")
        return

    # Handle client order submission
    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        if client_data['step'] == 'awaiting_order':
            platform = client_data['platform']
            text = update.message.text.lower() if update.message.text else ""
            photo_id = update.message.photo[-1].file_id if update.message.photo else None
            profile_url = None
            handle = None
            order_type = None
            follows, likes, comments, price = 0, 0, 0, 0

            # Parse the message
            if text.startswith('http'):
                parts = text.split()
                if len(parts) != 2:
                    await update.message.reply_text("Please provide a URL and bundle, e.g., 'https://instagram.com/username starter'")
                    return
                profile_url = parts[0]
                bundle_type = parts[1]
                if bundle_type not in package_limits['bundle'][platform]:
                    await update.message.reply_text(f"Invalid bundle! Available: {', '.join(package_limits['bundle'][platform].keys())}")
                    return
                order_type = 'bundle'
            elif text.startswith('@'):
                parts = text.split()
                if len(parts) != 2:
                    await update.message.reply_text("Please provide a handle and bundle, e.g., '@myhandle starter'")
                    return
                handle = parts[0][1:]  # Remove the @
                bundle_type = parts[1]
                if bundle_type not in package_limits['bundle'][platform]:
                    await update.message.reply_text(f"Invalid bundle! Available: {', '.join(package_limits['bundle'][platform].keys())}")
                    return
                order_type = 'bundle'
            elif text.startswith('package'):
                if not photo_id:
                    await update.message.reply_text("Please attach a screenshot of your profile with the package command!")
                    return
                parts = text.split()
                if len(parts) != 2:
                    await update.message.reply_text("Please provide a package type, e.g., 'package pro'")
                    return
                bundle_type = parts[1]
                if bundle_type not in package_limits['bundle'][platform]:
                    await update.message.reply_text(f"Invalid bundle! Available: {', '.join(package_limits['bundle'][platform].keys())}")
                    return
                order_type = 'bundle'
            else:
                # Custom order
                if not photo_id:
                    await update.message.reply_text("Custom orders require a screenshot! Format: 'username, 20 follows, 30 likes, 20 comments'")
                    return
                parts = text.split(',')
                if len(parts) != 4:
                    await update.message.reply_text("Custom order format: 'username, 20 follows, 30 likes, 20 comments' with a screenshot")
                    return
                handle = parts[0].strip()
                try:
                    follows = int(parts[1].split()[0])
                    likes = int(parts[2].split()[0])
                    comments = int(parts[3].split()[0])
                except (ValueError, IndexError):
                    await update.message.reply_text("Invalid numbers in custom order! Format: 'username, 20 follows, 30 likes, 20 comments'")
                    return
                order_type = 'custom'

            # Validate custom order limits
            if order_type == 'custom':
                if not (package_limits['followers'][platform]['min'] <= follows <= package_limits['followers'][platform]['max']):
                    await update.message.reply_text(f"Follows must be between {package_limits['followers'][platform]['min']} and {package_limits['followers'][platform]['max']}!")
                    return
                if not (package_limits['likes'][platform]['min'] <= likes <= package_limits['likes'][platform]['max']):
                    await update.message.reply_text(f"Likes must be between {package_limits['likes'][platform]['min']} and {package_limits['likes'][platform]['max']}!")
                    return
                if not (package_limits['comments'][platform]['min'] <= comments <= package_limits['comments'][platform]['max']):
                    await update.message.reply_text(f"Comments must be between {package_limits['comments'][platform]['min']} and {package_limits['comments'][platform]['max']}!")
                    return
                price = (follows * 50) + (likes * 30) + (comments * 50)

            # Set bundle values
            if order_type == 'bundle':
                bundle = package_limits['bundle'][platform][bundle_type]
                follows, likes, comments, price = bundle['follows'], bundle['likes'], bundle['comments'], bundle['price']

            # Create order
            order_details = {
                'client_id': user_id,
                'platform': platform,
                'handle': handle,
                'follows_left': follows,
                'likes_left': likes,
                'comments_left': comments,
                'priority': False
            }
            if photo_id:
                order_details['profile_image_id'] = photo_id
            if profile_url:
                order_details['profile_url'] = profile_url

            order_id = f"{user_id}_{int(time.time())}"
            users['pending_payments'][order_id] = {
                'user_id': user_id,
                'order_id': order_id,
                'order_details': order_details,
                'photo_id': photo_id
            }
            users['clients'][user_id] = {
                'step': 'awaiting_payment',
                'platform': platform,
                'order_id': order_id,
                'amount': price
            }
            await save_users()
            await update.message.reply_text(
                f"Order created! Total: ₦{price}. Use /pay to proceed or /cancel to start over."
            )
            return

    # Handle engager task submission (screenshot)
    if user_id in users['engagers']:
        user_data = users['engagers'][user_id]
        if not update.message.photo:
            # Handle withdrawal bank details
            if user_data.get('awaiting_payout'):
                bank_details = update.message.text
                if len(bank_details.split(',')) != 3:
                    await update.message.reply_text("Please provide bank details in this format: Account Number, Bank Name, Account Name")
                    return
                earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
                await application.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"Payout request from user {user_id}:\n"
                         f"Amount: ₦{earnings}\n"
                         f"Bank Details: {bank_details}"
                )
                await update.message.reply_text(
                    "Withdrawal request submitted! Awaiting admin approval."
                )
                return
            await update.message.reply_text("Please send a screenshot to submit a task!")
            return

        photo_id = update.message.photo[-1].file_id
        task_timers = user_data.get('task_timers', {})
        current_time = time.time()
        pending_task = None
        for timer_key, start_time in task_timers.items():
            if current_time - start_time < 60:  # 60 seconds minimum
                await update.message.reply_text("Please spend at least 60 seconds on the task before submitting!")
                return
            order_id, task_type = timer_key.split('_')
            if order_id in users['active_orders']:
                pending_task = {'order_id': order_id, 'task_type': task_type}
                break

        if not pending_task:
            await update.message.reply_text("No pending task found to submit this screenshot for!")
            return

        order_id = pending_task['order_id']
        task_type = pending_task['task_type']
        order = users['active_orders'][order_id]
        client_id = order['client_id']

        # Update order metrics
        if task_type == 'f':
            order['follows_left'] -= 1
            earnings = 50
        elif task_type == 'l':
            order['likes_left'] -= 1
            earnings = 30
        elif task_type == 'c':
            order['comments_left'] -= 1
            earnings = 50

        # Record the claim
        if 'claims' not in user_data:
            user_data['claims'] = []
        user_data['claims'].append({
            'order_id': order_id,
            'task_type': task_type,
            'photo_id': photo_id,
            'status': 'approved',
            'timestamp': current_time
        })

        # Update earnings and daily task count
        user_data['earnings'] = user_data.get('earnings', 0) + earnings
        user_data['daily_tasks']['count'] += 1
        del user_data['task_timers'][f"{order_id}_{task_type}"]

        # Check if order is complete
        if order['follows_left'] <= 0 and order['likes_left'] <= 0 and order['comments_left'] <= 0:
            del users['active_orders'][order_id]
            await application.bot.send_message(
                chat_id=client_id,
                text=f"Your order (ID: {order_id}) is complete! Start a new one with /client."
            )

        await save_users()
        await update.message.reply_text(
            f"Task submitted! You earned ₦{earnings}. Check your balance with /balance."
        )

        # Notify admin group
        task_name = {'f': 'follow', 'l': 'like', 'c': 'comment'}.get(task_type, 'task')
        await application.bot.send_photo(
            chat_id=ADMIN_GROUP_ID,
            photo=photo_id,
            caption=f"Task submission by user {user_id} for order {order_id} ({task_name})"
        )

    # Handle admin code submission in group
    if str(update.effective_chat.id) == str(ADMIN_GROUP_ID) and user_id == str(ADMIN_USER_ID):
        code = update.message.text
        for action_key, action_data in list(users['pending_admin_actions'].items()):
            if action_data['code'] == code:
                if time.time() > action_data['expiration']:
                    await update.message.reply_text("Code expired!")
                    del users['pending_admin_actions'][action_key]
                    await save_users()
                    return
                action = action_data['action']
                action_user_id = action_data['user_id']
                action_details = action_data['action_data']
                if action == 'approve_payment':
                    order_id = action_details['order_id']
                    payment = users['pending_payments'][order_id]
                    client_id = payment['user_id']
                    order_details = payment['order_details']
                    users['active_orders'][order_id] = order_details
                    del users['pending_payments'][order_id]
                    if str(client_id) in users['clients']:
                        users['clients'][str(client_id)]['step'] = 'completed'
                    await save_users()
                    await update.message.reply_text(f"Payment for order {order_id} approved!")
                    await application.bot.send_message(
                        chat_id=client_id,
                        text=f"Your payment for order {order_id} has been approved! Check progress with /status."
                    )
                elif action == 'reject_payment':
                    order_id = action_details['order_id']
                    payment = users['pending_payments'][order_id]
                    client_id = payment['user_id']
                    del users['pending_payments'][order_id]
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    await save_users()
                    await update.message.reply_text(f"Payment for order {order_id} rejected!")
                    await application.bot.send_message(
                        chat_id=client_id,
                        text=f"Your payment for order {order_id} was rejected. Please contact support or start a new order with /client."
                    )
                elif action == 'approve_payout':
                    target_user_id = action_details['user_id']
                    user_data = users['engagers'][target_user_id]
                    user_data['earnings'] = 0
                    user_data['signup_bonus'] = 0
                    user_data['awaiting_payout'] = False
                    await save_users()
                    await update.message.reply_text(f"Payout for user {target_user_id} approved!")
                    await application.bot.send_message(
                        chat_id=target_user_id,
                        text="Your payout has been approved and processed! Check your bank account."
                    )
                elif action == 'reject_payout':
                    target_user_id = action_details['user_id']
                    user_data = users['engagers'][target_user_id]
                    user_data['awaiting_payout'] = False
                    await save_users()
                    await update.message.reply_text(f"Payout for user {target_user_id} rejected!")
                    await application.bot.send_message(
                        chat_id=target_user_id,
                        text="Your payout request was rejected. Please contact support for more details."
                    )
                del users['pending_admin_actions'][action_key]
                await save_users()
                return
        await update.message.reply_text("Invalid or expired code!")

# Root route for health checks
@app.route('/', methods=['GET', 'HEAD'])
async def root():
    return jsonify({"status": "Bot is running"}), 200

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    await application.process_update(update)
    return jsonify({"status": "success"}), 200

# Serve success.html
@app.route('/static/success.html')
async def serve_success():
    order_id = request.args.get('order_id', '')
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Payment Successful</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background-color: #f4f4f4; }}
            h1 {{ color: #28a745; }}
            p {{ font-size: 18px; }}
            a {{ display: inline-block; margin-top: 20px; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
            a:hover {{ background-color: #0056b3; }}
        </style>
    </head>
    <body>
        <h1>Payment Successful!</h1>
        <p>Your payment for order {order_id} has been processed successfully.</p>
        <p>Return to the bot to check your order status.</p>
        <a href="https://t.me/{application.bot.username}?start=payment_success_{order_id}">Back to Bot</a>
    </body>
    </html>
    """
    return Response(html_content, mimetype='text/html')

# Main function
async def main():
    global application, users
    users = await load_users()
    if 'clients' not in users:
        users['clients'] = {}
    if 'engagers' not in users:
        users['engagers'] = {}
    if 'pending_payments' not in users:
        users['pending_payments'] = {}
    if 'pending_payouts' not in users:
        users['pending_payouts'] = {}
    if 'active_orders' not in users:
        users['active_orders'] = {}
    if 'pending_admin_actions' not in users:
        users['pending_admin_actions'] = {}

    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("client", client))
    application.add_handler(CommandHandler("engager", engager))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("tasks", tasks))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("order", order))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    application.add_error_handler(error_handler)

    # Initialize the application
    await application.initialize()  # Added this line to fix the error

    # Set up webhook
    await application.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

    # Wrap Flask app for ASGI
    asgi_app = WsgiToAsgi(app)

    # Run the bot and Flask app together
    config = uvicorn.Config(
        asgi_app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())