from fastapi import APIRouter
from . import health, auth, chat, sessions, attachments, og

routers = [
    health.router,
    auth.router,
    chat.router,
    sessions.router,
    attachments.router,
    og.router,
]
