from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import crud
from app.db import SessionLocal
from app.api import router as api_router
from app.scheduler import email_scheduler_loop
from app.web import register_web_routes
from app.utils import week_in_month_for_period


def create_app() -> FastAPI:
    app = FastAPI(title="工作周计划登记系统", version="0.1.0")

    base_dir = Path(__file__).resolve().parent.parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    templates.env.globals["week_in_month_period"] = week_in_month_for_period

    static_dir = base_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    async def _startup():
        (base_dir / "data").mkdir(parents=True, exist_ok=True)
        db = SessionLocal()
        try:
            crud.ensure_schema(db)
            crud.ensure_initial_data(db)
        finally:
            db.close()
        import asyncio

        app.state.email_scheduler_task = asyncio.create_task(email_scheduler_loop())

    @app.on_event("shutdown")
    async def _shutdown():
        task = getattr(app.state, "email_scheduler_task", None)
        if task:
            task.cancel()

    app.include_router(api_router)
    app.include_router(register_web_routes(templates))
    return app


app = create_app()
