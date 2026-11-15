FROM ghcr.io/mlflow/mlflow:latest

RUN MLFLOW_VERSION="$(python -c 'import mlflow; print(mlflow.__version__)')" \
    && pip install --no-cache-dir \
        "mlflow[mcp]==${MLFLOW_VERSION}" \
        psycopg2-binary

USER 10001:10001
