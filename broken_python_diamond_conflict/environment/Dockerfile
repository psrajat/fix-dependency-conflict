FROM python:3.12.3-slim

RUN apt-get update && apt-get install -y \
    asciinema \
    curl \
    procps \
    tmux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app/

RUN pip install --no-cache-dir \
    Flask==3.0.3 \
    pytest==8.3.5 \
    requests==2.32.3

RUN pip install --no-cache-dir \
    -r /app/requirements.txt

ENV APP_PORT=8000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

CMD ["python", "/app/app.py"]
