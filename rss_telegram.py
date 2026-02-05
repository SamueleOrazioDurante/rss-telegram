#!/usr/bin/env python3
import os
import time
import json
import logging
import feedparser
from datetime import datetime
import requests
import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
import re
import html

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get environment variables
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_FORUM_ID = os.environ.get('TELEGRAM_FORUM_ID')
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL', 3600))  # Default: 1 hour
FEEDS_FILE = os.environ.get('FEEDS_FILE', '/app/data/feeds.txt')
TELEGRAM_GROUPED_MESSAGES= os.environ.get('TELEGRAM_GROUPED_MESSAGES', 'true').lower() == 'true'  # Default: true
TELEGRAM_MESSAGE_LINKS_BUTTON= os.environ.get('TELEGRAM_MESSAGE_LINKS_BUTTON', 'false').lower() == 'true'  # Default: false
INCLUDE_DESCRIPTION = os.environ.get('INCLUDE_DESCRIPTION', 'false').lower() == 'true'  # Default: false
DISABLE_NOTIFICATION = os.environ.get('DISABLE_NOTIFICATION', 'false').lower() == 'true'  # Default: false
MAX_MESSAGE_LENGTH = 4096  # Maximum character limit for Telegram messages

# File to store already sent articles
HISTORY_FILE = "/app/data/sent_items.json"

# File to cache anime images from Anilist
IMAGE_CACHE_FILE = "/app/data/image_cache.json"

def clean_title(title):
    # Rimuovi tag tra parentesi quadre e tonde
    cleaned = re.sub(r'\[.*?\]', '', title)
    cleaned = re.sub(r'\(.*?\)', '', cleaned)
    # Rimuovi suffissi comuni come " - 03" o " Episode 03"
    cleaned = re.sub(r'\s*-\s*\d+\s*$', '', cleaned)  # Modificato per gestire spazi finali
    cleaned = re.sub(r'\s*Episode\s*\d+$', '', cleaned, flags=re.IGNORECASE)
    # Pulisci spazi extra
    return ' '.join(cleaned.split()).strip()


