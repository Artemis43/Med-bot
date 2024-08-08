import logging
import sqlite3
import os
import shutil
import sys
import psutil
import time
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types, exceptions
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from aiogram.utils.executor import start_webhook
from aiogram.utils.exceptions import MessageNotModified, ChatNotFound
from keep_alive import keep_alive
#import dotenv
#from dotenv import load_dotenv
#load_dotenv()

keep_alive()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Bot token and Admin ID
API_TOKEN = os.environ.get('ApiToken')
ADMIN_IDS = os.environ.get('AdminIds').split(',')
CHANNEL_ID = os.environ.get('MyChannel')
STICKER_ID = os.environ.get('MedSticker') 
DB_FILE_PATH = 'file_management.db'

# Webhook settings
WEBHOOK_HOST = os.environ.get('RenderUrl') #https://YourDomain.onrender.com  # Change this to your server URL
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# List of required channels to join
REQUIRED_CHANNELS = os.environ.get('ForcedSubs').split(',') #['@TheFitgirlRepacks', '@fitgirl_repacks_pc']  # Add your channel usernames here

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Connect to the SQLite database
conn = sqlite3.connect(DB_FILE_PATH)
cursor = conn.cursor()

# New db upload initiated only during /restore
awaiting_new_db_upload = False

# Function to check if a column exists in a table
# Prevents the error - sqlite3.OperationalError: duplicate column name: approved
def column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns

# Create tables for folders and files
cursor.execute('''
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER,
    FOREIGN KEY (parent_id) REFERENCES folders (id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    folder_id INTEGER,
    message_id INTEGER,
    FOREIGN KEY (folder_id) REFERENCES folders (id)
)
''')
conn.commit()

# Create table for users for broadcast
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
''')
conn.commit()

# Create table for storing the current caption
cursor.execute('''
CREATE TABLE IF NOT EXISTS current_caption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caption_type TEXT NOT NULL,  -- 'custom' or 'append'
    custom_text TEXT
)
''')
conn.commit()

# Add 'premium_expiration' column if it doesn't exist
if not column_exists(cursor, 'users', 'premium_expiration'):
    cursor.execute('''
    ALTER TABLE users ADD COLUMN premium_expiration DATETIME
    ''')
    conn.commit()

# Add 'approved' column if it doesn't exist
if not column_exists(cursor, 'users', 'approved'):
    cursor.execute('''
    ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 0
    ''')
    conn.commit()

# Add 'status' column if it doesn't exist
if not column_exists(cursor, 'users', 'status'):
    cursor.execute('''
    ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'pending'
    ''')
    conn.commit()

# Add 'caption' column if it doesn't exist
if not column_exists(cursor, 'files', 'caption'):
    cursor.execute('''
    ALTER TABLE files ADD COLUMN caption TEXT
    ''')
    conn.commit()

# Add 'premium' column if it doesn't exist
if not column_exists(cursor, 'folders', 'premium'):
    cursor.execute('''
    ALTER TABLE folders ADD COLUMN premium INTEGER DEFAULT 0
    ''')
    conn.commit()

# Add 'premium' column if it doesn't exist
if not column_exists(cursor, 'users', 'premium'):
    cursor.execute('''
    ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0
    ''')
    conn.commit()

# Add 'download_count' column if it doesn't exist
if not column_exists(cursor, 'folders', 'download_count'):
    cursor.execute('''
    ALTER TABLE folders ADD COLUMN download_count INTEGER DEFAULT 0
    ''')
    conn.commit()

# Add 'last_download' column if it doesn't exist
if not column_exists(cursor, 'users', 'last_download'):
    cursor.execute('''
    ALTER TABLE users ADD COLUMN last_download DATETIME
    ''')
    conn.commit()

# Global dictionary to track the current upload folder for each admin
# So that all admins can upload files simultaneously
current_upload_folders = {}

# Function to set the current upload folder for a user
def set_current_upload_folder(user_id, folder_name):
    current_upload_folders[user_id] = folder_name

# Function to get the current upload folder for a user
def get_current_upload_folder(user_id):
    return current_upload_folders.get(user_id)

# set premium status for a folder
@dp.message_handler(commands=['folder'])
async def set_premium_status(message: types.Message):
    # Split the message to get the folder ID and the new premium status
    try:
        parts = message.text.split()
        folder_id = int(parts[1])
        premium_status = int(parts[2])
        
        if premium_status not in (0, 1):
            raise ValueError("Invalid premium status. Use 0 for non-premium and 1 for premium.")
        
        # Update the premium status in the database
        cursor.execute('''
        UPDATE folders
        SET premium = ?
        WHERE id = ?
        ''', (premium_status, folder_id))
        conn.commit()

        await message.reply(f"Folder ID {folder_id} premium status set to {premium_status}.")
    except (IndexError, ValueError) as e:
        await message.reply("Usage: /setpremium <folder_id> <0 or 1>\nExample: /setpremium 123 1")
    except sqlite3.Error as e:
        await message.reply(f"An error occurred while updating the folder: {e}")

# Set premium status for a user
@dp.message_handler(commands=['user'])
async def set_premium(message: types.Message):
    if str(message.from_user.id) in ADMIN_IDS:
        args = message.get_args().split()

        if len(args) != 2:
            await message.reply("Usage: /setpremium <user_id> <on|off>")
            return

        user_id, action = args
        user_id = int(user_id)
        action = action.lower()

        if action == 'on':
            expiration_date = datetime.now() + timedelta(days=15)
            cursor.execute('''
                UPDATE users 
                SET premium = 1, premium_expiration = ? 
                WHERE user_id = ?
            ''', (expiration_date, user_id))
            conn.commit()
            
            await message.reply(f"User {user_id} has been marked as premium until {expiration_date}.")
            
            try:
                await bot.send_message(user_id, "Congratulations! You have been upgraded to Premium for 15 days.")
            except exceptions.BotBlocked:
                await message.reply(f"Could not notify user {user_id}, as they have blocked the bot.")

            # Schedule task to remove premium after 15 days
            asyncio.create_task(remove_premium_after_expiry(user_id, expiration_date))

        elif action == 'off':
            cursor.execute('''
                UPDATE users 
                SET premium = 0, premium_expiration = NULL 
                WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
            
            await message.reply(f"User {user_id} has been removed from premium status.")
        else:
            await message.reply("Invalid action. Use 'on' to set premium or 'off' to remove premium.")
    else:
        await message.reply("You are not authorized to perform this action.")

