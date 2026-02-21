import os
import secrets
from datetime import datetime, timezone
import uvicorn
import dotenv
import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slack_bolt import App
from slack_bolt.adapter.fastapi import SlackRequestHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.oauth import AuthorizeUrlGenerator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import DateTime, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

security = HTTPBearer()


# db shit! yay!
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    token: Mapped[str] = mapped_column(String, primary_key=True)
    slack_user_id: Mapped[str] = mapped_column(
        String, nullable=False
    )  # this one gets filled out when they oauth
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    slack_access: Mapped[str] = mapped_column(String, nullable=False)
    slack_refresh: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    user_emoji: Mapped[str] = mapped_column(String, nullable=True)


def get_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    token = credentials.credentials
    with Session(engine) as db:
        user = db.query(User).filter_by(token=token).first()
    if not user:
        raise HTTPException(401, detail="Invalid or expired token")
    return user


# a lot of misc stuff including redis initalization
dotenv.load_dotenv()
_redis_url = os.environ.get("REDIS_URL")
r = redis.Redis.from_url(_redis_url, decode_responses=True) if _redis_url else redis.Redis(decode_responses=True)

engine = create_engine("sqlite:///sessions.db")
Base.metadata.create_all(engine)


def get_slack_user_id(token: str) -> str | None:
    with Session(engine) as session:
        row = session.get(User, token)
        return row.slack_user_id if row else None  # type: ignore


limiter = Limiter(key_func=get_remote_address)
web_app = FastAPI()
web_app.state.limiter = limiter
web_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore
# Initializes your app with your bot token and signing secret
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)
bolt_handler = SlackRequestHandler(app)

web_app.mount("/static", StaticFiles(directory="static"), name="static")


