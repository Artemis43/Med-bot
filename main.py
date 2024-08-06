import logging
import sqlite3
import os
import shutil
import sys
import psutil
import time
#import dotenv
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, executor, types, exceptions
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from aiogram.utils.executor import start_webhook
from aiogram.utils.exceptions import MessageNotModified, ChatNotFound
from keep_alive import keep_alive
#from dotenv import load_dotenv

keep_alive()

#load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Bot token and Admin ID
API_TOKEN = os.environ.get('ApiToken')
ADMIN_IDS = os.environ.get('AdminIds').split(',')
CHANNEL_ID = os.environ.get('MyChannel')
STICKER_ID = os.environ.get('MedSticker')
DB_FILE_PATH = 'file_management.db'

# Webhook settings
WEBHOOK_HOST = os.environ.get('RenderUrl') #'https://rabid-owl-bot.onrender.com'  # Change this to your server URL
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

awaiting_new_db_upload = False

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

cursor.execute('''
ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 0
''')
conn.commit()

# Add a status column to users table
cursor.execute('''
ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'pending'
''')
conn.commit()



# Global dictionary to track the current upload folder for each user
current_upload_folders = {}

# Function to set the current upload folder for a user
def set_current_upload_folder(user_id, folder_name):
    current_upload_folders[user_id] = folder_name

# Function to get the current upload folder for a user
def get_current_upload_folder(user_id):
    return current_upload_folders.get(user_id)

async def notify_admins(user_id):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"User {user_id} is requesting access to the bot. Approve?\n\n/approve_{user_id}\n\n/reject_{user_id}"
            )
        except exceptions.BotBlocked:
            logging.warning(f"Admin {admin_id} has blocked the bot.")
        except exceptions.ChatNotFound:
            logging.warning(f"Admin {admin_id} chat not found.")
        except Exception as e:
            logging.error(f"Error sending message to admin {admin_id}: {e}")




# Helper function to check if the user is a member of the required channels
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

# Command to replace the existing database file with a new one
@dp.message_handler(commands=['restore'])
async def new_db(message: types.Message):
    global awaiting_new_db_upload

    if str(message.from_user.id) not in ADMIN_IDS:
        await message.reply("You are not authorized to upload a new database file.")
        return

    awaiting_new_db_upload = True
    await message.reply("Please upload the new 'file_management.db' file to replace the existing database.")

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

    # Compose the UI message text
    text = (
        f"**Welcome to The Medical Content Bot ✨**\n\n"
        #f"**New game added every 3 hrs! Report to admin of any issues 👾**\n\n"
        f"**How to Use:** /help\n\n"
        #f"**📁 Total Games:** {folder_count}\n\n"
        f"**List of Folders 🔽**\n\n"
    )

    # Fetch and list folders in alphabetical order
    cursor.execute('SELECT name FROM folders WHERE parent_id IS NULL ORDER BY name')
    folders = cursor.fetchall()

    # Add folders to the text
    for folder in folders:
        text += f"|-📁 `{folder[0]}`\n"

    text += "\n\n`Please share any files that you may think are useful to others :D`-[Share](https://t.me/MedContent_Adminbot)"

    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='Markdown')
    except exceptions.MessageNotModified:
        pass  # Handle the exception gracefully by ignoring it


"""def add_user_to_db(user_id):
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    conn.commit()"""

def add_user_to_db(user_id):
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user:
        status = 'approved' if str(user_id) in ADMIN_IDS else 'pending'
        cursor.execute('INSERT INTO users (user_id, status) VALUES (?, ?)', (user_id, status))
        conn.commit()



