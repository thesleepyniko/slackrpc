#!/usr/bin/env node

import os from 'os';
import crypto from 'crypto';
import * as Bridge from './bridge.js';
import Server from './server.js';

const rgb = (r, g, b, msg) => `\x1b[38;2;${r};${g};${b}m${msg}\x1b[0m`;
const log = (...args) => console.log(`[${rgb(88, 101, 242, 'slackRPC')}]`, ...args);

log('slackRPC v1.0.0, based on arRPC v3.6.0');

const authentication_key = process.env.SLACKRPC_AUTH_KEY || null;
const slackrpc_url = process.env.SLACKRPC_URL || 'https://slackrpc.nikoo.dev';
const HEADERS = {
  'Content-Type': 'application/json',
  'User-Agent': process.env.SLACKRPC_USER_AGENT || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
};

log(`Using User-Agent: ${HEADERS['User-Agent']}`);

async function pollURL(url, interval = 30000, maxAttempts=20) {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const response = await fetch(url, { headers: HEADERS });
      const data = await response.json();

      if (data.status === 'complete') {
        return data;
      }
    } catch (error) {
      log(rgb(242, 88, 88, `Polling error (attempt ${i + 1}/${maxAttempts}):`), error?.message ?? error);
      if (error?.stack) log(rgb(242, 88, 88, error.stack));
    }

    await new Promise(resolve => setTimeout(resolve, interval));
  }
  throw new Error('Polling timed out');
} 

if (authentication_key == null) {
  const authentication_code = Array.from(crypto.randomBytes(6)).map(b => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[b % 36]).join('');
  const params = new URLSearchParams({ code: authentication_code, hostname: os.hostname() });
  const res = await fetch(`${slackrpc_url}/api/oauth/start?${params}`, { headers: HEADERS });
  if (res.status === 429) {
    log(rgb(242, 88, 88, `Rate limited (HTTP 429) on /api/oauth/start — wait and try again.`));
    process.exit(1);
  }
  if (!res.ok) {
    let body = '';
    try { body = await res.text(); } catch {}
    log(rgb(242, 88, 88, `Failed to generate authentication URL (HTTP ${res.status}): ${body || '(empty body)'}. Check your internet connection.`));
    process.exit(1);
  }
  const data = await res.json();
  log('Welcome to SlackRPC! To get started, visit the following URL to link Slack: ' + data.url);
  try {
    const result = await pollURL(`${slackrpc_url}/api/oauth/poll?code=${authentication_code}`);
    log('Successfully authenticated with Slack!');
    process.env.SLACKRPC_AUTH_KEY = result.token;
  } catch (e) {
    log(rgb(242, 88, 88, `Authentication timed out: ${e?.message ?? e}. Please try again.`));
    process.exit(1);
  }
}

const server = await new Server();
server.on('activity', data => Bridge.send(data));