@web_app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>SlackRPC</title></head>
<body><h1>SlackRPC</h1></body>
</html>"""

@web_app.get("/success")
def success():
    return RedirectResponse("/static/success.html")

SLACK_CLIENT_ID = os.environ["OAUTH_CLIENT_ID"]
SLACK_CLIENT_SECRET = os.environ["OAUTH_CLIENT_SECRET"]
SLACK_REDIRECT_URI = os.environ["OAUTH_REDIRECT_URI"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

USAGE = (
    "*Usage:* `/slackrpc <command> [args]`\n\n"
    "`help` — Show this help message\n"
    "`stop` — Pause Discord Rich Presence updates\n"
    "`start` — Resume Discord Rich Presence updates"
)


class AuthRequest(BaseModel):
    code: str
    hostname: str


class RPCRequest(BaseModel):
    activity: dict


class KeyRequest(BaseModel):
    authentication_code: str
    hostname: str


# fastapi shit

def update_activity(
    user: User,
    activity: str,
    emoji: str,
    curr_access_token: str,
    curr_refresh_token: str,
):
    bot_client = WebClient(token=SLACK_BOT_TOKEN)
    try:
        client = WebClient(token=curr_access_token)
        client.users_profile_set(
            profile={
                "status_text": activity,
                "status_emoji": emoji,
                "status_expiration": 0,
            }
        )
    except SlackApiError as e:
        if e.response["error"] in ("token_expired", "invalid_auth", "token_revoked"):
            client = WebClient()
            response = client.oauth_v2_access(
                client_id=SLACK_CLIENT_ID,
                client_secret=SLACK_CLIENT_SECRET,
                refresh_token=curr_refresh_token,
                redirect_uri=SLACK_REDIRECT_URI,
                grant_type="refresh",
            )
            user.slack_access = str(response.get("access_token"))
            user.slack_refresh = str(response.get("refresh_token", user.slack_refresh))
            with Session(engine) as db:
                db.merge(user)
                db.commit()
            try:
                WebClient(token=str(user.slack_access)).users_profile_set(
                    profile={
                        "status_text": activity,
                        "status_emoji": emoji,
                        "status_expiration": 0,
                    }
                )
            except SlackApiError as e:
                bot_client.chat_postMessage(
                    channel=user.slack_user_id,
                    text=f"Hi <@{user.slack_user_id}>! SlackRPC could not authenticate you. Please reauthenticate yourself with the command slackrpc --reauthenticate",
                )
        else:
            raise HTTPException(502, detail=f"Slack API error: {e.response['error']}")


@web_app.get("/api/oauth/start")
@limiter.limit("4/minute")
def oauth_start(request: Request, code: str, hostname: str):
    if not len(code) == 6 or not code.isalnum():
        raise HTTPException(400, detail="Code must be alphanumeric and 6 characters")
    else:
        if r.getex(hostname, 6000):
            r.delete(f"auth:{code}")
            r.setex(f"auth:{code}", 6000, hostname)
        r.setex(hostname, 6000, code)
        r.setex(f"auth:{code}", 6000, hostname)
    state = f"{code}:{secrets.token_urlsafe(8)}"
    url = AuthorizeUrlGenerator(
        client_id=SLACK_CLIENT_ID,
        user_scopes=["users.profile:write", "users.profile:read"],
        redirect_uri=SLACK_REDIRECT_URI,
    ).generate(state=state)
    r.setex(f"CORS:{state}", 6000, "1")
    return {"url": url}


@web_app.get("/api/oauth/callback")
@limiter.limit("2/minute")
def generate_authentication_key(request: Request, code: str, state: str):
    if not r.getdel(f"CORS:{state}"):
        raise HTTPException(400, detail="Invalid state")
    cli_code = state.split(":")[0]
    hostname = str(r.getdel(f"auth:{cli_code}"))
    if not hostname:
        raise HTTPException(400, detail="Invalid state")
    else:
        client = WebClient()
        response = client.oauth_v2_access(
            client_id=SLACK_CLIENT_ID,
            client_secret=SLACK_CLIENT_SECRET,
            code=code,
            redirect_uri=SLACK_REDIRECT_URI,
            grant_type="authorization_code",
        )
        if response is None:
            raise HTTPException(500, detail="OAuth failed")
        authed_user = response.get("authed_user")
        if not isinstance(authed_user, dict) or "id" not in authed_user:
            raise HTTPException(500, detail="OAuth response missing user id")
        slack_user_id = authed_user["id"]
        access_token = authed_user["access_token"]
        refresh_token = authed_user["refresh_token"]
        with Session(engine) as db:
            while True:
                slackrpc_token = secrets.token_urlsafe(32)
                if not db.query(User).filter_by(token=slackrpc_token).first():
                    break
            existing = db.query(User).filter_by(slack_user_id=slack_user_id).first()
            if existing:
                existing.slack_access = access_token
                existing.slack_refresh = refresh_token
                existing.hostname = hostname
                existing.token = slackrpc_token
            else:
                db.add(
                    User(
                        token=slackrpc_token,
                        slack_user_id=slack_user_id,
                        hostname=hostname,
                        slack_access=access_token,
                        slack_refresh=refresh_token,
                    )
                )
            db.commit()
    r.setex(f"poll:{cli_code}", 6000, slackrpc_token)
    return RedirectResponse("/success")


@web_app.get("/api/auth/poll")
@limiter.limit("4/minute")
def poll_authentication_success(request: Request, code: str):
    token = r.getdel(f"poll:{code}")
    if token:
        return {"status": "complete", "token": token}
    else:
        return {"status": "waiting"}


@web_app.delete("/api/activity")
@limiter.limit("1/second")
def clear_acitivity(request: Request, user: User = Depends(get_user)):
    update_activity(user, "", "", user.slack_access, user.slack_refresh)
    return Response(status_code=204)


@web_app.post("/api/activity")
@limiter.limit("1/second")
def set_activity(
    request: Request, activity: RPCRequest, user: User = Depends(get_user)
):
    ACTIVITY_VERBS = {
        0: "Playing",
        1: "Streaming",
        2: "Listening to",
        3: "Watching",
        5: "Competing in",
    }

    ACTIVITY_EMOJIS = {
        0: ":joystick:",
        1: ":movie_camera:",
        2: ":headphones:",
        3: ":tv:",
        5: ":trophy:",
    }

    name = activity.activity.get("name", "something")
    details = activity.activity.get("details") or activity.activity.get("state", "")
    verb = ACTIVITY_VERBS.get(activity.activity.get("type", 0), "Playing")

    status_text = f"{verb} {name}: {details}" if details else f"{verb} {name}"
    status_text = status_text[:97] + "..." if len(status_text) > 100 else status_text
    emoji = ACTIVITY_EMOJIS.get(activity.activity.get("type", 0), ":video_game:")
    update_activity(user, status_text, emoji, user.slack_access, user.slack_refresh)
    return Response(status_code=204)


@app.command("/slackrpc")
def handle_command(ack, body, respond):
    # Acknowledge the command request immediately
    ack()
    parameter: list[str] = body.get("text", "").split()
    subcommand = parameter[0].lower() if parameter else ""

    if subcommand in ("", "help"):
        respond(
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": USAGE},
                }
            ]
        )
        return
    # if subcommand == "authenticate":
    #     code = parameter[1] if len(parameter) > 1 else ""
    #     if not code:
    #         respond(
    #             blocks=[
    #                 {
    #                     "type": "section",
    #                     "text": {"type": "mrkdwn", "text": USAGE},
    #                 }
    #             ]
    #         )
    #         return
    #     hostname = r.getdel(f"auth:{code}")
    #     if hostname is None:
    #         respond("Invalid authentication code, try again.")
    #     else:
    #         token = secrets.token_urlsafe(32)
    #         slack_user_id = body["user_id"]
    #         with Session(engine) as db:
    #             db.merge(
    #                 User(
    #                     token=token, slack_user_id=slack_user_id, hostname=hostname
    #                 )
    #             )
    #             db.commit()
    #         r.publish(f"auth_result:{code}", token)
    #         respond(
    #             f"Successfully authenticated *{hostname}*. Discord Rich Presence capable apps will begin updating your status."
    #         )
    if subcommand == "stop":
        with Session(engine) as db:
            user = db.query(User).filter_by(slack_user_id=body["user_id"]).first()
        if not user:
            respond(
                "You haven't linked SlackRPC yet. Visit the link provided by the CLI to authenticate, or get started at https://slackrpc.nikoo.dev"
            )
            return
        r.set(f"paused:{body['user_id']}", "1")
        respond(
            "Rich Presence updates to your Slack status have been stopped. Your status has been cleared."
        )
    elif subcommand == "start":
        with Session(engine) as db:
            user = db.query(User).filter_by(slack_user_id=body["user_id"]).first()
        if not user:
            respond(
                "You haven't linked SlackRPC yet. Visit the link provided by the CLI to authenticate, or get started at https://slackrpc.nikoo.dev"
            )
            return
        r.delete(f"paused:{body['user_id']}")
        respond(
            "Rich Presence updates to your Slack status have been restarted! You may need to wait until a new Discord Rich Presence takes effect."
        )
    else:
        respond(
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": USAGE},
                }
            ]
        )


@web_app.post("/slack/events")
async def slack_events(req: Request):
    return await bolt_handler.handle(req)


# Start your app
if __name__ == "__main__":
    uvicorn.run(web_app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
