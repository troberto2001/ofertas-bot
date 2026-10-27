FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright

COPY app ./app

# utilizador sem privilégios; /data é o volume com sessão, base de dados e regras
RUN useradd -u 10001 -m bot && mkdir -p /data && chown 10001:10001 /data
USER 10001

CMD ["python", "-m", "app.main"]
