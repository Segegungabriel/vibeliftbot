import os
import time
import random
import uuid
import logging
import json
import asyncio
from typing import Dict, Any
from datetime import datetime, timezone
import aiohttp  # Added for Paystack API calls
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, CallbackQuery
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

# Rate limiting (relaxed for testing)
RATE_LIMITS = {
    'start': {'limit': 5, 'window': 60},
    'client': {'limit': 5, 'window': 60, 'is_signup_action': True},
    'engager': {'limit': 5, 'window': 60, 'is_signup_action': True},
    'help': {'limit': 10, 'window': 60},
    'admin': {'limit': 10, 'window': 60}
}
user_rate_limits = {}

# Package limits with updated pricing
package_limits = {
    'bundle': {
        'instagram': {
            'starter': {'follows': 25, 'likes': 50, 'comments': 20, 'price': 8000},
            'pro': {'follows': 75, 'likes': 150, 'comments': 50, 'price': 30000}
        },
        'tiktok': {
            'starter': {'follows': 25, 'likes': 50, 'comments': 20, 'price': 7000},
            'pro': {'follows': 75, 'likes': 150, 'comments': 50, 'price': 26000}
        },
        'facebook': {
            'starter': {'follows': 25, 'likes': 50, 'comments': 20, 'price': 6000},
            'pro': {'follows': 75, 'likes': 150, 'comments': 50, 'price': 22000}
        },
        'twitter': {
            'starter': {'follows': 25, 'likes': 50, 'comments': 20, 'price': 5000},
            'pro': {'follows': 75, 'likes': 150, 'comments': 50, 'price': 18000}
        }
    },
    'custom_rates': {  # Per-unit rates for custom orders
        'instagram': 50,
        'tiktok': 45,
        'facebook': 35,
        'twitter': 40
    }
}

# Custom follow prices per handle
custom_follow_prices = {
    '@myhandle': 60,  # ₦60 per follow for @myhandle
    'https://instagram.com/username': 50,  # ₦50 per follow for this URL
}

# Daily tips
daily_tips = [
    "✨ Boost your vibe: Post at 6-9 PM for max likes! ⏰",
    "🤓 Pro tip: Use trending hashtags to skyrocket your reach!",
    "😸 Fun fact: Liking cat pics is a universal mood-lifter!",
    "💡 Engage back: Reply to comments to keep the love flowing!",
    "🎯 Consistency is key—post daily to grow your crew!"
]

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

def generate_referral_code(user_id: str) -> str:
    return f"VIBE{user_id}"

# Witty reply variations
witty_rate_limit = [
    "Whoa, speed demon! Chill for a sec! ⏳",
    "Easy, tiger! Give it a breather! 😸",
    "Too fast, hotshot! Take a chill pill! 😎"
]

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")
    if update and (update.message or update.callback_query):
        chat_id = update.effective_chat.id
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text="Oops, I tripped over a wire! 😜 Try again or hit up support!"
            )
        except Exception as e:
            logger.warning(f"Failed to send error message: {e}")

