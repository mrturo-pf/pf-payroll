FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir . \
    && useradd --no-create-home --shell /bin/false appuser

USER appuser

EXPOSE 8000

CMD ["uvicorn", "payroll.interfaces.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
