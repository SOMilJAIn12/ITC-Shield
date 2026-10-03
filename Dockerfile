# ITC Shield - single-service image: builds the React UI, then serves UI + API from FastAPI on $PORT.
# Works as-is on Render (Docker), Railway, Fly.io, or any container host.  docker build -t itc-shield . && docker run -p 8000:8000 itc-shield

# ---- stage 1: build the frontend ----
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# VITE_API_BASE stays empty: the API serves the UI, so relative /api URLs work and no CORS is needed.
RUN npm run build

# ---- stage 2: backend + built UI ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=ui /ui/dist ./frontend/dist
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health')"
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
