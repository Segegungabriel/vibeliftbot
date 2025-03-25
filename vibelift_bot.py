from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import logging
import os
import time
import asyncio
from flask import Flask, request, jsonify
import uvicorn
from asgiref.wsgi import WsgiToAsgi
from pymongo import MongoClient
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Access environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
MONGODB_URI = os.getenv("MONGODB_URI")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))  # Replace with your admin user ID
ADMIN_GROUP_ID = os.getenv("ADMIN_GROUP_ID", "")  # Replace with your admin group ID
OPAY_ACCOUNT = os.getenv("OPAY_ACCOUNT", "")  # Replace with your OPAY account number

# Initialize MongoDB client
client = AsyncIOMotorClient(MONGODB_URI)
db = client.get_database("vibeliftbot")  # Replace with your database name
users_collection = db.get_collection("users")  # Replace with your collection name

# Initialize Flask app
app = Flask(__name__)

# Health check endpoint for Render
@app.route('/', methods=['GET'])
def health_check():
    logger.info("Health check endpoint accessed")
    return jsonify({"status": "Bot is running"}), 200

# Webhook endpoint for Telegram updates
@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    logger.info(f"Received and processed update: {update}")
    await application.process_update(update)
    return jsonify({"status": "success"}), 200

# Global variables
application = None
users = None

# Load users from MongoDB (async)
async def load_users():
    logger.info("Loading users from MongoDB...")
    try:
        users_doc = await users_collection.find_one({"_id": "users"})
        if users_doc:
            logger.info(f"Users found in MongoDB: {users_doc}")
            return users_doc["data"]
        else:
            logger.info("No users found, creating default users...")
            default_users = {
                'clients': {}, 'engagers': {}, 'pending_payments': {}, 'pending_payouts': {},
                'active_orders': {}, 'pending_admin_actions': {}
            }
            await users_collection.insert_one({"_id": "users", "data": default_users})
            logger.info("Default users created in MongoDB")
            return default_users
    except Exception as e:
        logger.error(f"Error loading users from MongoDB: {str(e)}")
        raise

async def save_users():
    logger.info("Saving users to MongoDB...")
    try:
        await users_collection.update_one(
            {"_id": "users"},
            {"$set": {"data": users}},
            upsert=True
        )
        logger.info("Users saved to MongoDB")
    except Exception as e:
        logger.error(f"Error saving users to MongoDB: {str(e)}")
        raise

# Rate limiting function
def check_rate_limit(user_id, is_signup_action=False, action=None):
    current_time = time.time()
    # Skip rate limiting for /start (handled separately in start function)
    if action == 'start':
        return True
    last_command_time = user_last_command.get(user_id, 0)
    if current_time - last_command_time < RATE_LIMIT:
        return False
    user_last_command[user_id] = current_time
    return True

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /start command from user {update.effective_user.id}")
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    args = context.args

    # Check for payment success
    if args and args[0].startswith("payment_success_"):
        order_id = args[0].split("payment_success_")[1]
        if order_id in users.get("active_orders", {}):
            await update.message.reply_text(
                f"🎉 Payment successful! Your order (ID: {order_id}) is now active. Check progress with /status."
            )
        else:
            await update.message.reply_text(
                "⚠️ Payment confirmation is still processing. Please wait a moment or use /status to check."
            )
        return

    # Use a more lenient rate limit for /start (e.g., 5 seconds)
    current_time = time.time()
    last_start_time = user_last_command.get(f"{user_id}_start", 0)
    if current_time - last_start_time < 5:  # 5-second rate limit for /start
        logger.info(f"Rate limit hit for /start from user {user_id}")
        return
    user_last_command[f"{user_id}_start"] = current_time

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

