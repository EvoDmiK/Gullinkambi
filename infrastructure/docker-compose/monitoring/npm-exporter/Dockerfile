FROM python:3.12-alpine

WORKDIR /app
COPY exporter.py /app/exporter.py

ENV NPM_LOG_DIR=/npm/logs
ENV NPM_DATABASE=/npm/database.sqlite
ENV NPM_EXPORTER_HOST=0.0.0.0
ENV NPM_EXPORTER_PORT=9823

USER 65534:65534
EXPOSE 9823

CMD ["python", "/app/exporter.py"]
