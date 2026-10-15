import logging
import time
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.shared.db import SessionLocal
from app.shared.config import settings
from app.shared.errors import AppError
from app.modules.auth.router import router as auth_router
from app.modules.jobs.router import router as jobs_router
from app.modules.files.router import router as files_router
from app.modules.notes.router import router as notes_router

log = logging.getLogger("notes.api")
app = FastAPI(title="AI Notes M0", version="0.1.0")


class ErrorOut(BaseModel):
    code: str
    message: str
    request_id: str


class HealthOut(BaseModel):
    status: str


def error_response(request, status, code, message):
    return JSONResponse(status_code=status, content={"code": code, "message": message, "request_id": getattr(request.state, "request_id", str(uuid4()))})


@app.middleware("http")
async def guard(request: Request, call_next):
    request.state.request_id = str(uuid4())
    start = time.monotonic()
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != settings().app_origin:
        response = error_response(request, 403, "origin_invalid", "请求来源不受信任")
    else:
        try:
            response = await call_next(request)
        except Exception:
            log.error("request_failed request_id=%s", request.state.request_id)
            response = error_response(request, 500, "internal_error", "服务暂时不可用")
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    # No URL, query, bodies, cookies, or token-bearing headers in request logs.
    log.info("request_id=%s status=%s elapsed_ms=%s", request.state.request_id, response.status_code, round((time.monotonic() - start) * 1000))
    return response


@app.exception_handler(AppError)
async def app_error(request, exc):
    return error_response(request, exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's full error object can contain the submitted password.
    return error_response(request, 422, "validation_error", "输入格式不正确，请检查各项长度和格式")


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error_response(request, exc.status_code, "http_error", "请求不存在或不受支持")


@app.get("/api/v1/health/live", response_model=HealthOut)
def live():
    return {"status": "ok"}


@app.get("/api/v1/health/ready", response_model=HealthOut, responses={503: {"model": ErrorOut}})
def ready(request: Request):
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1 FROM users LIMIT 1"))
        return {"status": "ok"}
    except SQLAlchemyError:
        return error_response(request, 503, "database_unavailable", "数据库尚未就绪")


errors = {status: {"model": ErrorOut} for status in (400, 401, 403, 404, 409, 422, 429, 500)}
app.include_router(auth_router, prefix="/api/v1", responses=errors)
app.include_router(jobs_router, prefix="/api/v1", responses=errors)
app.include_router(files_router, prefix="/api/v1", responses=errors)
app.include_router(notes_router, prefix="/api/v1", responses=errors)
