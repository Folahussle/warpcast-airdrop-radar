#!/usr/bin/env python3
"""
Farcaster & Twitter Airdrop Alert Bot
Runs via GitHub Actions — no local setup needed.
Deduplication is handled via seen.json cache.
"""

import os
import json
import requests
import logging
import time
from datetime import datetime
from requests.exceptions import RequestException, HTTPError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment configuration
NEYNAR_API_KEY = os.getenv('NEYNAR_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TWITTER_API_KEY = os.getenv('TWITTER_API_KEY')
CHANNELS = [ch.strip() for ch in os.getenv('FARCASTER_CHANNELS', 'airdrop').split(',')]
KEYWORDS = [kw.strip().lower() for kw in os.getenv('KEYWORDS', 'airdrop').split(',')]
DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'
SEEN_FILE = 'seen.json'
TELEGRAM_DELAY = 0.6  # seconds between messages

# Validate required secrets
def validate_secrets():
    """Ensure all required environment variables are set."""
    required = {
        'NEYNAR_API_KEY': NEYNAR_API_KEY,
        'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
        'TWITTER_API_KEY': TWITTER_API_KEY,
    }
    for key, val in required.items():
        if not val:
            logger.error(f'FATAL: Missing secret "{key}". Add it in GitHub → Settings → Secrets → Actions.')
            exit(1)

validate_secrets()

# Load / save seen hashes
def load_seen():
    """Load previously seen post IDs from cache."""
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE, 'r') as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
        return set()
    except Exception as e:
        logger.warning(f'Could not load seen cache: {e}')
        return set()

def save_seen(seen):
    """Save seen post IDs to cache, keeping only recent 5000."""
    trimmed = sorted(list(seen))[-5000:]
    with open(SEEN_FILE, 'w') as f:
        json.dump(trimmed, f, indent=2)
    logger.info(f'Saved {len(trimmed)} seen post IDs to cache.')