def load_image_cache():
    """Load the image cache from file."""
    try:
        with open(IMAGE_CACHE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_image_cache(cache):
    """Save the image cache to file."""
    with open(IMAGE_CACHE_FILE, 'w') as f:
        json.dump(cache, f)

def get_anime_image_from_anilist(title):
    cache = load_image_cache()
    cleaned_title = clean_title(title)
    
    if cleaned_title in cache:
        return cache[cleaned_title]
    
    try:
        url = "https://graphql.anilist.co"
        query = """
        query Media($search: String) {
          Page {
            media(search: $search) {
              coverImage {
                extraLarge
              }
            }
          }
        }
        """
        variables = {"search": cleaned_title}
        response = requests.post(url, json={"query": query, "variables": variables})
        response.raise_for_status()
        data = response.json()
        
        if data['data']['Page']['media']:
            image_url = data['data']['Page']['media'][0]['coverImage']['extraLarge']
            cache[cleaned_title] = image_url
            save_image_cache(cache)
            return image_url
        
    except Exception as e:
        logger.error(f"Error while retrieving image from Anilist: {e}")
    return None

def strip_html(html_content: str) -> str:
    """Convert HTML to plain text by removing tags and unescaping entities."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', html_content)
    # Unescape HTML entities and normalize whitespace
    text = html.unescape(text)
    return ' '.join(text.split())


def load_feeds():
    """Load RSS feeds from configuration file."""
    try:
        with open(FEEDS_FILE, 'r') as f:
            feeds = [line.strip() for line in f.readlines() if line.strip() and not line.strip().startswith('#')]
            logger.info(f"Loaded {len(feeds)} feeds from {FEEDS_FILE}")
            return feeds
    except FileNotFoundError:
        logger.warning(f"Feed file {FEEDS_FILE} not found. Creating empty file...")
        with open(FEEDS_FILE, 'w') as f:
            f.write("# Add your RSS feeds here, one per line\n")
        return []
    except Exception as e:
        logger.error(f"Error loading feeds: {e}")
        return []


def load_sent_items():
    """Load history of already sent articles."""
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_sent_items(sent_items):
    """Save history of sent articles."""
    with open(HISTORY_FILE, 'w') as f:
        json.dump(sent_items, f)

async def send_telegram_message(bot, chat_id, message, message_thread_id=None, reply_markup=None):
    try:
        kwargs = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": ParseMode.MARKDOWN,
            "disable_notification": DISABLE_NOTIFICATION
        }

        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id

        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup

        await bot.send_message(**kwargs)
        return True
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return False

async def send_photo_message(bot, chat_id, photo_url, caption, message_thread_id=None, reply_markup=None):
    try:
        kwargs = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": ParseMode.MARKDOWN,
            "disable_notification": DISABLE_NOTIFICATION
        }

        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id

        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup

        await bot.send_photo(**kwargs)
        return True
    except Exception as e:
        logger.error(f"Error sending photo: {e}")
        return False

async def send_grouped_messages(bot, messages_by_feed):
    """Send messages grouped by feed."""
    if not messages_by_feed:
        logger.info("No new content to notify")
        return True

    for feed_title, entries in messages_by_feed.items():
        if not entries:
            continue

        header = f"📢 *New content from {feed_title}*\n\n"
        entries_text = ""

        for entry in entries:
            entry_text = f"• *{entry['title']}*\n"

            if INCLUDE_DESCRIPTION and entry.get('description'):
                desc = strip_html(entry['description'])
                if len(desc) > 150:
                    desc = desc[:147] + '...'
                entry_text += f"  _{desc}_\n"

            if TELEGRAM_MESSAGE_LINKS_BUTTON:
                entry_text += f"\n  [Open Link]({entry['link']})\n\n"
            else:
                entry_text += f"\n  {entry['link']}\n\n"

            if len(header) + len(entries_text) + len(entry_text) > MAX_MESSAGE_LENGTH:
                await send_telegram_message(bot, TELEGRAM_CHAT_ID, header + entries_text, TELEGRAM_FORUM_ID)
                entries_text = entry_text
            else:
                entries_text += entry_text

        if entries_text:
            await send_telegram_message(bot, TELEGRAM_CHAT_ID, header + entries_text, TELEGRAM_FORUM_ID)

        await asyncio.sleep(1)

    return True

async def send_single_messages(bot, messages_by_feed):
    """Send one message per item."""
    if not messages_by_feed:
        logger.info("No new content to notify")
        return True

    for feed_title, entries in messages_by_feed.items():
        for entry in entries:
            message = f"📢 *New content from {feed_title}*\n\n*{entry['title']}*\n"

            if INCLUDE_DESCRIPTION and entry.get('description'):
                desc = strip_html(entry['description'])
                if len(desc) > 150:
                    desc = desc[:147] + '...'
                message += f"  _{desc}_\n"

            reply_markup = None
            if TELEGRAM_MESSAGE_LINKS_BUTTON:
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Open Link", url=entry['link'])]])
            else:
                message += f"\n{entry['link']}"

            # Use AniList API to get image for anime entries
            image_url = entry.get('image_url')

            if image_url:
                success = await send_photo_message(bot, TELEGRAM_CHAT_ID, image_url, message, TELEGRAM_FORUM_ID, reply_markup)
                if not success:
                    # Fallback with text message if photo fails
                    await send_telegram_message(bot, TELEGRAM_CHAT_ID, message, TELEGRAM_FORUM_ID, reply_markup)
            else:
                await send_telegram_message(bot, TELEGRAM_CHAT_ID, message, TELEGRAM_FORUM_ID, reply_markup)
            
            await asyncio.sleep(1)

    return True

async def check_feeds(bot):
    """Check RSS feeds for new articles."""
    sent_items = load_sent_items()
    feeds = load_feeds()

    if not feeds:
        logger.warning("No feeds to check. Add feeds to the configuration file.")
        return sent_items

    messages_by_feed = {}

    for feed_url in feeds:
        if not feed_url.strip():
            continue

        logger.info(f"Checking feed: {feed_url}")

        try:
            feed = feedparser.parse(feed_url)

            if not feed.entries:
                logger.warning(f"No entries found in feed: {feed_url}")
                continue

            feed_title = feed.feed.title if hasattr(feed.feed, 'title') else feed_url
            sent_items.setdefault(feed_url, [])
            messages_by_feed.setdefault(feed_title, [])

            for entry in feed.entries:
                entry_id = entry.id if hasattr(entry, 'id') else entry.link
                if entry_id in sent_items[feed_url]:
                    continue

                title = entry.title if hasattr(entry, 'title') else "No title"
                link = entry.link if hasattr(entry, 'link') else ""
                description = ""
                if INCLUDE_DESCRIPTION:
                    description = getattr(entry, 'description', '') or getattr(entry, 'summary', '')

                image_url = get_anime_image_from_anilist(title)

                messages_by_feed[feed_title].append({'title': title, 'link': link, 'description': description, 'image_url': image_url})
                sent_items[feed_url].append(entry_id)
        except Exception as e:
            logger.error(f"Error checking feed {feed_url}: {e}")

    if TELEGRAM_GROUPED_MESSAGES:
        await send_grouped_messages(bot, messages_by_feed)
    else:
        await send_single_messages(bot, messages_by_feed)
    return sent_items

async def main_async():
    logger.info("Starting RSS feed monitoring")
    logger.info(f"Configuration: INCLUDE_DESCRIPTION={INCLUDE_DESCRIPTION}, DISABLE_NOTIFICATION={DISABLE_NOTIFICATION}")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Missing environment variables. Make sure to set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return
    
    if TELEGRAM_FORUM_ID:
        try:
            int(TELEGRAM_FORUM_ID)
        except ValueError:
            raise ValueError("TELEGRAM_FORUM_ID must be an integer")

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await send_telegram_message(bot, TELEGRAM_CHAT_ID, "🤖 *RSS Monitoring Bot started!*\nActive feed monitoring. Configuration loaded from file.", TELEGRAM_FORUM_ID)

    while True:
        sent_items = await check_feeds(bot)
        save_sent_items(sent_items)
        logger.info(f"Next check in {CHECK_INTERVAL} seconds")
        await asyncio.sleep(CHECK_INTERVAL)


def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()