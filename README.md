# slackRPC

Discord Rich Presence → Slack status. Based on [arRPC](https://github.com/OpenAsar/arRPC).

## How it works

The CLI intercepts Discord RPC calls locally and forwards activity to a backend, which updates your Slack status via OAuth.

## Setup

### Backend

Required environment variables:

```
SLACK_BOT_TOKEN=
SLACK_SIGNING_SECRET=
OAUTH_CLIENT_ID=
OAUTH_CLIENT_SECRET=
OAUTH_REDIRECT_URI=
```

Deploy with Docker (e.g. Coolify):

```sh
docker build -t slackrpc ./backend
docker run -p 8000:8000 --env-file .env slackrpc
```

Redis must be available at `localhost:6379` (or set `REDIS_URL`).

### CLI

```sh
npm install -g slackrpc
slackrpc
```

On first run, a URL will be printed — open it to link your Slack account. Subsequent runs start immediately.

## License

MIT