async def client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /client command from user {user_id}")
    
    if update.callback_query:
        message = update.callback_query.message
        await update.callback_query.answer()
    else:
        message = update.message

    if not check_rate_limit(user_id, action='client'):
        logger.info(f"User {user_id} is rate-limited for /client")
        await message.reply_text("Hang on a sec and try again!")
        return

    try:
        if user_id in users['clients']:
            client_data = users['clients'][user_id]
            if client_data['step'] == 'completed':
                logger.info(f"User {user_id} has a completed order")
                await message.reply_text("Your order is active! Results in 4-5 hours for small orders.")
                return
            elif client_data['step'] == 'awaiting_payment':
                logger.info(f"User {user_id} has an order awaiting payment")
                await message.reply_text("You have an order awaiting payment. Use /pay to complete it, or /cancel to start over!")
                return
            else:
                logger.info(f"User {user_id} is already a client, current step: {client_data['step']}")
                await message.reply_text("You’re already a client! Reply with your order or use /pay.")
                return

        users['clients'][user_id] = {'step': 'select_platform'}
        keyboard = [
            [InlineKeyboardButton("Instagram", callback_data='platform_instagram')],
            [InlineKeyboardButton("Facebook", callback_data='platform_facebook')],
            [InlineKeyboardButton("TikTok", callback_data='platform_tiktok')],
            [InlineKeyboardButton("Twitter", callback_data='platform_twitter')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Boost your social media! First, select a platform:", reply_markup=reply_markup
        )
        await save_users()
        logger.info(f"Sent platform selection prompt to user {user_id}")
    except Exception as e:
        logger.error(f"Error in /client for user {user_id}: {str(e)}")
        await message.reply_text("An error occurred while starting your order. Please try again or contact support.")
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /cancel command from user {user_id}")

    if not check_rate_limit(user_id, action='cancel'):
        logger.info(f"User {user_id} is rate-limited for /cancel")
        await update.message.reply_text("Hang on a sec and try again!")
        return

    try:
        # Check if the user is a client
        if user_id in users['clients']:
            client_data = users['clients'][user_id]
            if client_data['step'] in ['select_platform', 'awaiting_order', 'awaiting_payment']:
                # If the user has a pending payment, remove it
                if client_data['step'] == 'awaiting_payment' and 'order_id' in client_data:
                    order_id = client_data['order_id']
                    if order_id in users.get('pending_payments', {}):
                        del users['pending_payments'][order_id]
                        logger.info(f"Removed pending payment {order_id} for user {user_id}")
            
            # Remove the user from clients
            del users['clients'][user_id]
            await save_users()
            logger.info(f"User {user_id} canceled their client order")
            await update.message.reply_text("Your order has been canceled. Start a new order with /client!")
            return

        # Check if the user is an engager with a pending payout
        if user_id in users['engagers'] and users['engagers'][user_id].get('awaiting_payout', False):
            users['engagers'][user_id]['awaiting_payout'] = False
            await save_users()
            logger.info(f"User {user_id} canceled their payout request")
            await update.message.reply_text("Your payout request has been canceled. Use /withdraw to start a new request.")
            return

        # If the user has no active order or payout to cancel
        logger.info(f"User {user_id} has nothing to cancel")
        await update.message.reply_text("You don’t have an active order or payout to cancel.")
    except Exception as e:
        logger.error(f"Error in /cancel for user {user_id}: {str(e)}")
        await update.message.reply_text("An error occurred while canceling. Please try again or contact support.")

async def engager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if update.callback_query:
        message = update.callback_query.message
        await update.callback_query.answer()
    else:
        message = update.message

    if not check_rate_limit(user_id, action='engager'):
        await message.reply_text("Hang on a sec and try again!")
        return

    if user_id in users['engagers']:
        keyboard = [
            [InlineKeyboardButton("See Tasks", callback_data='tasks')],
            [InlineKeyboardButton("Check Balance", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text("You’re already an engager! Pick an action:", reply_markup=reply_markup)
        return

    keyboard = [[InlineKeyboardButton("Join Now", callback_data='join')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text(
        "Earn cash by engaging! Follow, like, or comment on social media posts.\n"
        "Earnings per task:\n- Follow: ₦20-50\n- Like: ₦10-30\n- Comment: ₦30-50\n"
        "Get a ₦500 signup bonus! Withdraw at ₦1,000 earned (excl. bonus).",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if update.callback_query:
        message = update.callback_query.message
        await update.callback_query.answer()
    else:
        message = update.message

    if not check_rate_limit(user_id, action='help'):
        await message.reply_text("Hang on a sec and try again!")
        return

    await message.reply_text(
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

async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if update.callback_query:
        message = update.callback_query.message
    else:
        message = update.message

    if not check_rate_limit(user_id, action='tasks'):
        await message.reply_text("Hang on a sec and try again!")
        return

    if user_id not in users['engagers']:
        keyboard = [[InlineKeyboardButton("Join as Engager", callback_data='engager')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text("You need to join as an engager to view tasks!", reply_markup=reply_markup)
        return

    user_data = users['engagers'][user_id]
    daily_tasks = user_data.get('daily_tasks', {'count': 0, 'last_reset': time.time()})
    current_time = time.time()
    if current_time - daily_tasks['last_reset'] > 24 * 60 * 60:
        daily_tasks['count'] = 0
        daily_tasks['last_reset'] = current_time
        user_data['daily_tasks'] = daily_tasks
        await save_users()

    if daily_tasks['count'] >= 10:
        await message.reply_text("You’ve reached your daily task limit! Try again tomorrow.")
        return

    available_tasks = []
    for order_id, order in users['active_orders'].items():
        platform = order['platform']
        handle = order['handle']
        if order.get('follows_left', 0) > 0:
            available_tasks.append((order_id, 'f', f"Follow {handle} on {platform} (₦20-50)"))
        if order.get('likes_left', 0) > 0:
            available_tasks.append((order_id, 'l', f"Like posts by {handle} on {platform} (₦10-30)"))
        if order.get('comments_left', 0) > 0:
            available_tasks.append((order_id, 'c', f"Comment on posts by {handle} on {platform} (₦30-50)"))

    if not available_tasks:
        await message.reply_text("No tasks available right now. Check back later!")
        return

    keyboard = []
    for order_id, task_type, task_text in available_tasks:
        callback_data = f"task_{task_type}_{order_id}"
        keyboard.append([InlineKeyboardButton(task_text, callback_data=callback_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text("Available Tasks:", reply_markup=reply_markup)

async def initiate_payment(user_id: str, amount: int, order_id: str) -> str:
    try:
        headers = {
            "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "amount": amount * 100,
            "email": f"{user_id}@vibeliftbot.com",
            "callback_url": "https://vibeliftbot.onrender.com/payment-success",
            "metadata": {"order_id": order_id}
        }
        response = requests.post("https://api.paystack.co/transaction/initialize", headers=headers, json=data)
        response.raise_for_status()
        payment_data = response.json()
        if not payment_data.get("status"):
            raise Exception("Payment initiation failed: " + payment_data.get("message", "Unknown error"))
        payment_url = payment_data["data"]["authorization_url"]
        logger.info(f"Payment initiated for user {user_id}, order {order_id}: {payment_url}")
        return payment_url
    except Exception as e:
        logger.error(f"Error initiating payment for user {user_id}, order {order_id}: {str(e)}")
        raise

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    logger.info(f"Received /pay command from user {user_id}")
    
    if not check_rate_limit(user_id, action='pay'):
        logger.info(f"User {user_id} is rate-limited for /pay")
        await update.message.reply_text("Hang on a sec and try again!")
        return

    if user_id not in users['clients']:
        logger.info(f"User {user_id} is not a client for /pay")
        await update.message.reply_text("Start an order first with /client!")
        return

    client_data = users['clients'][user_id]
    if client_data['step'] != 'awaiting_payment':
        logger.info(f"User {user_id} is not in awaiting_payment step, current step: {client_data['step']}")
        await update.message.reply_text("You don’t have an order awaiting payment. Start a new order with /client!")
        return

    try:
        amount = client_data['amount']
        order_id = client_data['order_id']
        platform = client_data['platform']
        logger.info(f"Processing payment for user {user_id}: Order {order_id}, Amount ₦{amount}, Platform {platform}")
        
        payment_message = (
            f"Please send ₦{amount} to this OPay account:\n"
            f"Account Number: {OPAY_ACCOUNT}\n\n"
            f"After payment, reply with a screenshot of your payment confirmation."
        )
        await update.message.reply_text(payment_message)
        logger.info(f"Sent payment instructions to user {user_id}")
    except Exception as e:
        logger.error(f"Error in /pay for user {user_id}: {str(e)}")
        await update.message.reply_text("An error occurred while processing your payment. Please try again or contact support.")

@app.route('/payment-success', methods=['GET'])
async def payment_success():
    order_id = request.args.get('order_id')
    if not order_id:
        logger.error("No order ID provided in payment-success redirect")
        return "Error: No order ID provided", 400
    logger.info(f"Payment success redirect received for order_id: {order_id}")
    return send_file("static/success.html")

@app.route('/payment_callback', methods=['POST'])
async def payment_callback():
    global users
    try:
        # Reload users from MongoDB to ensure we have the latest data
        users = await load_users()
        logger.info(f"Users loaded in payment_callback: pending_payments={users.get('pending_payments', {})}")

        data = request.get_json()
        logger.info(f"Payment callback received: {data}")
        event = data.get("event")
        payment_data = data.get("data", {})
        order_id = payment_data.get("metadata", {}).get("order_id")
        status = payment_data.get("status")

        if not order_id or not status:
            logger.error("Invalid callback data: missing order_id or status")
            return "Invalid data", 400

        payment_id = order_id
        if payment_id not in users.get("pending_payments", {}):
            logger.error(f"No pending payment found for order_id: {order_id}")
            return "Order not found", 404

        user_id = users["pending_payments"][payment_id]["user_id"]
        if event == "charge.success" and status == "success":
            order = users["pending_payments"].pop(payment_id)
            order_details = order["order_details"]
            users.setdefault("active_orders", {})[order_id] = order_details
            users["clients"][user_id]["step"] = "completed"
            await save_users()

            await application.bot.send_message(
                chat_id=user_id,
                text=f"🎉 Payment successful! Your order (ID: {order_id}) is now active. Check progress with /status."
            )
            await application.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Payment success for order {order_id} from {user_id}."
            )
            logger.info(f"Order {order_id} moved to active_orders for user {user_id}")
        else:
            logger.warning(f"Payment failed for order_id: {order_id}, status: {status}")
            await application.bot.send_message(
                chat_id=user_id,
                text="⚠️ Payment failed. Please try again with /pay or contact support."
            )

        return "OK", 200
    except Exception as e:
        logger.error(f"Error in payment callback: {str(e)}")
        return "Error", 500

async def initiate_payment(user_id: str, amount: int, order_id: str) -> str:
    """
    Initiates a payment with Paystack and returns the payment URL.

    Args:
        user_id (str): The Telegram user ID.
        amount (int): The amount to charge (in NGN, will be converted to kobo).
        order_id (str): The unique order ID for tracking the payment.

    Returns:
        str: The payment URL for the user to complete the payment.

    Raises:
        Exception: If the payment initiation fails.
    """
    try:
        headers = {
            "Authorization": f"Bearer {os.getenv('PAYMENT_API_KEY')}",
            "Content-Type": "application/json"
        }
        data = {
            "amount": amount * 100,
            "email": f"{user_id}@vibeliftbot.com",
            "callback_url": "https://vibeliftbot.onrender.com/payment-success",
            "metadata": {"order_id": order_id}
        }
        response = requests.post("https://api.paystack.co/transaction/initialize", headers=headers, json=data)
        response.raise_for_status()
        payment_data = response.json()
        if not payment_data.get("status"):
            raise Exception("Payment initiation failed: " + payment_data.get("message", "Unknown error"))
        payment_url = payment_data["data"]["authorization_url"]
        logger.info(f"Payment initiated for user {user_id}, order {order_id}: {payment_url}")
        return payment_url
    except Exception as e:
        logger.error(f"Error initiating payment for user {user_id}, order {order_id}: {str(e)}")
        raise

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
    callback_url = f"https://vibeliftbot.onrender.com/payment-success?order_id={users['clients'][user_id_str]['order_id']}"  # Hardcode for clarity
    payload = {
        "email": email,
        "amount": amount,
        "callback_url": callback_url,
        "metadata": {"order_id": users['clients'][user_id_str]['order_id']}  # Simplified metadata
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
        priority = "Priority" if order.get('priority植物', False) else "Normal"
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
    await save_users()

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

# Button handler (helper functions unchanged except for async save_users)
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
        await query.message.edit_text(f"Follow {order['handle']} on {order['platform']}. Send a screenshot here to earn!")
    elif task_type == 'l':
        if order.get('use_recent_posts'):
            await query.message.edit_text(f"Like the 3 latest posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each, then send a screenshot here!")
        else:
            await query.message.edit_text(f"Like this post: {order['like_url']}. Spend 60 seconds, then send a screenshot here!")
    elif task_type == 'c':
        if order.get('use_recent_posts'):
            await query.message.edit_text(f"Comment on the 3 latest posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each, then send a screenshot here!")
        else:
            await query.message.edit_text(f"Comment on the post: {order['comment_url']}. Spend 60 seconds, then send a screenshot here!")
    await save_users()

async def handle_tasks_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await tasks(update, context)

async def handle_balance_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await balance(update, context)

async def handle_withdraw_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await withdraw(update, context)

async def handle_admin_stats_button(query: CallbackQuery, user_id_str: str) -> None:
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

async def handle_admin_audit_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str != ADMIN_USER_ID:
        await query.message.edit_text("Admin only!")
        return
    users['pending_admin_actions'][f"audit_{user_id_str}"] = {'user_id': int(user_id_str), 'action': 'awaiting_audit_input', 'expiration': time.time() + 300}
    save_users()
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text("Reply with: <engager_id> <order_id> [reason]\nExample: 1518439839 1518439839_1742633918 Invalid proof", reply_markup=reply_markup)

async def handle_admin_view_withdrawals_button(query: CallbackQuery, user_id_str: str) -> None:
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

async def handle_admin_view_payments_button(query: CallbackQuery, user_id: int, user_id_str: str) -> None:
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

async def handle_admin_pending_button(query: CallbackQuery, user_id_str: str) -> None:
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

async def handle_admin_clear_pending_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_clear_pending(update, context)

async def handle_admin_view_tasks_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_view_tasks(update, context)

async def handle_admin_set_priority_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_set_priority(update, context)

async def handle_back_to_admin_button(query: CallbackQuery, user_id_str: str) -> None:
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

async def handle_approve_payout_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != ADMIN_USER_ID:
        await query.message.edit_text("Only admin can do this!")
        return
    payout_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'approve_payout', {'payout_id': payout_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to approve payout {payout_id}.", reply_markup=reply_markup)

async def handle_reject_payout_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != ADMIN_USER_ID:
        await query.message.edit_text("Only admin can do this!")
        return
    payout_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'reject_payout', {'payout_id': payout_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to reject payout {payout_id}.", reply_markup=reply_markup)

async def handle_approve_payment_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != ADMIN_USER_ID:
        await query.message.edit_text("Only admin can do this!")
        return
    payment_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'approve_payment', {'payment_id': payment_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to approve payment {payment_id}.", reply_markup=reply_markup)

async def handle_reject_payment_button(query: CallbackQuery, user_id: int, user_id_str: str, data: str) -> None:
    if user_id_str != ADMIN_USER_ID:
        await query.message.edit_text("Only admin can do this!")
        return
    payment_id = data.split('_')[2]
    action_id = await generate_admin_code(user_id, 'reject_payment', {'payment_id': payment_id})
    keyboard = [[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"Enter the 6-digit code sent to your private chat to reject payment {payment_id}.", reply_markup=reply_markup)

async def handle_set_priority_button(query: CallbackQuery, user_id_str: str, data: str) -> None:
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

async def handle_cancel_button(query: CallbackQuery, user_id_str: str) -> None:
    if user_id_str in users['clients']:
        del users['clients'][user_id_str]
        await query.message.edit_text("Order canceled. Start over with /client!")
        save_users()

# Main button handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    user_id_str = str(user_id)
    data = query.data
    await query.answer()
    logger.info(f"Button clicked by user {user_id}: {data}")

    if data == 'client':
        await client(update, context)
    elif data == 'engager':
        await engager(update, context)
    elif data == 'help':
        await help_command(update, context)
    elif data == 'join':
        if user_id_str in users['engagers']:
            keyboard = [
                [InlineKeyboardButton("See Tasks", callback_data='tasks')],
                [InlineKeyboardButton("Check Balance", callback_data='balance')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text("You’re already an engager! Pick an action:", reply_markup=reply_markup)
        else:
            users['engagers'][user_id_str] = {
                'earnings': 0,
                'signup_bonus': 500,
                'task_timers': {},
                'daily_tasks': {'count': 0, 'last_reset': time.time()},
                'claims': []
            }
            keyboard = [
                [InlineKeyboardButton("See Tasks", callback_data='tasks')],
                [InlineKeyboardButton("Check Balance", callback_data='balance')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                "🎉 Welcome, new engager! You’ve earned a ₦500 signup bonus!\nPick an action:",
                reply_markup=reply_markup
            )
            await save_users()
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
    elif data.startswith('platform_'):
        platform = data.split('platform_')[1]
        users['clients'][user_id_str]['platform'] = platform
        users['clients'][user_id_str]['step'] = 'awaiting_order'
        await query.message.edit_text(
            f"Selected {platform.capitalize()}.\n"
            "Now, send your order in this format:\n"
            "Handle, Follows, Likes, Comments\n"
            "Example: myhandle, 100, 200, 50\n"
            "Or reply with a specific post URL for likes/comments."
        )
        await save_users()
    elif data.startswith('task_'):
        await handle_task_button(query, user_id, user_id_str, data)
    elif data == 'admin':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            await query.message.edit_text("This command is only for admins in the admin group!")
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
            f"Admin Panel:\nWithdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\nPick an action:",
            reply_markup=reply_markup
        )
    elif data == 'admin_stats':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        total_clients = len(users['clients'])
        total_engagers = len(users['engagers'])
        pending_tasks = len(users.get('pending_admin_actions', {}))
        completed_tasks = sum(len(user.get('claims', [])) for user in users['engagers'].values())
        await query.message.edit_text(
            f"Admin Stats:\n- Total Clients: {total_clients}\n- Total Engagers: {total_engagers}\n- Pending Tasks: {pending_tasks}\n- Completed Tasks (approx.): {completed_tasks}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_audit':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        await query.message.edit_text(
            "Reply with: <engager_id> <order_id> [reason]\nExample: 1518439839 1518439839_1742633918 Invalid proof",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_view_withdrawals':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        withdrawals = []
        for user_id_str, user_data in users['engagers'].items():
            if user_data.get('awaiting_payout'):
                withdrawals.append(f"User {user_id_str}: ₦{user_data['earnings']}")
        message = "Pending Withdrawals:\n" + "\n".join(withdrawals) if withdrawals else "No pending withdrawals."
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_view_payments':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        payments = []
        for payment_id, payment in users.get('pending_payments', {}).items():
            payments.append(f"Payment {payment_id}: User {payment['user_id']}")
        message = "Pending Payments:\n" + "\n".join(payments) if payments else "No pending payments."
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_pending':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        pending = users.get('pending_admin_actions', {})
        message = "Pending Admin Actions:\n" + "\n".join([f"Task {task_id}: {task}" for task_id, task in pending.items()]) if pending else "No pending admin actions."
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_clear_pending':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        await query.message.edit_text(
            "Clear Pending Tasks:\nReply with the format:\n- `payment <payment_id>` to clear a pending payment\n- `payout <payout_id>` to clear a pending payout\n- `order <order_id>` to clear an active order\nExample: `payment 1234567890_1699999999`\n\n" +
            ("Active Orders:\n" + "\n".join([f"Order {order_id}" for order_id in users.get('active_orders', {}).keys()]) if users.get('active_orders') else "No active orders.") + "\n\n" +
            ("Pending Payments:\n" + "\n".join([f"Payment {payment_id}" for payment_id in users.get('pending_payments', {}).keys()]) if users.get('pending_payments') else "No pending payments.") + "\n\n" +
            ("Pending Payouts:\n" + "\n".join([f"Payout {user_id}" for user_id, user_data in users['engagers'].items() if user_data.get('awaiting_payout')]) if any(user_data.get('awaiting_payout') for user_data in users['engagers'].values()) else "No pending payouts."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_view_tasks':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        tasks = []
        for order_id, order in users.get('active_orders', {}).items():
            tasks.append(f"Order {order_id}: {order['platform']} - {order['handle']}")
        message = "Active Tasks:\n" + "\n".join(tasks) if tasks else "No active tasks."
        await query.message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'admin_set_priority':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
            return
        await query.message.edit_text(
            "Set Task Priority:\nReply with: <order_id> <priority>\nExample: 1234567890_1699999999 1",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Admin Menu", callback_data='back_to_admin')]])
        )
        await save_users()
    elif data == 'back_to_admin':
        if str(query.message.chat_id) != ADMIN_GROUP_ID or user_id != int(ADMIN_USER_ID):
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
            f"Admin Panel:\nWithdrawal limit: ₦{WITHDRAWAL_LIMIT} (trial). Edit code to change.\nPick an action:",
            reply_markup=reply_markup
        )

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
    withdrawable = earnings if earnings >= 1000 else 0
    message = (
        f"Your Balance:\n"
        f"Earnings: ₦{earnings}\n"
        f"Signup Bonus: ₦{signup_bonus}\n"
        f"Total: ₦{total_balance}\n"
        f"Withdrawable (excl. bonus): ₦{withdrawable}\n"
        f"Withdraw at ₦1,000 earned (excl. bonus)."
    )
    keyboard = [[InlineKeyboardButton("Withdraw", callback_data='withdraw')]] if withdrawable >= 1000 else []
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
    if earnings < 1000:
        await update.message.reply_text("You need at least ₦1,000 earned (excl. bonus) to withdraw!")
        return
    if user_data.get('awaiting_payout', False):
        await update.message.reply_text("You already have a pending withdrawal. Wait for admin approval!")
        return
    user_data['awaiting_payout'] = True
    await update.message.reply_text("Reply with your 10-digit OPay account number to withdraw.")
    await save_users()

# Message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    text = update.message.text.lower() if update.message.text else ""
    logger.info(f"Received message from user {user_id}: '{update.message.text}' (normalized: '{text}')")
    
    is_signup_action = (
        (user_id in users['clients'] and users['clients'][user_id]['step'] in ['select_platform', 'awaiting_order', 'awaiting_urls', 'awaiting_payment']) or
        (user_id in users['engagers'] and 'awaiting_payout' in users['engagers'][user_id])
    )
    if not check_rate_limit(update.message.from_user.id, is_signup_action=is_signup_action):
        logger.info(f"User {user_id} is rate-limited")
        await update.message.reply_text("Hang on a sec and try again!")
        return
    if str(update.message.chat_id) == ADMIN_GROUP_ID and user_id != ADMIN_USER_ID:
        logger.info(f"User {user_id} is not admin, ignoring message in admin group")
        return

    # Handle pending admin actions (e.g., audit, clear tasks, verification codes)
    pending_action = None
    action_id_to_remove = None
    for action_id, action_data in list(users.get('pending_admin_actions', {}).items()):
        if action_data['user_id'] == int(user_id) and time.time() < action_data['expiration']:
            pending_action = action_data
            action_id_to_remove = action_id
            break
        elif time.time() >= action_data['expiration']:
            del users['pending_admin_actions'][action_id]
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
                    await update.message.reply_text(f"Task {order_id} for {engager_id} rejected. Balance updated.")
                    del users['pending_admin_actions'][action_id_to_remove]
                    await save_users()
                    return
            await update.message.reply_text("Claim not found or already processed.")
            del users['pending_admin_actions'][action_id_to_remove]
            await save_users()
            return
    if pending_action and action_id_to_remove.startswith('clear_task_'):
        logger.info(f"Processing clear_task action for user {user_id}")
        if pending_action['action'] == 'awaiting_clear_task_input':
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text("Please provide: <type> <id>\nExample: payment 1234567890_1699999999\nTypes: payment, payout, order")
                return
            task_type, task_id = parts[0], parts[1]
            if task_type == 'payment':
                if task_id in users.get('pending_payments', {}):
                    payment = users['pending_payments'][task_id]
                    client_id = payment['user_id']
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    del users['pending_payments'][task_id]
                    await update.message.reply_text(f"Payment {task_id} cleared!")
                    await application.bot.send_message(
                        chat_id=client_id,
                        text="Your pending payment was cleared by the admin. Start over with /client."
                    )
                else:
                    await update.message.reply_text(f"Payment {task_id} not found!")
            elif task_type == 'payout':
                found = False
                for user_id_str, user_data in users['engagers'].items():
                    if user_id_str == task_id and user_data.get('awaiting_payout'):
                        user_data['awaiting_payout'] = False
                        await update.message.reply_text(f"Payout for user {task_id} cleared!")
                        await application.bot.send_message(
                            chat_id=task_id,
                            text="Your pending withdrawal was cleared by the admin. Contact support if needed."
                        )
                        found = True
                        break
                if not found:
                    await update.message.reply_text(f"Payout for user {task_id} not found!")
            elif task_type == 'order':
                if task_id in users.get('active_orders', {}):
                    order = users['active_orders'][task_id]
                    client_id = order['client_id']
                    del users['active_orders'][task_id]
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    await update.message.reply_text(f"Order {task_id} cleared!")
                    await application.bot.send_message(
                        chat_id=client_id,
                        text="Your active order was cleared by the admin. Start over with /client."
                    )
                else:
                    await update.message.reply_text(f"Order {task_id} not found!")
            else:
                await update.message.reply_text("Invalid type! Use: payment, payout, or order.")
                return
            del users['pending_admin_actions'][action_id_to_remove]
            await save_users()
            return
    if pending_action and text.isdigit() and len(text) == 6:
        logger.info(f"Processing verification code for user {user_id}")
        if text == pending_action['code']:
            action = pending_action['action']
            action_data = pending_action['action_data']
            del users['pending_admin_actions'][action_id_to_remove]
            await save_users()
            if action == 'approve_payout':
                payout_id = action_data['payout_id']
                if payout_id in users.get('pending_payouts', {}):
                    payout = users['pending_payouts'][payout_id]
                    engager_id = payout['engager_id']
                    amount = payout['amount']
                    account = payout['account']
                    users['engagers'][engager_id]['earnings'] -= amount
                    del users['pending_payouts'][payout_id]
                    await application.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"Payout of ₦{amount} to {account} for {engager_id} approved. Process it now!"
                    )
                    await application.bot.send_message(
                        chat_id=engager_id,
                        text=f"Your withdrawal of ₦{amount} to {account} is approved!"
                    )
                    await save_users()
            elif action == 'reject_payout':
                payout_id = action_data['payout_id']
                if payout_id in users.get('pending_payouts', {}):
                    engager_id = users['pending_payouts'][payout_id]['engager_id']
                    del users['pending_payouts'][payout_id]
                    await application.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"Payout request from {engager_id} rejected."
                    )
                    await application.bot.send_message(
                        chat_id=engager_id,
                        text="Your withdrawal was rejected. Contact support!"
                    )
                    await save_users()
            elif action == 'approve_payment':
                payment_id = action_data['payment_id']
                if payment_id in users.get('pending_payments', {}):
                    payment = users['pending_payments'][payment_id]
                    client_id = payment['user_id']
                    order_id = payment['order_id']
                    order_details = payment['order_details']
                    logger.info(f"Approving payment {payment_id}: Adding order {order_id} to active_orders with details {order_details}")
                    users['active_orders'][order_id] = order_details
                    del users['pending_payments'][payment_id]
                    if str(client_id) in users['clients']:
                        users['clients'][str(client_id)]['step'] = 'completed'
                    await application.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"Payment for order {order_id} from {client_id} approved. Tasks active!"
                    )
                    await application.bot.send_message(
                        chat_id=client_id,
                        text="Payment approved! Results in 4-5 hours for small orders."
                    )
                    await save_users()
            elif action == 'reject_payment':
                payment_id = action_data['payment_id']
                if payment_id in users.get('pending_payments', {}):
                    payment = users['pending_payments'][payment_id]
                    client_id = payment['user_id']
                    del users['pending_payments'][payment_id]
                    if str(client_id) in users['clients']:
                        del users['clients'][str(client_id)]
                    await application.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"Payment for order from {client_id} rejected."
                    )
                    await application.bot.send_message(
                        chat_id=client_id,
                        text="Payment rejected. Start over with /client."
                    )
                    await save_users()
            await application.bot.send_message(
                chat_id=user_id,
                text=f"{action.replace('_', ' ').title()} completed!"
            )
        else:
            await application.bot.send_message(chat_id=user_id, text="Wrong code! Try again.")
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

    # Client order submission
    if user_id in users['clients']:
        client_data = users['clients'][user_id]
        logger.info(f"User {user_id} is a client, current step: {client_data['step']}")
        if client_data['step'] == 'awaiting_order':
            logger.info(f"Processing order submission for user {user_id}")
            platform = client_data['platform']
            order_type = client_data['order_type']
            package = None
            profile_url = None
            profile_image_id = None

            if update.message.text:
                parts = update.message.text.split()
                if len(parts) < 2:
                    logger.info(f"User {user_id} provided invalid order format")
                    await update.message.reply_text("Please provide the URL and package (e.g., https://instagram.com/yourusername 10) or use the format: `package <package>` with a screenshot.")
                    return
                if parts[0].startswith('http'):
                    profile_url = parts[0]
                    package = parts[1]
                elif parts[0] == 'package':
                    package = parts[1]
                else:
                    logger.info(f"User {user_id} provided invalid order format")
                    await update.message.reply_text("Invalid format! Use: `<URL> <package>` or `package <package>` with a screenshot.")
                    return
            elif update.message.photo:
                profile_image_id = update.message.photo[-1].file_id
                if not text.startswith('package '):
                    logger.info(f"User {user_id} did not provide package with screenshot")
                    await update.message.reply_text("Please include the package in the format: `package <package>` (e.g., `package 10`) with your screenshot.")
                    return
                parts = text.split()
                package = parts[1]
            else:
                logger.info(f"User {user_id} did not provide URL or screenshot")
                await update.message.reply_text("Please provide a URL or a screenshot of your profile along with the package!")
                return

            try:
                if order_type != 'bundle':
                    if package not in package_limits[order_type][platform]:
                        logger.info(f"User {user_id} provided invalid package: {package}")
                        await update.message.reply_text(f"Invalid package! Available for {order_type} on {platform}: {', '.join(package_limits[order_type][platform].keys())}")
                        return
                    amount = pricing[order_type][platform][package]
                    follows = package_limits[order_type][platform][package] if order_type == 'followers' else 0
                    likes = package_limits[order_type][platform][package] if order_type == 'likes' else 0
                    comments = package_limits[order_type][platform][package] if order_type == 'comments' else 0
                else:
                    if package not in package_limits['bundle'][platform]:
                        logger.info(f"User {user_id} provided invalid bundle: {package}")
                        await update.message.reply_text(f"Invalid bundle! Available for {platform}: {', '.join(package_limits['bundle'][platform].keys())}")
                        return
                    bundle = package_limits['bundle'][platform][package]
                    follows = bundle['follows']
                    likes = bundle['likes']
                    comments = bundle['comments']
                    amount = bundle['price']

                handle = profile_url.split('/')[-1] if profile_url else f"@{user_id}"
                order_id = f"{user_id}_{int(time.time())}"
                order_details = {
                    'client_id': user_id,
                    'handle': handle,
                    'platform': platform,
                    'follows_left': follows,
                    'likes_left': likes,
                    'comments_left': comments,
                    'priority': False
                }
                if profile_url:
                    order_details['profile_url'] = profile_url
                if profile_image_id:
                    order_details['profile_image_id'] = profile_image_id

                users['clients'][user_id]['step'] = 'awaiting_payment'
                users['clients'][user_id]['order_id'] = order_id
                users['clients'][user_id]['amount'] = amount
                users['clients'][user_id]['order_details'] = order_details

                payment_id = order_id
                users.setdefault('pending_payments', {})[payment_id] = {
                    'user_id': user_id,
                    'client_id': user_id,
                    'order_id': order_id,
                    'order_details': order_details
                }
                if profile_image_id:
                    users['pending_payments'][payment_id]['photo_id'] = profile_image_id
                    await application.bot.send_photo(
                        chat_id=ADMIN_GROUP_ID,
                        photo=profile_image_id,
                        caption=f"New order from {user_id} for {platform} ({order_type} package: {package}). Amount: ₦{amount}"
                    )
                else:
                    await application.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        text=f"New order from {user_id} for {platform} ({order_type} package: {package}). Amount: ₦{amount}\nProfile URL: {profile_url}"
                    )
                await update.message.reply_text(
                    f"Order placed! Total: ₦{amount} for {follows} follows, {likes} likes, {comments} comments on {platform}.\n"
                    f"Use /pay to complete your payment, or /cancel to cancel your order."
                )
                await save_users()
                logger.info(f"Order placed for user {user_id}: Order ID {order_id}, Amount ₦{amount}")
            except Exception as e:
                logger.error(f"Error processing order for user {user_id}: {str(e)}")
                await update.message.reply_text("An error occurred while placing your order. Please try again or contact support.")
            return
        elif client_data['step'] == 'awaiting_payment':
            logger.info(f"User {user_id} is in awaiting_payment step, prompting for /pay")
            await update.message.reply_text("Please use /pay to proceed with payment, or /cancel to cancel your order.")
            return

    # Engager payout submission
    if user_id in users['engagers'] and users['engagers'][user_id].get('awaiting_payout', False):
        logger.info(f"Processing payout submission for user {user_id}")
        if not text.isdigit() or len(text) != 10:
            await update.message.reply_text("Please provide a valid 10-digit OPay account number!")
            return
        account_number = text
        user_data = users['engagers'][user_id]
        amount = user_data['earnings']
        payout_id = f"{user_id}_{int(time.time())}"
        users.setdefault('pending_payouts', {})[payout_id] = {
            'engager_id': user_id,
            'amount': amount,
            'account': account_number,
            'timestamp': time.time()
        }
        user_data['awaiting_payout'] = False
        await update.message.reply_text(f"Withdrawal request for ₦{amount} to OPay account {account_number} submitted. Awaiting admin approval!")
        await application.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"New withdrawal request from {user_id}: ₦{amount} to OPay account {account_number}."
        )
        await save_users()
        logger.info(f"Payout request submitted for user {user_id}: Amount ₦{amount}")
        return

    # Engager task proof submission
    if user_id in users['engagers']:
        user_data = users['engagers'][user_id]
        logger.info(f"Checking for task proof submission from user {user_id}")
        for timer_key, start_time in list(user_data.get('task_timers', {}).items()):
            order_id, task_type = timer_key.rsplit('_', 1)
            if order_id not in users.get('active_orders', {}):
                del user_data['task_timers'][timer_key]
                continue
            if not update.message.photo:
                await update.message.reply_text("Please send a screenshot to confirm task completion!")
                return
            elapsed_time = time.time() - start_time
            if elapsed_time < 60:
                await update.message.reply_text(f"Please spend at least 60 seconds on the task! Wait {int(60 - elapsed_time)} more seconds.")
                return
            photo_id = update.message.photo[-1].file_id
            amount = {'f': 50, 'l': 30, 'c': 50}.get(task_type, 20)
            user_data['earnings'] += amount
            user_data.setdefault('claims', []).append({
                'order_id': order_id,
                'task_type': task_type,
                'amount': amount,
                'status': 'approved'
            })
            if task_type == 'f':
                users['active_orders'][order_id]['follows_left'] -= 1
            elif task_type == 'l':
                users['active_orders'][order_id]['likes_left'] -= 1
            elif task_type == 'c':
                users['active_orders'][order_id]['comments_left'] -= 1
            if (users['active_orders'][order_id]['follows_left'] <= 0 and
                users['active_orders'][order_id]['likes_left'] <= 0 and
                users['active_orders'][order_id]['comments_left'] <= 0):
                client_id = users['active_orders'][order_id]['client_id']
                await application.bot.send_message(
                    chat_id=client_id,
                    text=f"Your order (ID: {order_id}) is complete! Start a new order with /client."
                )
                del users['active_orders'][order_id]
            user_data['daily_tasks']['count'] += 1
            del user_data['task_timers'][timer_key]
            task_name = {'f': 'Follow', 'l': 'Like', 'c': 'Comment'}.get(task_type, 'Task')
            await update.message.reply_text(f"{task_name} task completed! You earned ₦{amount}. Check your balance with /balance.")
            await application.bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=photo_id,
                caption=f"{task_name} proof from {user_id} for order {order_id} on {users['active_orders'][order_id]['platform']}."
            )
            await save_users()
            logger.info(f"Task completed for user {user_id}: Order {order_id}, Type {task_type}, Earned ₦{amount}")
            return

    # Admin group message handling for set priority
    if str(update.message.chat_id) == ADMIN_GROUP_ID and user_id == ADMIN_USER_ID:
        if pending_action and action_id_to_remove.startswith('set_priority_'):
            logger.info(f"Processing set_priority action for user {user_id}")
            if pending_action['action'] == 'awaiting_priority_input':
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    await update.message.reply_text("Please provide: <order_id> <priority>\nExample: 1234567890_1699999999 1")
                    return
                order_id, priority = parts[0], parts[1]
                try:
                    priority = int(priority)
                except ValueError:
                    await update.message.reply_text("Priority must be a number!")
                    return
                if order_id in users.get('active_orders', {}):
                    users['active_orders'][order_id]['priority'] = priority
                    await update.message.reply_text(f"Priority for order {order_id} set to {priority}.")
                else:
                    await update.message.reply_text(f"Order {order_id} not found!")
                del users['pending_admin_actions'][action_id_to_remove]
                await save_users()
                return

async def process_updates():
    while True:
        try:
            update = await application.update_queue.get()
            logger.info(f"Processing update: {update}")
            await application.process_update(update)
            logger.info(f"Successfully processed update: {update}")
        except Exception as e:
            logger.error(f"Error processing update: {str(e)}")

# Webhook routes
@app.route('/webhook', methods=['POST'])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        if update:
            await application.process_update(update)
            logger.info(f"Received and processed update: {update}")
        else:
            logger.warning("Received invalid update from Telegram")
        return "OK", 200
    except Exception as e:
        logger.error(f"Error in webhook route: {str(e)}")
        return "Error", 500

@app.route('/reset-webhook', methods=['GET'])
async def reset_webhook():
    try:
        await application.bot.delete_webhook()
        logger.info("Deleted existing webhook")
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook reset to {WEBHOOK_URL}")
        return "Webhook reset successfully", 200
    except Exception as e:
        logger.error(f"Error resetting webhook: {str(e)}")
        return f"Error resetting webhook: {str(e)}", 500

@app.route('/')
async def health_check():
    return "Service is running", 200

# Main function
async def main():
    global application, users
    logger.info("Starting bot...")

    # Initialize the Telegram application
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
    application.add_handler(CommandHandler("cancel", cancel))  # Now references the defined cancel function
    application.add_handler(CommandHandler("order", order))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))

    # Load users
    try:
        users = await load_users()
        logger.info("Users loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load users: {str(e)}")
        return

    # Initialize the application
    try:
        await application.initialize()
        logger.info("Application initialized")
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        return

    # Set the webhook
    try:
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {str(e)}")
        return

    # Wrap Flask app in WsgiToAsgi for ASGI compatibility
    asgi_app = WsgiToAsgi(app)

    # Run Uvicorn server
    port = int(os.getenv("PORT", 10000))  # Use Render's default port 10000
    config = uvicorn.Config(
        app=asgi_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        loop="asyncio",
        workers=1  # Single worker to avoid async issues
    )
    logger.info(f"Starting Uvicorn server on host 0.0.0.0, port {port}")
    server = uvicorn.Server(config)

    # Start the server
    try:
        await server.serve()
        logger.info("Uvicorn server is running")
    except Exception as e:
        logger.error(f"Failed to start Uvicorn server: {str(e)}")
        raise

# Run the main function
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Error running main: {str(e)}")
        raise