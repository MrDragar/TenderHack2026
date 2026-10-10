import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline.run import reference, run

MAX_TEXT = 1000


class RunRequest(BaseModel):
    text: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    reference()
    yield


app = FastAPI(
    title="Подбор кода КТРУ",
    description="Автозаполнение характеристик СТЕ по свободному описанию товара",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error(_, __):
    return JSONResponse(status_code=400, content={"error": "Опишите товар"})


@app.post("/api/run")
async def run_pipeline(payload: RunRequest):
    if not isinstance(payload.text, str) or not payload.text.strip():
        raise HTTPException(status_code=400, detail="Опишите товар")
    if len(payload.text) > MAX_TEXT:
        raise HTTPException(status_code=400, detail="Слишком длинное описание")

    text = payload.text.strip()
    try:
        return run(text)
    except Exception as error:
        logging.exception("pipeline failed on %r", text)
        raise HTTPException(status_code=500, detail="Не удалось разобрать описание") from error


@app.exception_handler(HTTPException)
async def http_error(_, error: HTTPException):
    return JSONResponse(status_code=error.status_code, content={"error": error.detail})
