import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import json
import time
import logging

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot token
TOKEN = '7637213737:AAHz9Kvcxj-UZhDlKyyhc9fqoD51JBSsViA'

# Replace with your numeric Telegram user ID (from @userinfobot)
ADMIN_USER_ID = '1518439839'  # Get this from @userinfobot

# Admin group ID
ADMIN_GROUP_ID = '-4762253610'

# Initialize users dictionary
users = {
    'clients': {},
    'engagers': {},
    'pending_tasks': {},
    'last_interaction': {},
    'active_orders': {},
    'pending_payouts': {},
    'pending_payments': {}  # New dictionary for pending payment approvals
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
    pass

def save_users():
    with open('users.json', 'w') as f:
        json.dump(users, f)

# Rate limit check (bypass for signup)
def check_rate_limit(user_id, is_signup_action=False):
    user_id_str = str(user_id)
    current_time = time.time()
    last_time = users['last_interaction'].get(user_id_str, 0)
    
    logger.info(f"Checking rate limit for user {user_id_str}: current_time={current_time}, last_time={last_time}, is_signup_action={is_signup_action}")
    
    if is_signup_action:
        users['last_interaction'][user_id_str] = current_time
        save_users()
        return True
    
    if current_time - last_time < 2:
        logger.info(f"Rate limit triggered for user {user_id_str}: wait_time={2 - (current_time - last_time)} seconds remaining")
        return False
    
    users['last_interaction'][user_id_str] = current_time
    save_users()
    return True

# Start command
def start(update, context):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [
        [InlineKeyboardButton("Grow My Account", callback_data='client')],
        [InlineKeyboardButton("Earn Cash", callback_data='engager')],
        [InlineKeyboardButton("Help", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Welcome to VibeLift! Boost your vibe or earn cash—your choice!",
        reply_markup=reply_markup
    )

# Help command
def help_command(update, context):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [
        [InlineKeyboardButton("Client Guide", callback_data='client_guide')],
        [InlineKeyboardButton("Engager Guide", callback_data='engager_guide')],
        [InlineKeyboardButton("Contact Support", callback_data='contact_support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.bot.send_message(
        chat_id=user_id,
        text="Welcome to VibeLift Help! 🚀\n"
             "Boost your social media presence with real engagement or earn cash by helping others grow!\n"
             "How can we assist you today?",
        reply_markup=reply_markup
    )

# Client command
def client(update, context):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [
        [InlineKeyboardButton("Get Followers", callback_data='get_followers')],
        [InlineKeyboardButton("Get Likes", callback_data='get_likes')],
        [InlineKeyboardButton("Get Comments", callback_data='get_comments')],
        [InlineKeyboardButton("Get a Bundle", callback_data='get_bundle')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.bot.send_message(
        chat_id=user_id,
        text="Grow your account with real vibes!\n"
             "Supported Platforms: Facebook, Twitter/X, Instagram, TikTok\n"
             "What would you like to boost?",
        reply_markup=reply_markup
    )
    users['clients'][str(user_id)] = {'step': 'select_package'}
    save_users()

# Engager command
def engager(update, context):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    if not check_rate_limit(user_id, is_signup_action=True):
        context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return
    keyboard = [[InlineKeyboardButton("Join Now", callback_data='join')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.bot.send_message(
        chat_id=user_id,
        text="Earn ₦100-₦1,250 daily lifting vibes!\nClick to join:",
        reply_markup=reply_markup
    )

# Tasks command
def tasks(update, context):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) not in users['engagers'] or not users['engagers'][str(user_id)].get('joined'):
        update.message.reply_text("Join first! Type /engager or click Earn Cash from /start.")
        return

    # Check daily task limit
    user_data = users['engagers'][str(user_id)]
    current_time = time.time()
    if 'daily_tasks' not in user_data:
        user_data['daily_tasks'] = {'count': 0, 'last_reset': current_time}
    else:
        last_reset = user_data['daily_tasks']['last_reset']
        if current_time - last_reset >= 86400:  # 24 hours
            user_data['daily_tasks'] = {'count': 0, 'last_reset': current_time}
    
    if user_data['daily_tasks']['count'] >= 25:
        update.message.reply_text("You’ve reached your daily task limit of 25. Come back tomorrow!")
        return

    # Check tasks per order
    if 'tasks_per_order' not in user_data:
        user_data['tasks_per_order'] = {}

    keyboard = []
    for order_id, order in users['active_orders'].items():
        platform = order['platform']
        handle = order['handle']
        payouts = {
            'Instagram': {'follow': 20, 'like': 10, 'comment': 30},
            'Facebook': {'follow': 30, 'like': 20, 'comment': 30},
            'TikTok': {'follow': 30, 'like': 20, 'comment': 40},
            'Twitter': {'follow': 25, 'like': 30, 'comment': 50}
        }
        payout = payouts[platform]
        
        # Check tasks per order limit
        order_tasks = user_data['tasks_per_order'].get(order_id, 0)
        if order_tasks >= 5:
            continue  # Skip this order if the engager has already done 5 tasks

        if order['follows_left'] > 0:
            keyboard.append([InlineKeyboardButton(
                f"Follow {handle} on {platform} (₦{payout['follow']})",
                callback_data=f'task_f_{order_id}'
            )])
        if order['likes_left'] > 0:
            text = f"Like post on {platform} (₦{payout['like']})" if not order.get('use_recent_posts') else f"Like 3 recent posts by {handle} on {platform} (₦{payout['like']} each)"
            keyboard.append([InlineKeyboardButton(
                text,
                callback_data=f'task_l_{order_id}'
            )])
        if order['comments_left'] > 0:
            text = f"Comment on post on {platform} (₦{payout['comment']})" if not order.get('use_recent_posts') else f"Comment on 3 recent posts by {handle} on {platform} (₦{payout['comment']} each)"
            keyboard.append([InlineKeyboardButton(
                text,
                callback_data=f'task_c_{order_id}'
            )])

    if not keyboard:
        update.message.reply_text("No tasks available right now. Check back soon!")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Pick a task (send screenshot after):\n"
        "Likes and comments require 60 seconds on the post before submitting proof!",
        reply_markup=reply_markup
    )

# Balance command
def balance(update, context):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) in users['engagers'] and users['engagers'][str(user_id)].get('joined'):
        earnings = users['engagers'][str(user_id)]['earnings']
        keyboard = [[InlineKeyboardButton("Withdraw Earnings", callback_data='withdraw')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(f"Your VibeLift balance: ₦{earnings}", reply_markup=reply_markup)
    else:
        update.message.reply_text("Join as an engager first! Type /engager.")

# Withdraw command
def withdraw(update, context):
    user_id = update.message.from_user.id
    if not check_rate_limit(user_id, is_signup_action=False):
        update.message.reply_text("Slow down! Wait 2 seconds before your next action.")
        return
    if str(user_id) not in users['engagers'] or not users['engagers'][str(user_id)].get('joined'):
        update.message.reply_text("Join as an engager first! Type /engager.")
        return
    earnings = users['engagers'][str(user_id)]['earnings']
    if earnings < 1000:  # Updated withdrawal limit
        update.message.reply_text("Minimum withdrawal is ₦1,000. Keep earning!")
        return
    update.message.reply_text("Reply with your OPay account number to request withdrawal (e.g., 8101234567).")
    users['engagers'][str(user_id)]['awaiting_payout'] = True
    save_users()

# Handle button clicks
def button(update, context):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    is_signup_action = data in ['client', 'engager', 'join', 'get_followers', 'get_likes', 'get_comments', 'get_bundle', 'help', 'client_guide', 'engager_guide', 'contact_support', 'back_to_help']
    if not check_rate_limit(user_id, is_signup_action=is_signup_action):
        context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return

    if data == 'client':
        client(update, context)
    elif data == 'engager':
        engager(update, context)
    elif data == 'help':
        help_command(update, context)
    elif data == 'client_guide':
        keyboard = [[InlineKeyboardButton("Back to Help", callback_data='help')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=user_id,
            text="Client Guide:\n"
                 "- Use /client to start your order.\n"
                 "- Choose a package (followers, likes, comments, or bundles) for Instagram, Facebook, TikTok, or Twitter.\n"
                 "- Pay to: 8101062411 OPay Oluwasegun Okusanya. Small orders are delivered in 4–5 hours!\n"
                 "- If there’s an issue, contact us at vibelift@gmail.com.",
            reply_markup=reply_markup
        )
    elif data == 'engager_guide':
        keyboard = [[InlineKeyboardButton("Back to Help", callback_data='help')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=user_id,
            text="Engager Guide:\n"
                 "- Use /engager to join and start earning ₦100-₦1,250 daily!\n"
                 "- Use /tasks to pick tasks (follow, like, comment). Likes/comments require 60 seconds on the post.\n"
                 "- Submit a screenshot as proof to earn ₦10-₦50 per task.\n"
                 "- Withdraw earnings at ₦1,000 minimum using /withdraw.\n"
                 "- Need help? Email vibelift@gmail.com.",
            reply_markup=reply_markup
        )
    elif data == 'contact_support':
        keyboard = [[InlineKeyboardButton("Back to Help", callback_data='help')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=user_id,
            text="Contact Support:\n"
                 "Email us at vibelift@gmail.com for any questions or issues. We’ll respond within 24 hours!",
            reply_markup=reply_markup
        )
    elif data == 'back_to_help':
        help_command(update, context)
    elif data == 'get_followers':
        context.bot.send_message(
            chat_id=user_id,
            text="Follower Packages:\n"
                 "- Instagram: 10 for ₦1,200 | 50 for ₦6,000 | 100 for ₦12,000\n"
                 "- Facebook: 10 for ₦1,500 | 50 for ₦7,500 | 100 for ₦15,000\n"
                 "- TikTok: 10 for ₦1,800 | 50 for ₦9,000 | 100 for ₦18,000\n"
                 "- Twitter: 10 for ₦800 | 50 for ₦4,000 | 100 for ₦8,000\n"
                 "Reply with: handle platform package (e.g., @NaijaFashion Instagram 10)."
        )
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'followers'}
        save_users()
    elif data == 'get_likes':
        context.bot.send_message(
            chat_id=user_id,
            text="Like Packages:\n"
                 "- Instagram: 20 for ₦600 | 100 for ₦3,000 | 200 for ₦6,000\n"
                 "- Facebook: 20 for ₦1,800 | 100 for ₦9,000 | 200 for ₦18,000\n"
                 "- TikTok: 20 for ₦1,800 | 100 for ₦9,000 | 200 for ₦18,000\n"
                 "- Twitter: 20 for ₦1,800 | 100 for ₦9,000 | 200 for ₦18,000\n"
                 "Reply with: handle platform package (e.g., @NaijaFashion Instagram 20)."
        )
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'likes'}
        save_users()
    elif data == 'get_comments':
        context.bot.send_message(
            chat_id=user_id,
            text="Comment Packages:\n"
                 "- Instagram: 5 for ₦300 | 10 for ₦600 | 50 for ₦3,000\n"
                 "- Facebook: 5 for ₦300 | 10 for ₦600 | 50 for ₦3,000\n"
                 "- TikTok: 5 for ₦600 | 10 for ₦1,200 | 50 for ₦6,000\n"
                 "- Twitter: 5 for ₦600 | 10 for ₦1,200 | 50 for ₦6,000\n"
                 "Reply with: handle platform package (e.g., @NaijaFashion Instagram 5)."
        )
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'comments'}
        save_users()
    elif data == 'get_bundle':
        context.bot.send_message(
            chat_id=user_id,
            text="Bundle Packages (Followers + Likes + Comments):\n"
                 "Instagram:\n"
                 "- Starter (10 followers, 20 likes, 5 comments): ₦1,890\n"
                 "- Pro (50 followers, 100 likes, 10 comments): ₦8,640\n"
                 "- Elite (100 followers, 200 likes, 50 comments): ₦18,900\n"
                 "Facebook:\n"
                 "- Starter (10 followers, 20 likes, 5 comments): ₦3,240\n"
                 "- Pro (50 followers, 100 likes, 10 comments): ₦15,390\n"
                 "- Elite (100 followers, 200 likes, 50 comments): ₦32,400\n"
                 "TikTok:\n"
                 "- Starter (10 followers, 20 likes, 5 comments): ₦3,780\n"
                 "- Pro (50 followers, 100 likes, 10 comments): ₦17,280\n"
                 "- Elite (100 followers, 200 likes, 50 comments): ₦37,800\n"
                 "Twitter:\n"
                 "- Starter (10 followers, 20 likes, 5 comments): ₦2,880\n"
                 "- Pro (50 followers, 100 likes, 10 comments): ₦12,780\n"
                 "- Elite (100 followers, 200 likes, 50 comments): ₦28,800\n"
                 "Reply with: handle platform bundle (e.g., @NaijaFashion Instagram Starter)."
        )
        users['clients'][str(user_id)] = {'step': 'awaiting_order', 'order_type': 'bundle'}
        save_users()
    elif data == 'join':
        users['engagers'][str(user_id)] = {
            'joined': True,
            'earnings': 0,
            'task_timers': {},
            'awaiting_payout': False,
            'daily_tasks': {'count': 0, 'last_reset': time.time()},
            'tasks_per_order': {}
        }
        keyboard = [
            [InlineKeyboardButton("See Tasks", callback_data='tasks'),
             InlineKeyboardButton("Check Balance", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=user_id,
            text="You’re in! Pick an option:",
            reply_markup=reply_markup
        )
        save_users()
    elif data.startswith('task_'):
        task_parts = data.split('_')
        task_type, order_id = task_parts[1], task_parts[2]
        if order_id not in users['active_orders']:
            context.bot.send_message(chat_id=user_id, text="Task no longer available!")
            return
        order = users['active_orders'][order_id]
        timer_key = f"{order_id}_{task_type}"
        users['engagers'][str(user_id)]['task_timers'][timer_key] = time.time()
        if task_type == 'f':
            context.bot.send_message(
                chat_id=user_id,
                text=f"Follow {order['handle']} on {order['platform']} and submit proof!"
            )
        elif task_type == 'l':
            if order.get('use_recent_posts'):
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"Like the 3 most recent posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each post before submitting proof!"
                )
            else:
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"Like this post: {order['like_url']}\nSpend 60 seconds on it before submitting proof!"
                )
        elif task_type == 'c':
            if order.get('use_recent_posts'):
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"Comment on the 3 most recent posts by {order['handle']} on {order['platform']}. Spend 60 seconds on each post before submitting proof!"
                )
            else:
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"Comment on this post: {order['comment_url']}\nSpend 60 seconds on it before submitting proof!"
                )
        save_users()
    elif data == 'tasks':
        tasks(update, context)
    elif data == 'balance':
        balance(update, context)
    elif data == 'withdraw':
        withdraw(update, context)
    elif data.startswith('approve_payout_') and str(user_id) == ADMIN_USER_ID:
        payout_id = data.split('_')[2]
        if payout_id in users['pending_payouts']:
            payout = users['pending_payouts'][payout_id]
            engager_id = payout['engager_id']
            amount = payout['amount']
            account = payout['account']
            users['engagers'][engager_id]['earnings'] -= amount
            del users['pending_payouts'][payout_id]
            context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Payout of ₦{amount} to {account} for {engager_id} approved. Process payment now!"
            )
            context.bot.send_message(
                chat_id=engager_id,
                text=f"Your withdrawal of ₦{amount} to {account} has been approved! Expect payment soon."
            )
            save_users()
    elif data.startswith('reject_payout_') and str(user_id) == ADMIN_USER_ID:
        payout_id = data.split('_')[2]
        if payout_id in users['pending_payouts']:
            payout = users['pending_payouts'][payout_id]
            engager_id = payout['engager_id']
            del users['pending_payouts'][payout_id]
            context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Payout request from {engager_id} rejected."
            )
            context.bot.send_message(
                chat_id=engager_id,
                text="Your withdrawal request was rejected. Contact support if there’s an issue!"
            )
            save_users()
    elif data.startswith('approve_payment_') and str(user_id) == ADMIN_USER_ID:
        payment_id = data.split('_')[2]
        if payment_id in users['pending_payments']:
            payment = users['pending_payments'][payment_id]
            client_id = payment['client_id']
            order_id = payment['order_id']
            # Move the order to active_orders
            users['active_orders'][order_id] = payment['order_details']
            del users['pending_payments'][payment_id]
            if str(client_id) in users['clients']:
                users['clients'][str(client_id)]['step'] = 'completed'
            context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Payment for order {order_id} from {client_id} approved. Tasks now active!"
            )
            context.bot.send_message(
                chat_id=client_id,
                text="Your payment has been approved! We’re lifting your vibe—results in 4-5 hours for small orders."
            )
            save_users()
    elif data.startswith('reject_payment_') and str(user_id) == ADMIN_USER_ID:
        payment_id = data.split('_')[2]
        if payment_id in users['pending_payments']:
            payment = users['pending_payments'][payment_id]
            client_id = payment['client_id']
            del users['pending_payments'][payment_id]
            if str(client_id) in users['clients']:
                del users['clients'][str(client_id)]
            context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Payment for order from {client_id} rejected."
            )
            context.bot.send_message(
                chat_id=client_id,
                text="Your payment was rejected. Please contact support or try again with /client."
            )
            save_users()
    elif data == 'cancel':
        if str(user_id) in users['clients']:
            del users['clients'][str(user_id)]
            context.bot.send_message(chat_id=user_id, text="Order canceled. Start over with /client if you’d like!")
            save_users()

# Handle user replies
def handle_message(update, context):
    user_id = str(update.message.from_user.id)
    text = update.message.text.lower() if update.message.text else ""

    is_signup_action = (
        (user_id in users['clients'] and users['clients'][user_id]['step'] in ['awaiting_order', 'awaiting_urls', 'awaiting_payment']) or
        (user_id in users['engagers'] and 'awaiting_payout' in users['engagers'][user_id])
    )
    if not check_rate_limit(update.message.from_user.id, is_signup_action=is_signup_action):
        context.bot.send_message(chat_id=user_id, text="Slow down! Wait 2 seconds before your next action.")
        return

    if str(update.message.chat_id) == ADMIN_GROUP_ID and str(user_id) != ADMIN_USER_ID:
        return

    # Client flow
    if user_id in users['clients'] and users['clients'][user_id]['step'] == 'awaiting_order':
        parts = text.split()
        if len(parts) != 3:
            context.bot.send_message(chat_id=user_id, text="Please include handle, platform, and package (e.g., @NaijaFashion Instagram 10).")
            return
        handle, platform, package = parts[0], parts[1], parts[2]
        order_type = users['clients'][user_id]['order_type']

        # Validate platform
        valid_platforms = ['instagram', 'facebook', 'tiktok', 'twitter']
        if platform.lower() not in valid_platforms:
            context.bot.send_message(chat_id=user_id, text="Invalid platform! Supported platforms: Instagram, Facebook, TikTok, Twitter.")
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

        # Validate package
        if order_type == 'bundle':
            if package.lower() not in package_limits['bundle'][platform.lower()]:
                context.bot.send_message(chat_id=user_id, text="Invalid bundle! Use Starter, Pro, or Elite.")
                return
        else:
            if package.lower() not in package_limits[order_type][platform.lower()]:
                context.bot.send_message(chat_id=user_id, text=f"Invalid package! Available packages: {', '.join(package_limits[order_type][platform.lower()].keys())}.")
                return

        # Store order details
        users['clients'][user_id]['handle'] = handle
        users['clients'][user_id]['platform'] = platform.lower()
        users['clients'][user_id]['package'] = package.lower()

        # Prompt for URLs if needed
        if order_type in ['likes', 'comments', 'bundle']:
            if order_type == 'likes':
                context.bot.send_message(
                    chat_id=user_id,
                    text="Please provide the post URL for likes (e.g., https://instagram.com/p/123)."
                )
            elif order_type == 'comments':
                context.bot.send_message(
                    chat_id=user_id,
                    text="Please provide the post URL for comments (e.g., https://instagram.com/p/123)."
                )
            else:  # bundle
                context.bot.send_message(
                    chat_id=user_id,
                    text="Likes and comments will be applied to your 3 most recent posts by default.\n"
                         "If you’d like to specify posts, provide the post URL for likes/comments (e.g., https://instagram.com/p/123). "
                         "If likes and comments are on different posts, provide both URLs separated by a space. Reply 'default' to use your recent posts."
                )
            users['clients'][user_id]['step'] = 'awaiting_urls'
        else:  # followers only
            order_id = f"{user_id}_{int(time.time())}"
            order_details = {
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
            users['clients'][user_id]['order_details'] = order_details  # Store order details temporarily
            keyboard = [[InlineKeyboardButton("Cancel Order", callback_data='cancel')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            context.bot.send_message(
                chat_id=user_id,
                text=f"Order received! Pay ₦{users['clients'][user_id]['amount']} to: 8101062411 OPay Oluwasegun Okusanya. Reply with payment proof.",
                reply_markup=reply_markup
            )
            context.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"New order from {user_id}: {handle} {platform} {package} (followers). Awaiting payment."
            )
        save_users()

    elif user_id in users['clients'] and users['clients'][user_id]['step'] == 'awaiting_urls':
        order_type = users['clients'][user_id]['order_type']
        handle = users['clients'][user_id]['handle']
        platform = users['clients'][user_id]['platform']
        package = users['clients'][user_id]['package']

        # Define package limits and pricing (same as above)
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
                    context.bot.send_message(chat_id=user_id, text="Please provide one URL (for both likes and comments) or two URLs (one for likes, one for comments).")
                    return
                # Validate URLs
                if not use_recent_posts and not (like_url.startswith('http://') or like_url.startswith('https://')):
                    context.bot.send_message(chat_id=user_id, text="Invalid URL! Must start with http:// or https://.")
                    return
                if not use_recent_posts and platform not in like_url.lower():
                    context.bot.send_message(chat_id=user_id, text=f"URL must match the platform ({platform}).")
                    return
            order_details = {
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
        else:  # likes or comments
            urls = text.split()
            if len(urls) != 1:
                context.bot.send_message(chat_id=user_id, text="Please provide exactly one URL.")
                return
            url = urls[0]
            if not (url.startswith('http://') or url.startswith('https://')):
                context.bot.send_message(chat_id=user_id, text="Invalid URL! Must start with http:// or https://.")
                return
            if platform not in url.lower():
                context.bot.send_message(chat_id=user_id, text=f"URL must match the platform ({platform}).")
                return
            if order_type == 'likes':
                order_details = {
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
            else:  # comments
                order_details = {
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
        users['clients'][user_id]['order_details'] = order_details  # Store order details temporarily
        keyboard = [[InlineKeyboardButton("Cancel Order", callback_data='cancel')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=user_id,
            text=f"Order received! Pay ₦{amount} to: 8101062411 OPay Oluwasegun Okusanya. Reply with payment proof.",
            reply_markup=reply_markup
        )
        context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"New order from {user_id}: {handle} {platform} {package} ({order_type}). Awaiting payment."
        )
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
            context.bot.send_message(
                chat_id=user_id,
                text="Payment proof submitted! Awaiting admin approval."
            )
            keyboard = [
                [InlineKeyboardButton("Approve", callback_data=f'approve_payment_{payment_id}'),
                 InlineKeyboardButton("Reject", callback_data=f'reject_payment_{payment_id}')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            context.bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=update.message.photo[-1].file_id,
                caption=f"Payment proof from {user_id} for order: {users['clients'][user_id]['order_id']}. Amount: ₦{users['clients'][user_id]['amount']}",
                reply_markup=reply_markup
            )
            save_users()
        else:
            context.bot.send_message(chat_id=user_id, text="Please include a screenshot of your payment proof!")

    # Task submissions with auto-accept
    elif user_id in users['engagers'] and users['engagers'][user_id].get('joined') and update.message.photo:
        user_data = users['engagers'][user_id]
        for order_id in users['active_orders']:
            for task_type in ['f', 'l', 'c']:
                timer_key = f"{order_id}_{task_type}"
                if timer_key in user_data['task_timers']:
                    claim_time = user_data['task_timers'][timer_key]
                    time_spent = time.time() - claim_time
                    order = users['active_orders'][order_id]
                    if task_type in ['l', 'c'] and time_spent < 60:
                        context.bot.send_message(
                            chat_id=user_id,
                            text=f"Too fast! Spend 60 seconds on the post. Only {int(time_spent)}s elapsed."
                        )
                        logger.info(f"Sending rejected task screenshot to admin group {ADMIN_GROUP_ID} for user {user_id}, task {task_type}, order {order_id}")
                        context.bot.send_photo(
                            chat_id=ADMIN_GROUP_ID,
                            photo=update.message.photo[-1].file_id,
                            caption=f"Rejected task:\nEngager: {user_id}\nTask: {task_type}\nOrder: {order_id}\nTime Spent: {int(time_spent)}s"
                        )
                        return
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
                        del users['active_orders'][order_id]
                    del user_data['task_timers'][timer_key]
                    context.bot.send_message(
                        chat_id=user_id,
                        text=f"Your {task_type.upper()} task was auto-approved! +₦{earnings}. New balance: ₦{user_data['earnings']}."
                    )
                    logger.info(f"Sending auto-approved task screenshot to admin group {ADMIN_GROUP_ID} for user {user_id}, task {task_type}, order {order_id}")
                    context.bot.send_photo(
                        chat_id=ADMIN_GROUP_ID,
                        photo=update.message.photo[-1].file_id,
                        caption=f"Auto-approved task:\nEngager: {user_id}\nTask: {task_type}\nOrder: {order_id}\nTime Spent: {int(time_spent)}s"
                    )
                    save_users()
                    return
        context.bot.send_message(chat_id=user_id, text="Claim a task first with /tasks!")

    # Payout request
    elif user_id in users['engagers'] and users['engagers'][user_id].get('awaiting_payout', False):
        account = text.strip()
        if not account.isdigit() or len(account) != 10:
            context.bot.send_message(chat_id=user_id, text="Invalid account number! Provide a 10-digit OPay number.")
            return
        earnings = users['engagers'][user_id]['earnings']
        if earnings < 1000:
            context.bot.send_message(chat_id=user_id, text="Minimum withdrawal is ₦1,000. Keep earning!")
            return
        payout_id = f"{user_id}_{int(time.time())}"
        users['pending_payouts'][payout_id] = {
            'engager_id': user_id,
            'amount': earnings,
            'account': account
        }
        users['engagers'][user_id]['awaiting_payout'] = False
        context.bot.send_message(
            chat_id=user_id,
            text=f"Withdrawal request for ₦{earnings} to {account} submitted! Awaiting approval."
        )
        keyboard = [
            [InlineKeyboardButton("Approve", callback_data=f'approve_payout_{payout_id}'),
             InlineKeyboardButton("Reject", callback_data=f'reject_payout_{payout_id}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Payout request:\nEngager: {user_id}\nAmount: ₦{earnings}\nAccount: {account}",
            reply_markup=reply_markup
        )
        save_users()

# Main function
def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("client", client))
    dp.add_handler(CommandHandler("engager", engager))
    dp.add_handler(CommandHandler("tasks", tasks))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("withdraw", withdraw))
    dp.add_handler(CallbackQueryHandler(button))
    dp.add_handler(MessageHandler(Filters.text | Filters.photo, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()