from pathlib import Path
import base64
import secrets
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from app.config import settings
from app.api import router

if settings.app_env == "production":
    settings.validate_production()

app = FastAPI(title="Samruddhi Finance", version="1.0.0")

@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Basic "):
        return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
    try:
        user, pwd = base64.b64decode(auth_header[6:]).decode().split(":", 1)
    except Exception:
        return PlainTextResponse("Unauthorized", 401, headers={"WWW-Authenticate": "Basic"})
    if not (secrets.compare_digest(user, settings.app_username) and secrets.compare_digest(pwd, settings.app_password)):
        return PlainTextResponse("Unauthorized", 401, headers={"WWW-Authenticate": "Basic"})
    return await call_next(request)

app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.include_router(router)

@app.get("/health")
async def health():
    return {"status": "ok"}
