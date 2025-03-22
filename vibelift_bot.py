import os
import json
import time
import logging
import asyncio
import random
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
import requests
import uvicorn
from asgiref.wsgi import WsgiToAsgi

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app for handling webhooks
app = Flask(__name__)

# Environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PAYSTACK_WEBHOOK_URL = os.getenv("PAYSTACK_WEBHOOK_URL", f"{WEBHOOK_URL.rsplit('/', 1)[0]}/paystack-webhook")
STORAGE_PATH = "users.json"  # Use root directory for free tier

# Paystack API headers
PAYSTACK_HEADERS = {
    "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
    "Content-Type": "application/json"
}

# Bot token, admin IDs, and withdrawal limit
TOKEN = TELEGRAM_TOKEN or '7637213737:AAHz9Kvcxj-UZhDlKyyhc9fqoD51JBSsViA'
ADMIN_USER_ID = '1518439839'
ADMIN_GROUP_ID = '-4762253610'
WITHDRAWAL_LIMIT = 2000  # Trial limit for withdrawals

# Initialize users dictionary
users = {
    'clients': {}, 'engagers': {}, 'pending_tasks': {}, 'last_interaction': {},
    'active_orders': {}, 'pending_payouts': {}, 'pending_payments': {}, 'pending_admin_actions': {}
}
try:
    with open(STORAGE_PATH, 'r') as f:
        loaded_users = json.load(f)
        users.update(loaded_users)
        for key in ['last_interaction', 'pending_payments', 'pending_admin_actions']:
            if key not in users:
                users[key] = {}
except FileNotFoundError:
    logger.info(f"{STORAGE_PATH} not found, starting with empty users dictionary")

def save_users():
    try:
        with open(STORAGE_PATH, 'w') as f:
            json.dump(users, f)
        logger.info(f"{STORAGE_PATH} saved successfully")
    except Exception as e:
        logger.error(f"Error saving {STORAGE_PATH}: {e}")

def check_rate_limit(user_id, is_signup_action=False, action=None):
    user_id_str = str(user_id)
    current_time = time.time()
    last_time = users['last_interaction'].get(user_id_str, 0)
    
    # Bypass for engagers on tasks/balance
    if action in ['tasks', 'balance'] and user_id_str in users['engagers'] and users['engagers'][user_id_str].get('joined'):
        logger.info(f"User {user_id_str} is an engager, bypassing rate limit for {action}")
        users['last_interaction'][user_id_str] = current_time
        save_users()
        return True
    
    # Signup actions (e.g., client, engager, help): 1-second limit
    if is_signup_action:
        if current_time - last_time < 1:  # Reduced from 2 to 1
            logger.info(f"User {user_id_str} rate limited for signup action")
            return False
        # Update timestamp only if action succeeds
        users['last_interaction'][user_id_str] = current_time
        save_users()
        return True
    
    # Other actions: 2-second limit
    if current_time - last_time < 2:
        logger.info(f"User {user_id_str} rate limited for action {action}")
        return False
    users['last_interaction'][user_id_str] = current_time
    save_users()
    return True

# Initialize the Application object
logger.info("Building Application object...")
application = Application.builder().token(TOKEN).build()
logger.info("Application object built successfully")

# Function to generate and send admin verification code
async def generate_admin_code(user_id, action, action_data=None):
    code = str(random.randint(100000, 999999))
    action_id = f"{user_id}_{int(time.time())}"
    users['pending_admin_actions'][action_id] = {
        'user_id': user_id,
        'action': action,
        'action_data': action_data,
        'code': code,
        'expiration': time.time() + 300
    }
    await application.bot.send_message(chat_id=ADMIN_USER_ID, text=f"Admin verification code for {action}: {code}\nThis code expires in 5 minutes.")
    save_users()
    return action_id

