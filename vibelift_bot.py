import os
import time
import logging
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from datetime import datetime
import json
import random
import string
import requests
import hmac
import hashlib
from flask import Flask, request
from pymongo import MongoClient

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot token and admin details
BOT_TOKEN = os.getenv("BOT_TOKEN")
logger.info(f"BOT_TOKEN value: {BOT_TOKEN}")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set. Please set it in your environment or Render dashboard.")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WITHDRAWAL_LIMIT = 5000

# Paystack headers
PAYSTACK_HEADERS = {
    "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
    "Content-Type": "application/json"
}

# Rate limiting
RATE_LIMIT = 2  # seconds between commands
user_last_command = {}

# MongoDB setup
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("MONGODB_URI environment variable not set. Please set it in your environment or Render dashboard.")
client = MongoClient(MONGODB_URI)
db = client['vibelift_db']
users_collection = db['users']
logger.info("Successfully connected to MongoDB Atlas")

# Flask app
app = Flask(__name__)

# Global application variable
application = None

# Load users from MongoDB (or initialize if not exists)
def load_users():
    logger.info("Loading users from MongoDB...")
    users_doc = users_collection.find_one({"_id": "users"})
    if users_doc:
        logger.info(f"Users found in MongoDB: {users_doc}")
        return users_doc["data"]
    else:
        logger.info("No users found, creating default users...")
        default_users = {
            'clients': {},
            'engagers': {},
            'pending_payments': {},
            'pending_payouts': {},
            'active_orders': {},
            'pending_admin_actions': {}
        }
        users_collection.insert_one({"_id": "users", "data": default_users})
        logger.info("Default users created in MongoDB")
        return default_users

def save_users():
    logger.info("Saving users to MongoDB...")
    users_collection.update_one(
        {"_id": "users"},
        {"$set": {"data": users}},
        upsert=True
    )
    logger.info("Users saved to MongoDB")

# Initialize users
users = load_users()

# Rate limiting function
def check_rate_limit(user_id, is_signup_action=False, action=None):
    current_time = time.time()
    last_command_time = user_last_command.get(user_id, 0)
    if current_time - last_command_time < RATE_LIMIT:
        return False
    user_last_command[user_id] = current_time
    return True

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        await update.message.reply_text("Hang on a sec and try again!")
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

