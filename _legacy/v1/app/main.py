from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import api_router
from app.config import production_problems, settings
from app.database import engine, init_db
from app.security import BasicAuthMiddleware
from app.version import __version__

TEMPLATES_DIR = "app/templates"
PAGES = {
    "/": ("index.html", "Dashboard"),
    "/transactions": ("transactions.html", "Transactions"),
    "/uncategorized": ("uncategorized.html", "Review"),
    "/formats": ("formats.html", "SMS formats"),
    "/settings": ("settings.html", "Settings"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = production_problems(settings)
    if problems:
        raise RuntimeError("Refusing to start: " + "; ".join(problems))
    await init_db()
    yield
    await engine.dispose()


app = FastAPI(
    title="Samruddhi Fin",
    description="Household finance tracker fed by forwarded bank SMS",
    version="1.0.0",
    lifespan=lifespan,
)

if settings.app_password:
    app.add_middleware(BasicAuthMiddleware, username=settings.app_username, password=settings.app_password)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico", media_type="image/x-icon")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

app.include_router(api_router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def page(request: Request):
    template, title = PAGES["/"]
    return templates.TemplateResponse(template, {"request": request, "page_title": title})


@app.get("/transactions", response_class=HTMLResponse, include_in_schema=False)
async def page_transactions(request: Request):
    template, title = PAGES["/transactions"]
    return templates.TemplateResponse(template, {"request": request, "page_title": title})


@app.get("/formats", response_class=HTMLResponse, include_in_schema=False)
async def page_formats(request: Request):
    template, title = PAGES["/formats"]
    return templates.TemplateResponse(template, {"request": request, "page_title": title})


@app.get("/uncategorized", response_class=HTMLResponse, include_in_schema=False)
async def page_uncategorized(request: Request):
    template, title = PAGES["/uncategorized"]
    return templates.TemplateResponse(template, {"request": request, "page_title": title})


@app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
async def page_settings(request: Request):
    template, title = PAGES["/settings"]
    return templates.TemplateResponse(template, {"request": request, "page_title": title})


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "samruddhi-fin", "version": __version__}