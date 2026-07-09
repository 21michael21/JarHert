FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY assistant ./assistant
COPY backend ./backend
COPY gateway_bot ./gateway_bot
COPY reminders ./reminders
COPY telegram_collector ./telegram_collector
COPY scripts ./scripts
COPY hermes ./hermes
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "gateway_bot.telegram_app"]
