import os
import time
import random
import uuid
import logging
import json
import asyncio
from typing import Dict, Any
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from flask import Flask, request, jsonify, Response
from asgiref.wsgi import WsgiToAsgi
import uvicorn

# Constants
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://vibeliftbot.onrender.com/webhook"
ADMIN_USER_ID = "1518439839"  # Replace with your admin user ID
REVIEW_GROUP_CHAT_ID = "YOUR_GROUP_CHAT_ID"  # Replace with your group chat ID
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
ADMINS = [ADMIN_USER_ID]

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variables
app = Flask(__name__)
application = None
users: Dict[str, Any] = {}

# Rate limiting
RATE_LIMITS = {
    'start': {'limit': 3, 'window': 60},
    'client': {'limit': 2, 'window': 60, 'is_signup_action': True},
    'engager': {'limit': 2, 'window': 60, 'is_signup_action': True},
    'help': {'limit': 5, 'window': 60},
    'admin': {'limit': 5, 'window': 60}
}
user_rate_limits = {}

# Package limits
package_limits = {
    'bundle': {
        'instagram': {
            'starter': {'follows': 50, 'likes': 100, 'comments': 20, 'price': 500},
            'pro': {'follows': 200, 'likes': 400, 'comments': 80, 'price': 1500}
        },
        'facebook': {
            'starter': {'follows': 50, 'likes': 100, 'comments': 20, 'price': 500},
            'pro': {'follows': 200, 'likes': 400, 'comments': 80, 'price': 1500}
        },
        'tiktok': {
            'starter': {'follows': 50, 'likes': 100, 'comments': 20, 'price': 500},
            'pro': {'follows': 200, 'likes': 400, 'comments': 80, 'price': 1500}
        },
        'twitter': {
            'starter': {'follows': 50, 'likes': 100, 'comments': 20, 'price': 500},
            'pro': {'follows': 200, 'likes': 400, 'comments': 80, 'price': 1500}
        }
    }
}

