FROM node:22-alpine AS control_room
WORKDIR /ui
COPY control-room-next/package.json control-room-next/package-lock.json ./
RUN npm ci
COPY control-room-next/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m appuser
COPY --chown=appuser:appuser app/ app/
COPY --from=control_room --chown=appuser:appuser /ui/out/ app/static/control-room/
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
