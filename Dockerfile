# Two stages so the image carries the built dashboard but not node_modules.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
COPY munshi/ /munshi/
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY munshi/ ./munshi/
RUN pip install --no-cache-dir .
COPY --from=web /munshi/static ./munshi/static
COPY evaluation/ ./evaluation/

# Seed at build time so the container starts with the demo book loaded.
RUN python -m munshi.seed.load

EXPOSE 8000
ENV MUNSHI_ADAPTER=simulator
CMD ["uvicorn", "munshi.api:app", "--host", "0.0.0.0", "--port", "8000"]
