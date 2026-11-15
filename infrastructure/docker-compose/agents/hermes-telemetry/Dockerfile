FROM python:3.13-alpine

WORKDIR /app
COPY collector.py orca_calls.py hermes_sessions.py /app/

RUN addgroup -S telemetry && adduser -S -G telemetry telemetry \
    && mkdir -p /var/lib/hermes-telemetry \
    && chown -R telemetry:telemetry /var/lib/hermes-telemetry

USER telemetry

ENTRYPOINT ["python", "/app/collector.py"]
