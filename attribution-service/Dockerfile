FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10002 appuser
USER appuser

EXPOSE 8010
CMD ["uvicorn", "aaw_attribution.main:app", "--host", "0.0.0.0", "--port", "8010"]
