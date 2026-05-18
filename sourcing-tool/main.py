"""亚马逊选品 SOP 自动化工具 — FastAPI 入口。"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.routes import router
from app.models import init_db
from app.auth import AuthMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="亚马逊选品 SOP 自动化工具",
    description="ABA报表 → 清洗过滤 → Sorftime补数据 → 候选清单",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(AuthMiddleware)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
