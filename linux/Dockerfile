FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
    vim htop tree jq fonts-dejavu-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY src/ ./src/
COPY entrypoint.sh .
COPY serif.txt ./serif.txt
RUN chmod +x entrypoint.sh

RUN mkdir -p /workspace /shared

CMD ["./entrypoint.sh"]
