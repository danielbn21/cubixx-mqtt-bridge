# Standalone Docker image. The Home Assistant app lives in ./cubixx_bridge and has
# its own Dockerfile; both use the same code from cubixx_bridge/app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8080

WORKDIR /srv
COPY cubixx_bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cubixx_bridge/app ./app

VOLUME /data
EXPOSE 8080
CMD ["python", "app/main.py"]