# Define all handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("Received /start command from user: %s", update.message.from_user.id)
        user_id = update.message.from_user.id
        if not check_rate_limit(user_id, is_signup_action=True):
            await update.message.reply_text("Hang on a sec and try again!")
            return
        keyboard = [
            [InlineKeyboardButton("Grow My Account", callback_data='client')],
            [InlineKeyboardButton("Earn Cash", callback_data='engager')],
            [InlineKeyboardButton("Help", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Welcome to VibeLift! 🚀\n- Boost your social media with real vibes.\n- Earn cash with simple tasks.\nWhat’s your vibe today?",
            reply_markup=reply_markup
        )
        logger.info("Sent /start response to user: %s", user_id)
    except Exception as e:
        logger.error(f"Error in start handler for user {update.message.from_user.id}: {e}")
        raise

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    reply_target = update.message or update.callback_query.message
    if not check_rate_limit(user_id, is_signup_action=True):
        await reply_target.reply_text("Hang on a sec and try again!")
        return
    keyboard = [
        [InlineKeyboardButton("Back to Start", callback_data='start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_target.reply_text(
        "Need help?\n- Clients: Use /client, pick a package, pay with /pay.\n- Engagers: Join with /engager, earn via /tasks.\nEmail vibelift@gmail.com for support!",
        reply_markup=reply_markup
    )

async def client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    reply_target = update.message or update.callback_query.message
    if not check_rate_limit(user_id, is_signup_action=True):
        await reply_target.reply_text("Hang on a sec and try again!")
        return
    await reply_target.reply_text(
        "Let’s grow your account! Reply with:\n`handle platform package`\nExample: `@NaijaFashion instagram 50` for 50 followers.\nSupported: Instagram, Facebook, TikTok, Twitter.\nPackages:\n- Followers: 10, 50, 100\n- Likes: 20, 100, 200\n- Comments: 5, 10, 50\n- Bundles: starter, pro, elite (e.g., `@NaijaFashion instagram starter`)"
    )
    users['clients'][str(user_id)] = {'step': 'awaiting_order'}
    save_users()

async def engager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    reply_target = update.message or update.callback_query.message
    if not check_rate_limit(user_id, is_signup_action=True):
        await reply_target.reply_text("Hang on a sec and try again!")
        return
    keyboard = [[InlineKeyboardButton("Join Now", callback_data='join')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_target.reply_text("Earn ₦100-₦1,250 daily lifting vibes!\nClick to join:", reply_markup=reply_markup)

async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    reply_target = update.message or update.callback_query.message
    if not check_rate_limit(user_id, is_signup_action=False, action='tasks'):
        await reply_target.reply_text("Hang on a sec and try again!")
        return
    user_id_str = str(user_id)
    if user_id_str not in users['engagers'] or not users['engagers'][user_id_str].get('joined'):
        await reply_target.reply_text("Join first! Type /engager or click Earn Cash from /start.")
        return
    user_data = users['engagers'][user_id_str]
    current_time = time.time()
    if 'daily_tasks' not in user_data:
        user_data['daily_tasks'] = {'count': 0, 'last_reset': current_time}
    elif current_time - user_data['daily_tasks']['last_reset'] >= 86400:
        user_data['daily_tasks'] = {'count': 0, 'last_reset': current_time}
    if user_data['daily_tasks']['count'] >= 25:
        await reply_target.reply_text("You’ve hit your daily limit of 25 tasks. Check back tomorrow!")
        return
    if 'tasks_per_order' not in user_data:
        user_data['tasks_per_order'] = {}
    logger.info(f"Active orders available for tasks: {users['active_orders']}")
    keyboard = []
    for order_id, order in users['active_orders'].items():
        platform = order.get('platform', 'unknown')
        handle = order.get('handle', 'unknown')
        follows_left = order.get('follows_left', 0)
        likes_left = order.get('likes_left', 0)
        comments_left = order.get('comments_left', 0)
        logger.info(f"Order {order_id}: platform={platform}, handle={handle}, follows_left={follows_left}, likes_left={likes_left}, comments_left={comments_left}")
        payouts = {
            'instagram': {'follow': 20, 'like': 10, 'comment': 30},
            'facebook': {'follow': 30, 'like': 20, 'comment': 30},
            'tiktok': {'follow': 30, 'like': 20, 'comment': 40},
            'twitter': {'follow': 25, 'like': 30, 'comment': 50}
        }
        payout = payouts.get(platform, {'follow': 0, 'like': 0, 'comment': 0})
        order_tasks = user_data['tasks_per_order'].get(order_id, 0)
        if order_tasks >= 5:
            logger.info(f"Order {order_id} skipped: engager {user_id} has completed {order_tasks} tasks")
            continue
        if follows_left > 0:
            keyboard.append([InlineKeyboardButton(f"Follow {handle} on {platform} (₦{payout['follow']})", callback_data=f'task_f_{order_id}')])
        if likes_left > 0:
            text = f"Like post on {platform} (₦{payout['like']})" if not order.get('use_recent_posts') else f"Like 3 recent posts by {handle} on {platform} (₦{payout['like']} each)"
            keyboard.append([InlineKeyboardButton(text, callback_data=f'task_l_{order_id}')])
        if comments_left > 0:
            text = f"Comment on post on {platform} (₦{payout['comment']})" if not order.get('use_recent_posts') else f"Comment on 3 recent posts by {handle} on {platform} (₦{payout['comment']} each)"
            keyboard.append([InlineKeyboardButton(text, callback_data=f'task_c_{order_id}')])
    if not keyboard:
        await reply_target.reply_text("No tasks right now. Check back in 5-10 minutes!")
        logger.info(f"No tasks available for user {user_id}")
        return
    keyboard.append([InlineKeyboardButton("Back to Start", callback_data='start')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_target.reply_text("Pick a task (send screenshot after):\nLikes/comments need 60 seconds on the post!", reply_markup=reply_markup)
    logger.info(f"Displayed {len(keyboard)} tasks for user {user_id}")
    save_users()

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    reply_target = update.message or update.callback_query.message
    if not check_rate_limit(user_id, is_signup_action=False, action='balance'):
        await reply_target.reply_text("Hang on a sec and try again!")
        return
    user_id_str = str(user_id)
    if user_id_str in users['engagers'] and users['engagers'][user_id_str].get('joined'):
        bonus = users['engagers'][user_id_str].get('signup_bonus', 0)
        earnings = users['engagers'][user_id_str].get('earnings', 0)
        total_balance = bonus + earnings
        keyboard = [
            [InlineKeyboardButton("Withdraw Earnings", callback_data='withdraw')],
            [InlineKeyboardButton("Back to Start", callback_data='start')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await reply_target.reply_text(
            f"Your VibeLift balance:\n- Signup Bonus: ₦{bonus}\n- Earned: ₦{earnings}\n- Total: ₦{total_balance}\nWithdraw when earned amount hits ₦1,000!",
            reply_markup=reply_markup
        )
    else:
        await reply_target.reply_text("Join as an engager first! Type /engager or click Earn Cash from /start.")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    user_id_str = str(user_id)
    if user_id_str not in users['engagers'] or not users['engagers'][user_id_str].get('joined'):
        await update.message.reply_text("Join as an engager first! Type /engager.")
        return
    earnings = users['engagers'][user_id_str].get('earnings', 0)
    bonus = users['engagers'][user_id_str].get('signup_bonus', 0)
    total_balance = earnings + bonus
    if earnings < 1000:
        await update.message.reply_text(f"Need ₦1,000 earned (excluding ₦{bonus} bonus) to withdraw. Current earned: ₦{earnings}. Keep going!")
        return
    await update.message.reply_text(
        f"Reply with your OPay account number (e.g., 8101234567) to withdraw.\n"
        f"Trial limit: ₦{WITHDRAWAL_LIMIT}. Your balance: ₦{total_balance}.{' You’re good to go!' if total_balance <= WITHDRAWAL_LIMIT else ' Your balance is over the limit—wait for it to lift.'}"
    )
    if total_balance > WITHDRAWAL_LIMIT:
        return
    users['engagers'][user_id_str]['awaiting_payout'] = True
    save_users()

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    user_id_str = str(user_id)
    if user_id_str not in users['clients'] or users['clients'][user_id_str]['step'] != 'awaiting_payment':
        await update.message.reply_text("Start an order first with /client!")
        return
    email = f"{user_id}@vibeliftbot.com"
    amount = users['clients'][user_id_str]['amount'] * 100  # Convert to kobo
    payload = {
        "email": email,
        "amount": amount,
        "callback_url": PAYSTACK_WEBHOOK_URL,
        "metadata": {"user_id": user_id, "order_id": users['clients'][user_id_str]['order_id']}
    }
    try:
        response = requests.post("https://api.paystack.co/transaction/initialize", json=payload, headers=PAYSTACK_HEADERS)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Paystack API response: {data}")
        if data["status"]:
            payment_url = data["data"]["authorization_url"]
            keyboard = [
                [InlineKeyboardButton(f"Pay ₦{users['clients'][user_id_str]['amount']}", url=payment_url)],
                [InlineKeyboardButton("Cancel Order", callback_data='cancel')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Click to pay via Paystack:", reply_markup=reply_markup)
        else:
            error_message = data.get("message", "Unknown error")
            logger.error(f"Paystack API error: {error_message}")
            await update.message.reply_text(f"Payment failed: {error_message}. Try again.")
    except Exception as e:
        logger.error(f"Error initiating payment: {e}")
        await update.message.reply_text("Something went wrong. Try again later.")

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("Admin only!")
        return
    keyboard = [
        [InlineKeyboardButton("📊 View Stats", callback_data='admin_stats')],
        [InlineKeyboardButton("🔍 Audit Task", callback_data='admin_audit')],
        [InlineKeyboardButton("💸 View Withdrawals", callback_data='admin_view_withdrawals')],
        [InlineKeyboardButton("💳 View Pending Payments", callback_data='admin_view_payments')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Admin Panel:\n"
        f"Withdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\n"
        f"Pick an action:", 
        reply_markup=reply_markup
    )

async def audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("Admin only!")
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /audit <engager_id> <order_id> [reason]")
        return
    engager_id, order_id = context.args[0], context.args[1]
    reason = " ".join(context.args[2:]) if len(context.args) > 2 else "No reason provided"
    if engager_id not in users['engagers'] or 'claims' not in users['engagers'][engager_id]:
        await update.message.reply_text("No claims found for this user.")
        return
    for claim in users['engagers'][engager_id]['claims']:
        if claim['order_id'] == order_id and claim['status'] == 'approved':
            claim['status'] = 'rejected'
            claim['rejection_reason'] = reason
            users['engagers'][engager_id]['earnings'] -= claim['amount']
            await context.bot.send_message(chat_id=engager_id, 
                                           text=f"Your task {order_id} was rejected after audit. Reason: {reason}. ₦{claim['amount']} removed.")
            await update.message.reply_text(f"Task {order_id} for {engager_id} rejected. Balance updated.")
            save_users()
            return
    await update.message.reply_text("Claim not found or already processed.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_id_str = str(user_id)
    data = query.data
    is_signup_action = data in ['client', 'engager', 'join', 'help', 'start']
    action = data if data in ['tasks', 'balance'] else None
    if not check_rate_limit(user_id, is_signup_action=is_signup_action, action=action):
        await query.message.reply_text("Hang on a sec and try again!")
        return
    await query.answer()
    if data == 'start':
        await start(update, context)
    elif data == 'client':
        await client(update, context)
    elif data == 'engager':
        await engager(update, context)
    elif data == 'help':
        await help_command(update, context)
    elif data == 'join':
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
        save_users()
    elif data.startswith('task_'):
        task_parts = data.split('_', 2)
        task_type = task_parts[1]
        order_id = task_parts[2]
        logger.info(f"Task claim attempt: user={user_id}, order_id={order_id}, active_orders={users['active_orders']}")
        if order_id not in users['active_orders']:
            await query.message.reply_text("Task no longer available!")
            return
        order = users['active_orders'][order_id]
        if task_type == 'f' and order.get('follows_left', 0) <= 0:
            await query.message.reply_text("Task no longer available!")
            return
        elif task_type == 'l' and order.get('likes_left', 0) <= 0:
            await query.message.reply_text("Task no longer available!")
            return
        elif task_type == 'c' and order.get('comments_left', 0) <= 0:
            await query.message.reply_text("Task no longer available!")
            return
        user_data = users['engagers'].get(user_id_str, {})
        claims = user_data.get('claims', [])
        for claim in claims:
            if claim['order_id'] == order_id and claim['task_type'] == task_type and claim['status'] == 'approved':
                task_name = {'f': 'Follow', 'l': 'Like', 'c': 'Comment'}.get(task_type, 'Task')
                await query.message.reply_text(f"You’ve already done the {task_name} task for this order!")
                return
        timer_key = f"{order_id}_{task_type}"
        if 'tasks_per_order' not in user_data:
            user_data['tasks_per_order'] = {}
        users['engagers'][user_id_str]['task_timers'][timer_key] = time.time()
        if task_type == 'f':
            await query.message.reply_text(f"Follow {order['handle']} on {order['platform']}.\nSend a screenshot here to earn!")
        elif task_type == 'l':
            if order.get('use_recent_posts'):
                await query.message.reply_text(f"Like the 3 latest posts by {order['handle']} on {order['platform']}.\nSpend 60 seconds on each, then send a screenshot here!")
            else:
                await query.message.reply_text(f"Like this post: {order['like_url']}\nSpend 60 seconds, then send a screenshot here!")
        elif task_type == 'c':
            if order.get('use_recent_posts'):
                await query.message.reply_text(f"Comment on the 3 latest posts by {order['handle']} on {order['platform']}.\nSpend 60 seconds on each, then send a screenshot here!")
            else:
                await query.message.reply_text(f"Comment on this post: {order['comment_url']}\nSpend 60 seconds, then send a screenshot here!")
        save_users()
    elif data == 'tasks':
        await tasks(update, context)
    elif data == 'balance':
        await balance(update, context)
    elif data == 'withdraw':
        await withdraw(update, context)
    elif data == 'admin_stats':
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Admin only!")
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
        await query.message.reply_text(stats_text, reply_markup=reply_markup)
    elif data == 'admin_audit':
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Admin only!")
            return
        users['pending_admin_actions'][f"audit_{user_id_str}"] = {'user_id': int(user_id_str), 'action': 'awaiting_audit_input', 'expiration': time.time() + 300}
        save_users()
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Reply with: <engager_id> <order_id> [reason]\nExample: 1518439839 1518439839_1742633918 Invalid proof", reply_markup=reply_markup)
    elif data == 'admin_view_withdrawals':
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Admin only!")
            return
        if not users['pending_payouts']:
            keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text("No pending withdrawals.", reply_markup=reply_markup)
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
        await query.message.reply_text(message, reply_markup=reply_markup)
    elif data == 'admin_view_payments':
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Admin only!")
            return
        if not users['pending_payments']:
            keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text("No pending payments.", reply_markup=reply_markup)
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
            await context.bot.send_photo(
                chat_id=user_id,
                photo=payment['photo_id'],
                caption=f"Payment proof from {client_id} for order: {order_id}. Amount: ₦{amount}",
                reply_markup=reply_markup
            )
    elif data == 'back_to_admin':
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Admin only!")
            return
        keyboard = [
            [InlineKeyboardButton("📊 View Stats", callback_data='admin_stats')],
            [InlineKeyboardButton("🔍 Audit Task", callback_data='admin_audit')],
            [InlineKeyboardButton("💸 View Withdrawals", callback_data='admin_view_withdrawals')],
            [InlineKeyboardButton("💳 View Pending Payments", callback_data='admin_view_payments')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"Admin Panel:\n"
            f"Withdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\n"
            f"Pick an action:", 
            reply_markup=reply_markup
        )
    elif data.startswith('approve_payout_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Only admin can do this!")
            return
        payout_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'approve_payout', {'payout_id': payout_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(f"Enter the 6-digit code sent to your private chat to approve payout {payout_id}.", reply_markup=reply_markup)
    elif data.startswith('reject_payout_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Only admin can do this!")
            return
        payout_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'reject_payout', {'payout_id': payout_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(f"Enter the 6-digit code sent to your private chat to reject payout {payout_id}.", reply_markup=reply_markup)
    elif data.startswith('approve_payment_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Only admin can do this!")
            return
        payment_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'approve_payment', {'payment_id': payment_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(f"Enter the 6-digit code sent to your private chat to approve payment {payment_id}.", reply_markup=reply_markup)
    elif data.startswith('reject_payment_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.reply_text("Only admin can do this!")
            return
        payment_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'reject_payment', {'payment_id': payment_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(f"Enter the 6-digit code sent to your private chat to reject payment {payment_id}.", reply_markup=reply_markup)
    elif data == 'cancel':
        if user_id_str in users['clients']:
            del users['clients'][user_id_str]
            await query.message.reply_text("Order canceled. Start over with /client!")
            save_users()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = update.message.text.lower() if update.message.text else ""
    is_signup_action = (
        (user_id in users['clients'] and users['clients'][user_id]['step'] in ['awaiting_order', 'awaiting_urls', 'awaiting_payment']) or
        (user_id in users['engagers'] and 'awaiting_payout' in users['engagers'][user_id])
    )
    if not check_rate_limit(update.message.from_user.id, is_signup_action=is_signup_action):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if str(update.message.chat_id) == ADMIN_GROUP_ID and user_id != ADMIN_USER_ID:
        return

    pending_action = None
    action_id_to_remove = None
    for action_id, action_data in list(users['pending_admin_actions'].items()):
        if action_data['user_id'] == int(user_id) and time.time() < action_data['expiration']:
            pending_action = action_data
            action_id_to_remove = action_id
            break
        elif time.time() >= action_data['expiration']:
            del users['pending_admin_actions'][action_id]
    if pending_action and action_id_to_remove.startswith('audit_'):
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
                save_users()
                return
            for claim in users['engagers'][engager_id]['claims']:
                if claim['order_id'] == order_id and claim['status'] == 'approved':
                    claim['status'] = 'rejected'
                    claim['rejection_reason'] = reason
                    users['engagers'][engager_id]['earnings'] -= claim['amount']
                    await context.bot.send_message(chat_id=engager_id, 
                                                   text=f"Your task {order_id} was rejected after audit. Reason: {reason}. ₦{claim['amount']} removed.")
                    await update.message.reply_text(f"Task {order_id} for {engager_id} rejected. Balance updated.")
                    del users['pending_admin_actions'][action_id_to_remove]
                    save_users()
                    return
            await update.message.reply_text("Claim not found or already processed.")
            del users['pending_admin_actions'][action_id_to_remove]
            save_users()
            return
    if pending_action and text.isdigit() and len(text) == 6:
        if text == pending_action['code']:
            action = pending_action['action']
            action_data = pending_action['action_data']
            del users['pending_admin_actions'][action_id_to_remove]
            save_users()
            if action == 'approve_payout':
                payout_id = action_data['payout_id']
                if payout_id in users['pending_payouts']:
                    payout = users['pending_payouts'][payout_id]
                    engager_id = payout['engager_id']
                    amount = payout['amount']
                    account = payout['account']
                    users['engagers'][engager_id]['earnings'] -= amount
                    del users['pending_payouts'][payout_id]
                    await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout of ₦{amount} to {account} for {engager_id} approved. Process it now!")
                    await context.bot.send_message(chat_id=engager_id, text=f"Your withdrawal of ₦{amount} to {account} is approved!")
                    save_users()
            elif action == 'reject_payout':
                payout_id = action_data['payout_id']
                if payout_id in users['pending_payouts']:
                    engager_id = users['pending_payouts'][payout_id]['engager_id']
                    del users['pending_payouts'][payout_id]
                    await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout request from {engager_id} rejected.")
                    await context.bot.send_message(chat_id=engager_id, text="Your withdrawal was rejected. Contact support!")
                    save_users()
            elif action == 'approve_payment':
                payment_id = action_data['payment_id']
                if payment_id in users['pending_payments']:
                    payment = users['pending_payments'][payment_id]
                    client_id = payment['client_id']
                    order_id = payment['order_id']
                    order_details = payment['order_details']
                    logger.info(f"Approving payment {payment_id}: Adding order {order_id} to active_orders with details {order_details}")
                    users['active_orders'][order_id] = order_details
                    del users['pending_payments'][payment_id]
                    if str(client_id) in users['clients']:
                        users['clients'][str(client_id)]['step'] = 'completed'
                    await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payment for order {order_id} from {client_id} approved. Tasks active!")
                    await context.bot.send_message(chat_id=client_id, text="Payment approved! Results in 4-5 hours for small orders.")
                    save_users()
            elif action == 'reject_payment':
                payment_id = action_data['payment_id']
                if payment_id in users['pending_payments']:
                    payment = users['pending_payments'][payment_id]
                    client_id = payment['client_id']
                    del users['pending_payments'][payment_id]
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payment for order from {client_id} rejected.")
                    await context.bot.send_message(chat_id=client_id, text="Payment rejected. Start over with /client.")
                    save_users()
            await context.bot.send_message(chat_id=user_id, text=f"{action.replace('_', ' ').title()} completed!")
        else:
            await context.bot.send_message(chat_id=user_id, text="Wrong code! Try again.")
        return

    package_limits = {
        'followers': {
            'instagram': {'10': 10, '50': 50, '100': 100},
            'facebook': {'10': 10, '50': 50, '100': 100},
            'tiktok': {'10': 10, '50': 50, '100': 100},
            'twitter': {'10': 10, '50': 50, '100': 100}
        },
        'likes': {
            'instagram': {'20': 20, '100': 100, '200': 200},
            'facebook': {'20': 20, '100': 100, '200': 200},
            'tiktok': {'20': 20, '100': 100, '200': 200},
            'twitter': {'20': 20, '100': 100, '200': 200}
        },
        'comments': {
            'instagram': {'5': 5, '10': 10, '50': 50},
            'facebook': {'5': 5, '10': 10, '50': 50},
            'tiktok': {'5': 5, '10': 10, '50': 50},
            'twitter': {'5': 5, '10': 10, '50': 50}
        },
        'bundle': {
            'instagram': {
                'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 1890},
                'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 8640},
                'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 18900}
            },
            'facebook': {
                'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 3240},
                'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 15390},
                'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 32400}
            },
            'tiktok': {
                'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 3780},
                'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 17280},
                'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 37800}
            },
            'twitter': {
                'starter': {'follows': 10, 'likes': 20, 'comments': 5, 'price': 2880},
                'pro': {'follows': 50, 'likes': 100, 'comments': 10, 'price': 12780},
                'elite': {'follows': 100, 'likes': 200, 'comments': 50, 'price': 28800}
            }
        }
    }
    pricing = {
        'followers': {
            'instagram': {'10': 1200, '50': 6000, '100': 12000},
            'facebook': {'10': 1500, '50': 7500, '100': 15000},
            'tiktok': {'10': 1800, '50': 9000, '100': 18000},
            'twitter': {'10': 800, '50': 4000, '100': 8000}
        },
        'likes': {
            'instagram': {'20': 600, '100': 3000, '200': 6000},
            'facebook': {'20': 1800, '100': 9000, '200': 18000},
            'tiktok': {'20': 1800, '100': 9000, '200': 18000},
            'twitter': {'20': 1800, '100': 9000, '200': 18000}
        },
        'comments': {
            'instagram': {'5': 300, '10': 600, '50': 3000},
            'facebook': {'5': 300, '10': 600, '50': 3000},
            'tiktok': {'5': 600, '10': 1200, '50': 6000},
            'twitter': {'5': 600, '10': 1200, '50': 6000}
        }
    }

    if user_id in users['clients'] and users['clients'][user_id]['step'] == 'awaiting_order':
        parts = text.split()
        if len(parts) != 3:
            await update.message.reply_text("Reply with: `handle platform package` (e.g., @NaijaFashion instagram 50).")
            return
        handle, platform, package = parts[0], parts[1], parts[2]
        valid_platforms = ['instagram', 'facebook', 'tiktok', 'twitter']
        platform = platform.lower()
        package = package.lower()
        if platform not in valid_platforms:
            await update.message.reply_text("Platform must be: Instagram, Facebook, TikTok, Twitter.")
            return
        order_type = None
        if package in package_limits['followers'][platform]:
            order_type = 'followers'
        elif package in package_limits['likes'][platform]:
            order_type = 'likes'
        elif package in package_limits['comments'][platform]:
            order_type = 'comments'
        elif package in package_limits['bundle'][platform]:
            order_type = 'bundle'
        else:
            await update.message.reply_text(f"Invalid package! Options: {', '.join(list(package_limits['followers'][platform].keys()) + list(package_limits['likes'][platform].keys()) + list(package_limits['comments'][platform].keys()) + list(package_limits['bundle'][platform].keys()))}")
            return
        users['clients'][user_id]['handle'] = handle
        users['clients'][user_id]['platform'] = platform
        users['clients'][user_id]['package'] = package
        users['clients'][user_id]['order_type'] = order_type
        if order_type in ['likes', 'comments', 'bundle']:
            if order_type == 'likes':
                await update.message.reply_text("Send the post URL for likes (e.g., https://instagram.com/p/123).")
            elif order_type == 'comments':
                await update.message.reply_text("Send the post URL for comments (e.g., https://instagram.com/p/123).")
            else:
                await update.message.reply_text("Likes/comments on 3 recent posts by default.\nSend a URL for likes/comments or reply 'default'.")
            users['clients'][user_id]['step'] = 'awaiting_urls'
        else:
            order_id = f"{user_id}_{int(time.time())}"
            order_details = {
                'client_id': user_id,
                'handle': handle,
                'platform': platform,
                'follows_left': package_limits['followers'][platform][package],
                'likes_left': 0,
                'comments_left': 0,
                'like_url': '',
                'comment_url': '',
                'order_type': order_type,
                'use_recent_posts': False
            }
            users['clients'][user_id]['step'] = 'awaiting_payment'
            users['clients'][user_id]['order_id'] = order_id
            users['clients'][user_id]['amount'] = pricing['followers'][platform][package]
            users['clients'][user_id]['order_details'] = order_details
            keyboard = [[InlineKeyboardButton("Cancel Order", callback_data='cancel')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Got it! Boosting {package} followers for {handle} on {platform}. Use /pay to complete your order!", reply_markup=reply_markup)
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"New order from {user_id}: {handle} {platform} {package} (followers). Awaiting payment.")
        save_users()
    elif user_id in users['clients'] and users['clients'][user_id]['step'] == 'awaiting_urls':
        order_type = users['clients'][user_id]['order_type']
        handle = users['clients'][user_id]['handle']
        platform = users['clients'][user_id]['platform']
        package = users['clients'][user_id]['package']
        order_id = f"{user_id}_{int(time.time())}"
        order_details = {}
        if order_type == 'bundle':
            limits = package_limits['bundle'][platform][package]
            use_recent_posts = (text == 'default')
            if use_recent_posts:
                like_url = comment_url = "Recent posts"
            else:
                urls = text.split()
                if len(urls) == 1:
                    like_url = comment_url = urls[0]
                elif len(urls) == 2:
                    like_url, comment_url = urls[0], urls[1]
                else:
                    await update.message.reply_text("Send one URL (for both) or two URLs (likes, comments).")
                    return
                if not use_recent_posts and not (like_url.startswith('http://') or like_url.startswith('https://')):
                    await update.message.reply_text("Invalid URL! Must start with http:// or https://.")
                    return
                if not use_recent_posts and platform not in like_url.lower():
                    await update.message.reply_text(f"URL must match {platform}.")
                    return
            order_details = {
                'client_id': user_id,
                'handle': handle,
                'platform': platform,
                'follows_left': limits['follows'],
                'likes_left': limits['likes'],
                'comments_left': limits['comments'],
                'like_url': like_url,
                'comment_url': comment_url,
                'order_type': order_type,
                'use_recent_posts': use_recent_posts
            }
            amount = limits['price']
            package_display = f"{limits['follows']} followers, {limits['likes']} likes, {limits['comments']} comments"
        else:
            urls = text.split()
            if len(urls) != 1:
                await update.message.reply_text("Send exactly one URL.")
                return
            url = urls[0]
            if not (url.startswith('http://') or url.startswith('https://')):
                await update.message.reply_text("Invalid URL! Must start with http:// or https://.")
                return
            if platform not in url.lower():
                await update.message.reply_text(f"URL must match {platform}.")
                return
            if order_type == 'likes':
                order_details = {
                    'client_id': user_id,
                    'handle': handle,
                    'platform': platform,
                    'follows_left': 0,
                    'likes_left': package_limits['likes'][platform][package],
                    'comments_left': 0,
                    'like_url': url,
                    'comment_url': '',
                    'order_type': order_type,
                    'use_recent_posts': False
                }
                amount = pricing['likes'][platform][package]
                package_display = f"{package} likes"
            else:
                order_details = {
                    'client_id': user_id,
                    'handle': handle,
                    'platform': platform,
                    'follows_left': 0,
                    'likes_left': 0,
                    'comments_left': package_limits['comments'][platform][package],
                    'like_url': '',
                    'comment_url': url,
                    'order_type': order_type,
                    'use_recent_posts': False
                }
                amount = pricing['comments'][platform][package]
                package_display = f"{package} comments"
        users['clients'][user_id]['step'] = 'awaiting_payment'
        users['clients'][user_id]['order_id'] = order_id
        users['clients'][user_id]['amount'] = amount
        users['clients'][user_id]['order_details'] = order_details
        keyboard = [[InlineKeyboardButton("Cancel Order", callback_data='cancel')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"Got it! Boosting {package_display} for {handle} on {platform}. Use /pay to complete your order!", reply_markup=reply_markup)
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"New order from {user_id}: {handle} {platform} {package} ({order_type}). Awaiting payment.")
        save_users()
    elif user_id in users['clients'] and users['clients'][user_id]['step'] == 'awaiting_payment' and 'proof' in text:
        if update.message.photo:
            payment_id = f"{user_id}_{int(time.time())}"
            users['pending_payments'][payment_id] = {
                'client_id': user_id,
                'order_id': users['clients'][user_id]['order_id'],
                'order_details': users['clients'][user_id]['order_details'],
                'photo_id': update.message.photo[-1].file_id
            }
            await update.message.reply_text("Payment proof sent! Waiting for admin approval.")
            keyboard = [[InlineKeyboardButton("Approve", callback_data=f'approve_payment_{payment_id}'), InlineKeyboardButton("Reject", callback_data=f'reject_payment_{payment_id}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=update.message.photo[-1].file_id,
                                        caption=f"Payment proof from {user_id} for order: {users['clients'][user_id]['order_id']}. Amount: ₦{users['clients'][user_id]['amount']}",
                                        reply_markup=reply_markup)
            save_users()
        else:
            await update.message.reply_text("Attach a screenshot of your payment proof!")
    elif user_id in users['engagers'] and users['engagers'][user_id].get('joined') and update.message.photo:
        user_data = users['engagers'][user_id]
        if 'task_timers' not in user_data or not user_data['task_timers']:
            await update.message.reply_text("Claim a task first with /tasks!")
            return
        task_claimed = False
        for order_id in list(users['active_orders'].keys()):
            for task_type in ['f', 'l', 'c']:
                timer_key = f"{order_id}_{task_type}"
                if timer_key in user_data['task_timers']:
                    task_claimed = True
                    claim_time = user_data['task_timers'][timer_key]
                    time_spent = time.time() - claim_time
                    if task_type in ['l', 'c'] and time_spent < 60:
                        await update.message.reply_text(f"Too quick! Spend 60 seconds. You took {int(time_spent)}s.")
                        await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=update.message.photo[-1].file_id,
                                                    caption=f"Rejected task:\nEngager: {user_id}\nTask: {task_type}\nOrder: {order_id}\nTime: {int(time_spent)}s")
                        return
                    order = users['active_orders'][order_id]
                    platform = order['platform']
                    payouts = {
                        'instagram': {'f': 20, 'l': 10, 'c': 30},
                        'facebook': {'f': 30, 'l': 20, 'c': 30},
                        'tiktok': {'f': 30, 'l': 20, 'c': 40},
                        'twitter': {'f': 25, 'l': 30, 'c': 50}
                    }
                    earnings = payouts[platform][task_type]
                    user_data['earnings'] = user_data.get('earnings', 0) + earnings
                    user_data['daily_tasks']['count'] += 1
                    user_data['tasks_per_order'][order_id] = user_data['tasks_per_order'].get(order_id, 0) + 1
                    user_data['claims'].append({
                        'order_id': order_id,
                        'task_type': task_type,
                        'status': 'approved',
                        'amount': earnings,
                        'timestamp': time.time()
                    })
                    if task_type == 'f':
                        order['follows_left'] -= 1
                    elif task_type == 'l':
                        order['likes_left'] -= 1
                    elif task_type == 'c':
                        order['comments_left'] -= 1
                    if order['follows_left'] <= 0 and order['likes_left'] <= 0 and order['comments_left'] <= 0:
                        client_id = order.get('client_id')
                        if client_id:
                            await context.bot.send_message(chat_id=client_id, text=f"Your order for {order['handle']} on {order['platform']} is done! Check your account.")
                        del users['active_orders'][order_id]
                    del user_data['task_timers'][timer_key]
                    total_balance = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
                    await update.message.reply_text(f"Task approved! +₦{earnings}. Balance: ₦{total_balance}. Earn ₦{1000 - user_data.get('earnings', 0)} more to withdraw!")
                    await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=update.message.photo[-1].file_id,
                                                caption=f"Approved task:\nEngager: {user_id}\nTask: {task_type}\nOrder: {order_id}\nTime: {int(time_spent)}s")
                    save_users()
                    return
        if not task_claimed:
            await update.message.reply_text("Claim a task first with /tasks!")
    elif user_id in users['engagers'] and users['engagers'][user_id].get('awaiting_payout', False):
        account = text.strip()
        if not account.isdigit() or len(account) != 10:
            await update.message.reply_text("Invalid account! Use a 10-digit OPay number.")
            return
        earnings = users['engagers'][user_id].get('earnings', 0)
        bonus = users['engagers'][user_id].get('signup_bonus', 0)
        total_balance = earnings + bonus
        if earnings < 1000:
            await update.message.reply_text(f"Need ₦1,000 earned (excluding ₦{bonus} bonus). Current: ₦{earnings}. Keep earning!")
            return
        if total_balance > WITHDRAWAL_LIMIT:
            await update.message.reply_text(f"Trial limit is ₦{WITHDRAWAL_LIMIT}. Your balance (₦{total_balance}) is too high. Wait for the limit to lift.")
            return
        payout_id = f"{user_id}_{int(time.time())}"
        users['pending_payouts'][payout_id] = {
            'engager_id': user_id,
            'amount': total_balance,
            'account': account,
            'timestamp': time.time()
        }
        users['engagers'][user_id]['awaiting_payout'] = False
        await update.message.reply_text(f"Withdrawal request for ₦{total_balance} to {account} sent!")
        keyboard = [[InlineKeyboardButton("Approve", callback_data=f'approve_payout_{payout_id}'), InlineKeyboardButton("Reject", callback_data=f'reject_payout_{payout_id}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout request:\nEngager: {user_id}\nAmount: ₦{total_balance}\nAccount: {account}", reply_markup=reply_markup)
        save_users()

async def setup_application():
    logger.info("Initializing Application...")
    await application.initialize()
    logger.info("Application initialized successfully")

    logger.info("Registering handlers...")
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("client", client))
    application.add_handler(CommandHandler("engager", engager))
    application.add_handler(CommandHandler("tasks", tasks))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("audit", audit))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    logger.info("Handlers registered successfully")

    logger.info("Setting webhook...")
    await application.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook set successfully to {WEBHOOK_URL}")

@app.route('/')
def home():
    return "VibeLift Bot is running! Interact with the bot on Telegram."

@app.route('/webhook', methods=['GET'])
def webhook_get():
    return "This endpoint is for Telegram webhooks (POST only).", 200

@app.route('/webhook', methods=['POST'])
async def telegram_webhook():
    try:
        data = request.get_json()
        logger.info("Received Telegram webhook update: %s", data)
        update = Update.de_json(data, application.bot)
        logger.info("Parsed update: %s", update)
        logger.info("Dispatching update to handlers...")
        await application.process_update(update)
        logger.info("Update processed successfully")
        return "OK", 200
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        return "Error", 500

@app.route('/paystack-webhook', methods=['POST', 'GET'])
async def paystack_webhook():
    if request.method == 'POST':
        try:
            event = request.get_json()
            logger.info(f"Received Paystack webhook event: {event}")
            if event['event'] == 'charge.success':
                user_id = event['data']['metadata'].get('user_id')
                order_id = event['data']['metadata'].get('order_id')
                amount = event['data']['amount'] / 100
                user_id_str = str(user_id)
                if user_id_str in users['clients'] and users['clients'][user_id_str]['order_id'] == order_id:
                    order_details = users['clients'][user_id_str]['order_details']
                    order_details['client_id'] = user_id
                    logger.info(f"Paystack payment approved: Adding order {order_id} to active_orders with details {order_details}")
                    users['active_orders'][order_id] = order_details
                    users['clients'][user_id_str]['step'] = 'completed'
                    await application.bot.send_message(chat_id=user_id, text=f"Payment of ₦{amount} approved! Your order is active.")
                    await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Paystack payment of ₦{amount} from {user_id} approved for order {order_id}")
                    save_users()
            return "Webhook received", 200
        except Exception as e:
            logger.error(f"Error processing Paystack webhook: {e}")
            return "Error", 500
    else:
        html_response = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Payment Successful - VibeLiftBot</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    text-align: center;
                    padding: 50px;
                    background-color: #f4f4f4;
                }
                h1 {
                    color: #28a745;
                }
                p {
                    font-size: 18px;
                    color: #333;
                }
                a {
                    display: inline-block;
                    margin-top: 20px;
                    padding: 10px 20px;
                    background-color: #007bff;
                    color: white;
                    text-decoration: none;
                    border-radius: 5px;
                }
                a:hover {
                    background-color: #0056b3;
                }
            </style>
        </head>
        <body>
            <h1>Payment Successful!</h1>
            <p>Thank you for your payment. Your order is now active.</p>
            <p>Head back to Telegram to keep using VibeLiftBot.</p>
            <a href="https://t.me/VibeLiftBot">Return to Telegram</a>
        </body>
        </html>
        """
        return html_response, 200

async def main():
    await setup_application()
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting Flask server on port {port} with uvicorn...")
    asgi_app = WsgiToAsgi(app)
    config = uvicorn.Config(app=asgi_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Error in main function: {e}")
        raise