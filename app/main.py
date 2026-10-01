from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import api_router
from app.database import engine, init_db

TEMPLATES_DIR = "app/templates"
PAGES = {
    "/": ("index.html", "Dashboard"),
    "/transactions": ("transactions.html", "Transactions"),
    "/uncategorized": ("uncategorized.html", "Review"),
    "/settings": ("settings.html", "Settings"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await engine.dispose()


app = FastAPI(
    title="Samruddhi Fin",
    description="Household finance tracker fed by forwarded bank SMS",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
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
    return {"status": "healthy", "service": "samruddhi-fin"}