@dp.message_handler(commands=['start'])
async def handle_start(message: types.Message):
    user_id = message.from_user.id
    add_user_to_db(user_id)
    cursor.execute('SELECT status FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if user[0] == 'pending':
        await message.answer("Welcome to The Medical Content Bot ✨\n\nTo prevent scammers and copyright strikes, we allow only Medical students to use this bot 🙃\n\nSend us your ID-Proof as a Medico @MedContent_Adminbot\n\nYou will be granted access only after verification!")
        await notify_admins(user_id)  # Ensure this is after the initial message to the user
    elif user[0] == 'approved':
        await message.answer("Welcome! You have been given access to the bot 😉")
        if not await is_user_member(user_id):
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(2)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
            keyboard = InlineKeyboardMarkup(row_width=1)
            for channel in REQUIRED_CHANNELS:
                button = InlineKeyboardButton(text=channel, url=f"https://t.me/{channel.lstrip('@')}")
                keyboard.add(button)
            await message.reply(join_message, reply_markup=keyboard)
        else:
            sticker_msg = await bot.send_sticker(message.chat.id, STICKER_ID)
            await asyncio.sleep(1)
            await bot.delete_message(message.chat.id, sticker_msg.message_id)
            await send_ui(message.chat.id)
    elif user[0] == 'rejected':
        await message.answer("Your access request has been rejected. You cannot use this bot 😢\n\nIf you think this is a mistake, Contact Us: @MedContent_Adminbot")




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

@dp.message_handler(lambda message: message.text.startswith('/reject_') and str(message.from_user.id) in ADMIN_IDS)
async def reject_user(message: types.Message):
    user_id = int(message.text.split('_')[1])
    cursor.execute('UPDATE users SET status = ? WHERE user_id = ?', ('rejected', user_id))
    conn.commit()
    await message.answer(f"User {user_id} has been rejected.")
    try:
        await bot.send_message(user_id, "You have been rejected from using the bot 🫤\n\nIf you think this is a mistake, **Contact Us:** [Here](https://t.me/MedContent_Adminbot)")
    except exceptions.BotBlocked:
        logging.warning(f"User {user_id} has blocked the bot.")
    except exceptions.ChatNotFound:
        logging.warning(f"User {user_id} chat not found.")
    except Exception as e:
        logging.error(f"Error sending rejection message to user {user_id}: {e}")

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

# Command to display help information
@dp.message_handler(commands=['help'])
async def help(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
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
            "|- Search for your folder in the list\n\n"
            "|- Tap on the folder name to copy it\n\n"
            "|- Long press on /download\n\n"
            "|- Paste the folder name after /download\n\n"
            "|- Send and get all your files👌\n\n"
            "**NOTE:\n\n**"
            "`This bot is for educational purposes only`\n\n"
            "**Contact Us:** [Here](https://t.me/MedContent_Adminbot)"
        )
        await message.reply(help_text, parse_mode='Markdown')

# Command to display help information
@dp.message_handler(commands=['about'])
async def help(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        about_text = (
            "**The Medical Content Bot ✨**\n\n"
            "Platform - `Render`\n"
            "Usage limit - `0.1 CPU|512 MB (RAM)`\n"
            "Framework - `Python-Flask`\n\n"
            "**Contact Us:** [Here](https://t.me/MedContent_Adminbot)\n\n"
            "All the Best!"
        )
        await message.reply(about_text, parse_mode='Markdown')

# Command to create a new folder
@dp.message_handler(commands=['newfolder'])
async def create_folder(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        #global current_upload_folder

        # Only the admin can create folders
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to create folders.")
            return

        folder_name = message.get_args()
        if not folder_name:
            await message.reply("Please specify a folder name.")
            return

        # Insert the new folder into the database
        cursor.execute('INSERT INTO folders (name) VALUES (?)', (folder_name,))
        conn.commit()

        # Set the current upload folder for the user to the newly created folder
        set_current_upload_folder(user_id, folder_name)

        #await send_or_edit_message()

        await message.reply(f"Folder '{folder_name}' created and set as the current upload folder.")

# Command to delete a file by name (Admin only)
@dp.message_handler(commands=['deletefile'])
async def delete_file(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to delete files.")
            return

        file_name = message.get_args()
        if not file_name:
            await message.reply("Please specify a file name.")
            return

        # Get the message ID of the file to be deleted
        cursor.execute('SELECT message_id FROM files WHERE file_name = ?', (file_name,))
        message_id = cursor.fetchone()
        if message_id:
            message_id = message_id[0]

            # Delete the file from the database
            cursor.execute('DELETE FROM files WHERE file_name = ?', (file_name,))
            conn.commit()

            # Delete the message from the channel
            await bot.delete_message(CHANNEL_ID, message_id)

            await message.reply(f"File '{file_name}' deleted.")
        else:
            await message.reply("File not found.")

# Command to delete a folder and its contents (Admin only)
@dp.message_handler(commands=['deletefolder'])
async def delete_folder(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
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
        if folder_id:
            folder_id = folder_id[0]

            # Get the message IDs of the files in the folder
            cursor.execute('SELECT message_id FROM files WHERE folder_id = ?', (folder_id,))
            message_ids = cursor.fetchall()

            # Delete the files from the channel and the database
            for message_id in message_ids:
                await bot.delete_message(CHANNEL_ID, message_id[0])
            cursor.execute('DELETE FROM files WHERE folder_id = ?', (folder_id,))

            # Delete the folder from the database
            cursor.execute('DELETE FROM folders WHERE id = ?', (folder_id,))
            conn.commit()

            await message.reply(f"Folder '{folder_name}' and its contents deleted.")
        else:
            await message.reply("Folder not found.")

# Command to retrieve and send all files in a specified folder
@dp.message_handler(commands=['download'])
async def get_all_files(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        folder_name = message.get_args()
        if not folder_name:
            await message.reply("Please specify a game name.")
            return

        # Get the folder ID
        cursor.execute('SELECT id FROM folders WHERE name = ?', (folder_name,))
        folder_id = cursor.fetchone()
        if folder_id:
            folder_id = folder_id[0]

            # Get the file IDs and names in the folder
            cursor.execute('SELECT file_id, file_name FROM files WHERE folder_id = ?', (folder_id,))
            files = cursor.fetchall()

            if files:
                for file in files:
                    await bot.send_document(message.chat.id, file[0], caption=file[1])
            else:
                await message.reply("No files found in the specified folder.")
        else:
            await message.reply("Folder not found.")

#Callback Query handler to filter UI based on inline selections
@dp.callback_query_handler(lambda c: c.data)
async def process_callback(callback_query: types.CallbackQuery):
    global current_upload_folder
    user_id = callback_query.from_user.id

    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await bot.answer_callback_query(callback_query.id)
        await bot.send_message(callback_query.from_user.id, join_message)
        return

    code = callback_query.data

    if code == 'back':
        current_upload_folder = None
        await send_ui(callback_query.from_user.id, callback_query.message.message_id)
    elif code == 'root':
        await send_ui(callback_query.from_user.id, callback_query.message.message_id)
    elif code.startswith('letter_'):
        selected_letter = code.split('_')[1]
        await send_ui(callback_query.from_user.id, callback_query.message.message_id, selected_letter=selected_letter)
    else:
        current_upload_folder = code
        await send_ui(callback_query.from_user.id, callback_query.message.message_id, current_folder=current_upload_folder)

    await bot.answer_callback_query(callback_query.id)


# Command to stop the bot (Admin only)
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
                await bot.send_message(user_id[0], "Regular maintenance 👾 for 10 mins.")
            except Exception as e:
                logging.error(f"Error sending broadcast to user {user_id[0]}: {e}")

        await message.reply(f"Broadcast sent to {len(user_ids)} users.")
    except Exception as e:
        logging.error(f"Error fetching users: {e}")
        await message.reply("Error fetching users. Please try again later.")
    
    # Use sys.exit to terminate the bot
    sys.exit("Bot stopped by admin command.")

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

# Handler for incoming documents
@dp.message_handler(content_types=[types.ContentType.DOCUMENT])
async def handle_document(message: types.Message):
    user_id = message.from_user.id

    # Check if the user is a member of the required channels
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        global awaiting_new_db_upload

        # Check if the bot is awaiting a new database upload and if the uploaded file is 'file_management.db'
        if awaiting_new_db_upload and message.document.file_name == "file_management.db":
            # Only the admin can upload a new database file
            if str(message.from_user.id) not in ADMIN_IDS:
                awaiting_new_db_upload = False
                await message.reply("You are not authorized to upload a new database file.")
                return

            # Save the uploaded file to a temporary location
            file_path = f"new_{message.document.file_name}"
            await message.document.download(destination_file=file_path)

            # Replace the existing database file with the new one
            shutil.move(file_path, DB_FILE_PATH)
            awaiting_new_db_upload = False
            await message.reply("Database file replaced successfully. Restarting the bot to apply changes.")

            # Restart the bot
            os.execl(sys.executable, sys.executable, *sys.argv)
            return

        # Existing document handling code
        # Only admins can upload files, so check admin authorization
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to upload files.")
            return

        # Get file details from the incoming document
        file_id = message.document.file_id
        file_name = message.document.file_name

        # Determine the folder ID for the current upload folder
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

        # Send the file to the channel and get the message ID
        sent_message = await bot.send_document(CHANNEL_ID, file_id, caption=f"New file uploaded: {file_name}")
        message_id = sent_message.message_id

        # Insert the file into the database with the message ID
        cursor.execute('INSERT INTO files (file_id, file_name, folder_id, message_id) VALUES (?, ?, ?, ?)', 
                       (file_id, file_name, folder_id, message_id))
        conn.commit()

        await message.reply(f"File '{file_name}' uploaded successfully.")

# Callback query handler for inline buttons
@dp.callback_query_handler(lambda c: c.data)
async def process_callback(callback_query: types.CallbackQuery):
    if callback_query.data == 'back':
        # Logic to go back to the previous folder
        await send_ui(callback_query.message.chat.id)  # This should be updated with the actual previous folder logic
    elif callback_query.data == 'root':
        await send_ui(callback_query.message.chat.id)
    # Handle other callback data if necessary

# Command to rename a folder (Admin only)
@dp.message_handler(commands=['renamefolder'])
async def rename_folder(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to rename folders.")
            return

        args = message.get_args().split(',')
        if len(args) != 2:
            await message.reply("Please specify the current folder name and the new folder name in the format: /renamegame <current_name>,<new_name>")
            return

        current_name, new_name = args

        # Check if the folder with the current name exists
        cursor.execute('SELECT id FROM folders WHERE name = ?', (current_name,))
        folder_id = cursor.fetchone()
        if folder_id:
            # Update the folder name in the database
            cursor.execute('UPDATE folders SET name = ? WHERE id = ?', (new_name, folder_id[0]))
            conn.commit()
            await message.reply(f"Folder '{current_name}' has been renamed to '{new_name}'.")
        else:
            await message.reply("Folder not found.")

# Command to rename a file (Admin only)
@dp.message_handler(commands=['renamefile'])
async def rename_file(message: types.Message):
    user_id = message.from_user.id
    if not await is_user_member(user_id):
        join_message = "Welcome to The Medical Content Bot ✨\n\nI have the ever-growing archive of Medical content 👾\n\nJoin our backup channels to remain connected 😉\n"
        for channel in REQUIRED_CHANNELS:
            join_message += f"{channel}\n"
        await message.reply(join_message)
    else:
        if str(message.from_user.id) not in ADMIN_IDS:
            await message.reply("You are not authorized to rename files.")
            return

        args = message.get_args().split(',')
        if len(args) != 2:
            await message.reply("Please specify the current file name and the new file name in the format: /renamefile <current_name>,<new_name>")
            return

        current_name, new_name = args

        # Check if the file with the current name exists
        cursor.execute('SELECT id FROM files WHERE file_name = ?', (current_name,))
        file_id = cursor.fetchone()
        if file_id:
            # Update the file name in the database
            cursor.execute('UPDATE files SET file_name = ? WHERE id = ?', (new_name, file_id[0]))
            conn.commit()
            await message.reply(f"File '{current_name}' has been renamed to '{new_name}'.")
        else:
            await message.reply("File not found.")

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
