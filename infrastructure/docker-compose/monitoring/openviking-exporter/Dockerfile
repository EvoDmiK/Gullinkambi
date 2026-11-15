FROM hermes-telemetry:latest

WORKDIR /app
COPY exporter.py /app/exporter.py

ENV OPENVIKING_DATA_DIR=/openviking/data
ENV OPENVIKING_EXPORTER_HOST=0.0.0.0
ENV OPENVIKING_EXPORTER_PORT=9822

USER 100:101
EXPOSE 9822

ENTRYPOINT []
CMD ["python", "/app/exporter.py"]
