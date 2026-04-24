FROM ghcr.io/astral-sh/uv:alpine

ADD . /app
WORKDIR /app

RUN uv sync --locked

ENTRYPOINT ["uv", "run", "main.py"]