# AI Food Analyzer - single image, runs either the HTTP API or the CLI.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai/ ./ai/
COPY src/ ./src/
COPY data/ ./data/
COPY tests/ ./tests/
COPY foodanalyzer.py pytest.ini ./

RUN mkdir -p data/uploads

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]