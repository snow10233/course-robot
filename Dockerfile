# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.12.11-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12.11-slim-bookworm AS runtime

# 兩個服務共用同一個 image，差別只在啟動指令：
#   backend  → backend-serve
#   calendar → calendar-daemon
ARG ENTRYPOINT_CMD="course-robot --help"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Taipei

RUN groupadd --gid 10001 course-robot \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin course-robot \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001
ENV COURSE_ROBOT_ENTRYPOINT="${ENTRYPOINT_CMD}"
ENTRYPOINT ["/bin/sh", "-c", "exec ${COURSE_ROBOT_ENTRYPOINT}"]
