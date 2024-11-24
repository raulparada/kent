FROM python:3.11.5-slim-bookworm

WORKDIR /app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY . .
RUN pip install -U 'pip>=8' && \
pip install --no-cache-dir .

RUN groupadd -r kent && useradd --no-log-init -r -g kent kent
USER kent

ENTRYPOINT ["/usr/local/bin/kent-server"]
CMD ["run"]
