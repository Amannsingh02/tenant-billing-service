from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from app.config import get_settings
from app.database import get_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "app": settings.APP_NAME}

    @app.get("/health/db")
    async def health_db():
        async for session in get_db():
            await session.execute(text("SELECT 1"))
            return {"status": "ok", "database": "reachable"}

    from app.routers.customers import router as customers_router
    app.include_router(customers_router)

    return app


app = create_app()