async def remove_premium_after_expiry(user_id: int, expiration_date: datetime):
    now = datetime.now()
    sleep_time = (expiration_date - now).total_seconds()
    await asyncio.sleep(sleep_time)
    
    cursor.execute('''
        UPDATE users 
        SET premium = 0, premium_expiration = NULL 
        WHERE user_id = ? AND premium_expiration <= ?
    ''', (user_id, datetime.now()))
    conn.commit()
    
    try:
        await bot.send_message(user_id, "Your Premium has expired.")
    except exceptions.BotBlocked:
        logging.warning(f"Could not notify user {user_id} about premium expiration, as they have blocked the bot.")

# Notifies Admins for approve/reject permission of the bot for new users
async def notify_admins(user_id, username):
    username = username or "N/A"  # Use "N/A" if the username is None
    first_admin_id = ADMIN_IDS[0]  # Get the first admin ID

    try:
        await bot.send_message(
            first_admin_id,
            f"User @{username} (ID: {user_id}) is requesting access to the bot. Approve?\n\n/approve_{user_id}\n\n/reject_{user_id}"
        )
    except exceptions.BotBlocked:
        logging.warning(f"Admin {first_admin_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"Admin {first_admin_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending message to admin {first_admin_id}: {e}")

# Helper function to check if the user is a member of the required channels (ForcedSubs)
async def is_user_member(user_id):
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logging.error(f"Error checking membership for channel {channel}: {e}")
            return False
    return True

"""# The UI of the bot
async def send_ui(chat_id, message_id=None, current_folder=None, selected_letter=None):
    # Fetch the number of files and folders
    cursor.execute('SELECT COUNT(*) FROM folders')
    folder_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM files')
    file_count = cursor.fetchone()[0]

    # Visual representation of the current location
    current_path = "Root"
    if current_folder:
        current_path = f"Root / {current_folder}"

    # Create inline keyboard for navigation
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🙃 Refresh", callback_data='root'))

    # Get chat info to retrieve the name
    chat = await bot.get_chat(chat_id)
    chat_name = chat.full_name if chat.full_name else chat.username

    # Compose the UI message text
    text = (
        f"**Hello `{chat_name}`👋,**\n\n"
        f"**I'm The Medical Content Bot ✨**\n"
        f"**About Me:** /about\n"
        f"**How to Use:** /help\n\n"
        f"**List of Folders 🔽**\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n"
    )

    # Check if the user is premium
    cursor.execute('SELECT premium FROM users WHERE user_id = ?', (chat_id,))
    is_premium_user = cursor.fetchone()
    is_premium_user = is_premium_user and is_premium_user[0]

    # Fetch and list folders in alphabetical order, filter based on premium status
    if is_premium_user:
        cursor.execute('SELECT name FROM folders WHERE parent_id IS NULL ORDER BY name')
    else:
        cursor.execute('SELECT name FROM folders WHERE parent_id IS NULL AND premium = 0 ORDER BY name')

    folders = cursor.fetchall()

    # Add folders to the text
    for folder in folders:
        text += f"|-📒 `{folder[0]}`\n"

    text += "\n\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n`Please share any files that you may think are useful to others :D` - [Share](https://t.me/MedContent_Adminbot)"

    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
    except exceptions.MessageNotModified:
        pass  # Handle the exception gracefully by ignoring it"""

async def send_ui(chat_id, message_id=None, current_folder=None, selected_letter=None):
    # Fetch the number of files and folders
    cursor.execute('SELECT COUNT(*) FROM folders')
    folder_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM files')
    file_count = cursor.fetchone()[0]

    # Visual representation of the current location
    current_path = "Root"
    if current_folder:
        current_path = f"Root / {current_folder}"

    # Create inline keyboard for navigation
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🙃 Refresh", callback_data='root'))

    # Get chat info to retrieve the name
    chat = await bot.get_chat(chat_id)
    chat_name = chat.full_name if chat.full_name else chat.username

    # Check if the user is a premium user and fetch the premium expiration date
    cursor.execute('SELECT premium, premium_expiration FROM users WHERE user_id = ?', (chat_id,))
    user_data = cursor.fetchone()

    is_premium_user = user_data and user_data[0] == 1
    premium_expiration = user_data[1] if is_premium_user else None

    """# Convert premium_expiration to a datetime object if it exists
    expiration_date_str = "Unknown"
    if premium_expiration:
        try:
            # Assuming the premium_expiration is stored in a format like 'YYYY-MM-DD HH:MM:SS'
            premium_expiration = datetime.strptime(premium_expiration, '%Y-%m-%d %H:%M:%S')
            expiration_date_str = premium_expiration.strftime('%Y-%m-%d')
        except ValueError:
            premium_expiration = None  # Handle any unexpected date format"""

    # Compose the UI message text
    text = f"**Hello `{chat_name}`👋,**\n\n"
    text += f"**I'm The Medical Content Bot ✨**\n"
    text += f"**About Me:** /about\n"
    text += f"**How to Use:** /help\n\n"

    if is_premium_user:
        text += f"🥳 **You are a Premium User!**\n\n"
        #text += f"**Your premium status expires on:** `{expiration_date_str}`\n\n"
    else:
        text += f"🌟 **[Upgrade to Premium](https://t.me/medcontentbotinformation/2)**\n\n"

    text += f"**List of Folders 🔽**\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n"

    # Fetch and list all folders, including premium status
    cursor.execute('SELECT name, premium FROM folders WHERE parent_id IS NULL ORDER BY name')
    folders = cursor.fetchall()

    # Add folders to the text with appropriate labeling
    for folder_name, premium in folders:
        if is_premium_user or not premium:
            text += f"|-📒 `{folder_name}`\n"
        else:
            text += f"|-📒 `{folder_name}` (Premium)\n"

    text += "\n\n\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\n\n`Share files, Get Rewards :D`\n[Share Now](https://t.me/medcontentbotinformation/3)"

    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
    except exceptions.MessageNotModified:
        pass  # Handle the exception gracefully by ignoring it

# Adds new users to the database
def add_user_to_db(user_id):
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user:
        status = 'approved' if str(user_id) in ADMIN_IDS else 'pending'
        cursor.execute('INSERT INTO users (user_id, status) VALUES (?, ?)', (user_id, status))
        conn.commit()

# /start command
@dp.message_handler(commands=['start'])
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    add_user_to_db(user_id)
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if user[0] == 'pending':
        await message.answer("Hello,\nI'm The Medical Content Bot ✨\n\nTo prevent scammers and copyright strikes, we allow only Medical students to use this bot 🙃\n\n👉 Verify Now:\nhttps://t.me/medcontentbotinformation/4>\n\nYou will be granted access only after verification!")
        await notify_admins(user_id, username)  # Ensure this is after the initial message to the user
    elif user[0] == 'approved':
        await message.answer("Welcome! You have been given access to the bot 🙌")
        if not await is_user_member(user_id):
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(3)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n\nAfter joining 👉 /start\n"
            keyboard = InlineKeyboardMarkup(row_width=1)
            for channel in REQUIRED_CHANNELS:
                button = InlineKeyboardButton(text=channel, url=f"https://t.me/{channel.lstrip('@')}")
                keyboard.add(button)
            await message.reply(join_message, reply_markup=keyboard)
        else:
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(2)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            await send_ui(message.chat.id)
    elif user[0] == 'rejected':
        await message.answer("Your access request has been rejected. You cannot use this bot 😢\n\nIf you think this is a mistake, Contact Us: @MedContent_Adminbot")


# Approve handler
@dp.message_handler(lambda message: message.text.startswith('/approve_') and str(message.from_user.id) in ADMIN_IDS)
async def approve_user(message: types.Message):
    user_id = int(message.text.split('_')[1])
    cursor.execute('UPDATE users SET status = ? WHERE user_id = ?', ('approved', user_id))
    conn.commit()
    await message.answer(f"User {user_id} has been approved.")
    try:
        await bot.send_message(user_id, "You have been approved to use the bot\n\nClick here 👉 /start")
    except exceptions.BotBlocked:
        logging.warning(f"User {user_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {user_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending approval message to user {user_id}: {e}")

# Reject handler
@dp.message_handler(lambda message: message.text.startswith('/reject_') and str(message.from_user.id) in ADMIN_IDS)
async def reject_user(message: types.Message):
    user_id = int(message.text.split('_')[1])
    cursor.execute('UPDATE users SET status = ? WHERE user_id = ?', ('rejected', user_id))
    conn.commit()
    await message.answer(f"User {user_id} has been rejected.")
    try:
        await bot.send_message(user_id, "You have been rejected from using the bot 🫤\n\nIf you think this is a mistake, **Contact Us:** @MedContent_Adminbot")
    except exceptions.BotBlocked:
        logging.warning(f"User {user_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {user_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending rejection message to user {user_id}: {e}")

# Command to display help information
@dp.message_handler(commands=['help'])
async def help(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        help_text = (
            "**The Medical Content Bot ✨**\n\n"
            "/start - Start the bot\n"
            "/help - Display this help message\n"
            "/download <folder\\_name> - Send with Folder name to get all files\n\n"
            "**💫 How to Use:**\n\n"
            "|- Put folder name after /download\n\n"
            "|- Send and get all your files👌\n\n"
            "**NOTE:\n\n**"
            "`We donot host any content; All the content is from third-party servers.`"
            #"**Contact Us:** [Here](https://t.me/MedContent_Adminbot)"
        )
        await message.reply(help_text, parse_mode='Markdown')

# Command to display about information
@dp.message_handler(commands=['about'])
async def help(message: types.Message):
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        about_text = (
            "**The Medical Content Bot ✨**\n\n"
            "I knew Telegram was a gold mine for all the students who are interested to learn\n"
            "But, most of my time was gone in the search of the desired content. Thus, I came up with an idea of this bot!\n\n"
            "However, sometimes the things I would create may need some help to be alive...\nAlone, I do so little. Believe me when I say this - Together, we can do much better!\n\n"
            "**Upgrade to Premium:** [Here](https://t.me/medcontentbotinformation/2)\n\n"
            "About the Bot:\n"
            "Usage limit - `1 CPU|2 GB (RAM)`\n"
            "Hosting Cost ~ `₹560/Month`\n"
            "Framework - `Python-Flask`\n\n"
            "All the Best!"
        )
        await message.reply(about_text, parse_mode='Markdown')

# command to create new folder (Admin only)
@dp.message_handler(commands=['newfolder'])
async def create_folder(message: types.Message):
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to create folders. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(user_id) not in ADMIN_IDS:
            await message.reply("You are not authorized to create folders.")
            return

        args = message.get_args().split(' ', 1)
        folder_name = args[0]
        premium = 0  # Default to non-premium

        if len(args) > 1 and args[1].strip().upper() == 'PREMIUM':
            premium = 1

        if not folder_name:
            await message.reply("Please specify a folder name.")
            return

        cursor.execute('INSERT INTO folders (name, premium) VALUES (?, ?)', (folder_name, premium))
        conn.commit()

        set_current_upload_folder(user_id, folder_name)

        await message.reply(f"Folder '{folder_name}' created {'as a PREMIUM folder' if premium else ''} and set as the current upload folder.")

# Command to delete a folder and its contents (Admin only)
@dp.message_handler(commands=['deletefolder'])
async def delete_folder(message: types.Message):
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to delete folders. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to delete folders.")
        return

    folder_name = message.get_args()
    if not folder_name:
        await message.reply("Please specify a folder name.")
        return

    # Get the folder ID to be deleted
    cursor.execute('SELECT id FROM folders WHERE name = ?', (folder_name,))
    folder_id = cursor.fetchone()

    if not folder_id:
        await message.reply("Folder not found.")
        return

    folder_id = folder_id[0]

    # Get the message IDs of the files in the folder
    cursor.execute('SELECT message_id FROM files WHERE folder_id = ?', (folder_id,))
    message_ids = cursor.fetchall()

    # Delete the files from the channel and the database
    for message_id in message_ids:
        try:
            await bot.delete_message(CHANNEL_ID, message_id[0])
        except exceptions.MessageToDeleteNotFound:
            continue  # Skip if the message is not found

    cursor.execute('DELETE FROM files WHERE folder_id = ?', (folder_id,))

    # Delete the folder from the database
    cursor.execute('DELETE FROM folders WHERE id = ?', (folder_id,))
    conn.commit()

    await message.reply(f"Folder '{folder_name}' and its contents deleted.")

# Download files from a folder
@dp.message_handler(commands=['download'])
async def get_all_files(message: types.Message):
    user_id = message.from_user.id

    cursor.execute('SELECT status, premium, last_download FROM users WHERE user_id = ?', (user_id,))
    user_info = cursor.fetchone()

    if not user_info or user_info[0] != 'approved':
        await message.reply("You are not authorized to download content. Please wait for admin approval.")
        return

    user_status, is_premium, last_download = user_info

    # Check if the user is a member of the required channels
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
        return

    # Check the time since the last download
    current_time = datetime.now()
    time_interval = timedelta(minutes=2) if is_premium else timedelta(minutes=10)

    if last_download:
        last_download_time = datetime.strptime(last_download, "%Y-%m-%d %H:%M:%S")
        if current_time - last_download_time < time_interval:
            wait_time = (last_download_time + time_interval) - current_time
            await message.reply(f"Please wait {wait_time.seconds // 60} minutes and {wait_time.seconds % 60} seconds before downloading again.")
            return

    folder_name = message.get_args()
    if not folder_name:
        await message.reply("Please specify a folder name.")
        return

    # Get the folder ID, premium status, and download count
    cursor.execute('SELECT id, premium FROM folders WHERE name = ?', (folder_name,))
    folder_info = cursor.fetchone()

    if not folder_info:
        await message.reply("Folder not found.")
        return

    folder_id, is_premium_folder = folder_info

    # Check if the folder is premium and if the user is allowed to access it
    if is_premium_folder and not is_premium:
        await message.reply("This folder is for premium users only. Please upgrade to access it.")
        return

    # Increment the download count
    cursor.execute('''
    UPDATE folders
    SET download_count = download_count + 1
    WHERE id = ?
    ''', (folder_id,))
    conn.commit()

    # Get the file IDs, names, and captions in the folder
    cursor.execute('SELECT file_id, file_name, caption FROM files WHERE folder_id = ?', (folder_id,))
    files = cursor.fetchall()

    if files:
        # Determine the time to delete messages based on the number of files
        num_files = len(files)
        if num_files <= 100:
            delete_time = 180  # 3 minutes in seconds
        elif num_files <= 200:
            delete_time = 360  # 6 minutes in seconds
        elif num_files <= 300:
            delete_time = 540  # 9 minutes in seconds
        else:
            delete_time = 600  # Default to 10 minutes if more than 300 files

        messages_to_delete = []
        for file in files:
            sent_message = await bot.send_document(message.chat.id, file[0], caption=file[2])
            messages_to_delete.append(sent_message.message_id)

            # Update the last download time for the user
            cursor.execute('''
            UPDATE users
            SET last_download = ?
            WHERE user_id = ?
            ''', (current_time.strftime("%Y-%m-%d %H:%M:%S"), user_id))
            conn.commit()

        # Notify the user that files will be deleted in a specified time
        warning_message = await message.reply(f"The files will be deleted in {delete_time // 60} minutes.")

        # Schedule deletion of messages after the calculated time
        await asyncio.sleep(delete_time)

        for message_id in messages_to_delete:
            try:
                await bot.delete_message(message.chat.id, message_id)
            except exceptions.MessageToDeleteNotFound:
                continue

        # Edit the warning message to indicate files have been deleted
        try:
            await bot.edit_message_text("Files deleted.", chat_id=message.chat.id, message_id=warning_message.message_id)
        except MessageNotModified:
            pass
    else:
        await message.reply("No files found in the specified folder.")

# command to broadcast messages to users (Admin only)
@dp.message_handler(commands=['broadcast'])
async def broadcast_message(message: types.Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to send broadcasts.")
        return

    broadcast_message = message.get_args()
    if not broadcast_message:
        await message.reply("Please provide a message to broadcast.")
        return

    try:
        # Fetch all users from the database
        cursor.execute('SELECT user_id FROM users')
        user_ids = cursor.fetchall()

        # Send the broadcast message to all users
        for user_id in user_ids:
            try:
                await bot.send_message(user_id[0], broadcast_message)
            except Exception as e:
                logging.error(f"Error sending broadcast to user {user_id[0]}: {e}")

        await message.reply(f"Broadcast sent to {len(user_ids)} users.")
    except Exception as e:
        logging.error(f"Error fetching users: {e}")
        await message.reply("Error fetching users. Please try again later.")

# Command to send a backup of the database file (Admin only)
@dp.message_handler(commands=['backup'])
async def send_backup(message: types.Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to get the backup.")
        return

    # Path to the database file
    db_file_path = 'file_management.db'
    
    try:
        await bot.send_document(message.chat.id, types.InputFile(db_file_path))
    except Exception as e:
        logging.error(f"Error sending backup file: {e}")
        await message.reply("Error sending backup file. Please try again later.")

# Command to replace the existing database file with a new one
@dp.message_handler(commands=['restore'])
async def new_db(message: types.Message):
    global awaiting_new_db_upload

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to upload a new database file.")
        return

    awaiting_new_db_upload = True
    await message.reply("Please upload the new 'file_management.db' file to replace the existing database.")

@dp.message_handler(commands=['caption'])
async def set_caption(message: types.Message):
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to set captions. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(user_id) not in ADMIN_IDS:
            await message.reply("You are not authorized to set captions.")
            return

        args = message.get_args()
        if not args:
            await message.reply("Please specify 'custom <your text>' or 'append <your text>'.")
            return

        args_split = args.split(" ", 1)
        caption_type = args_split[0].lower()
        custom_text = args_split[1] if len(args_split) > 1 else ""

        if caption_type not in ['custom', 'append']:
            await message.reply("Invalid option. Use 'custom <your text>' or 'append <your text>'.")
            return

        # Clear the existing caption configuration
        cursor.execute('DELETE FROM current_caption')
        cursor.execute('INSERT INTO current_caption (caption_type, custom_text) VALUES (?, ?)', (caption_type, custom_text))
        conn.commit()

        await message.reply(f"Caption set to '{caption_type}' with text: {custom_text}")

# Handler for incoming documents
@dp.message_handler(content_types=[types.ContentType.DOCUMENT])
async def handle_document(message: types.Message):
    user_id = message.from_user.id

    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to upload documents. Please wait for admin approval.")
        return

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        global awaiting_new_db_upload

        if awaiting_new_db_upload and message.document.file_name == "file_management.db":
            if str(user_id) not in ADMIN_IDS:
                awaiting_new_db_upload = False
                await message.reply("You are not authorized to upload a new database file.")
                return

            file_path = f"new_{message.document.file_name}"
            await message.document.download(destination_file=file_path)

            shutil.move(file_path, DB_FILE_PATH)
            awaiting_new_db_upload = False
            await message.reply("Database file replaced successfully. Restarting the bot to apply changes.")

            os.execl(sys.executable, sys.executable, *sys.argv)
            return

        if str(user_id) not in ADMIN_IDS:
            await message.reply("You are not authorized to upload files.")
            return

        file_id = message.document.file_id
        file_name = message.document.file_name

        current_upload_folder = get_current_upload_folder(user_id)
        if current_upload_folder:
            cursor.execute('SELECT id FROM folders WHERE name = ?', (current_upload_folder,))
            folder_id = cursor.fetchone()
            if folder_id:
                folder_id = folder_id[0]
            else:
                folder_id = None
        else:
            folder_id = None

        # Get the current caption configuration
        cursor.execute('SELECT caption_type, custom_text FROM current_caption ORDER BY id DESC LIMIT 1')
        caption_config = cursor.fetchone()

        if caption_config:
            caption_type, custom_text = caption_config
            if caption_type == 'custom':
                specific_caption = custom_text
            elif caption_type == 'append':
                # Append the custom text to the document's original caption
                specific_caption = f"{message.caption or ''}\n{custom_text}"
        else:
            # Default caption if no custom caption is set
            specific_caption = message.caption or "@Medical_Contentbot\nEver-growing archive of medical content"

        # Proceed with the file upload
        current_upload_folder = get_current_upload_folder(user_id)
        if current_upload_folder:
            cursor.execute('SELECT id FROM folders WHERE name = ?', (current_upload_folder,))
            folder_id = cursor.fetchone()
            if folder_id:
                folder_id = folder_id[0]
            else:
                folder_id = None
        else:
            folder_id = None

        # Send the file to the channel with the specific caption and get the message ID
        sent_message = await bot.send_document(CHANNEL_ID, file_id, caption=specific_caption)
        message_id = sent_message.message_id

        cursor.execute('INSERT INTO files (file_id, file_name, folder_id, message_id, caption) VALUES (?, ?, ?, ?, ?)', 
                    (file_id, file_name, folder_id, message_id, specific_caption))
        conn.commit()

        await message.reply(f"File '{file_name}' uploaded successfully with the caption: {specific_caption}")

#Callback Query handler to filter UI based on inline selections
@dp.callback_query_handler(lambda c: c.data)
async def process_callback(callback_query: types.CallbackQuery):
    global current_upload_folder
    user_id = callback_query.from_user.id

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await bot.answer_callback_query(callback_query.id)
        await bot.send_message(callback_query.from_user.id, join_message)
        return

    code = callback_query.data

    if code == 'root':
        await send_ui(callback_query.from_user.id, callback_query.message.message_id)
    else:
        current_upload_folder = code
        await send_ui(callback_query.from_user.id, callback_query.message.message_id, current_folder=current_upload_folder)

    await bot.answer_callback_query(callback_query.id)

# get the list of all (Admin Only)
# Get the list of all folders, premium folders, users, and download counts (Admin Only)
@dp.message_handler(commands=['list'])
async def list_all(message: types.Message):
    user_id = message.from_user.id
    
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to use the bot. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to access the database.")
            return
        
    try:
        # Fetch all folders with download counts
        cursor.execute('SELECT id, name, download_count FROM folders')
        folders = cursor.fetchall()

        # Fetch all premium folders with download counts
        cursor.execute('SELECT id, name, download_count FROM folders WHERE premium = 1')
        premium_folders = cursor.fetchall()

        # Fetch all users
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()

        # Fetch all premium users
        cursor.execute('SELECT user_id FROM users WHERE premium = 1')
        premium_users = cursor.fetchall()

        # Prepare the response message
        response = "<b>Folders:</b>\n"
        if folders:
            response += "\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in folders])
        else:
            response += "No folders found."

        response += "\n\n<b>Premium Folders:</b>\n"
        if premium_folders:
            response += "\n".join([f"- {folder[1]} (ID: {folder[0]}, Downloads: {folder[2]})" for folder in premium_folders])
        else:
            response += "No premium folders found."

        response += "\n\n<b>Users:</b>\n"
        if users:
            response += "\n".join([f"- User ID: {user[0]}" for user in users])
        else:
            response += "No users found."

        response += "\n\n<b>Premium Users:</b>\n"
        if premium_users:
            response += "\n".join([f"- User ID: {user[0]}" for user in premium_users])
        else:
            response += "No premium users found."

        # Send the response message
        await message.answer(response, parse_mode=ParseMode.HTML)
    
    except Exception as e:
        logging.error(f"Error in /list command: {e}")
        await message.answer("An error occurred while fetching the list.")

# Command to stop the bot and notify users (Admin only)
@dp.message_handler(commands=['stop'])
async def stop(message: types.Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to stop the bot.")
        return

    await message.reply("Bot is stopping...")

    try:
        # Fetch all users from the database
        cursor.execute('SELECT user_id FROM users')
        user_ids = cursor.fetchall()

        # Send the broadcast message to all users
        for user_id in user_ids:
            try:
                await bot.send_message(user_id[0], "Bot under maintenance")
            except Exception as e:
                logging.error(f"Error sending broadcast to user {user_id[0]}: {e}")

        await message.reply(f"Broadcast sent to {len(user_ids)} users.")
    except Exception as e:
        logging.error(f"Error fetching users: {e}")
        await message.reply("Error fetching users. Please try again later.")
    
    # Use sys.exit to terminate the bot
    sys.exit("Bot stopped by admin command.")

# Command to rename a folder (Admin only)
@dp.message_handler(commands=['renamefolder'])
async def rename_folder(message: types.Message):
    user_id = message.from_user.id
    
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user or user[0] != 'approved':
        await message.reply("You are not authorized to rename folders. Please wait for admin approval.")
        return
    
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected ✊\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to rename folders.")
            return

        args = message.get_args().split(',')
        if len(args) != 2:
            await message.reply("Please specify the current folder name and the new folder name in the format: /renamefolder <current_name>,<new_name>")
            return

        current_name, new_name = args

        # Check if the folder with the current name exists
        cursor.execute('SELECT id FROM folders WHERE name = ?', (current_name,))
        folder_id = cursor.fetchone()

        if not folder_id:
            await message.reply("Folder not found.")
            return

        # Check if the new folder name already exists
        cursor.execute('SELECT id FROM folders WHERE name = ?', (new_name,))
        existing_folder = cursor.fetchone()

        if existing_folder:
            await message.reply(f"A folder with the name '{new_name}' already exists. Please choose a different name.")
            return

        # Update the folder name in the database
        cursor.execute('UPDATE folders SET name = ? WHERE id = ?', (new_name, folder_id[0]))
        conn.commit()
        
        await message.reply(f"Folder '{current_name}' has been renamed to '{new_name}'.")

# Set up webhook
async def on_startup(dispatcher):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(dispatcher):
    logging.warning('Shutting down..')
    await bot.delete_webhook()
    conn.close()
    logging.warning('Bye!')

# Start the bot
if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
