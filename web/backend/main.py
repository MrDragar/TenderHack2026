import csv
import io
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline.data import reference
from pipeline.run import run

MAX_TEXT = 1000
MAX_ROWS = 1000
CSV_HEADER = "наименование товара"


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


def csv_texts(raw: bytes) -> list[str]:
    # Русский Excel сохраняет CSV в cp1251 и с разделителем ";", а запятые внутри описаний не кавычит.
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1251")
    texts = [row[0].strip() for row in csv.reader(io.StringIO(content), delimiter=";") if row]
    texts = [text for text in texts if text]
    if texts and texts[0].casefold() == CSV_HEADER:
        texts = texts[1:]
    return texts


@app.post("/api/csv")
async def read_csv(request: Request):
    texts = csv_texts(await request.body())
    if not texts:
        raise HTTPException(status_code=400, detail="В файле нет описаний товаров")
    if len(texts) > MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"Не больше {MAX_ROWS} строк в файле")
    return {"texts": texts}


@app.exception_handler(HTTPException)
async def http_error(_, error: HTTPException):
    return JSONResponse(status_code=error.status_code, content={"error": error.detail})
