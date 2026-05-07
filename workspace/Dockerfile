FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m appuser
USER appuser
COPY --chown=appuser:appuser app/ app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
