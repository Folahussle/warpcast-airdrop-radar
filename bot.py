/**
 * Farcaster Airdrop Alert Bot
 * Runs via GitHub Actions — no local setup needed.
 * Deduplication is handled by GitHub Actions cache (seen.json).
 */

'use strict';

const axios = require('axios');
const fs    = require('fs');
const path  = require('path');

// ─── Read settings from GitHub Actions environment ────────────────────────────

const NEYNAR_API_KEY     = process.env.NEYNAR_API_KEY;
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID   = process.env.TELEGRAM_CHAT_ID;
const CHANNELS           = (process.env.FARCASTER_CHANNELS || 'airdrop').split(',').map(s => s.trim());
const KEYWORDS           = (process.env.KEYWORDS || 'airdrop').split(',').map(s => s.trim().toLowerCase());
const DRY_RUN            = process.env.DRY_RUN === 'true';
const SEEN_FILE          = path.join(process.cwd(), 'seen.json');
const TELEGRAM_DELAY     = 600; // ms between messages

// ─── Validate all required secrets are present ───────────────────────────────

const required = { NEYNAR_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID };
for (const [key, val] of Object.entries(required)) {
  if (!val) {
    console.error(`FATAL: Missing secret "${key}". Add it in GitHub → Settings → Secrets → Actions.`);
    process.exit(1);
  }
}

// ─── Load / save seen hashes (persisted via GitHub Actions cache) ─────────────
// GitHub Actions re-uses this file between runs so we never send duplicate alerts.

function loadSeen() {
  try {
    const data = JSON.parse(fs.readFileSync(SEEN_FILE, 'utf8'));
    return new Set(Array.isArray(data) ? data : []);
  } catch {
    return new Set();
  }
}

function saveSeen(seen) {
  const trimmed = [...seen].slice(-5000); // keep most recent 5000 only
  fs.writeFileSync(SEEN_FILE, JSON.stringify(trimmed, null, 2), 'utf8');
  console.log(`Saved ${trimmed.length} seen hashes to cache.`);
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const escapeHtml = str =>
  String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const sleep = ms => new Promise(r => setTimeout(r, ms));

const ts = () => new Date().toISOString().replace('T', ' ').slice(0, 19);

function extractUrl(text) {
  const m = text.match(/https?:\/\/[^\s]+/);
  return m ? m[0] : null;
}

// ─── Fetch posts from Farcaster ───────────────────────────────────────────────

async function fetchChannelCasts(channel) {
  const res = await axios.get('https://api.neynar.com/v2/farcaster/feed/channel', {
    params:  { channel_id: channel, limit: 25 },
    headers: { 'api_key': NEYNAR_API_KEY },
    timeout: 12000,
  });
  return res.data?.casts || [];
}

// ─── Send a Telegram message ──────────────────────────────────────────────────

async function sendTelegram(text, attempt = 1) {
  if (DRY_RUN) {
    console.log(`\n[DRY-RUN] Would send:\n${'─'.repeat(60)}\n${text}\n${'─'.repeat(60)}\n`);
    return;
  }
  try {
    await axios.post(
      `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
      { chat_id: TELEGRAM_CHAT_ID, text, parse_mode: 'HTML', disable_web_page_preview: false },
      { timeout: 12000 }
    );
  } catch (err) {
    if (attempt < 2) { await sleep(2500); return sendTelegram(text, attempt + 1); }
    console.error(`Telegram send failed: ${err.message}`);
  }
}

// ─── Filter and format ────────────────────────────────────────────────────────

const matchesKeywords = text => KEYWORDS.some(kw => text.toLowerCase().includes(kw));

function formatMessage(cast, channel) {
  const author   = escapeHtml(cast.author?.username || 'unknown');
  const body     = escapeHtml(cast.text || '');
  const castUrl  = `https://warpcast.com/${cast.author?.username}/${cast.hash}`;
  const extraUrl = extractUrl(cast.text || '');
  const time     = cast.timestamp
    ? new Date(cast.timestamp).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
    : ts() + ' UTC';

  let msg = `🪂 <b>New Airdrop Alert — #${escapeHtml(channel)}</b>\n\n`
          + `👤 @${author}\n`
          + `💬 ${body}\n\n`
          + `🔗 <a href="${castUrl}">View on Warpcast</a>`;

  if (extraUrl) msg += `\n🌐 <a href="${escapeHtml(extraUrl)}">Linked URL</a>`;
  msg += `\n⏰ ${time}`;
  return msg;
}

// ─── Poll a single channel ────────────────────────────────────────────────────

async function pollChannel(channel, seen) {
  try {
    const casts = await fetchChannelCasts(channel);
    let sent = 0;

    for (const cast of casts) {
      const hash = cast.hash;
      if (!hash || seen.has(hash)) continue;
      if (!matchesKeywords(cast.text || '')) continue;

      seen.add(hash);
      sent++;
      await sendTelegram(formatMessage(cast, channel));
      await sleep(TELEGRAM_DELAY);
    }

    console.log(`[${ts()}] #${channel} — ${sent > 0 ? `${sent} alert(s) sent ✅` : 'no new matches 🔍'}`);
  } catch (err) {
    console.error(`[${ts()}] Error on #${channel}: ${err.message}`);
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log('='.repeat(60));
  console.log('  Farcaster Airdrop Bot — GitHub Actions Run');
  console.log('='.repeat(60));
  console.log(`Channels : ${CHANNELS.join(', ')}`);
  console.log(`Keywords : ${KEYWORDS.join(', ')}`);
  console.log(`Dry-run  : ${DRY_RUN}`);
  console.log('');

  const seen = loadSeen();
  console.log(`Loaded ${seen.size} previously seen hashes from cache.\n`);

  for (const channel of CHANNELS) {
    await pollChannel(channel, seen);
  }

  saveSeen(seen);
  console.log('\nRun complete. GitHub Actions will run this again on schedule.');
}

main().catch(err => {
  console.error(`FATAL: ${err.message}`);
  process.exit(1);
});
