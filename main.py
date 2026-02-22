from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app import cache
from app.routes import router

app = FastAPI(title="Bitcoin Miner Weekly Advisor")

cache.init_db()
app.include_router(router)

frontend = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=frontend), name="static")


@app.get("/")
async def index():
    return FileResponse(frontend / "index.html")
