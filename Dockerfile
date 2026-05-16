# syntax=docker/dockerfile:1
# Two-stage image for the read-only wiki viewer.
# Stage 1 renders the MkDocs site from wiki/; stage 2 serves it with nginx.

FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir \
    'mkdocs-material>=9.7.6' \
    'mkdocs-roamlinks-plugin>=0.3.2' \
    'pyyaml>=6.0'

COPY mkdocs.yml mkdocs_hooks.py ./
COPY src/ ./src/
COPY wiki/ ./wiki/
COPY raw/ ./raw/

RUN mkdocs build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/site/ /usr/share/nginx/html/

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