# Start command with referral support
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /start command from user {user_id}")
    if not check_rate_limit(user_id, action='start'):
        reply = random.choice(witty_rate_limit)
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(reply)
        else:
            await update.message.reply_text(reply)
        return

    # Check for referral code
    referral_code = context.args[0] if context.args else None
    if referral_code and referral_code.startswith("VIBE"):
        referrer_id = referral_code[4:]
        if referrer_id in users['referrals'] and referrer_id != user_id:
            users['referrals'][user_id] = {'referred_by': referrer_id}
            await save_users()
            bonus_msg = "🎉 Sweet! You’ve snagged a bonus thanks to your pal’s code!"
        else:
            bonus_msg = "🤔 Hmm, that code’s a mystery—let’s roll without it!"
    else:
        bonus_msg = ""

    keyboard = [
        [InlineKeyboardButton("Join as Client", callback_data='client')],
        [InlineKeyboardButton("Join as Engager", callback_data='engager')],
        [InlineKeyboardButton("Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = (
        f"Well, well, look who’s here to vibe! 🚀\n"
        f"Boost your socials or stack some cash—what’s your jam?\n"
        f"{bonus_msg}\n"
        "Pick your squad:"
    )
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    logger.info(f"Sent /start response to user {user_id}")

# Client command
async def client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /client command from user {user_id}")
    if not check_rate_limit(user_id, action='client', is_signup_action=True):
        reply = random.choice(witty_rate_limit)
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(reply)
        else:
            await update.message.reply_text(reply)
        return
    if user_id in users['engagers']:
        message_text = "You’re already an engager, fam! 💼 Switch gears with /cancel or roll with /engager."
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
                "Yo, you’ve got an order waiting to get paid! 💰\n"
                "[*Order* ➡️ Payment ➡️ Approval ➡️ Active]\n"
                "Hit /pay to seal the deal or /cancel to bail."
            )
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                await query.message.edit_text(message_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(message_text, parse_mode='Markdown')
            return
        elif client_data['step'] == 'awaiting_approval':
            message_text = (
                "Your order’s in the VIP line for admin approval! ⏳\n"
                "[Order ➡️ Payment ➡️ *Approval* ➡️ Active]\n"
                "Hang tight—check /status for updates!"
            )
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                await query.message.edit_text(message_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(message_text, parse_mode='Markdown')
            return
        elif client_data['step'] == 'awaiting_order':
            platform = client_data['platform']
            bundles = "\n".join(
                f"- *{k.capitalize()}*: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
                for k, v in package_limits['bundle'][platform].items()
            )
            message_text = (
                f"Time to boost *{platform.capitalize()}*! 🚀\n"
                "[*Order* ➡️ Payment ➡️ Approval ➡️ Active]\n"
                f"Pick your vibe:\n{bundles}\n"
                "*How to order:*\n"
                "1. *Handle + Bundle* ➡️ `@myhandle starter`\n"
                "2. *URL + Bundle* ➡️ `https://instagram.com/username pro`\n"
                "3. *Package + Pic* ➡️ `package starter` + 📸\n"
                "4. *Custom + Pic* ➡️ `username, 20 follows, 30 likes, 20 comments` + 📸\n"
                "Custom limits: 10-500. Pics optional for 1 & 2."
            )
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                await query.message.edit_text(message_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(message_text, parse_mode='Markdown')
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
    message_text = "Which platform are we juicing up today? 🎯"
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
        reply = random.choice(witty_rate_limit)
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(reply)
        else:
            await update.message.reply_text(reply)
        return
    if user_id in users['clients']:
        message_text = "You’re a client already, boss! 🌟 Switch it up with /cancel or stick to /client."
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
        message_text = "Back in the engager game, huh? 💼 Pick your move:"
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
        'awaiting_payout': False,
        'level': 1,  # Gamification: Start at level 1
        'xp': 0      # Experience points for tasks
    }
    referral_bonus = users['referrals'].get(user_id, {}).get('referred_by')
    if referral_bonus:
        users['engagers'][user_id]['signup_bonus'] += 300  # Bonus for referred engagers
    keyboard = [
        [InlineKeyboardButton("See Tasks", callback_data='tasks')],
        [InlineKeyboardButton("Check Balance", callback_data='balance')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = (
        "🎉 Welcome to the engager squad, newbie! You’ve scored a ₦500 signup bonus! 💰\n"
        f"{'+ ₦300 referral bonus! 🎁' if referral_bonus else ''}"
        "Ready to hustle? Pick an action:"
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
        reply = random.choice(witty_rate_limit)
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.message.edit_text(reply)
        else:
            await update.message.reply_text(reply)
        return
    keyboard = [
        [InlineKeyboardButton("How to Order", callback_data='help_order')],
        [InlineKeyboardButton("How to Earn", callback_data='help_earn')],
        [InlineKeyboardButton("Check Status", callback_data='help_status')],
        [InlineKeyboardButton("Support", callback_data='help_support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = (
        "Lost in the vibe? 🤓 I’ve got your back!\n"
        "Pick your lifeline:"
    )
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.edit_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)
    logger.info(f"Help sent to user {user_id}")

# Referral command
async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /refer command from user {user_id}")
    referral_code = users['referrals'].get(user_id, {}).get('code', generate_referral_code(user_id))
    if user_id not in users['referrals']:
        users['referrals'][user_id] = {'code': referral_code, 'referred': [], 'earnings': 0}
        await save_users()
    referred_count = len(users['referrals'][user_id]['referred'])
    earnings = users['referrals'][user_id]['earnings']
    message_text = (
        f"Spread the vibe and stack cash! 🎁\n"
        f"Your code: *{referral_code}*\n"
        f"Share: 'Join with /start {referral_code} for a bonus!'\n"
        f"Friends joined: {referred_count} | Earnings: ₦{earnings}\n"
        "Score ₦500 per pal who jumps in and gets active!"
    )
    await update.message.reply_text(message_text, parse_mode='Markdown')
    logger.info(f"Referral info sent to user {user_id}")

# Leaderboard command
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /leaderboard command from user {user_id}")
    # Top 5 engagers by XP
    top_engagers = sorted(
        users['engagers'].items(),
        key=lambda x: x[1].get('xp', 0),
        reverse=True
    )[:5]
    leaderboard_text = "🏆 *Vibelift Legends* 🏆\n"
    for i, (uid, data) in enumerate(top_engagers, 1):
        level = data.get('level', 1)
        xp = data.get('xp', 0)
        leaderboard_text += f"{i}. User {uid} - Level {level} (XP: {xp}) 🌟\n"
    your_xp = users['engagers'][user_id].get('xp', 0) if user_id in users['engagers'] else 0
    your_level = users['engagers'][user_id].get('level', 1) if user_id in users['engagers'] else 1
    leaderboard_text += f"\nYou: Level {your_level} (XP: {your_xp})—keep climbing! 🚀"
    await update.message.reply_text(leaderboard_text, parse_mode='Markdown')
    logger.info(f"Leaderboard sent to user {user_id}")

# Paystack API integration
async def initialize_paystack_transaction(order_id: str, amount: int, email: str) -> Dict[str, Any]:
    url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "amount": amount * 100,  # Convert to kobo
        "email": email,
        "reference": order_id,
        "callback_url": "https://vibeliftbot.onrender.com/static/success.html"  # User redirect only
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            logger.info(f"Paystack init response: {response.status} - {await response.text()}")
            if response.status != 200:
                logger.error(f"Paystack API error: {await response.text()}")
                return {"error": "Failed to initialize transaction"}
            return await response.json()

# Pay command
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /pay command from user {user_id}")
    if user_id not in users['clients'] or users['clients'][user_id]['step'] != 'awaiting_payment':
        await update.message.reply_text("No order to pay for yet, fam! 🌟 Start with /client!")
        return
    client_data = users['clients'][user_id]
    order_id = client_data['order_id']
    order = users['pending_orders'][order_id]
    amount = order['price'] * 100  # Paystack uses kobo
    payment_data = {
        "amount": amount,
        "email": f"{user_id}@vibeliftbot.com",  # Dummy email
        "reference": order_id,
        "callback_url": f"https://vibeliftbot.onrender.com/static/success.html"
    }
    response = await initialize_paystack_transaction(payment_data)
    if "error" in response or not response.get("data"):
        logger.error(f"Paystack API error: {response}")
        # If duplicate reference, generate a new order_id
        if response.get("code") == "duplicate_reference":
            new_order_id = str(uuid.uuid4())
            order['reference'] = new_order_id  # Update reference
            users['pending_orders'][new_order_id] = order
            del users['pending_orders'][order_id]
            client_data['order_id'] = new_order_id
            payment_data["reference"] = new_order_id
            response = await initialize_paystack_transaction(payment_data)
            if "error" in response or not response.get("data"):
                await update.message.reply_text("Payment’s tripping! 😵 Try again or hit up support!")
                return
            order_id = new_order_id
        else:
            await update.message.reply_text("Payment’s tripping! 😵 Try again or hit up support!")
            return
    auth_url = response["data"]["authorization_url"]
    await update.message.reply_text(
        f"Time to make it rain! 💸\n"
        f"Order *{order_id}*: ₦{order['price']}\n"
        f"[Pay Here]({auth_url})",
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

# Withdraw command
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /withdraw command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("You’re not an engager yet, fam! 💼 Join with /engager!")
        return
    user_data = users['engagers'][user_id]
    if user_data.get('awaiting_payout'):
        await update.message.reply_text("Hold up—your payout’s already in the queue! ⏳ Chill and wait!")
        return
    total_earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
    if total_earnings < 1000:
        await update.message.reply_text("Need at least ₦1000 to cash out, hustler! 🏆 Keep grinding!")
        return
    user_data['awaiting_payout'] = True
    await save_users()
    await update.message.reply_text(
        f"Your ₦{total_earnings} withdrawal is in the VIP line for review! 💸\n"
        "We’ll ping you when it’s a done deal!"
    )
    # Notify admin in group chat
    message = (
        f"💰 *Payout Request Alert* 💰\n"
        f"Engager ID: {user_id}\n"
        f"Amount: ₦{total_earnings}"
    )
    keyboard = [
        [InlineKeyboardButton("Approve ✅", callback_data=f"approve_payout_{user_id}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"reject_payout_{user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await application.bot.send_message(
            chat_id=REVIEW_GROUP_CHAT_ID,
            text=message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.warning(f"Failed to notify review group: {e}")

# Paystack verification
async def verify_paystack_transaction(reference: str) -> Dict[str, Any]:
    url = f"https://api.paystack.co/transaction/verify/{reference}"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            logger.info(f"Paystack verify response: {response.status} - {await response.text()}")
            if response.status != 200:
                logger.error(f"Paystack verification error: {await response.text()}")
                return {"error": "Failed to verify transaction"}
            return await response.json()

# Paystack webhook endpoint
@app.route('/paystack-webhook', methods=['POST'])
async def paystack_webhook():
    payload = request.get_json()
    logger.info(f"Paystack webhook received with payload: {json.dumps(payload, indent=2)}")
    if not payload or payload.get('event') != 'charge.success':
        logger.info(f"Ignoring webhook event: {payload.get('event', 'no event')}")
        return jsonify({"status": "ignored"}), 200
    reference = payload['data']['reference']
    logger.info(f"Processing webhook for reference: {reference}")
    verification = await verify_paystack_transaction(reference)
    if "error" in verification or not verification.get("status") or verification["data"]["status"] != "success":
        logger.error(f"Payment verification failed for {reference}: {verification}")
        return jsonify({"status": "error"}), 400
    order_id = reference
    if order_id not in users['pending_orders']:
        logger.warning(f"Order {order_id} not found in pending_orders")
        return jsonify({"status": "order_not_found"}), 404
    order = users['pending_orders'][order_id]
    client_id = order['client_id']
    users['clients'][client_id]['step'] = 'awaiting_approval'
    await save_users()
    # Notify client
    try:
        await application.bot.send_message(
            chat_id=int(client_id),
            text=(
                f"🎉 Cha-ching! Your payment for order *{order_id}* is golden! 💰\n"
                "[Order ➡️ Payment ➡️ *Approval* ➡️ Active]\n"
                "Admins are on it—check /status for updates!"
            ),
            parse_mode='Markdown'
        )
        logger.info(f"Notified client {client_id} of payment success")
    except Exception as e:
        logger.warning(f"Failed to notify client {client_id}: {e} ⚠️")
    # Send to review group (unchanged, just for context)
    order_message = (
        f"🌟 *New Order Up for Grabs* (ID: {order_id}) 🌟\n"
        f"Client ID: {client_id}\n"
        f"Platform: {order['platform'].capitalize()}\n"
        f"Handle/URL: {order['handle_or_url']}\n"
        f"Follows: {order['follows']} | Likes: {order['likes']} | Comments: {order['comments']}\n"
        f"Price: ₦{order['price']}"
    )
    keyboard = [
        [InlineKeyboardButton("Approve ✅", callback_data=f"admin_approve_order_{order_id}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"admin_reject_order_{order_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        if 'screenshot' in order and order['screenshot']:
            await application.bot.send_photo(
                chat_id=REVIEW_GROUP_CHAT_ID,
                photo=order['screenshot'],
                caption=order_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await application.bot.send_message(
                chat_id=REVIEW_GROUP_CHAT_ID,
                text=order_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        logger.info(f"Sent order {order_id} to review group")
    except Exception as e:
        logger.warning(f"Failed to notify review group: {e} ⚠️")
    return jsonify({"status": "success"}), 200

# /paystack-webhook debug
@app.route('/paystack-webhook', methods=['GET'])
async def paystack_webhook_debug():
    logger.info(f"Debug: Received GET request to /paystack-webhook with args: {request.args}")
    return jsonify({"status": "error", "message": "Use POST for Paystack webhooks!"}), 405

# Admin dashboard helper
async def update_admin_dashboard(query: CallbackQuery) -> None:
    message = "🛠️ *Admin Command Center* 🛠️\n\n"
    message += "📊 *Pending Orders*:\n"
    pending_orders = users.get('pending_orders', {})
    message += f"{len(pending_orders)} waiting\n" if pending_orders else "All clear! ✅\n"
    message += "\n📋 *Pending Tasks*:\n"
    pending_tasks = users.get('pending_task_completions', {})
    message += f"{len(pending_tasks)} up for review\n" if pending_tasks else "Nada here! ✅\n"
    message += "\n💸 *Pending Payouts*:\n"
    pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
    message += f"{len(pending_payouts)} ready\n" if pending_payouts else "No cash-outs yet! ✅\n"
    message += "\n🚀 *Active Orders*:\n"
    active_orders = users.get('active_orders', {})
    message += f"{len(active_orders)} in flight\n" if active_orders else "All quiet! ✅\n"
    keyboard = [
        [InlineKeyboardButton("Approve Order ✅", callback_data="admin_approve_order"),
         InlineKeyboardButton("Reject Order ❌", callback_data="admin_reject_order")],
        [InlineKeyboardButton("Approve Task ✅", callback_data="admin_approve_task"),
         InlineKeyboardButton("Reject Task ❌", callback_data="admin_reject_task")],
        [InlineKeyboardButton("Approve Payout ✅", callback_data="admin_approve_payout"),
         InlineKeyboardButton("Reject Payout ❌", callback_data="admin_reject_payout")],
        [InlineKeyboardButton("Set Priority ⏫", callback_data="admin_set_priority"),
         InlineKeyboardButton("Cancel Order 🚫", callback_data="admin_cancel_order")],
        [InlineKeyboardButton("Generate Code 🎟️", callback_data="admin_generate_code")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to update admin dashboard: {e}")

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
            f"- *{k.capitalize()}*: {v['follows']} follows, {v['likes']} likes, {v['comments']} comments (₦{v['price']})"
            for k, v in package_limits['bundle'][platform].items()
        )
        await query.message.edit_text(
            f"Locked in *{platform.capitalize()}*! 🚀\n"
            "[*Order* ➡️ Payment ➡️ Approval ➡️ Active]\n"
            f"Pick your vibe:\n{bundles}\n"
            "*How to order:*\n"
            "1. *Handle + Bundle* ➡️ `@myhandle starter`\n"
            "2. *URL + Bundle* ➡️ `https://instagram.com/username pro`\n"
            "3. *Package + Pic* ➡️ `package starter` + 📸\n"
            "4. *Custom + Pic* ➡️ `username, 20 follows, 30 likes, 20 comments` + 📸\n"
            "Custom limits: 10-500. Pics optional for 1 & 2.",
            parse_mode='Markdown'
        )
    elif data.startswith('task_'):
        await handle_task_button(query, user_id, user_id_str, data)
    elif data.startswith('help_'):
        await handle_help_button(query, data)
    elif data.startswith('cancel_'):
        await handle_cancel_button(query, user_id_str, data)
    elif data.startswith('admin_') or data.startswith('approve_payout_') or data.startswith('reject_payout_') or data.startswith('priority_') or data.startswith('cancel_order_'):
        await handle_admin_button(query, user_id, user_id_str, data)

# Handle help button
async def handle_help_button(query: CallbackQuery, data: str) -> None:
    action = data.split('_')[1]
    if action == 'order':
        await query.message.edit_text(
            "🌟 *How to Order Like a Pro* 🌟\n"
            "1. Hit /client and pick a platform 🎯\n"
            "2. Send your order (e.g., `@myhandle starter`) ➡️\n"
            "3. Pay up with /pay 💰\n"
            "4. Wait for admin magic—track it with /status! ✨",
            parse_mode='Markdown'
        )
    elif action == 'earn':
        await query.message.edit_text(
            "💼 *How to Stack Cash* 💼\n"
            "1. Join with /engager 🏆\n"
            "2. Grab tasks with /tasks ⏰\n"
            "3. Submit proof—admins review it! ✅\n"
            "4. Cash out with /withdraw when you hit ₦1000! 💸",
            parse_mode='Markdown'
        )
    elif action == 'status':
        await query.message.edit_text(
            "🔍 *Check Your Vibe* 🔍\n"
            "Just type /status to see where your order’s at! 🚀\n"
            "From payment to active—it’s all there!",
            parse_mode='Markdown'
        )
    elif action == 'support':
        await query.message.edit_text(
            "🆘 *Need a Hero?* 🆘\n"
            "Drop a line to [Your Support Link] and we’ll swoop in! 😎",
            parse_mode='Markdown'
        )

# Handle cancel button
async def handle_cancel_button(query: CallbackQuery, user_id: str, data: str) -> None:
    action = data.split('_')[1]
    if action == 'yes':
        if user_id in users['clients']:
            client_data = users['clients'][user_id]
            if client_data['step'] in ['awaiting_payment', 'awaiting_approval']:
                order_id = client_data.get('order_id')
                if order_id and order_id in users['pending_orders']:
                    del users['pending_orders'][order_id]
                del users['clients'][user_id]
                await save_users()
                await query.message.edit_text("Order wiped out! 🚫 Start fresh with /client!")
            else:
                await query.message.edit_text("Nothing to ditch here! 😏 Kick off with /client!")
        elif user_id in users['engagers']:
            del users['engagers'][user_id]
            await save_users()
            await query.message.edit_text("You’re out of the engager club! 🎬 Rejoin with /engager!")
        else:
            await query.message.edit_text("Nothing to cancel, fam! 🌟 Pick a role with /start!")
    elif action == 'no':
        await query.message.edit_text("Phew, crisis averted! 😅 Back to business—try /client or /engager!")

# Handle task button with gamification
async def handle_task_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    action = data.split('_')[1]
    task_id = data.split('_')[-1]

    if action == 'claim':
        if task_id not in users['active_orders']:
            await query.message.edit_text("Task’s gone poof! 🚫 Check /tasks for fresh ones!")
            return
        if task_id in users['engagers'][user_id_str].get('claims', []):
            await query.message.edit_text("You’ve already nabbed this one, sneaky! 😏")
            return
        order = users['active_orders'][task_id]
        platform = order['platform']
        task_type = random.choice(['follow', 'like', 'comment'])
        task_earnings = 20
        xp_gain = 10  # Gamification: Earn XP per task

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
        users['engagers'][user_id_str]['xp'] = users['engagers'][user_id_str].get('xp', 0) + xp_gain
        level = users['engagers'][user_id_str]['level']
        xp = users['engagers'][user_id_str]['xp']
        if xp >= level * 50:  # Level up every 50 XP per level
            users['engagers'][user_id_str]['level'] += 1
            await application.bot.send_message(
                chat_id=user_id,
                text=f"🎉 Level Up! You’re now Level {level + 1}, legend! 🌟 Keep slaying!"
            )
        await save_users()

        task_message = (
            f"📋 *Task Up for Review* (ID: {completion_id}) 📋\n"
            f"Engager ID: {user_id_str}\n"
            f"Task ID: {task_id}\n"
            f"Platform: {platform.capitalize()}\n"
            f"Task: {task_type.capitalize()}\n"
            f"Earnings: ₦{task_earnings} | XP: {xp_gain}"
        )
        keyboard = [
            [InlineKeyboardButton("Approve ✅", callback_data=f"admin_approve_task_{completion_id}"),
             InlineKeyboardButton("Reject ❌", callback_data=f"admin_reject_task_{completion_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await application.bot.send_message(
                chat_id=REVIEW_GROUP_CHAT_ID,
                text=task_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.warning(f"Failed to notify review group: {e} ⚠️")

        await query.message.edit_text(
            f"Task submitted! ⏳\n"
            "[Claimed ➡️ *Review* ➡️ Earnings]\n"
            f"XP +{xp_gain}—check your rank with /leaderboard!"
        )

# Handle admin button
# Replace the entire handle_admin_button in Part 2
async def handle_admin_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != str(ADMIN_USER_ID):
        await query.message.edit_text("Admin zone, fam! 🛡️ No entry unless you’re the boss!")
        return
    action = data.split('_', 2)[-1] if '_' in data else data
    logger.info(f"Admin action triggered: {data}")
    try:
        if action == 'approve_order':
            if not users.get('pending_orders'):
                await query.message.edit_text("No orders in the queue, chief! ✅ All quiet!")
                logger.info("No pending orders to approve")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"Order {order_id}", callback_data=f'admin_approve_order_{order_id}')]
                    for order_id in users['pending_orders'].keys()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Pick an order to green-light! 🚀", reply_markup=reply_markup)
                logger.info("Sent order approval options")
        elif action == 'reject_order':
            if not users.get('pending_orders'):
                await query.message.edit_text("Nada to nix here! ✅ Queue’s empty!")
                logger.info("No pending orders to reject")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"Order {order_id}", callback_data=f'admin_reject_order_{order_id}')]
                    for order_id in users['pending_orders'].keys()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Which order’s getting the boot? 🚫", reply_markup=reply_markup)
                logger.info("Sent order rejection options")
        elif action == 'approve_task':
            if not users.get('pending_task_completions'):
                await query.message.edit_text("No tasks waiting, boss! ✅ All done!")
                logger.info("No pending tasks to approve")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"Task {completion_id}", callback_data=f'admin_approve_task_{completion_id}')]
                    for completion_id in users['pending_task_completions'].keys()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Which task gets the thumbs-up? 👍", reply_markup=reply_markup)
                logger.info("Sent task approval options")
        elif action == 'reject_task':
            if not users.get('pending_task_completions'):
                await query.message.edit_text("No tasks to toss! ✅ All clear!")
                logger.info("No pending tasks to reject")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"Task {completion_id}", callback_data=f'admin_reject_task_{completion_id}')]
                    for completion_id in users['pending_task_completions'].keys()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Which task’s outta here? 🚫", reply_markup=reply_markup)
                logger.info("Sent task rejection options")
        elif action == 'approve_payout':
            pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
            if not pending_payouts:
                await query.message.edit_text("No payouts to bless! ✅ Cash flow’s chill!")
                logger.info("No pending payouts to approve")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"User {uid}: ₦{v['earnings'] + v['signup_bonus']}", callback_data=f'approve_payout_{uid}')]
                    for uid, v in pending_payouts.items()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Who’s getting paid today? 💸", reply_markup=reply_markup)
                logger.info("Sent payout approval options")
        elif action == 'reject_payout':
            pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
            if not pending_payouts:
                await query.message.edit_text("No payouts to deny! ✅ All good!")
                logger.info("No pending payouts to reject")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"User {uid}: ₦{v['earnings'] + v['signup_bonus']}", callback_data=f'reject_payout_{uid}')]
                    for uid, v in pending_payouts.items()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Who’s payout’s getting the axe? 🚫", reply_markup=reply_markup)
                logger.info("Sent payout rejection options")
        elif action == 'set_priority':
            if not users.get('active_orders'):
                await query.message.edit_text("No orders to juice up! ✅ All quiet!")
                logger.info("No active orders to prioritize")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"Order {order_id}", callback_data=f'priority_{order_id}')]
                    for order_id in users['active_orders'].keys()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Which order’s jumping the line? ⏫", reply_markup=reply_markup)
                logger.info("Sent priority options")
        elif action == 'cancel_order':
            if not users.get('active_orders'):
                await query.message.edit_text("No orders to zap! ✅ All chill!")
                logger.info("No active orders to cancel")
            else:
                keyboard = [
                    [InlineKeyboardButton(f"Order {order_id}", callback_data=f'cancel_order_{order_id}')]
                    for order_id in users['active_orders'].keys()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("Which order’s biting the dust? 🚫", reply_markup=reply_markup)
                logger.info("Sent cancel order options")
        elif action == 'generate_code':
            code = generate_admin_code()
            users['pending_admin_actions'][code] = {'type': 'admin_code', 'used': False}
            await save_users()
            await query.message.edit_text(
                f"🎟️ Fresh admin code: *{code}*\n"
                "Perfect for bonuses or VIP tricks—use it wisely!"
            )
            logger.info(f"Generated admin code: {code}")
            await update_admin_dashboard(query)
        elif data.startswith('admin_approve_order_'):
            order_id = data.split('_', 3)[3]
            if order_id in users['pending_orders']:
                order = users['pending_orders'].pop(order_id)
                client_id = order['client_id']
                users['active_orders'][order_id] = order
                if str(client_id) in users['clients']:
                    users['clients'][str(client_id)]['step'] = 'completed'
                await save_users()
                await query.message.edit_text(f"Order *{order_id}* is live—boom! 💥")
                logger.info(f"Approved order {order_id}")
                await application.bot.send_message(int(client_id), f"🎉 Your order *{order_id}* is approved and rolling! 🚀 Check /status!")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"Order *{order_id}* ghosted us! 👻 Already handled?")
                logger.info(f"Order {order_id} not found for approval")
        elif data.startswith('admin_reject_order_'):
            order_id = data.split('_', 3)[3]
            if order_id in users['pending_orders']:
                order = users['pending_orders'].pop(order_id)
                client_id = order['client_id']
                if str(client_id) in users['clients']:
                    del users['clients'][str(client_id)]
                await save_users()
                await query.message.edit_text(f"Order *{order_id}* axed! 🚫 Tough call, boss!")
                logger.info(f"Rejected order {order_id}")
                await application.bot.send_message(int(client_id), f"😕 Your order *{order_id}* got the boot—hit up support or retry with /client!")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"Order *{order_id}* vanished! 👻 Already sorted?")
                logger.info(f"Order {order_id} not found for rejection")
        elif data.startswith('admin_approve_task_'):
            completion_id = data.split('_', 3)[3]
            if completion_id in users['pending_task_completions']:
                completion = users['pending_task_completions'].pop(completion_id)
                engager_id = completion['engager_id']
                task_id = completion['task_id']
                earnings = completion['earnings']
                users['engagers'][engager_id]['earnings'] = users['engagers'][engager_id].get('earnings', 0) + earnings
                if task_id in users['active_orders']:
                    order = users['active_orders'][task_id]
                    task_type = completion['task_type']
                    if task_type in order and order[task_type] > 0:
                        order[task_type] -= 1
                        if all(order.get(metric, 0) == 0 for metric in ['follows', 'likes', 'comments']):
                            users['active_orders'].pop(task_id)
                            client_id = order['client_id']
                            try:
                                await application.bot.send_message(
                                    chat_id=int(client_id),
                                    text=f"🎉 Your order *{task_id}* is fully vibed out—donezo!"
                                )
                                logger.info(f"Notified client {client_id} of order completion")
                            except Exception as e:
                                logger.warning(f"Failed to notify client {client_id}: {e} ⚠️")
                await save_users()
                await query.message.edit_text(f"Task *{completion_id}* approved—{engager_id} scores ₦{earnings}! 💰")
                logger.info(f"Approved task {completion_id} for {engager_id}")
                try:
                    await application.bot.send_message(
                        chat_id=int(engager_id),
                        text=f"🏆 Task *{task_id}* approved! You bagged ₦{earnings}—check /balance, rockstar!"
                    )
                    logger.info(f"Notified engager {engager_id} of task approval")
                except Exception as e:
                    logger.warning(f"Failed to notify engager {engager_id}: {e} ⚠️")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"Task *{completion_id}* slipped away! 👻 Already handled?")
                logger.info(f"Task {completion_id} not found for approval")
        elif data.startswith('admin_reject_task_'):
            completion_id = data.split('_', 3)[3]
            if completion_id in users['pending_task_completions']:
                completion = users['pending_task_completions'].pop(completion_id)
                engager_id = completion['engager_id']
                task_id = completion['task_id']
                if task_id in users['engagers'][engager_id].get('claims', []):
                    users['engagers'][engager_id]['claims'].remove(task_id)
                await save_users()
                await query.message.edit_text(f"Task *{completion_id}* nixed! 🚫 Back to the drawing board!")
                logger.info(f"Rejected task {completion_id}")
                try:
                    await application.bot.send_message(
                        chat_id=int(engager_id),
                        text=f"😬 Task *{task_id}* got rejected—chat with support for the tea!"
                    )
                    logger.info(f"Notified engager {engager_id} of task rejection")
                except Exception as e:
                    logger.warning(f"Failed to notify engager {engager_id}: {e} ⚠️")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"Task *{completion_id}* ghosted! 👻 Already sorted?")
                logger.info(f"Task {completion_id} not found for rejection")
        elif data.startswith('approve_payout_'):
            target_user_id = data.split('_', 2)[2]
            if target_user_id in users['engagers']:
                user_data = users['engagers'][target_user_id]
                if not user_data.get('awaiting_payout'):
                    await query.message.edit_text(f"No payout pending for *{target_user_id}*! 🤔 All good?")
                    logger.info(f"No payout to approve for {target_user_id}")
                    return
                amount = user_data['earnings'] + user_data['signup_bonus']
                user_data['earnings'] = 0
                user_data['signup_bonus'] = 0
                user_data['awaiting_payout'] = False
                await save_users()
                await query.message.edit_text(f"Payout of ₦{amount} for *{target_user_id}* sent—cha-ching! 💸")
                logger.info(f"Approved payout of ₦{amount} for {target_user_id}")
                try:
                    await application.bot.send_message(
                        chat_id=int(target_user_id),
                        text=f"💰 Your ₦{amount} payout just dropped—check your bank, baller!"
                    )
                    logger.info(f"Notified {target_user_id} of payout approval")
                except Exception as e:
                    logger.warning(f"Failed to notify engager {target_user_id}: {e} ⚠️")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"User *{target_user_id}* is MIA! 👻")
                logger.info(f"User {target_user_id} not found for payout approval")
        elif data.startswith('reject_payout_'):
            target_user_id = data.split('_', 2)[2]
            if target_user_id in users['engagers']:
                user_data = users['engagers'][target_user_id]
                if not user_data.get('awaiting_payout'):
                    await query.message.edit_text(f"No payout to reject for *{target_user_id}*! 🤔 All clear?")
                    logger.info(f"No payout to reject for {target_user_id}")
                    return
                user_data['awaiting_payout'] = False
                await save_users()
                await query.message.edit_text(f"Payout for *{target_user_id}* denied! 🚫 Tough love!")
                logger.info(f"Rejected payout for {target_user_id}")
                try:
                    await application.bot.send_message(
                        chat_id=int(target_user_id),
                        text=f"😕 Your payout got a no-go—hit up support for deets!"
                    )
                    logger.info(f"Notified {target_user_id} of payout rejection")
                except Exception as e:
                    logger.warning(f"Failed to notify engager {target_user_id}: {e} ⚠️")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"User *{target_user_id}* vanished! 👻")
                logger.info(f"User {target_user_id} not found for payout rejection")
        elif data.startswith('priority_'):
            order_id = data.split('_', 1)[1]
            if order_id in users['active_orders']:
                users['active_orders'][order_id]['priority'] = True
                await save_users()
                await query.message.edit_text(f"Order *{order_id}* bumped to the front—VIP style! ⏫")
                logger.info(f"Set priority for order {order_id}")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"Order *{order_id}* slipped through! 👻 Already gone?")
                logger.info(f"Order {order_id} not found for priority")
        elif data.startswith('cancel_order_'):
            order_id = data.split('_', 2)[2]
            if order_id in users['active_orders']:
                order = users['active_orders'].pop(order_id)
                client_id = order['client_id']
                await save_users()
                await query.message.edit_text(f"Order *{order_id}* zapped—gone for good! 🚫")
                logger.info(f"Canceled order {order_id}")
                try:
                    await application.bot.send_message(
                        chat_id=int(client_id),
                        text=f"😱 Your order *{order_id}* got canceled by the boss—reach out to support!"
                    )
                    logger.info(f"Notified client {client_id} of order cancellation")
                except Exception as e:
                    logger.warning(f"Failed to notify client {client_id}: {e} ⚠️")
                await update_admin_dashboard(query)
            else:
                await query.message.edit_text(f"Order *{order_id}* already outta here! 👻")
                logger.info(f"Order {order_id} not found for cancellation")
    except Exception as e:
        logger.error(f"Admin button error: {e}")
        await application.bot.send_message(chat_id=user_id, text=f"Button’s acting up! 😵 Error: {str(e)}")

# Part 3

# Status command
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /status command from user {user_id}")
    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        step = client_data['step']
        if step == 'select_platform':
            await update.message.reply_text("You’re just picking a platform, fam! 🎯 Finish with /client!")
        elif step == 'awaiting_order':
            await update.message.reply_text(
                f"Order time for *{client_data['platform'].capitalize()}*! 🚀\n"
                "[*Order* ➡️ Payment ➡️ Approval ➡️ Active]\n"
                "Send your details—check /client for the how-to!"
            )
        elif step == 'awaiting_payment':
            order_id = client_data['order_id']
            order = users['pending_orders'][order_id]
            await update.message.reply_text(
                f"Order *{order_id}* is waiting on your wallet! 💰\n"
                "[Order ➡️ *Payment* ➡️ Approval ➡️ Active]\n"
                f"Total: ₦{order['price']}—hit /pay to make it rain!"
            )
        elif step == 'awaiting_approval':
            order_id = client_data['order_id']
            order = users['pending_orders'][order_id]
            await update.message.reply_text(
                f"Order *{order_id}* is in the admin’s hands! ⏳\n"
                "[Order ➡️ Payment ➡️ *Approval* ➡️ Active]\n"
                f"Boosting {order['platform'].capitalize()}—hang tight!"
            )
        elif step == 'completed':
            order_id = client_data['order_id']
            if order_id in users['active_orders']:
                order = users['active_orders'][order_id]
                await update.message.reply_text(
                    f"Order *{order_id}* is live and popping! 🚀\n"
                    "[Order ➡️ Payment ➡️ Approval ➡️ *Active*]\n"
                    f"Follows: {order['follows']} | Likes: {order['likes']} | Comments: {order['comments']}"
                )
            else:
                await update.message.reply_text(
                    f"Order *{order_id}* is all wrapped up—vibe achieved! 🎉\n"
                    "Start a new one with /client!"
                )
    elif user_id in users['engagers']:
        user_data = users['engagers'][user_id]
        earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
        level = user_data.get('level', 1)
        xp = user_data.get('xp', 0)
        await update.message.reply_text(
            f"Engager status, rockstar! 🌟\n"
            f"Level: {level} | XP: {xp} (Next level: {level * 50})\n"
            f"Cash: ₦{earnings}—cash out at ₦1000 with /withdraw!\n"
            "Grab more tasks with /tasks!"
        )
    else:
        await update.message.reply_text("No status yet, newbie! 😏 Pick a role with /start!")
    logger.info(f"Sent status to user {user_id}")

# Tasks command
async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /tasks command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("Join the engager crew first! 💼 Use /engager to jump in!")
        return
    active_orders = users.get('active_orders', {})
    if not active_orders:
        await update.message.reply_text("No tasks up for grabs right now! ⏰ Check back soon!")
        return
    keyboard = [
        [InlineKeyboardButton(f"Task {task_id} - ₦20", callback_data=f"task_claim_{task_id}")]
        for task_id in active_orders.keys()
        if task_id not in users['engagers'][user_id].get('claims', [])
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏆 *Task Time!* 🏆\n"
        "Snag a task, earn ₦20 + 10 XP—let’s hustle!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Cancel command
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /cancel command from user {user_id}")
    if user_id not in users['clients'] and user_id not in users['engagers']:
        await update.message.reply_text("Nothing to ditch, fam! 🌟 Start with /start!")
        return
    keyboard = [
        [InlineKeyboardButton("Yes ✅", callback_data=f"cancel_yes"),
         InlineKeyboardButton("No ❌", callback_data=f"cancel_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "You sure, boss? 😏 This wipes your current gig!",
        reply_markup=reply_markup
    )

# Order command
async def order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /order command from user {user_id}")
    if user_id not in users['clients']:
        await update.message.reply_text("No order vibes yet! 🌟 Kick off with /client!")
        return
    client_data = users['clients'][user_id]
    if client_data['step'] in ['select_platform', 'awaiting_order']:
        await client(update, context)
    else:
        await status(update, context)

# Admin command
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /admin command from user {user_id}")
    if user_id not in ADMINS:
        await update.message.reply_text("Admin zone, fam! 🛡️ No entry unless you’re the boss!")
        return
    if not check_rate_limit(str(user_id), action='admin'):
        await update.message.reply_text(random.choice(witty_rate_limit))
        return
    message = "🛠️ *Admin Command Center* 🛠️\n\n"
    message += "📊 *Pending Orders*:\n"
    pending_orders = users.get('pending_orders', {})
    message += f"{len(pending_orders)} waiting\n" if pending_orders else "All clear! ✅\n"
    message += "\n📋 *Pending Tasks*:\n"
    pending_tasks = users.get('pending_task_completions', {})
    message += f"{len(pending_tasks)} up for review\n" if pending_tasks else "Nada here! ✅\n"
    message += "\n💸 *Pending Payouts*:\n"
    pending_payouts = {k: v for k, v in users['engagers'].items() if v.get('awaiting_payout')}
    if pending_payouts:
        message += f"{len(pending_payouts)} ready to roll:\n"
        for uid in pending_payouts:
            amount = pending_payouts[uid]['earnings'] + pending_payouts[uid]['signup_bonus']
            message += f"- User {uid}: ₦{amount}\n"
    else:
        message += "No cash-outs yet! ✅\n"
    message += "\n🚀 *Active Orders*:\n"
    active_orders = users.get('active_orders', {})
    message += f"{len(active_orders)} in flight\n" if active_orders else "All quiet! ✅\n"
    keyboard = [
        [InlineKeyboardButton("Approve Order ✅", callback_data="admin_approve_order"),
         InlineKeyboardButton("Reject Order ❌", callback_data="admin_reject_order")],
        [InlineKeyboardButton("Approve Task ✅", callback_data="admin_approve_task"),
         InlineKeyboardButton("Reject Task ❌", callback_data="admin_reject_task")],
        [InlineKeyboardButton("Approve Payout ✅", callback_data="admin_approve_payout"),
         InlineKeyboardButton("Reject Payout ❌", callback_data="admin_reject_payout")],
        [InlineKeyboardButton("Set Priority ⏫", callback_data="admin_set_priority"),
         InlineKeyboardButton("Cancel Order 🚫", callback_data="admin_cancel_order")],
        [InlineKeyboardButton("Generate Code 🎟️", callback_data="admin_generate_code")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

# Balance command
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /balance command from user {user_id}")
    if user_id not in users['engagers']:
        await update.message.reply_text("No wallet yet, champ! 💼 Join with /engager!")
        return
    user_data = users['engagers'][user_id]
    total_earnings = user_data.get('earnings', 0) + user_data.get('signup_bonus', 0)
    level = user_data.get('level', 1)
    xp = user_data.get('xp', 0)
    await update.message.reply_text(
        f"💰 *Your Vibe Vault* 💰\n"
        f"Level: {level} | XP: {xp}\n"
        f"Cash: ₦{total_earnings}\n"
        f"Hit /withdraw when you’re at ₦1000, baller!"
    )

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

            if text:
                if text.startswith('@') or text.startswith('http'):
                    parts = text.split()
                    if len(parts) != 2:
                        await message.reply_text(
                            "🤔 Nope! Use: `@myhandle starter` or `https://instagram.com/username pro`"
                        )
                        return
                    handle_or_url, bundle = parts
                    if bundle not in package_limits['bundle'][platform]:
                        await message.reply_text(
                            f"🚫 Bad bundle! Try: {', '.join(package_limits['bundle'][platform].keys())}"
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
                    if not photo:
                        await message.reply_text("📸 Pic required for packages—snap it!")
                        return
                    parts = text.split()
                    if len(parts) != 2 or parts[0] != 'package':
                        await message.reply_text("🤔 Huh? Use: `package pro` with a pic!")
                        return
                    bundle = parts[1]
                    if bundle not in package_limits['bundle'][platform]:
                        await message.reply_text(
                            f"🚫 Wrong bundle! Options: {', '.join(package_limits['bundle'][platform].keys())}"
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
                    if not photo:
                        await message.reply_text("📸 Custom orders need a pic—snap it!")
                        return
                    parts = text.split(',')
                    if len(parts) != 4:
                        await message.reply_text(
                            "🤓 Format’s off! Try: `username, 20 follows, 30 likes, 20 comments`"
                        )
                        return
                    username = parts[0].strip()
                    try:
                        follows = int(parts[1].split()[0])
                        likes = int(parts[2].split()[0])
                        comments = int(parts[3].split()[0])
                        if not (10 <= follows <= 500 and 10 <= likes <= 500 and 10 <= comments <= 500):
                            await message.reply_text("🚫 Limits are 10-500—keep it real!")
                            return
                        base_rate = package_limits['custom_rates'][platform]
                        follow_price = custom_follow_prices.get(username, base_rate)
                        price = (follows * follow_price) + (likes * base_rate) + (comments * base_rate)
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
                            "🤔 Messed up! Use: `username, 20 follows, 30 likes, 20 comments`"
                        )
                        return

            if order_details:
                users['pending_orders'][order_id] = order_details
                client_data['step'] = 'awaiting_payment'
                client_data['order_id'] = order_id
                await save_users()
                await message.reply_text(
                    f"Order *{order_id}* locked in! Total: ₦{order_details['price']} 💰\n"
                    "[*Order* ➡️ Payment ➡️ Approval ➡️ Active]\n"
                    "Drop the cash with /pay—let’s roll!"
                )
                return
    await message.reply_text(
        "Lost in the sauce? 😜 Hit /start to pick a role or /help for the scoop!"
    )

# Root route for health checks
@app.route('/', methods=['GET', 'HEAD'])
async def root():
    return jsonify({"status": "Vibeliftbot’s alive and kicking! 🚀"}), 200

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
    reference = request.args.get('reference', request.args.get('trxref', ''))
    if not reference or reference not in users['pending_orders']:
        logger.warning(f"Success page hit with invalid/missing reference: {request.args}")
        return Response("Oops, order’s lost in the vibe! 🚫 Check /status or retry with /client!", status=400)
    order_id = reference
    order = users['pending_orders'][order_id]
    client_id = order['client_id']
    # Move to awaiting_approval (temp fix)
    users['clients'][client_id]['step'] = 'awaiting_approval'
    await save_users()
    # Notify client
    try:
        await application.bot.send_message(
            chat_id=int(client_id),
            text=f"🎉 Cha-ching! Your payment for order *{order_id}* is golden! 💰\n[Order ➡️ Payment ➡️ *Approval* ➡️ Active]\nAdmins are on it—check /status!"
        )
        logger.info(f"Fallback: Notified client {client_id} from success page")
    except Exception as e:
        logger.warning(f"Fallback notification failed for {client_id}: {e} ⚠️")
    # Notify admin group
    order_message = (
        f"🌟 *New Order Up for Grabs* (ID: {order_id}) 🌟\n"
        f"Client ID: {client_id}\n"
        f"Platform: {order['platform'].capitalize()}\n"
        f"Handle/URL: {order['handle_or_url']}\n"
        f"Follows: {order['follows']} | Likes: {order['likes']} | Comments: {order['comments']}\n"
        f"Price: ₦{order['price']}"
    )
    keyboard = [
        [InlineKeyboardButton("Approve ✅", callback_data=f"admin_approve_order_{order_id}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"admin_reject_order_{order_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        if 'screenshot' in order and order['screenshot']:
            await application.bot.send_photo(
                chat_id=REVIEW_GROUP_CHAT_ID,
                photo=order['screenshot'],
                caption=order_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await application.bot.send_message(
                chat_id=REVIEW_GROUP_CHAT_ID,
                text=order_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        logger.info(f"Fallback: Sent order {order_id} to review group {REVIEW_GROUP_CHAT_ID}")
    except Exception as e:
        logger.warning(f"Failed to notify review group {REVIEW_GROUP_CHAT_ID}: {e} ⚠️")
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
        <h1>Payment Successful! 🎉</h1>
        <p>Order *{order_id}* is locked and loaded—admins are on it! ✅</p>
        <p>Back to the bot for the full scoop!</p>
        <a href="https://t.me/{application.bot.username}?start=payment_success_{order_id}">Back to Vibeliftbot 🚀</a>
    </body>
    </html>
    """
    logger.info(f"Serving success page for order {order_id}")
    return Response(html_content, mimetype='text/html')

# Daily tips scheduler
async def send_daily_tips():
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now.hour >= 9:
            next_run = next_run.replace(day=now.day + 1)
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        tip = random.choice(daily_tips)
        for user_id in set(users['clients'].keys()) | set(users['engagers'].keys()):
            if users.get('daily_tip', {}).get(user_id, 0) != now.day:
                try:
                    await application.bot.send_message(
                        chat_id=int(user_id),
                        text=f"{tip}\nCatch you tomorrow for more vibes! 😎",
                        parse_mode='Markdown'
                    )
                    if 'daily_tip' not in users:
                        users['daily_tip'] = {}
                    users['daily_tip'][user_id] = now.day
                    await save_users()
                except Exception as e:
                    logger.warning(f"Failed to send tip to {user_id}: {e} ⚠️")

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
    if 'active_orders' not in users:
        users['active_orders'] = {}
    if 'pending_task_completions' not in users:
        users['pending_task_completions'] = {}
    if 'pending_admin_actions' not in users:
        users['pending_admin_actions'] = {}
    if 'referrals' not in users:
        users['referrals'] = {}
    if 'daily_tip' not in users:
        users['daily_tip'] = {}

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
    application.add_handler(CommandHandler("refer", refer))
    application.add_handler(CommandHandler("leaderboard", leaderboard))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    application.add_error_handler(error_handler)

    # Initialize the application
    await application.initialize()
    logger.info("Application initialized successfully—let’s vibe!")

    # Set up webhook
    await application.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

    # Start daily tips scheduler
    asyncio.create_task(send_daily_tips())
    logger.info("Daily tips scheduler fired up! ✨")

    # Wrap Flask app for ASGI
    asgi_app = WsgiToAsgi(app)

    # Run the bot and Flask app together
    config = uvicorn.Config(
        asgi_app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())