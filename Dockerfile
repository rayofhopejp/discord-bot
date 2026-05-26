  FROM python:3.10-slim
  WORKDIR /usr/src/app
  COPY requirements.txt ./
  RUN pip install --no-cache-dir -r requirements.txt
  COPY serifu.txt /usr/src/serifu.txt
  COPY bot/ ./
  CMD ["python3", "main.py"]