# Client command
async def client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not check_rate_limit(user_id, action='client'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if user_id in users['clients']:
        if users['clients'][user_id]['step'] == 'completed':
            await update.message.reply_text("Your order is active! Results in 4-5 hours for small orders.")
            return
        elif users['clients'][user_id]['step'] == 'awaiting_payment':
            await update.message.reply_text("You have an order awaiting payment. Use /pay to complete it!")
            return
        else:
            await update.message.reply_text("You’re already a client! Reply with your order or use /pay.")
            return
    users['clients'][user_id] = {'step': 'select_platform'}
    keyboard = [
        [InlineKeyboardButton("Instagram", callback_data='platform_instagram')],
        [InlineKeyboardButton("Facebook", callback_data='platform_facebook')],
        [InlineKeyboardButton("TikTok", callback_data='platform_tiktok')],
        [InlineKeyboardButton("Twitter", callback_data='platform_twitter')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Boost your social media! First, select a platform:", reply_markup=reply_markup
    )
    save_users()

# Engager command
async def engager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not check_rate_limit(user_id, action='engager'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if user_id in users['engagers']:
        keyboard = [
            [InlineKeyboardButton("See Tasks", callback_data='tasks')],
            [InlineKeyboardButton("Check Balance", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("You’re already an engager! Pick an action:", reply_markup=reply_markup)
        return
    keyboard = [[InlineKeyboardButton("Join Now", callback_data='join')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Earn cash by engaging! Follow, like, or comment on social media posts.\n"
        "Earnings per task:\n- Follow: ₦20-50\n- Like: ₦10-30\n- Comment: ₦30-50\n"
        "Get a ₦500 signup bonus! Withdraw at ₦1,000 earned (excl. bonus).",
        reply_markup=reply_markup
    )

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_rate_limit(user_id, action='help'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    await update.message.reply_text(
        "Welcome to VibeLiftBot! Here’s how to use me:\n\n"
        "👥 For Clients:\n"
        "- /client: Place an order to boost your social media.\n"
        "- /pay: Pay for your order.\n"
        "- /status: Check your order status.\n\n"
        "🤝 For Engagers:\n"
        "- /engager: Join as an engager to earn money.\n"
        "- /tasks: Complete tasks to earn rewards.\n"
        "- /balance: Check your earnings.\n"
        "- /withdraw: Withdraw your earnings.\n\n"
        "🛠️ For Admins:\n"
        "- /admin: Access the admin panel (in the admin group).\n\n"
        "Need more help? Contact support in the admin group!"
    )

# Pay command
@app.route('/payment-success', methods=['GET'])
async def payment_success():
    order_id = request.args.get('order_id')
    if not order_id:
        return "Error: No order ID provided", 400

    logger.info(f"Payment success redirect received for order_id: {order_id}")

    if order_id in users['pending_payments']:
        user_id = users['pending_payments'][order_id]['user_id']
        order_details = users['pending_payments'][order_id]['order_details']
        order_details['priority'] = False
        users['active_orders'][order_id] = order_details
        users['clients'][str(user_id)]['step'] = 'completed'
        del users['pending_payments'][order_id]
        save_users()

        await application.bot.send_message(
            chat_id=user_id,
            text=f"Payment successful! Your order (ID: {order_id}) is now active. Check progress with /status."
        )
        await application.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Payment success redirect for order {order_id} from user {user_id}."
        )
        logger.info(f"Order {order_id} moved to active_orders for user {user_id}")

    return f"""
    <html>
        <body>
            <h1>Payment Successful!</h1>
            <p>Your payment for order ID {order_id} has been received.</p>
            <p>Return to Telegram to check your order status using /status.</p>
            <a href="https://t.me/VibeLiftBot">Go back to VibeLiftBot</a>
        </body>
    </html>
    """

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
    callback_url = f"{WEBHOOK_URL.rsplit('/', 1)[0]}/payment-success?order_id={users['clients'][user_id_str]['order_id']}"
    payload = {
        "email": email,
        "amount": amount,
        "callback_url": callback_url,
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
                [InlineKeyboardButton("Cancel Order", callback_data='cancel')],
                [InlineKeyboardButton("Back to Start", callback_data='start')]
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

# Flask routes
@app.route('/paystack-webhook', methods=['POST'])
async def paystack_webhook():
    logger.info("Received Paystack webhook request")
    if request.method == 'POST':
        paystack_secret = os.getenv("PAYSTACK_SECRET_KEY")
        signature = request.headers.get('X-Paystack-Signature')
        body = request.get_data()
        computed_signature = hmac.new(
            paystack_secret.encode('utf-8'),
            body,
            hashlib.sha512
        ).hexdigest()
        
        if signature != computed_signature:
            logger.warning("Invalid Paystack webhook signature")
            return "Invalid signature", 401

        try:
            data = request.get_json()
            logger.info(f"Paystack webhook data: {data}")
            event = data.get('event')
            if event == 'charge.success':
                payment_data = data.get('data')
                metadata = payment_data.get('metadata', {})
                user_id = metadata.get('user_id')
                order_id = metadata.get('order_id')
                amount = payment_data.get('amount', 0) // 100  # Convert from kobo to naira
                if str(user_id) in users['clients'] and users['clients'][str(user_id)].get('order_id') == order_id:
                    order_details = users['clients'][str(user_id)]['order_details']
                    order_details['priority'] = False
                    users['active_orders'][order_id] = order_details
                    users['clients'][str(user_id)]['step'] = 'completed'
                    await application.bot.send_message(
                        chat_id=user_id,
                        text="Payment confirmed! Your order is now active. Check progress with /status."
                    )
                    await application.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"Payment of ₦{amount} from {user_id} for order {order_id} confirmed. Tasks active!"
                    )
                    save_users()
                    logger.info(f"Payment confirmed for user {user_id}, order {order_id}")
                else:
                    logger.warning(f"Webhook received but no matching order found: user {user_id}, order {order_id}")
            return "Webhook received", 200
        except Exception as e:
            logger.error(f"Error in Paystack webhook: {e}")
            return "Error processing webhook", 500
    return "Method not allowed", 405

# Status command
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not check_rate_limit(user_id, action='status'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if user_id not in users['clients']:
        await update.message.reply_text("You haven't placed an order yet. Start with /client!")
        return
    client_data = users['clients'][user_id]
    step = client_data['step']
    if step == 'select_platform':
        await update.message.reply_text("You're in the process of selecting a platform. Reply with your choice!")
    elif step == 'awaiting_order':
        await update.message.reply_text("You're in the process of placing an order. Reply with: `handle package` (e.g., @NaijaFashion 50).")
    elif step == 'awaiting_payment':
        order_id = client_data['order_id']
        amount = client_data['amount']
        if order_id in users['pending_payments']:
            await update.message.reply_text(
                f"Your order (ID: {order_id}) is awaiting admin approval for payment of ₦{amount}."
            )
        else:
            await update.message.reply_text(
                f"Your order (ID: {order_id}) is awaiting payment of ₦{amount}. Use /pay to complete it!"
            )
    elif step == 'completed':
        order_id = client_data['order_id']
        if order_id in users['active_orders']:
            order = users['active_orders'][order_id]
            message = (
                f"Order Status (ID: {order_id}):\n"
                f"Handle: {order['handle']}\n"
                f"Platform: {order['platform']}\n"
                f"Follows Remaining: {order.get('follows_left', 0)}\n"
                f"Likes Remaining: {order.get('likes_left', 0)}\n"
                f"Comments Remaining: {order.get('comments_left', 0)}\n"
                f"Priority: {'Yes' if order.get('priority', False) else 'No'}"
            )
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(
                f"Your order (ID: {order_id}) is complete! Start a new order with /client."
            )

# Admin command
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    logger.info(f"Admin command used by user {user_id} in chat {chat_id}, expected ADMIN_GROUP_ID: {ADMIN_GROUP_ID}")
    if str(chat_id) != str(ADMIN_GROUP_ID):
        await update.message.reply_text("This command can only be used in the admin group.")
        return
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("Admin only!")
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
    await update.message.reply_text(
        f"Admin Panel:\n"
        f"Withdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\n"
        f"Pick an action:", 
        reply_markup=reply_markup
    )

# Admin view tasks
async def admin_view_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.callback_query.from_user.id)
    if user_id != ADMIN_USER_ID:
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

# Admin clear pending
async def admin_clear_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.callback_query.from_user.id)
    if user_id != ADMIN_USER_ID:
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
    save_users()

# Admin set priority
async def admin_set_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.callback_query.from_user.id)
    if user_id != ADMIN_USER_ID:
        await update.callback_query.message.reply_text("Admin only!")
        return
    await update.callback_query.answer()
    
    logger.info(f"Active orders before displaying priority menu: {users['active_orders']}")
    
    if not users['active_orders']:
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.reply_text("No active tasks to prioritize!", reply_markup=reply_markup)
        return

    message = "Select a Task to Set as Priority:\n"
    keyboard = []
    for order_id, order in users['active_orders'].items():
        handle = order.get('handle', 'unknown')
        platform = order.get('platform', 'unknown')
        priority = order.get('priority', False)
        status = "Priority" if priority else "Normal"
        message += f"\n- Order {order_id}: {handle} on {platform} (Current: {status})\n"
        keyboard.append([
            InlineKeyboardButton(f"Set Priority: {order_id}", callback_data=f'set_priority_{order_id}')
        ])
    
    keyboard.append([InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(message, reply_markup=reply_markup)

# Generate admin code
async def generate_admin_code(user_id, action, action_data):
    code = ''.join(random.choices(string.digits, k=6))
    users['pending_admin_actions'][f"{action}_{user_id}"] = {
        'user_id': user_id,
        'action': action,
        'action_data': action_data,
        'code': code,
        'expiration': time.time() + 300
    }
    save_users()
    await application.bot.send_message(chat_id=user_id, text=f"Your 6-digit code for {action}: {code}")
    return code

# Button handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    user_id_str = str(user_id)
    data = query.data
    action = data if data in ['client', 'engager', 'help', 'tasks', 'balance'] else None
    is_signup_action = data in ['client', 'engager', 'join', 'help', 'start']
    if not check_rate_limit(user_id, is_signup_action=is_signup_action, action=action):
        await query.message.reply_text("Hang on a sec and try again!")
        return
    await query.answer()
    logger.info(f"Button clicked by user {user_id}: {data}")

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
    elif data.startswith('platform_'):
        platform = data.split('_')[1]
        users['clients'][user_id_str]['platform'] = platform
        users['clients'][user_id_str]['step'] = 'select_package'
        save_users()
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
    elif data.startswith('select_'):
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
        save_users()
    elif data.startswith('task_'):
        task_parts = data.split('_', 2)
        task_type = task_parts[1]
        order_id = task_parts[2]
        logger.info(f"Task claim attempt: user={user_id}, order_id={order_id}, active_orders={users['active_orders']}")
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
                task_name = {'f': 'Follow', 'l': 'Like', 'c': 'Comment'}.get(task_type, 'Task')
                await query.message.edit_text(f"You’ve already done the {task_name} task for this order!")
                return
        timer_key = f"{order_id}_{task_type}"
        if 'tasks_per_order' not in user_data:
            user_data['tasks_per_order'] = {}
        users['engagers'][user_id_str]['task_timers'][timer_key] = time.time()
        if task_type == 'f':
            await query.message.edit_text(f"Follow {order['handle']} on {order['platform']}.\nSend a screenshot here to earn!")
        elif task_type == 'l':
            if order.get('use_recent_posts'):
                await query.message.edit_text(f"Like the 3 latest posts by {order['handle']} on {order['platform']}.\nSpend 60 seconds on each, then send a screenshot here!")
            else:
                await query.message.edit_text(f"Like this post: {order['like_url']}\nSpend 60 seconds, then send a screenshot here!")
        elif task_type == 'c':
            if order.get('use_recent_posts'):
                await query.message.edit_text(f"Comment on the 3 latest posts by {order['handle']} on {order['platform']}.\nSpend 60 seconds on each, then send a screenshot here!")
            else:
                await query.message.edit_text(f"Comment on this post: {order['comment_url']}\nSpend 60 seconds, then send a screenshot here!")
        save_users()
    elif data == 'tasks':
        await tasks(update, context)
    elif data == 'balance':
        await balance(update, context)
    elif data == 'withdraw':
        await withdraw(update, context)
    elif data == 'admin_stats':
        if user_id_str != ADMIN_USER_ID:
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
    elif data == 'admin_audit':
        if user_id_str != ADMIN_USER_ID:
            await query.message.edit_text("Admin only!")
            return
        users['pending_admin_actions'][f"audit_{user_id_str}"] = {'user_id': int(user_id_str), 'action': 'awaiting_audit_input', 'expiration': time.time() + 300}
        save_users()
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Reply with: <engager_id> <order_id> [reason]\nExample: 1518439839 1518439839_1742633918 Invalid proof", reply_markup=reply_markup)
    elif data == 'admin_view_withdrawals':
        if user_id_str != ADMIN_USER_ID:
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
    elif data == 'admin_view_payments':
        if user_id_str != ADMIN_USER_ID:
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
    elif data == 'admin_pending':
        if user_id_str != ADMIN_USER_ID:
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
    elif data == 'admin_clear_pending':
        await admin_clear_pending(update, context)
    elif data == 'admin_view_tasks':
        await admin_view_tasks(update, context)
    elif data == 'admin_set_priority':
        await admin_set_priority(update, context)
    elif data == 'back_to_admin':
        if user_id_str != ADMIN_USER_ID:
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
    elif data.startswith('approve_payout_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.edit_text("Only admin can do this!")
            return
        payout_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'approve_payout', {'payout_id': payout_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to approve payout {payout_id}.", reply_markup=reply_markup)
    elif data.startswith('reject_payout_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.edit_text("Only admin can do this!")
            return
        payout_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'reject_payout', {'payout_id': payout_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to reject payout {payout_id}.", reply_markup=reply_markup)
    elif data.startswith('approve_payment_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.edit_text("Only admin can do this!")
            return
        payment_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'approve_payment', {'payment_id': payment_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to approve payment {payment_id}.", reply_markup=reply_markup)
    elif data.startswith('reject_payment_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.edit_text("Only admin can do this!")
            return
        payment_id = data.split('_')[2]
        action_id = await generate_admin_code(user_id, 'reject_payment', {'payment_id': payment_id})
        keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to reject payment {payment_id}.", reply_markup=reply_markup)
    elif data.startswith('set_priority_'):
        if user_id_str != ADMIN_USER_ID:
            await query.message.edit_text("Only admin can do this!")
            return
        order_id = data.split('_')[2]
        logger.info(f"Attempting to set priority for order {order_id}, current active_orders: {users['active_orders']}")
        if order_id in users['active_orders']:
            current_priority = users['active_orders'][order_id].get('priority', False)
            users['active_orders'][order_id]['priority'] = not current_priority
            status = "Priority" if not current_priority else "Normal"
            await query.message.edit_text(f"Order {order_id} set to {status}!")
            save_users()
        else:
            await query.message.edit_text(f"Order {order_id} no longer exists! It may have been completed or cleared.")
    elif data == 'cancel':
        if user_id_str in users['clients']:
            del users['clients'][user_id_str]
            await query.message.edit_text("Order canceled. Start over with /client!")
            save_users()

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = update.message.text.lower() if update.message.text else ""
    is_signup_action = (
        (user_id in users['clients'] and users['clients'][user_id]['step'] in ['select_platform', 'awaiting_order', 'awaiting_urls', 'awaiting_payment']) or
        (user_id in users['engagers'] and 'awaiting_payout' in users['engagers'][user_id])
    )
    if not check_rate_limit(update.message.from_user.id, is_signup_action=is_signup_action):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if str(update.message.chat_id) == ADMIN_GROUP_ID and user_id != ADMIN_USER_ID:
        return

    logger.info(f"Received message from user {user_id}: '{update.message.text}' (normalized: '{text}')")

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
                    await application.bot.send_message(chat_id=engager_id, 
                                                    text=f"Your task {order_id} was rejected after audit. Reason: {reason}. ₦{claim['amount']} removed.")
                    await update.message.reply_text(f"Task {order_id} for {engager_id} rejected. Balance updated.")
                    del users['pending_admin_actions'][action_id_to_remove]
                    save_users()
                    return
            await update.message.reply_text("Claim not found or already processed.")
            del users['pending_admin_actions'][action_id_to_remove]
            save_users()
            return
    if pending_action and action_id_to_remove.startswith('clear_task_'):
        if pending_action['action'] == 'awaiting_clear_task_input':
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text("Please provide: <type> <id>\nExample: payment 1234567890_1699999999\nTypes: payment, payout, order")
                return
            task_type, task_id = parts[0], parts[1]
            if task_type == 'payment':
                if task_id in users['pending_payments']:
                    payment = users['pending_payments'][task_id]
                    client_id = payment['client_id']
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    del users['pending_payments'][task_id]
                    await update.message.reply_text(f"Payment {task_id} cleared!")
                    await application.bot.send_message(chat_id=client_id, text="Your pending payment was cleared by the admin. Start over with /client.")
                else:
                    await update.message.reply_text(f"Payment {task_id} not found!")
            elif task_type == 'payout':
                if task_id in users['pending_payouts']:
                    payout = users['pending_payouts'][task_id]
                    engager_id = payout['engager_id']
                    del users['pending_payouts'][task_id]
                    await update.message.reply_text(f"Payout {task_id} cleared!")
                    await application.bot.send_message(chat_id=engager_id, text="Your pending withdrawal was cleared by the admin. Contact support if needed.")
                else:
                    await update.message.reply_text(f"Payout {task_id} not found!")
            elif task_type == 'order':
                if task_id in users['active_orders']:
                    order = users['active_orders'][task_id]
                    client_id = order['client_id']
                    del users['active_orders'][task_id]
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    await update.message.reply_text(f"Order {task_id} cleared!")
                    await application.bot.send_message(chat_id=client_id, text="Your active order was cleared by the admin. Start over with /client.")
                else:
                    await update.message.reply_text(f"Order {task_id} not found!")
            else:
                await update.message.reply_text("Invalid type! Use: payment, payout, or order.")
                return
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
                    await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout of ₦{amount} to {account} for {engager_id} approved. Process it now!")
                    await application.bot.send_message(chat_id=engager_id, text=f"Your withdrawal of ₦{amount} to {account} is approved!")
                    save_users()
            elif action == 'reject_payout':
                payout_id = action_data['payout_id']
                if payout_id in users['pending_payouts']:
                    engager_id = users['pending_payouts'][payout_id]['engager_id']
                    del users['pending_payouts'][payout_id]
                    await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payout request from {engager_id} rejected.")
                    await application.bot.send_message(chat_id=engager_id, text="Your withdrawal was rejected. Contact support!")
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
                    await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payment for order {order_id} from {client_id} approved. Tasks active!")
                    await application.bot.send_message(chat_id=client_id, text="Payment approved! Results in 4-5 hours for small orders.")
                    save_users()
            elif action == 'reject_payment':
                payment_id = action_data['payment_id']
                if payment_id in users['pending_payments']:
                    payment = users['pending_payments'][payment_id]
                    client_id = payment['client_id']
                    del users['pending_payments'][payment_id]
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Payment for order from {client_id} rejected.")
                    await application.bot.send_message(chat_id=client_id, text="Payment rejected. Start over with /client.")
                    save_users()
            await application.bot.send_message(chat_id=user_id, text=f"{action.replace('_', ' ').title()} completed!")
        else:
            await application.bot.send_message(chat_id=user_id, text="Wrong code! Try again.")
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
        platform = users['clients'][user_id]['platform']
        order_type = users['clients'][user_id]['order_type']
        package = None
        profile_url = None
        profile_image_id = None

        if update.message.text:
            parts = update.message.text.split()
            if len(parts) < 2:
                await update.message.reply_text("Please provide the URL and package (e.g., https://instagram.com/yourusername 50), or send a screenshot with the package.")
                return
            potential_url, package = parts[0], parts[1].lower()
            valid_url = False
            if potential_url.startswith('https://'):
                if platform == 'instagram' and 'instagram.com' in potential_url:
                    valid_url = True
                elif platform == 'facebook' and 'facebook.com' in potential_url:
                    valid_url = True
                elif platform == 'tiktok' and 'tiktok.com' in potential_url:
                    valid_url = True
                elif platform == 'twitter' and 'twitter.com' in potential_url:
                    valid_url = True
            if valid_url:
                profile_url = potential_url
            else:
                await update.message.reply_text(f"Please provide a valid {platform.capitalize()} URL (e.g., https://{platform}.com/yourusername).")
                return

        elif update.message.photo and update.message.caption:
            parts = update.message.caption.lower().split()
            if len(parts) < 2 or parts[0] != 'package':
                await update.message.reply_text("Please include the package in the caption (e.g., `package 50`).")
                return
            package = parts[1]
            profile_image_id = update.message.photo[-1].file_id

        else:
            await update.message.reply_text("Please provide the URL and package, or send a screenshot with the package in the caption.")
            return

        if package not in package_limits[order_type][platform]:
            await update.message.reply_text(f"Invalid package! Options: {', '.join(list(package_limits[order_type][platform].keys()))}")
            return

        handle = None
        if profile_url:
            try:
                handle = profile_url.split('/')[-1] if profile_url.split('/')[-1] else profile_url.split('/')[-2]
                if platform == 'tiktok' and handle.startswith('@'):
                    handle = handle[1:]
            except:
                await update.message.reply_text("Could not extract username from the URL. Please ensure the URL is correct.")
                return
        else:
            handle = "Provided via image"

        order_id = f"{user_id}_{int(time.time())}"
        if order_type == 'bundle':
            amount = package_limits['bundle'][platform][package]['price']
            order_details = {
                'client_id': int(user_id),
                'order_id': order_id,
                'handle': handle,
                'platform': platform,
                'profile_url': profile_url,
                'profile_image_id': profile_image_id,
                'follows_left': package_limits['bundle'][platform][package]['follows'],
                'likes_left': package_limits['bundle'][platform][package]['likes'],
                'comments_left': package_limits['bundle'][platform][package]['comments'],
                'use_recent_posts': True,
                'priority': False
            }
        else:
            amount = pricing[order_type][platform][package]
            order_details = {
                'client_id': int(user_id),
                'order_id': order_id,
                'handle': handle,
                'platform': platform,
                'profile_url': profile_url,
                'profile_image_id': profile_image_id,
                'follows_left': package_limits[order_type][platform][package] if order_type == 'followers' else 0,
                'likes_left': package_limits[order_type][platform][package] if order_type == 'likes' else 0,
                'comments_left': package_limits[order_type][platform][package] if order_type == 'comments' else 0,
                'use_recent_posts': True,
                'priority': False
            }
        users['clients'][user_id]['step'] = 'awaiting_payment'
        users['clients'][user_id]['amount'] = amount
        users['clients'][user_id]['order_id'] = order_id
        users['clients'][user_id]['order_details'] = order_details
        summary_message = (
            f"Order Summary:\n"
            f"Handle: {handle}\n"
            f"Platform: {platform}\n"
            f"Package: {package}\n"
            f"Amount: ₦{amount}\n"
        )
        if profile_url:
            summary_message += f"Profile URL: {profile_url}\n"
        summary_message += "Use /pay to proceed with payment."
        if profile_image_id:
            await update.message.reply_photo(
                photo=profile_image_id,
                caption=summary_message
            )
        else:
            await update.message.reply_text(summary_message)
        admin_message = (
            f"New order from {user_id} (ID: {order_id}):\n"
            f"Handle: {handle}\n"
            f"Platform: {platform}\n"
            f"Package: {package}\n"
            f"Amount: ₦{amount}\n"
        )
        if profile_url:
            admin_message += f"Profile URL: {profile_url}\n"
        if profile_image_id:
            await application.bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=profile_image_id,
                caption=admin_message
            )
        else:
            await application.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=admin_message
            )
        save_users()
    elif user_id in users['clients'] and users['clients'][user_id]['step'] == 'awaiting_payment' and text == 'proof':
        if not update.message.photo:
            await update.message.reply_text("Please send a screenshot of your payment proof.")
            return
        photo_id = update.message.photo[-1].file_id
        order_id = users['clients'][user_id]['order_id']
        order_details = users['clients'][user_id]['order_details']
        users['pending_payments'][order_id] = {
            'client_id': int(user_id),
            'order_id': order_id,
            'photo_id': photo_id,
            'order_details': order_details,
            'timestamp': time.time()
        }
        await update.message.reply_text("Payment proof submitted! Awaiting admin approval.")
        await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"New payment proof from {user_id} for order {order_id}. Check /admin.")
        save_users()
    elif user_id in users['engagers'] and users['engagers'][user_id].get('awaiting_payout', False):
        account_number = text
        if not (account_number.isdigit() and len(account_number) == 10):
            await update.message.reply_text("Please provide a valid 10-digit OPay account number.")
            return
        amount = users['engagers'][user_id]['earnings'] + users['engagers'][user_id]['signup_bonus']
        payout_id = f"{user_id}_{int(time.time())}"
        users['pending_payouts'][payout_id] = {
            'engager_id': user_id,
            'amount': amount,
            'account': account_number,
            'timestamp': time.time()
        }
        users['engagers'][user_id]['awaiting_payout'] = False
        await update.message.reply_text(f"Withdrawal request for ₦{amount} to {account_number} submitted! Awaiting admin approval.")
        await application.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"New withdrawal request from {user_id}: ₦{amount} to {account_number}. Check /admin.")
        save_users()
    elif any(timer_key in users['engagers'].get(user_id, {}).get('task_timers', {}) for timer_key in users['engagers'].get(user_id, {}).get('task_timers', {})):
        if not update.message.photo:
            await update.message.reply_text("Please send a screenshot to verify your task completion.")
            return
        photo_id = update.message.photo[-1].file_id
        for timer_key in list(users['engagers'][user_id]['task_timers'].keys()):
            order_id, task_type = timer_key.rsplit('_', 1)
            if order_id not in users['active_orders']:
                del users['engagers'][user_id]['task_timers'][timer_key]
                await update.message.reply_text("This task is no longer available!")
                continue
            time_spent = time.time() - users['engagers'][user_id]['task_timers'][timer_key]
            if task_type in ['l', 'c'] and time_spent < 60:
                await update.message.reply_text(f"Spend at least 60 seconds on the task! You've spent {int(time_spent)} seconds.")
                return
            del users['engagers'][user_id]['task_timers'][timer_key]
            reward_ranges = {
                'f': (20, 50),
                'l': (10, 30),
                'c': (30, 50)
            }
            min_reward, max_reward = reward_ranges[task_type]
            amount = random.randint(min_reward, max_reward)
            order = users['active_orders'][order_id]
            if task_type == 'f':
                order['follows_left'] = max(0, order.get('follows_left', 0) - 1)
            elif task_type == 'l':
                order['likes_left'] = max(0, order.get('likes_left', 0) - 1)
            elif task_type == 'c':
                order['comments_left'] = max(0, order.get('comments_left', 0) - 1)
            if (order.get('follows_left', 0) == 0 and 
                order.get('likes_left', 0) == 0 and 
                order.get('comments_left', 0) == 0):
                client_id = order['client_id']
                del users['active_orders'][order_id]
                if str(client_id) in users['clients']:
                    del users['clients'][str(client_id)]
                await application.bot.send_message(
                    chat_id=client_id,
                    text=f"Your order (ID: {order_id}) is complete! Start a new order with /client."
                )
                await application.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"Order {order_id} for client {client_id} is complete!"
                )
            if 'claims' not in users['engagers'][user_id]:
                users['engagers'][user_id]['claims'] = []
            users['engagers'][user_id]['claims'].append({
                'order_id': order_id,
                'task_type': task_type,
                'amount': amount,
                'photo_id': photo_id,
                'status': 'pending',
                'timestamp': time.time()
            })
            users['engagers'][user_id]['earnings'] = users['engagers'][user_id].get('earnings', 0) + amount
            task_name = {'f': 'Follow', 'l': 'Like', 'c': 'Comment'}[task_type]
            order = users['active_orders'][order_id]
            admin_caption = (
                f"New {task_name} task completion by {user_id} for order {order_id}. Reward: ₦{amount}.\n"
                f"Handle: {order['handle']}\n"
                f"Platform: {order['platform']}\n"
            )
            if order.get('profile_url'):
                admin_caption += f"Profile URL: {order['profile_url']}\n"
            admin_caption += "Approve or audit in /admin."
            if order.get('profile_image_id'):
                await application.bot.send_photo(
                    chat_id=ADMIN_GROUP_ID,
                    photo=order['profile_image_id'],
                    caption=admin_caption
                )
                await application.bot.send_photo(
                    chat_id=ADMIN_GROUP_ID,
                    photo=photo_id,
                    caption=f"Screenshot of {task_name} task completion by {user_id} for order {order_id}."
                )
            else:
                await application.bot.send_photo(
                    chat_id=ADMIN_GROUP_ID,
                    photo=photo_id,
                    caption=admin_caption
                )
            await update.message.reply_text(
                f"Task submitted! You’ve earned ₦{amount} (pending approval). Check /balance."
            )
            save_users()
    else:
        await update.message.reply_text("I didn't understand that. Use a command like /start, /client, or /engager to get started!")

async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    if not check_rate_limit(user_id, action='tasks'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if not users['active_orders']:
        await update.message.reply_text("No tasks available right now. Check back later!")
        return
    message = "Available Tasks:\n"
    keyboard = []
    for order_id, order in sorted(users['active_orders'].items(), key=lambda x: x[1].get('priority', False), reverse=True):
        platform = order.get('platform', 'unknown')
        handle = order.get('handle', 'unknown')
        follows_left = order.get('follows_left', 0)
        likes_left = order.get('likes_left', 0)
        comments_left = order.get('comments_left', 0)
        priority = order.get('priority', False)
        if follows_left > 0:
            message += f"\n- Follow {handle} on {platform}: ₦20-50 {'(Priority)' if priority else ''}\n"
            keyboard.append([InlineKeyboardButton(f"Follow: {handle}", callback_data=f'task_f_{order_id}')])
        if likes_left > 0:
            message += f"\n- Like post by {handle} on {platform}: ₦10-30 {'(Priority)' if priority else ''}\n"
            keyboard.append([InlineKeyboardButton(f"Like: {handle}", callback_data=f'task_l_{order_id}')])
        if comments_left > 0:
            message += f"\n- Comment on post by {handle} on {platform}: ₦30-50 {'(Priority)' if priority else ''}\n"
            keyboard.append([InlineKeyboardButton(f"Comment: {handle}", callback_data=f'task_c_{order_id}')])
    if not keyboard:
        await update.message.reply_text("No tasks available for these orders (all tasks completed).")
        return
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    if not check_rate_limit(user_id, action='balance'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    user_data = users['engagers'][user_id]
    earnings = user_data.get('earnings', 0)
    bonus = user_data.get('signup_bonus', 0)
    total_balance = earnings + bonus
    tasks_left = max(0, 1000 - earnings) // 20
    message = f"Your Balance: ₦{total_balance}\n- Earnings: ₦{earnings}\n- Signup Bonus: ₦{bonus}\n"
    if earnings < 1000:
        message += f"Need {tasks_left} more tasks to withdraw at ₦1,000 earned (excl. bonus)."
    else:
        message += "You can withdraw now! Use /withdraw."
    await update.message.reply_text(message)

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in users['engagers']:
        await update.message.reply_text("Join as an engager first with /engager!")
        return
    if not check_rate_limit(user_id, action='withdraw'):
        await update.message.reply_text("Hang on a sec and try again!")
        return
    user_data = users['engagers'][user_id]
    earnings = user_data.get('earnings', 0)
    bonus = user_data.get('signup_bonus', 0)
    total_balance = earnings + bonus
    if earnings < 1000:
        await update.message.reply_text(f"Need ₦1,000 earned (excl. ₦{bonus} bonus). Current: ₦{earnings}. Keep earning!")
        return
    if total_balance > WITHDRAWAL_LIMIT:
        await update.message.reply_text(f"Trial limit is ₦{WITHDRAWAL_LIMIT}. Your balance (₦{total_balance}) is too high. Wait for the limit to lift.")
        return
    if user_data.get('awaiting_payout', False):
        await update.message.reply_text("You already have a pending withdrawal. Wait for admin approval!")
        return
            user_data['awaiting_payout'] = True
    await update.message.reply_text("Reply with your 10-digit OPay account number to withdraw.")
    save_users()

def main():
    global application
    logger.info("Starting bot...")
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("client", client))
    application.add_handler(CommandHandler("engager", engager))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("tasks", tasks))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # Use the port from Render's environment variable, default to 8443 if not set
    port = int(os.getenv("PORT", 8443))
    logger.info(f"Setting webhook to {WEBHOOK_URL} on port {port}")
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=f"/{BOT_TOKEN}",
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )

if __name__ == '__main__':
    port = int(os.getenv("PORT", 8443))
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port))
    flask_thread.start()
    main()