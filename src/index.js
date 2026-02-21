#!/usr/bin/env node

import os from 'os';
import crypto from 'crypto';
import * as Bridge from './bridge.js';
import Server from './server.js';

const rgb = (r, g, b, msg) => `\x1b[38;2;${r};${g};${b}m${msg}\x1b[0m`;
const log = (...args) => console.log(`[${rgb(88, 101, 242, 'arRPC')}]`, ...args);

log('slackRPC v1.0.0, based on arRPC v3.6.0');

const authentication_key = process.env.SLACKRPC_AUTH_KEY || null;
const slackrpc_url = process.env.SLACKRPC_URL || 'https://slackrpc.nikoo.dev';

async function pollURL(url, interval = 30, maxAttempts=20) {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const response = await fetch(url);
      const data = await response.json();

      if (data.status === 'complete') {
        return data;
      }
    } catch (error) {
      log(rgb(242, 88, 88, 'Polling error:'), error);
    }

    await new Promise(resolve => setTimeout(resolve, interval));
  }
  throw new Error('Polling timed out');
} 

if (authentication_key == null) {
  const authentication_code = Array.from(crypto.randomBytes(6)).map(b => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[b % 36]).join('');
  const params = new URLSearchParams({ code: authentication_code, hostname: os.hostname() });
  const res = await fetch(`${slackrpc_url}/api/oauth/start?${params}`);
  if (res.status === 429) {
    log(rgb(242, 88, 88, 'You\'re generating too many URLs! Wait and try again.'));
    process.exit(1);
  }
  if (!res.ok) {
    log(rgb(242, 88, 88, 'Failed to generate authentication URL! Please check your internet connection and try again.'));
    process.exit(1);
  }
  const data = await res.json();
  log('Welcome to SlackRPC! To get started, visit the following URL to link Slack: ' + data.url);
  try {
    const result = await pollURL(`${slackrpc_url}/api/oauth/poll?code=${authentication_code}`);
    log('Successfully authenticated with Slack!');
    os.env.SLACKRPC_AUTH_KEY = result.token;
  } catch {
    log(rgb(242, 88, 88, 'Authentication timed out. Please try again.'));
    process.exit(1);
  }
}

const server = await new Server();
server.on('activity', data => Bridge.send(data));
