FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY . /app
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /var/lib/ai-notes/files \
    && chown appuser:appuser /var/lib/ai-notes/files
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