# Helper functions
def escape_html(text):
    """Escape HTML special characters for Telegram."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def ts():
    """Get current timestamp in ISO format."""
    return datetime.utcnow().isoformat().replace('T', ' ')[:-7]

def extract_url(text):
    """Extract first HTTP(S) URL from text."""
    import re
    match = re.search(r'https?://\S+', text or '')
    return match.group(0) if match else None

# Fetch posts from Farcaster
def fetch_channel_casts(channel):
    """Fetch casts from Neynar API for a given channel."""
    try:
        url = 'https://api.neynar.com/v2/farcaster/feed/channel'
        params = {'channel_id': channel, 'limit': 25}
        headers = {'api_key': NEYNAR_API_KEY}
        response = requests.get(url, params=params, headers=headers, timeout=12)
        response.raise_for_status()
        return response.json().get('casts', [])
    except HTTPError as err:
        if err.response.status_code == 401:
            logger.error('Invalid NEYNAR_API_KEY')
        else:
            logger.error(f'HTTP error fetching casts from {channel}: {err}')
        return []
    except RequestException as err:
        logger.error(f'Error fetching casts from {channel}: {err}')
        return []

# Fetch tweets from Twitter
def fetch_twitter_airdrop_tweets():
    """Fetch airdrop tweets from Twitter API."""
    try:
        url = 'https://api.twitterapi.io/search'
        params = {
            'query': 'airdrop',
            'type': 'Latest',
            'count': 25
        }
        headers = {'Authorization': f'Bearer {TWITTER_API_KEY}'}
        response = requests.get(url, params=params, headers=headers, timeout=12)
        response.raise_for_status()
        tweets = response.json().get('tweets', [])
        return tweets
    except HTTPError as err:
        if err.response.status_code == 401:
            logger.error('Invalid TWITTER_API_KEY')
        else:
            logger.error(f'HTTP error fetching tweets: {err}')
        return []
    except RequestException as err:
        logger.error(f'Error fetching tweets: {err}')
        return []

# Send Telegram message with retry
def send_telegram(text, attempt=1):
    """Send alert to Telegram with retry logic."""
    if DRY_RUN:
        logger.info(f'\n[DRY-RUN] Would send:\n{"-" * 60}\n{text}\n{"-" * 60}\n')
        return
    
    try:
        url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False,
        }
        response = requests.post(url, json=data, timeout=12)
        response.raise_for_status()
        logger.info('Alert sent to Telegram successfully.')
    except HTTPError as err:
        if err.response.status_code == 401:
            logger.error('Invalid TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID')
        else:
            if attempt < 2:
                logger.warning(f'Retrying Telegram send (attempt {attempt + 1})')
                time.sleep(2.5)
                send_telegram(text, attempt + 1)
            else:
                logger.error(f'Telegram send failed: {err}')
    except RequestException as err:
        if attempt < 2:
            logger.warning(f'Retrying Telegram send (attempt {attempt + 1})')
            time.sleep(2.5)
            send_telegram(text, attempt + 1)
        else:
            logger.error(f'Telegram send failed: {err}')

# Filter and format messages
def matches_keywords(text):
    """Check if text contains any of the keywords."""
    return any(kw in (text or '').lower() for kw in KEYWORDS)

def format_farcaster_message(cast, channel):
    """Format Farcaster cast into Telegram message with HTML."""
    author = escape_html(cast.get('author', {}).get('username', 'unknown'))
    body = escape_html(cast.get('text', ''))
    cast_hash = cast.get('hash', '')
    cast_url = f'https://warpcast.com/{author}/{cast_hash}'
    extra_url = extract_url(cast.get('text', ''))
    
    timestamp = cast.get('timestamp')
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            time_str = dt.isoformat()[:-7] + ' UTC'
        except:
            time_str = ts() + ' UTC'
    else:
        time_str = ts() + ' UTC'
    
    msg = f'🪂 <b>New Airdrop Alert — Farcaster #{escape_html(channel)}</b>\n\n'
    msg += f'👤 @{author}\n'
    msg += f'💬 {body}\n\n'
    msg += f'🔗 <a href="{cast_url}">View on Warpcast</a>'
    
    if extra_url:
        msg += f'\n🌐 <a href="{escape_html(extra_url)}">Linked URL</a>'
    
    msg += f'\n⏰ {time_str}'
    return msg

def format_twitter_message(tweet):
    """Format Twitter tweet into Telegram message with HTML."""
    author = escape_html(tweet.get('author', {}).get('username', 'unknown'))
    body = escape_html(tweet.get('text', ''))
    tweet_id = tweet.get('id', '')
    tweet_url = f'https://twitter.com/{author}/status/{tweet_id}'
    extra_url = extract_url(tweet.get('text', ''))
    
    timestamp = tweet.get('created_at', ts())
    
    msg = f'🪂 <b>New Airdrop Alert — Twitter</b>\n\n'
    msg += f'👤 @{author}\n'
    msg += f'💬 {body}\n\n'
    msg += f'🔗 <a href="{tweet_url}">View on Twitter</a>'
    
    if extra_url:
        msg += f'\n🌐 <a href="{escape_html(extra_url)}">Linked URL</a>'
    
    msg += f'\n⏰ {timestamp}'
    return msg

# Poll a single Farcaster channel
def poll_channel(channel, seen):
    """Poll a Farcaster channel for new airdrop alerts."""
    try:
        casts = fetch_channel_casts(channel)
        sent = 0
        
        for cast in casts:
            cast_hash = cast.get('hash')
            if not cast_hash or cast_hash in seen:
                continue
            if not matches_keywords(cast.get('text', '')):
                continue
            
            seen.add(cast_hash)
            sent += 1
            send_telegram(format_farcaster_message(cast, channel))
            time.sleep(TELEGRAM_DELAY)
        
        status = f'{sent} alert(s) sent ✅' if sent > 0 else 'no new matches 🔍'
        logger.info(f'[{ts()}] Farcaster #{channel} — {status}')
    except Exception as err:
        logger.error(f'[{ts()}] Error on Farcaster #{channel}: {err}')

# Poll Twitter for airdrop tweets
def poll_twitter(seen):
    """Poll Twitter for new airdrop alerts."""
    try:
        tweets = fetch_twitter_airdrop_tweets()
        sent = 0
        
        for tweet in tweets:
            tweet_id = tweet.get('id')
            if not tweet_id or tweet_id in seen:
                continue
            if not matches_keywords(tweet.get('text', '')):
                continue
            
            seen.add(tweet_id)
            sent += 1
            send_telegram(format_twitter_message(tweet))
            time.sleep(TELEGRAM_DELAY)
        
        status = f'{sent} alert(s) sent ✅' if sent > 0 else 'no new matches 🔍'
        logger.info(f'[{ts()}] Twitter — {status}')
    except Exception as err:
        logger.error(f'[{ts()}] Error on Twitter: {err}')

# Main execution
def main():
    """Main bot loop."""
    logger.info('=' * 60)
    logger.info('  Farcaster & Twitter Airdrop Bot — GitHub Actions Run')
    logger.info('=' * 60)
    logger.info(f'Farcaster Channels : {", ".join(CHANNELS)}')
    logger.info(f'Keywords           : {", ".join(KEYWORDS)}')
    logger.info(f'Monitoring Twitter : Yes')
    logger.info(f'Dry-run            : {DRY_RUN}')
    logger.info('')
    
    seen = load_seen()
    logger.info(f'Loaded {len(seen)} previously seen post IDs from cache.\n')
    
    # Poll Farcaster channels
    for channel in CHANNELS:
        poll_channel(channel, seen)
    
    # Poll Twitter
    poll_twitter(seen)
    
    save_seen(seen)
    logger.info('\nRun complete. GitHub Actions will run this again on schedule.')

if __name__ == '__main__':
    try:
        main()
    except Exception as err:
        logger.error(f'FATAL: {err}')
        exit(1)
