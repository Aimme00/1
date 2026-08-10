FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ASKDATA_HOST=0.0.0.0 \
    ASKDATA_PORT=8000 \
    ASKDATA_RUNTIME_DIR=/app/runtime_data

WORKDIR /app

RUN useradd --create-home --uid 10001 askdata
COPY requirements-core.txt requirements-web.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements-web.txt

COPY --chown=askdata:askdata . .
RUN mkdir -p /app/runtime_data && chown -R askdata:askdata /app/runtime_data

USER askdata
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