# Helper functions
async def load_users() -> Dict[str, Any]:
    try:
        with open('users.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

async def save_users() -> None:
    with open('users.json', 'w') as f:
        json.dump(users, f, indent=4)

def check_rate_limit(user_id: str, action: str, is_signup_action: bool = False) -> bool:
    current_time = time.time()
    user_key = f"{user_id}_{action}"
    if user_key not in user_rate_limits:
        user_rate_limits[user_key] = []
    user_timestamps = user_rate_limits[user_key]
    user_timestamps[:] = [t for t in user_timestamps if current_time - t < RATE_LIMITS[action]['window']]
    if len(user_timestamps) >= RATE_LIMITS[action]['limit']:
        return False
    user_timestamps.append(current_time)
    return True

def generate_admin_code() -> str:
    return str(uuid.uuid4())[:8]

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")
    if update and (update.message or update.callback_query):
        chat_id = update.effective_chat.id
        await application.bot.send_message(
            chat_id=chat_id,
            text="An error occurred. Please try again or contact support."
        )

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /start command from user {user_id}")
    if not check_rate_limit(user_id, action='start'):
        logger.info(f"User {user_id} is rate-limited for /start")
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text("Please wait a moment before trying again!")
        else:
            await update.message.reply_text("Please wait a moment before trying again!")
        return

    keyboard = [
        [InlineKeyboardButton("Join as Client", callback_data='client')],
        [InlineKeyboardButton("Join as Engager", callback_data='engager')],
        [InlineKeyboardButton("Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = (
        "Welcome to VibeLiftBot! 🚀\n"
        "Boost your social media or earn cash by engaging.\n"
        "Pick your role:"
    )
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    logger.info(f"Sent /start response to user {user_id}")

# Client command
async def client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /client command from user {user_id}")
    if not check_rate_limit(user_id, action='client', is_signup_action=True):
        logger.info(f"User {user_id} is rate-limited for /client")
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text("Please wait a moment before trying again!")
        else:
            await update.message.reply_text("Please wait a moment before trying again!")
        return
    if user_id in users['engagers']:
        message_text = "You are already an engager! Use /engager to continue or /cancel to switch roles."
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        if client_data['step'] == 'awaiting_payment':
            message_text = (
                "You have an order pending payment!\n"
                "Use /pay to proceed or /cancel to start over."
            )
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                await query.message.edit_text(message_text)
            else:
                await update.message.reply_text(message_text)
            return
        elif client_data['step'] == 'awaiting_approval':
            message_text = (
                "Your order is awaiting admin approval.\n"
                "You’ll be notified once it’s approved. Check status with /status."
            )
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                await query.message.edit_text(message_text)
            else:
                await update.message.reply_text(message_text)
            return
        elif client_data['step'] == 'awaiting_order':
            platform = client_data['platform']
            bundles = "\n".join(
                f"- {k.capitalize()}: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
                for k, v in package_limits['bundle'][platform].items()
            )
            message_text = (
                f"You're ready to submit an order for {platform.capitalize()}!\n"
                f"Available bundles:\n{bundles}\n"
                "Options:\n"
                "1. Handle + Bundle: '@myhandle starter'\n"
                "2. URL + Bundle: 'https://instagram.com/username starter'\n"
                "3. Package + Screenshot: 'package pro' with photo\n"
                "4. Custom + Screenshot: 'username, 20 follows, 30 likes, 20 comments' with photo\n"
                "Custom limits: 10-500 per metric. Screenshot optional for options 1 and 2."
            )
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                await query.message.edit_text(message_text)
            else:
                await update.message.reply_text(message_text)
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
    message_text = "Select a platform to boost:"
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    logger.info(f"Sent platform selection to user {user_id}")

# Engager command
async def engager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /engager command from user {user_id}")
    if not check_rate_limit(user_id, action='engager', is_signup_action=True):
        logger.info(f"User {user_id} is rate-limited for /engager")
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text("Please wait a moment before trying again!")
        else:
            await update.message.reply_text("Please wait a moment before trying again!")
        return
    if user_id in users['clients']:
        message_text = "You are already a client! Use /client to continue or /cancel to switch roles."
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(message_text)
        else:
            await update.message.reply_text(message_text)
        return
    if user_id in users['engagers']:
        keyboard = [
            [InlineKeyboardButton("See Tasks", callback_data='tasks')],
            [InlineKeyboardButton("Check Balance", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "You're already an engager! Pick an action:"
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(message_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message_text, reply_markup=reply_markup)
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
    message_text = (
        "🎉 Welcome, new engager! You’ve earned a ₦500 signup bonus!\n"
        "Pick an action:"
    )
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    await save_users()
    logger.info(f"User {user_id} joined as engager")

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /help command from user {user_id}")
    if not check_rate_limit(user_id, action='help'):
        logger.info(f"User {user_id} is rate-limited for /help")
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text("Please wait a moment before trying again!")
        else:
            await update.message.reply_text("Please wait a moment before trying again!")
        return
    message_text = (
        "Need help?\n"
        "- Clients: Boost your social media with /client.\n"
        "- Engagers: Earn cash with /tasks.\n"
        "- Check your order status with /status.\n"
        "- Contact support: [Your Support Link]"
    )
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text)
    else:
        await update.message.reply_text(message_text)
    logger.info(f"Help sent to user {user_id}")

# Pay command
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /pay command from user {user_id}")
    if user_id not in users['clients']:
        await update.message.reply_text("You need to submit an order first! Use /client to start.")
        return
    client_data = users['clients'][user_id]
    if client_data['step'] != 'awaiting_payment':
        await update.message.reply_text("You don’t have an order awaiting payment! Use /client to submit an order.")
        return
    order_id = client_data['order_id']
    if order_id not in users['pending_orders']:
        await update.message.reply_text("Order not found! Please start a new order with /client.")
        return
    order = users['pending_orders'][order_id]
    amount = order['price']
    # Generate Paystack payment link (simplified for this example)
    payment_url = f"https://paystack.com/pay/vibeliftbot-{order_id}?amount={amount * 100}"
    await update.message.reply_text(
        f"Please complete your payment of ₦{amount} for order {order_id}:\n"
        f"{payment_url}\n"
        "After payment, your order will be submitted for admin review."
    )

# Status command
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /status command from user {user_id}")
    if user_id not in users['clients']:
        await update.message.reply_text("You haven’t submitted any orders yet! Use /client to start.")
        return
    client_data = users['clients'][user_id]
    if client_data['step'] == 'awaiting_payment':
        await update.message.reply_text(
            "You have an order pending payment. Use /pay to complete it or /cancel to start over."
        )
        return
    elif client_data['step'] == 'awaiting_approval':
        await update.message.reply_text(
            "Your order is awaiting admin approval. You’ll be notified once it’s approved."
        )
        return
    elif client_data['step'] == 'completed':
        order_id = client_data['order_id']
        if order_id in users['active_orders']:
            order = users['active_orders'][order_id]
            await update.message.reply_text(
                f"Order {order_id} Status:\n"
                f"Platform: {order['platform'].capitalize()}\n"
                f"Remaining - Follows: {order['follows']}, Likes: {order['likes']}, Comments: {order['comments']}"
            )
        else:
            await update.message.reply_text(
                "Your order has been completed or canceled. Start a new order with /client."
            )
        return
    await update.message.reply_text("No active orders. Use /client to submit a new order.")

# Tasks command
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /tasks command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("You need to join as an engager first! Use /engager to start.")
        return
    if not users['active_orders']:
        await update.message.reply_text("No tasks available at the moment. Check back later!")
        return
    keyboard = [
        [InlineKeyboardButton(f"Task {task_id}", callback_data=f"task_claim_{task_id}")]
        for task_id in users['active_orders'].keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text("Available Tasks:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Available Tasks:", reply_markup=reply_markup)

# Cancel command
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /cancel command from user {user_id}")
    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        if client_data['step'] in ['awaiting_payment', 'awaiting_approval']:
            order_id = client_data.get('order_id')
            if order_id and order_id in users['pending_orders']:
                del users['pending_orders'][order_id]
            del users['clients'][user_id]
            await save_users()
            await update.message.reply_text(
                "Your order has been canceled. You can start a new order with /client."
            )
        else:
            await update.message.reply_text("No order to cancel. Use /client to start a new order.")
    elif user_id in users['engagers']:
        del users['engagers'][user_id]
        await save_users()
        await update.message.reply_text(
            "You have been removed as an engager. You can rejoin with /engager."
        )
    else:
        await update.message.reply_text("Nothing to cancel. Pick a role with /start.")

# Order command (for admin to view orders)
async def order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /order command from user {user_id}")
    if user_id != str(ADMIN_USER_ID):
        await update.message.reply_text("This command is for admins only!")
        return
    if not users['active_orders']:
        await update.message.reply_text("No active orders at the moment.")
        return
    message = "Active Orders:\n"
    for order_id, order in users['active_orders'].items():
        message += (
            f"Order {order_id}:\n"
            f"Client: {order['client_id']}\n"
            f"Platform: {order['platform'].capitalize()}\n"
            f"Follows: {order['follows']}, Likes: {order['likes']}, Comments: {order['comments']}\n\n"
        )
    await update.message.reply_text(message)

# Admin command
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /admin command from user {user_id}")
    if user_id != str(ADMIN_USER_ID):
        await update.message.reply_text("You are not authorized to use this command!")
        return
    if not check_rate_limit(user_id, action='admin'):
        logger.info(f"User {user_id} is rate-limited for /admin")
        await update.message.reply_text("Please wait a moment before trying again!")
        return

    # Prepare the admin dashboard message
    message = "Admin Dashboard:\n\n"

    # Pending orders
    message += "Pending Orders:\n"
    if users.get('pending_orders'):
        message += f"{len(users['pending_orders'])} pending order(s)\n"
    else:
        message += "None\n"

    # Pending task completions
    message += "\nPending Task Completions:\n"
    if users.get('pending_task_completions'):
        message += f"{len(users['pending_task_completions'])} pending task completion(s)\n"
    else:
        message += "None\n"

    # Pending payouts
    pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
    message += "\nPending Payouts:\n"
    if pending_payouts:
        message += f"{len(pending_payouts)} pending payout(s)\n"
    else:
        message += "None\n"

    # Active orders
    message += "\nActive Orders:\n"
    if users.get('active_orders'):
        message += f"{len(users['active_orders'])} active order(s)\n"
    else:
        message += "None\n"

    # Admin action buttons
    keyboard = [
        [InlineKeyboardButton("Approve Order", callback_data="admin_approve_order"),
         InlineKeyboardButton("Reject Order", callback_data="admin_reject_order")],
        [InlineKeyboardButton("Approve Task", callback_data="admin_approve_task"),
         InlineKeyboardButton("Reject Task", callback_data="admin_reject_task")],
        [InlineKeyboardButton("Approve Payout", callback_data="admin_approve_payout"),
         InlineKeyboardButton("Reject Payout", callback_data="admin_reject_payout")],
        [InlineKeyboardButton("Set Priority", callback_data="admin_set_priority"),
         InlineKeyboardButton("Cancel Order", callback_data="admin_cancel_order")],
        [InlineKeyboardButton("Generate Admin Code", callback_data="admin_generate_code")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)

# Balance command
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /balance command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("You need to join as an engager first! Use /engager to start.")
        return
    user_data = users['engagers'][user_id]
    earnings = user_data.get('earnings', 0)
    signup_bonus = user_data.get('signup_bonus', 0)
    total = earnings + signup_bonus
    message_text = (
        f"Your Balance:\n"
        f"Earnings: ₦{earnings}\n"
        f"Signup Bonus: ₦{signup_bonus}\n"
        f"Total: ₦{total}\n"
        "Use /withdraw to request a payout."
    )
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text)
    else:
        await update.message.reply_text(message_text)

# Withdraw command
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /withdraw command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("You need to join as an engager first! Use /engager to start.")
        return
    user_data = users['engagers'][user_id]
    if user_data.get('awaiting_payout'):
        await update.message.reply_text("You already have a pending payout request. Please wait for admin approval.")
        return
    total_earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
    if total_earnings < 1000:
        await update.message.reply_text("You need at least ₦1000 to request a withdrawal.")
        return
    user_data['awaiting_payout'] = True
    await save_users()
    await update.message.reply_text(
        f"Your withdrawal request for ₦{total_earnings} has been submitted for admin review.\n"
        "You’ll be notified once it’s processed."
    )
    # Notify admin in the group chat
    message = (
        f"New Payout Request for Review\n"
        f"Engager ID: {user_id}\n"
        f"Amount: ₦{total_earnings}"
    )
    keyboard = [
        [InlineKeyboardButton("Approve", callback_data=f"approve_payout_{user_id}"),
         InlineKeyboardButton("Reject", callback_data=f"reject_payout_{user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await application.bot.send_message(
        chat_id=REVIEW_GROUP_CHAT_ID,
        text=message,
        reply_markup=reply_markup
    )

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
    elif data.startswith('admin_') or data.startswith('approve_payout_') or data.startswith('reject_payout_') or data.startswith('priority_') or data.startswith('cancel_order_'):
        await handle_admin_button(query, user_id, user_id_str, data)

# Handle task button
async def handle_task_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    action = data.split('_')[1]
    task_id = data.split('_')[-1]

    if action == 'claim':
        if task_id not in users['active_orders']:
            await query.message.edit_text("This task is no longer available!")
            return
        if task_id in users['engagers'][user_id_str].get('claims', []):
            await query.message.edit_text("You have already claimed this task!")
            return
        order = users['active_orders'][task_id]
        platform = order['platform']
        task_type = random.choice(['follow', 'like', 'comment'])
        task_earnings = 10  # Example earnings per task

        # Add task completion to pending_task_completions
        completion_id = str(uuid.uuid4())
        users['pending_task_completions'][completion_id] = {
            'engager_id': user_id_str,
            'task_id': task_id,
            'task_type': task_type,
            'earnings': task_earnings,
            'platform': platform,
            'timestamp': time.time()
        }
        users['engagers'][user_id_str]['claims'].append(task_id)
        await save_users()

        # Send task completion details to the review group chat
        task_message = (
            f"Task Completion for Review (ID: {completion_id})\n"
            f"Engager ID: {user_id_str}\n"
            f"Task ID: {task_id}\n"
            f"Platform: {platform.capitalize()}\n"
            f"Task Type: {task_type.capitalize()}\n"
            f"Earnings: ₦{task_earnings}"
        )
        keyboard = [
            [InlineKeyboardButton("Approve", callback_data=f"admin_approve_task_{completion_id}"),
             InlineKeyboardButton("Reject", callback_data=f"admin_reject_task_{completion_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await application.bot.send_message(
            chat_id=REVIEW_GROUP_CHAT_ID,
            text=task_message,
            reply_markup=reply_markup
        )

        await query.message.edit_text(
            "Task completion submitted for admin review. You’ll be notified once it’s approved!"
        )

# Handle admin button
async def handle_admin_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin only!")
        return
    action = data.split('_', 2)[-1]
    if action == 'approve_order':
        if not users.get('pending_orders'):
            await query.message.edit_text("No pending orders to approve!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'admin_approve_order_{order_id}')]
            for order_id in users['pending_orders'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select an order to approve:", reply_markup=reply_markup)
    elif action == 'reject_order':
        if not users.get('pending_orders'):
            await query.message.edit_text("No pending orders to reject!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'admin_reject_order_{order_id}')]
            for order_id in users['pending_orders'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select an order to reject:", reply_markup=reply_markup)
    elif action == 'approve_task':
        if not users.get('pending_task_completions'):
            await query.message.edit_text("No pending task completions to approve!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Task {completion_id}", callback_data=f'admin_approve_task_{completion_id}')]
            for completion_id in users['pending_task_completions'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a task completion to approve:", reply_markup=reply_markup)
    elif action == 'reject_task':
        if not users.get('pending_task_completions'):
            await query.message.edit_text("No pending task completions to reject!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Task {completion_id}", callback_data=f'admin_reject_task_{completion_id}')]
            for completion_id in users['pending_task_completions'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select a task completion to reject:", reply_markup=reply_markup)
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
    elif action == 'set_priority':
        if not users.get('active_orders'):
            await query.message.edit_text("No active orders to prioritize!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'priority_{order_id}')]
            for order_id in users['active_orders'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select an order to prioritize:", reply_markup=reply_markup)
    elif action == 'cancel_order':
        if not users.get('active_orders'):
            await query.message.edit_text("No active orders to cancel!")
            return
        keyboard = [
            [InlineKeyboardButton(f"Order {order_id}", callback_data=f'cancel_order_{order_id}')]
            for order_id in users['active_orders'].keys()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Select an order to cancel:", reply_markup=reply_markup)
    elif action == 'generate_code':
        code = generate_admin_code()
        users['pending_admin_actions'][code] = {'type': 'admin_code', 'used': False}
        await save_users()
        await query.message.edit_text(
            f"Generated admin code: {code}\n"
            "This code can be used for special actions (e.g., signup bonuses, discounts)."
        )
    elif data.startswith('admin_approve_order_'):
        order_id = data.split('_', 3)[3]
        if order_id in users['pending_orders']:
            order = users['pending_orders'].pop(order_id)
            client_id = order['client_id']
            users['active_orders'][order_id] = order
            if str(client_id) in users['clients']:
                users['clients'][str(client_id)]['step'] = 'completed'
            await save_users()
            await query.message.edit_text(f"Order {order_id} approved and moved to active orders!")
            await application.bot.send_message(
                chat_id=client_id,
                text=f"Your order {order_id} has been approved! Check progress with /status."
            )
        else:
            await query.message.edit_text("Order not found!")
    elif data.startswith('admin_reject_order_'):
        order_id = data.split('_', 3)[3]
        if order_id in users['pending_orders']:
            order = users['pending_orders'].pop(order_id)
            client_id = order['client_id']
            if str(client_id) in users['clients']:
                del users['clients'][str(client_id)]
            await save_users()
            await query.message.edit_text(f"Order {order_id} rejected!")
            await application.bot.send_message(
                chat_id=client_id,
                text=f"Your order {order_id} was rejected by the admin. Please contact support or start a new order with /client."
            )
        else:
            await query.message.edit_text("Order not found!")
    elif data.startswith('admin_approve_task_'):
        completion_id = data.split('_', 3)[3]
        if completion_id in users['pending_task_completions']:
            completion = users['pending_task_completions'].pop(completion_id)
            engager_id = completion['engager_id']
            task_id = completion['task_id']
            earnings = completion['earnings']
            users['engagers'][engager_id]['earnings'] = users['engagers'][engager_id].get('earnings', 0) + earnings
            # Update order progress
            if task_id in users['active_orders']:
                order = users['active_orders'][task_id]
                task_type = completion['task_type']
                if task_type in order and order[task_type] > 0:
                    order[task_type] -= 1
                    if all(order.get(metric, 0) == 0 for metric in ['follows', 'likes', 'comments']):
                        users['active_orders'].pop(task_id)
                        client_id = order['client_id']
                        await application.bot.send_message(
                            chat_id=client_id,
                            text=f"Your order {task_id} has been fully completed!"
                        )
            await save_users()
            await query.message.edit_text(f"Task completion {completion_id} approved! Engager {engager_id} earned ₦{earnings}.")
            await application.bot.send_message(
                chat_id=engager_id,
                text=f"Your task completion for task {task_id} has been approved! You earned ₦{earnings}. Check your balance with /balance."
            )
        else:
            await query.message.edit_text("Task completion not found!")
    elif data.startswith('admin_reject_task_'):
        completion_id = data.split('_', 3)[3]
        if completion_id in users['pending_task_completions']:
            completion = users['pending_task_completions'].pop(completion_id)
            engager_id = completion['engager_id']
            task_id = completion['task_id']
            # Remove the task from the engager's claims
            if task_id in users['engagers'][engager_id].get('claims', []):
                users['engagers'][engager_id]['claims'].remove(task_id)
            await save_users()
            await query.message.edit_text(f"Task completion {completion_id} rejected!")
            await application.bot.send_message(
                chat_id=engager_id,
                text=f"Your task completion for task {task_id} was rejected by the admin. Please contact support for more details."
            )
        else:
            await query.message.edit_text("Task completion not found!")
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
    elif data.startswith('priority_'):
        order_id = data.split('_', 1)[1]
        if order_id in users['active_orders']:
            users['active_orders'][order_id]['priority'] = True
            await save_users()
            await query.message.edit_text(f"Order {order_id} prioritized!")
        else:
            await query.message.edit_text("Order not found!")
    elif data.startswith('cancel_order_'):
        order_id = data.split('_', 2)[2]
        if order_id in users['active_orders']:
            order = users['active_orders'].pop(order_id)
            client_id = order['client_id']
            await save_users()
            await query.message.edit_text(f"Order {order_id} has been canceled.")
            await application.bot.send_message(
                chat_id=int(client_id),
                text=f"Your order {order_id} has been canceled by an admin. Please contact support."
            )
        else:
            await query.message.edit_text(f"Order {order_id} is no longer active.")

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    message = update.message
    text = message.text.lower() if message.text else None
    photo = message.photo

    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        if client_data['step'] == 'awaiting_order':
            platform = client_data['platform']
            order_id = str(uuid.uuid4())
            order_details = None

            # Parse the order based on the input format
            if text:
                if text.startswith('@') or text.startswith('http'):
                    # Handle + Bundle or URL + Bundle
                    parts = text.split()
                    if len(parts) != 2:
                        await message.reply_text(
                            "Invalid format! Use: '@myhandle starter' or 'https://instagram.com/username starter'"
                        )
                        return
                    handle_or_url, bundle = parts
                    if bundle not in package_limits['bundle'][platform]:
                        await message.reply_text(
                            f"Invalid bundle! Available bundles: {', '.join(package_limits['bundle'][platform].keys())}"
                        )
                        return
                    bundle_data = package_limits['bundle'][platform][bundle]
                    order_details = {
                        'client_id': user_id,
                        'platform': platform,
                        'handle_or_url': handle_or_url,
                        'follows': bundle_data['follows'],
                        'likes': bundle_data['likes'],
                        'comments': bundle_data['comments'],
                        'price': bundle_data['price']
                    }
                elif 'package' in text:
                    # Package + Screenshot
                    if not photo:
                        await message.reply_text("Please attach a screenshot for package orders!")
                        return
                    parts = text.split()
                    if len(parts) != 2 or parts[0] != 'package':
                        await message.reply_text("Invalid format! Use: 'package pro' with a screenshot")
                        return
                    bundle = parts[1]
                    if bundle not in package_limits['bundle'][platform]:
                        await message.reply_text(
                            f"Invalid bundle! Available bundles: {', '.join(package_limits['bundle'][platform].keys())}"
                        )
                        return
                    bundle_data = package_limits['bundle'][platform][bundle]
                    order_details = {
                        'client_id': user_id,
                        'platform': platform,
                        'handle_or_url': 'package',
                        'follows': bundle_data['follows'],
                        'likes': bundle_data['likes'],
                        'comments': bundle_data['comments'],
                        'price': bundle_data['price'],
                        'screenshot': photo[-1].file_id if photo else None
                    }
                else:
                    # Custom + Screenshot
                    if not photo:
                        await message.reply_text("Please attach a screenshot for custom orders!")
                        return
                    parts = text.split(',')
                    if len(parts) != 4:
                        await message.reply_text(
                            "Invalid format! Use: 'username, 20 follows, 30 likes, 20 comments' with a screenshot"
                        )
                        return
                    username = parts[0].strip()
                    try:
                        follows = int(parts[1].split()[0])
                        likes = int(parts[2].split()[0])
                        comments = int(parts[3].split()[0])
                        if not (10 <= follows <= 500 and 10 <= likes <= 500 and 10 <= comments <= 500):
                            await message.reply_text("Custom metrics must be between 10 and 500!")
                            return
                        price = (follows + likes + comments) * 5  # Example pricing
                        order_details = {
                            'client_id': user_id,
                            'platform': platform,
                            'handle_or_url': username,
                            'follows': follows,
                            'likes': likes,
                            'comments': comments,
                            'price': price,
                            'screenshot': photo[-1].file_id if photo else None
                        }
                    except (ValueError, IndexError):
                        await message.reply_text(
                            "Invalid format! Use: 'username, 20 follows, 30 likes, 20 comments'"
                        )
                        return

            if order_details:
                # Store order and move to payment step
                users['pending_orders'][order_id] = order_details
                client_data['step'] = 'awaiting_payment'
                client_data['order_id'] = order_id
                await save_users()
                await message.reply_text(
                    f"Order {order_id} created! Total: ₦{order_details['price']}\n"
                    "Use /pay to complete your payment via Paystack."
                )
                return
    await message.reply_text("I’m not sure how to handle that. Use /start to pick a role or /help for assistance.")

# Root route for health checks
@app.route('/', methods=['GET', 'HEAD'])
async def root():
    return jsonify({"status": "Bot is running"}), 200

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        update = Update.de_json(request.get_json(), application.bot)
        if update is None:
            logger.error("Received invalid update from Telegram")
            return jsonify({"status": "error", "message": "Invalid update"}), 400
        if not application.updater:
            logger.error("Application not initialized yet")
            return jsonify({"status": "error", "message": "Application not initialized"}), 503
        await application.process_update(update)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Error processing webhook update: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Serve success.html (Paystack callback)
@app.route('/static/success.html')
async def serve_success():
    order_id = request.args.get('order_id', '')
    if not order_id:
        return Response("Invalid order ID", status=400)
    if order_id not in users['pending_orders']:
        return Response("Order not found", status=404)
    order = users['pending_orders'][order_id]
    client_id = order['client_id']
    # In a real implementation, verify the payment with Paystack API here
    # For this example, assume payment is successful
    # Move order to pending_orders for admin review
    users['clients'][client_id]['step'] = 'awaiting_approval'
    await save_users()
    # Send order details to the review group chat
    order_message = (
        f"New Order for Review (ID: {order_id})\n"
        f"Client ID: {client_id}\n"
        f"Platform: {order['platform'].capitalize()}\n"
        f"Handle/URL: {order['handle_or_url']}\n"
        f"Follows: {order['follows']}\n"
        f"Likes: {order['likes']}\n"
        f"Comments: {order['comments']}\n"
        f"Price: ₦{order['price']}"
    )
    keyboard = [
        [InlineKeyboardButton("Approve", callback_data=f"admin_approve_order_{order_id}"),
         InlineKeyboardButton("Reject", callback_data=f"admin_reject_order_{order_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if 'screenshot' in order and order['screenshot']:
        await application.bot.send_photo(
            chat_id=REVIEW_GROUP_CHAT_ID,
            photo=order['screenshot'],
            caption=order_message,
            reply_markup=reply_markup
        )
    else:
        await application.bot.send_message(
            chat_id=REVIEW_GROUP_CHAT_ID,
            text=order_message,
            reply_markup=reply_markup
        )
    await application.bot.send_message(
        chat_id=client_id,
        text="Payment successful! Your order has been submitted for admin review. You’ll be notified once it’s approved."
    )
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
    if 'pending_orders' not in users:
        users['pending_orders'] = {}
    if 'pending_payouts' not in users:
        users['pending_payouts'] = {}
    if 'active_orders' not in users:
        users['active_orders'] = {}
    if 'pending_admin_actions' not in users:
        users['pending_admin_actions'] = {}
    if 'pending_task_completions' not in users:
        users['pending_task_completions'] = {}

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
    await application.initialize()
    logger.info("Application initialized successfully")

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