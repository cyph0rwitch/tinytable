FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY models.py replika_provider.py kindroid_provider.py run_replika_tinytable.sh ./
# Setup helpers used by `docker compose run --rm setup` (see docs/SETUP.md).
COPY scripts/matrix_bootstrap.py scripts/tinytable_setup.py ./scripts/
COPY tools/extract_kindroid_session.py ./tools/
COPY --from=frontend /build/dist ./frontend/dist
# The runtime UID is non-root, so the provider wrapper must be executable by it.
RUN chmod 0555 /app/run_replika_tinytable.sh /app/replika_provider.py /app/kindroid_provider.py
ENV TINYTABLE_DB=/data/tinytable.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
