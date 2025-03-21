import os
import json
import time
import logging
import asyncio
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

# Paystack API headers
PAYSTACK_HEADERS = {
    "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
    "Content-Type": "application/json"
}

# Bot token, admin IDs
TOKEN = TELEGRAM_TOKEN or '7637213737:AAHz9Kvcxj-UZhDlKyyhc9fqoD51JBSsViA'
ADMIN_USER_ID = '1518439839'
ADMIN_GROUP_ID = '-4762253610'

# Initialize users dictionary
users = {
    'clients': {}, 'engagers': {}, 'pending_tasks': {}, 'last_interaction': {},
    'active_orders': {}, 'pending_payouts': {}, 'pending_payments': {}
}
try:
    with open('users.json', 'r') as f:
        loaded_users = json.load(f)
        users.update(loaded_users)
        if 'last_interaction' not in users:
            users['last_interaction'] = {}
        if 'pending_payments' not in users:
            users['pending_payments'] = {}
except FileNotFoundError:
    logger.info("users.json not found, starting with empty users dictionary")

def save_users():
    try:
        with open('users.json', 'w') as f:
            json.dump(users, f)
    except Exception as e:
        logger.error(f"Error saving users.json: {e}")

def check_rate_limit(user_id, is_signup_action=False):
    user_id_str = str(user_id)
    current_time = time.time()
    last_time = users['last_interaction'].get(user_id_str, 0)
    if is_signup_action:
        users['last_interaction'][user_id_str] = current_time
        save_users()
        return True
    if current_time - last_time < 2:
        return False
    users['last_interaction'][user_id_str] = current_time
    save_users()
    return True

# Initialize the Application object at the module level
logger.info("Building Application object...")
application = Application.builder().token(TOKEN).build()
logger.info("Application object built successfully")

# Define all handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("Received /start command from user: %s", update.message.from_user.id)
        user_id = update.message.from_user.id
        if not check_rate_limit(user_id, is_signup_action=True):
            await update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
            return
        keyboard = [
            [InlineKeyboardButton("Grow My Account", callback_data='client')],
            [InlineKeyboardButton("Earn Cash", callback_data='engager')],
            [InlineKeyboardButton("Help", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Welcome to VibeLift! Boost your vibe or earn cash—your choice!", reply_markup=reply_markup)
        logger.info("Sent /start response to user: %s", user_id)
    except Exception as e:
        logger.error(f"Error in start handler for user {update.message.from_user.id}: {e}")
        raise

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        await context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [
        [InlineKeyboardButton("Client Guide", callback_data='client_guide')],
        [InlineKeyboardButton("Engager Guide", callback_data='engager_guide')],
        [InlineKeyboardButton("Contact Support", callback_data='contact_support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=user_id, text="Welcome to VibeLift Help! 🚀\nBoost your social media or earn cash!\nHow can we assist?", reply_markup=reply_markup)

async def client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        await context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [
        [InlineKeyboardButton("Get Followers", callback_data='get_followers')],
        [InlineKeyboardButton("Get Likes", callback_data='get_likes')],
        [InlineKeyboardButton("Get Comments", callback_data='get_comments')],
        [InlineKeyboardButton("Get a Bundle", callback_data='get_bundle')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=user_id, text="Grow your account with real vibes!\nSupported Platforms: Facebook, Twitter/X, Instagram, TikTok\nWhat would you like to boost?", reply_markup=reply_markup)
    users['clients'][str(user_id)] = {'step': 'select_package'}
    save_users()

async def engager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        await context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [[InlineKeyboardButton("Join Now", callback_data='join')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=user_id, text="Earn ₦100-₦1,250 daily lifting vibes!\nClick to join:", reply_markup=reply_markup)

async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        await update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) not in users['engagers'] or not users['engagers'][str(user_id)].get('joined'):
        await update.message.reply_text("Join first! Type /engager or click Earn Cash from /start.")
        return
    user_data = users['engagers'][str(user_id)]
    current_time = time.time()
    if 'daily_tasks' not in user_data:
        user_data['daily_tasks'] = {'count': 0, 'last_reset': current_time}
    elif current_time - user_data['daily_tasks']['last_reset'] >= 86400:
        user_data['daily_tasks'] = {'count': 0, 'last_reset': current_time}
    if user_data['daily_tasks']['count'] >= 25:
        await update.message.reply_text("You’ve reached your daily task limit of 25. Come back tomorrow!")
        return
    if 'tasks_per_order' not in user_data:
        user_data['tasks_per_order'] = {}
    keyboard = []
    for order_id, order in users['active_orders'].items():
        platform = order['platform']
        handle = order['handle']
        payouts = {
            'instagram': {'follow': 20, 'like': 10, 'comment': 30},
            'facebook': {'follow': 30, 'like': 20, 'comment': 30},
            'tiktok': {'follow': 30, 'like': 20, 'comment': 40},
            'twitter': {'follow': 25, 'like': 30, 'comment': 50}
        }
        payout = payouts[platform]
        order_tasks = user_data['tasks_per_order'].get(order_id, 0)
        if order_tasks >= 5:
            continue
        if order['follows_left'] > 0:
            keyboard.append([InlineKeyboardButton(f"Follow {handle} on {platform} (₦{payout['follow']})", callback_data=f'task_f_{order_id}')])
        if order['likes_left'] > 0:
            text = f"Like post on {platform} (₦{payout['like']})" if not order.get('use_recent_posts') else f"Like 3 recent posts by {handle} on {platform} (₦{payout['like']} each)"
            keyboard.append([InlineKeyboardButton(text, callback_data=f'task_l_{order_id}')])
        if order['comments_left'] > 0:
            text = f"Comment on post on {platform} (₦{payout['comment']})" if not order.get('use_recent_posts') else f"Comment on 3 recent posts by {handle} on {platform} (₦{payout['comment']} each)"
            keyboard.append([InlineKeyboardButton(text, callback_data=f'task_c_{order_id}')])
    if not keyboard:
        await update.message.reply_text("No tasks available right now. Check back soon!")
        return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Pick a task (send screenshot after):\nLikes and comments require 60 seconds on the post!", reply_markup=reply_markup)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        await update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) in users['engagers'] and users['engagers'][str(user_id)].get('joined'):
        earnings = users['engagers'][str(user_id)]['earnings']
        keyboard = [[InlineKeyboardButton("Withdraw Earnings", callback_data='withdraw')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"Your VibeLift balance: ₦{earnings}", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Join as an engager first! Type /engager.")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        await update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) not in users['engagers'] or not users['engagers'][str(user_id)].get('joined'):
        await update.message.reply_text("Join as an engager first! Type /engager.")
        return
    earnings = users['engagers'][str(user_id)]['earnings']
    if earnings < 1000:
        await update.message.reply_text("Minimum withdrawal is ₦1,000. Keep earning!")
        return
    await update.message.reply_text("Reply with your OPay account number to request withdrawal (e.g., 8101234567).")
    users['engagers'][str(user_id)]['awaiting_payout'] = True
    save_users()

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        await update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) not in users['clients'] or users['clients'][str(user_id)]['step'] != 'awaiting_payment':
        await update.message.reply_text("Start an order first with /client!")
        return
    email = f"{user_id}@vibeliftbot.com"
    amount = users['clients'][str(user_id)]['amount'] * 100  # Convert to kobo
    payload = {
        "email": email,
        "amount": amount,
        "callback_url": PAYSTACK_WEBHOOK_URL,
        "metadata": {"user_id": user_id, "order_id": users['clients'][str(user_id)]['order_id']}
    }
    try:
        response = requests.post("https://api.paystack.co/transaction/initialize", json=payload, headers=PAYSTACK_HEADERS)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Paystack API response: {data}")
        if data["status"]:
            payment_url = data["data"]["authorization_url"]
            keyboard = [[InlineKeyboardButton(f"Pay ₦{users['clients'][str(user_id)]['amount']}", url=payment_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Click to pay via Paystack:", reply_markup=reply_markup)
        else:
            error_message = data.get("message", "Unknown error")
            logger.error(f"Paystack API error: {error_message}")
            await update.message.reply_text(f"Payment initiation failed: {error_message}. Try again.")
    except Exception as e:
        logger.error(f"Error initiating payment: {e}")
        await update.message.reply_text("An error occurred. Try again later.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    is_signup_action = data in ['client', 'engager', 'join', 'get_followers', 'get_likes', 'get_comments', 'get_bundle', 'help', 'client_guide', 'engager_guide', 'contact_support', 'back_to_help']
    if not check_rate_limit(user_id, is_signup_action=is_signup_action):
        await context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    if data == 'client':
        await client(update, context)
    elif data == 'engager':
        await engager(update, context)
    elif data == 'help':
        await help_command(update, context)
    elif data == 'client_guide':
        keyboard = [[InlineKeyboardButton("Back to Help", callback_data='help')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=user_id, text="Client Guide:\n- Use /client to start your order.\n- Pay to: 8101062411 OPay Oluwasegun Okusanya or use /pay.", reply_markup=reply_markup)
    elif data == 'engager_guide':
        keyboard = [[InlineKeyboardButton("Back to Help", callback_data='help')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=user_id, text="Engager Guide:\n- Use /engager to join.\n- Earn ₦10-₦50 per task with /tasks.", reply_markup=reply_markup)
    elif data == 'contact_support':
        keyboard = [[InlineKeyboardButton("Back to Help", callback_data='help')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=user_id, text="Contact Support:\nEmail us at vibelift@gmail.com!", reply_markup=reply_markup)
    elif data == 'back_to_help':
        await help_command(update, context)
    elif data == 'get_followers':
        await context.bot.send_message(chat_id=user_id, text="Follower Packages:\n- Instagram: 10 for ₦1,200 | 50 for ₦6,000 | 100 for ₦12,000\n- Facebook: 10 for ₦1,500 | 50 for ₦7,500 | 100 for ₦15,000\n- TikTok: 10 for ₦1,800 | 50 for ₦9,000 | 100 for ₦18,000\n- Twitter: 10 for ₦800 | 50 for ₦4,000 | 100 for ₦8,000\nReply with: handle platform package")
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'followers'}
        save_users()
    elif data == 'get_likes':
        await context.bot.send_message(chat_id=user_id, text="Like Packages:\n- Instagram: 20 for ₦600 | 100 for ₦3,000 | 200 for ₦6,000\n- Facebook: 20 for ₦1,800 | 100 for ₦9,000 | 200 for ₦18,000\n- TikTok: 20 for ₦1,800 | 100 for ₦9,000 | 200 for ₦18,000\n- Twitter: 20 for ₦1,800 | 100 for ₦9,000 | 200 for ₦18,000\nReply with: handle platform package")
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'likes'}
        save_users()
    elif data == 'get_comments':
        await context.bot.send_message(chat_id=user_id, text="Comment Packages:\n- Instagram: 5 for ₦300 | 10 for ₦600 | 50 for ₦3,000\n- Facebook: 5 for ₦300 | 10 for ₦600 | 50 for ₦3,000\n- TikTok: 5 for ₦600 | 10 for ₦1,200 | 50 for ₦6,000\n- Twitter: 5 for ₦600 | 10 for ₦1,200 | 50 for ₦6,000\nReply with: handle platform package")
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'comments'}
        save_users()
    elif data == 'get_bundle':
        await context.bot.send_message(chat_id=user_id, text="Bundle Packages:\nInstagram:\n- Starter (10 followers, 20 likes, 5 comments): ₦1,890\n- Pro (50 followers, 100 likes, 10 comments): ₦8,640\n- Elite (100 followers, 200 likes, 50 comments): ₦18,900\nFacebook:\n- Starter: ₦3,240\n- Pro: ₦15,390\n- Elite: ₦32,400\nTikTok:\n- Starter: ₦3,780\n- Pro: ₦17,280\n- Elite: ₦37,800\nTwitter:\n- Starter: ₦2,880\n- Pro: ₦12,780\n- Elite: ₦28,800\nReply with: handle platform bundle")
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'bundle'}
        save_users()
    elif data == 'join':
        users['engagers'][str(user_id)] = {'joined': True, 'earnings': 0, 'task_timers': {}, 'awaiting_payout': False, 'daily_tasks': {'count': 0, 'last_reset': time.time()}, 'tasks_per_order': {}}
        keyboard = [[InlineKeyboardButton("See Tasks", callback_data='tasks'), InlineKeyboardButton("Check Balance", callback_data='balance')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=user_id, text="You’re in! Pick an option:", reply_markup=reply_markup)
        save_users()
    elif data.startswith('task_'):
        task_parts = data.split('_')
        task_type, order_id = task_parts[1], task_parts[2]
        if order_id not in users['active_orders']:
            await context.bot.send_message(chat_id=user_id, text="Task no longer available!")
            return
        order = users['active_orders'][order_id]
        timer_key = f"{order_id}_{task_type}"
        users['engagers'][str(user_id)]['task_timers'][timer_key] = time.time()
        if task_type == 'f':
            await context.bot.send_message(chat_id=user_id, text=f"Follow {order['handle']} on {order['platform']} and submit proof!")
        elif task_type == 'l':
            if order.get('use_recent_posts'):
                await context.bot.send_message(chat_id=user_id, text=f"Like the 3 most recent posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each!")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"Like this post: {order['like_url']}\nSpend 60 seconds before submitting proof!")
        elif task_type == 'c':
            if order.get('use_recent_posts'):
                await context.bot.send_message(chat_id=user_id, text=f"Comment on the 3 most recent posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each!")
            else:
                await context.bot.send_message(chat_id=user_id, text=f"Comment on this post: {order['comment_url']}\nSpend 60 seconds before submitting proof!")
        save_users()
    elif data == 'tasks':
        await tasks(update, context)
    elif data == 'balance':
        await balance(update, context)
    elif data == 'withdraw':
        await withdraw(update, context)
    elif data.startswith('approve_payout_') and str(user_id) == ADMIN_USER_ID:
        payout_id = data.split('_')[2]
        if payout_id in users['pending_payouts']:
            payout = users['pending_payouts'][payout_id]
            engager_id = payout['engager_id']
            amount = payout['amount']
            account = payout['account']
            users['engagers'][engager_id]['earnings'] -= amount
            del users['pending_payouts'][payout_id]
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout of ₦{amount} to {account} for {engager_id} approved. Process payment now!")
            await context.bot.send_message(chat_id=engager_id, text=f"Your withdrawal of ₦{amount} to {account} approved!")
            save_users()
    elif data.startswith('reject_payout_') and str(user_id) == ADMIN_USER_ID:
        payout_id = data.split('_')[2]
        if payout_id in users['pending_payouts']:
            engager_id = users['pending_payouts'][payout_id]['engager_id']
            del users['pending_payouts'][payout_id]
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout request from {engager_id} rejected.")
            await context.bot.send_message(chat_id=engager_id, text="Your withdrawal was rejected. Contact support!")
            save_users()
    elif data.startswith('approve_payment_') and str(user_id) == ADMIN_USER_ID:
        payment_id = data.split('_')[2]
        if payment_id in users['pending_payments']:
            payment = users['pending_payments'][payment_id]
            client_id = payment['client_id']
            order_id = payment['order_id']
            users['active_orders'][order_id] = payment['order_details']
            del users['pending_payments'][payment_id]
            if str(client_id) in users['clients']:
                users['clients'][str(client_id)]['step'] = 'completed'
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payment for order {order_id} from {client_id} approved. Tasks now active!")
            await context.bot.send_message(chat_id=client_id, text="Your payment approved! Results in 4-5 hours for small orders.")
            save_users()
    elif data.startswith('reject_payment_') and str(user_id) == ADMIN_USER_ID:
        payment_id = data.split('_')[2]
        if payment_id in users['pending_payments']:
            payment = users['pending_payments'][payment_id]
            client_id = payment['client_id']
            del users['pending_payments'][payment_id]
            if str(client_id) in users['clients']:
                del users['clients'][str(client_id)]
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payment for order from {client_id} rejected.")
            await context.bot.send_message(chat_id=client_id, text="Your payment was rejected. Try again with /client.")
            save_users()
    elif data == 'cancel':
        if str(user_id) in users['clients']:
            del users['clients'][str(user_id)]
            await context.bot.send_message(chat_id=user_id, text="Order canceled. Start over with /client!")
            save_users()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = update.message.text.lower() if update.message.text else ""
    is_signup_action = (
        (user_id in users['clients'] and users['clients'][user_id]['step'] in ['awaiting_order', 'awaiting_urls', 'awaiting_payment']) or
        (user_id in users['engagers'] and 'awaiting_payout' in users['engagers'][user_id])
    )
    if not check_rate_limit(update.message.from_user.id, is_signup_action=is_signup_action):
        await context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    if str(update.message.chat_id) == ADMIN_GROUP_ID and str(user_id) != ADMIN_USER_ID:
        return

    # Define package limits and pricing
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
            await context.bot.send_message(chat_id=user_id, text="Please include handle, platform, and package (e.g., @NaijaFashion Instagram 10).")
            return
        handle, platform, package = parts[0], parts[1], parts[2]
        order_type = users['clients'][user_id]['order_type']
        valid_platforms = ['instagram', 'facebook', 'tiktok', 'twitter']
        if platform.lower() not in valid_platforms:
            await context.bot.send_message(chat_id=user_id, text="Invalid platform! Supported: Instagram, Facebook, TikTok, Twitter.")
            return
        if order_type == 'bundle':
            if package.lower() not in package_limits['bundle'][platform.lower()]:
                await context.bot.send_message(chat_id=user_id, text="Invalid bundle! Use Starter, Pro, or Elite.")
                return
        else:
            if package.lower() not in package_limits[order_type][platform.lower()]:
                await context.bot.send_message(chat_id=user_id, text=f"Invalid package! Available: {', '.join(package_limits[order_type][platform.lower()].keys())}.")
                return
        users['clients'][user_id]['handle'] = handle
        users['clients'][user_id]['platform'] = platform.lower()
        users['clients'][user_id]['package'] = package.lower()
        if order_type in ['likes', 'comments', 'bundle']:
            if order_type == 'likes':
                await context.bot.send_message(chat_id=user_id, text="Provide the post URL for likes (e.g., https://instagram.com/p/123).")
            elif order_type == 'comments':
                await context.bot.send_message(chat_id=user_id, text="Provide the post URL for comments (e.g., https://instagram.com/p/123).")
            else:
                await context.bot.send_message(chat_id=user_id, text="Likes/comments on 3 recent posts by default.\nSpecify URL for likes/comments or reply 'default'.")
            users['clients'][user_id]['step'] = 'awaiting_urls'
        else:
            order_id = f"{user_id}_{int(time.time())}"
            order_details = {
                'client_id': user_id,
                'handle': handle,
                'platform': platform.lower(),
                'follows_left': package_limits['followers'][platform.lower()][package.lower()],
                'likes_left': 0,
                'comments_left': 0,
                'like_url': '',
                'comment_url': '',
                'order_type': order_type,
                'use_recent_posts': False
            }
            users['clients'][user_id]['step'] = 'awaiting_payment'
            users['clients'][user_id]['order_id'] = order_id
            users['clients'][user_id]['amount'] = pricing['followers'][platform.lower()][package.lower()]
            users['clients'][user_id]['order_details'] = order_details
            keyboard = [[InlineKeyboardButton("Cancel Order", callback_data='cancel')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(chat_id=user_id, text=f"Order received! Pay ₦{users['clients'][user_id]['amount']} to: 8101062411 OPay or use /pay.", reply_markup=reply_markup)
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
                    await context.bot.send_message(chat_id=user_id, text="Provide one URL (for both) or two URLs (likes, comments).")
                    return
                if not use_recent_posts and not (like_url.startswith('http://') or like_url.startswith('https://')):
                    await context.bot.send_message(chat_id=user_id, text="Invalid URL! Must start with http:// or https://.")
                    return
                if not use_recent_posts and platform not in like_url.lower():
                    await context.bot.send_message(chat_id=user_id, text=f"URL must match {platform}.")
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
        else:
            urls = text.split()
            if len(urls) != 1:
                await context.bot.send_message(chat_id=user_id, text="Please provide exactly one URL.")
                return
            url = urls[0]
            if not (url.startswith('http://') or url.startswith('https://')):
                await context.bot.send_message(chat_id=user_id, text="Invalid URL! Must start with http:// or https://.")
                return
            if platform not in url.lower():
                await context.bot.send_message(chat_id=user_id, text=f"URL must match {platform}.")
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
        users['clients'][user_id]['step'] = 'awaiting_payment'
        users['clients'][user_id]['order_id'] = order_id
        users['clients'][user_id]['amount'] = amount
        users['clients'][user_id]['order_details'] = order_details
        keyboard = [[InlineKeyboardButton("Cancel Order", callback_data='cancel')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=user_id, text=f"Order received! Pay ₦{amount} to: 8101062411 OPay or use /pay.", reply_markup=reply_markup)
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
            await context.bot.send_message(chat_id=user_id, text="Payment proof submitted! Awaiting admin approval.")
            keyboard = [[InlineKeyboardButton("Approve", callback_data=f'approve_payment_{payment_id}'), InlineKeyboardButton("Reject", callback_data=f'reject_payment_{payment_id}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=update.message.photo[-1].file_id,
                                        caption=f"Payment proof from {user_id} for order: {users['clients'][user_id]['order_id']}. Amount: ₦{users['clients'][user_id]['amount']}",
                                        reply_markup=reply_markup)
            save_users()
        else:
            await context.bot.send_message(chat_id=user_id, text="Please include a screenshot of your payment proof!")
    elif user_id in users['engagers'] and users['engagers'][user_id].get('joined') and update.message.photo:
        user_data = users['engagers'][user_id]
        for order_id in list(users['active_orders'].keys()):
            for task_type in ['f', 'l', 'c']:
                timer_key = f"{order_id}_{task_type}"
                if timer_key in user_data['task_timers']:
                    claim_time = user_data['task_timers'][timer_key]
                    time_spent = time.time() - claim_time
                    if task_type in ['l', 'c'] and time_spent < 60:
                        await context.bot.send_message(chat_id=user_id, text=f"Too fast! Spend 60 seconds. Only {int(time_spent)}s elapsed.")
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
                    user_data['earnings'] += earnings
                    user_data['daily_tasks']['count'] += 1
                    user_data['tasks_per_order'][order_id] = user_data['tasks_per_order'].get(order_id, 0) + 1
                    if task_type == 'f':
                        order['follows_left'] -= 1
                    elif task_type == 'l':
                        order['likes_left'] -= 1
                    elif task_type == 'c':
                        order['comments_left'] -= 1
                    if order['follows_left'] <= 0 and order['likes_left'] <= 0 and order['comments_left'] <= 0:
                        client_id = order.get('client_id')
                        if client_id:
                            await context.bot.send_message(chat_id=client_id, text=f"Your order for {order['handle']} on {order['platform']} has been completed! Check your account for the results.")
                        del users['active_orders'][order_id]
                    del user_data['task_timers'][timer_key]
                    await context.bot.send_message(chat_id=user_id, text=f"Task auto-approved! +₦{earnings}. Balance: ₦{user_data['earnings']}.")
                    await context.bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=update.message.photo[-1].file_id,
                                                caption=f"Auto-approved task:\nEngager: {user_id}\nTask: {task_type}\nOrder: {order_id}\nTime: {int(time_spent)}s")
                    save_users()
                    return
        await context.bot.send_message(chat_id=user_id, text="Claim a task first with /tasks!")
    elif user_id in users['engagers'] and users['engagers'][user_id].get('awaiting_payout', False):
        account = text.strip()
        if not account.isdigit() or len(account) != 10:
            await context.bot.send_message(chat_id=user_id, text="Invalid account number! Provide a 10-digit OPay number.")
            return
        earnings = users['engagers'][user_id]['earnings']
        if earnings < 1000:
            await context.bot.send_message(chat_id=user_id, text="Minimum withdrawal is ₦1,000. Keep earning!")
            return
        payout_id = f"{user_id}_{int(time.time())}"
        users['pending_payouts'][payout_id] = {'engager_id': user_id, 'amount': earnings, 'account': account}
        users['engagers'][user_id]['awaiting_payout'] = False
        await context.bot.send_message(chat_id=user_id, text=f"Withdrawal request for ₦{earnings} to {account} submitted!")
        keyboard = [[InlineKeyboardButton("Approve", callback_data=f'approve_payout_{payout_id}'), InlineKeyboardButton("Reject", callback_data=f'reject_payout_{payout_id}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout request:\nEngager: {user_id}\nAmount: ₦{earnings}\nAccount: {account}", reply_markup=reply_markup)
        save_users()

# Async setup function to initialize the application, register handlers, and set the webhook
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
                        if str(user_id) in users['clients'] and users['clients'][str(user_id)]['order_id'] == order_id:
                            order_details = users['clients'][str(user_id)]['order_details']
                            order_details['client_id'] = user_id
                            users['active_orders'][order_id] = order_details
                            users['clients'][str(user_id)]['step'] = 'completed'
                            await application.bot.send_message(chat_id=user_id, text=f"Payment of ₦{amount} approved! Your order is active.")
                            await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Paystack payment of ₦{amount} from {user_id} approved for order {order_id}")
                            save_users()
                    return "Webhook received", 200
                except Exception as e:
                    logger.error(f"Error processing Paystack webhook: {e}")
                    return "Error", 500
            else:  # GET request
                # Return an HTML page for the callback redirect
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
                    <p>You can return to Telegram to continue using VibeLiftBot.</p>
                    <a href="https://t.me/VibeLiftBot">Return to Telegram</a>
                </body>
                </html>
                """
                return html_response, 200

async def main():
    # Run the async setup
    await setup_application()

    # Start the Flask app with uvicorn, wrapped with WsgiToAsgi
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