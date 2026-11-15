#!/usr/bin/env bash
set -euo pipefail

# 1. Resolve script directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# Load .env if it exists
ENV_FILE="${BASE_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    echo "Loading environment variables from $ENV_FILE..."
    # Export vars, ignoring comments
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "Warning: .env file not found at $ENV_FILE. Will use default/environment variables."
fi

# Set default values if not defined in .env
N8N_POSTGRES_USER="${N8N_POSTGRES_USER:-n8n}"
TZ="${TZ:-Asia/Seoul}"
TS_AUTHKEY="${TS_AUTHKEY:-}"
TS_HOSTNAME_MLFLOW="${TS_HOSTNAME_MLFLOW:-dove-mlflow}"
TS_AUTH_ONCE="${TS_AUTH_ONCE:-true}"

# Backend store URI. Prefer MLFLOW_BACKEND_STORE_URI from .env; otherwise build it from
# N8N_POSTGRES_PASSWORD (URL-encoded). Never hard-code the password here.
if [ -z "${MLFLOW_BACKEND_STORE_URI:-}" ]; then
    : "${N8N_POSTGRES_PASSWORD:?set MLFLOW_BACKEND_STORE_URI or N8N_POSTGRES_PASSWORD in .env}"
    ENCODED_PASSWORD="$(python3 -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ["N8N_POSTGRES_PASSWORD"], safe=""))')"
    MLFLOW_BACKEND_STORE_URI="postgresql+psycopg2://${N8N_POSTGRES_USER}:${ENCODED_PASSWORD}@postgres-n8n:5432/mlflow?sslmode=disable"
fi
DB_URI="$MLFLOW_BACKEND_STORE_URI"
IMAGE_NAME="mlflow-local"
NETWORK_NAME="homelab_default"

# 2. Build image if it doesn't exist
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "Building Docker image $IMAGE_NAME..."
    docker build -t "$IMAGE_NAME" - < "$SCRIPT_DIR/Dockerfile"
else
    echo "Docker image $IMAGE_NAME already exists."
fi

# 3. Create network if it doesn't exist
if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "Creating network $NETWORK_NAME..."
    docker network create "$NETWORK_NAME"
fi

# Helper to clean up containers
cleanup_container() {
    local name=$1
    if docker ps -a --format '{{.Names}}' | grep -Eq "^${name}$"; then
        echo "Stopping and removing existing container: $name..."
        docker stop "$name" >/dev/null 2>&1 || true
        docker rm "$name" >/dev/null 2>&1 || true
    fi
}

# Mode selection: default to standard host port mapping unless tailscale mode requested
MODE="${1:-standard}"

if [ "$MODE" = "tailscale" ]; then
    echo "Starting in TAILSCALE mode..."

    # Check tailscale auth key
    if [ -z "$TS_AUTHKEY" ]; then
        echo "Error: TS_AUTHKEY is not set in .env. Cannot run Tailscale mode."
        exit 1
    fi

    # Cleanup existing containers
    cleanup_container "mlflow"
    cleanup_container "tailscale-mlflow"

    # Run tailscale-mlflow container
    echo "Starting tailscale-mlflow..."
    docker run -d \
        --name tailscale-mlflow \
        --hostname "$TS_HOSTNAME_MLFLOW" \
        -e TS_AUTHKEY="$TS_AUTHKEY" \
        -e TS_STATE_DIR=/var/lib/tailscale \
        -e TS_AUTH_ONCE="$TS_AUTH_ONCE" \
        -e TS_EXTRA_ARGS="--reset" \
        -v "$SCRIPT_DIR/tailscale:/var/lib/tailscale" \
        --device /dev/net/tun:/dev/net/tun \
        --cap-add NET_ADMIN \
        --cap-add SYS_MODULE \
        --shm-size 64mb \
        --memory 128m \
        --network "$NETWORK_NAME" \
        --restart unless-stopped \
        tailscale/tailscale:latest

    # Run mlflow container in tailscale's network namespace
    echo "Starting mlflow..."
    docker run -d \
        --name mlflow \
        --network container:tailscale-mlflow \
        -e PYTHONUNBUFFERED=1 \
        -v "$SCRIPT_DIR/artifacts:/mlflow/artifacts" \
        --memory 2g \
        --restart unless-stopped \
        "$IMAGE_NAME" \
        mlflow server \
        --backend-store-uri "$DB_URI" \
        --serve-artifacts \
        --artifacts-destination /mlflow/artifacts \
        --host 0.0.0.0 \
        --port 80 \
        --workers 1 \
        --allowed-hosts "*" \
        --cors-allowed-origins "http://192.0.2.10:5000,https://mlflow.example.com"

else
    echo "Starting in STANDARD mode (exposing on host port)..."

    # Cleanup existing container
    cleanup_container "mlflow"

    # Expose port (default 5000 to host)
    HOST_PORT="${MLFLOW_PORT:-5000}"

    echo "Starting mlflow container (Host Port: $HOST_PORT)..."
    docker run -d \
        --name mlflow \
        --network "$NETWORK_NAME" \
        -p "$HOST_PORT:80" \
        -e PYTHONUNBUFFERED=1 \
        -v "$SCRIPT_DIR/artifacts:/mlflow/artifacts" \
        --memory 2g \
        --restart unless-stopped \
        "$IMAGE_NAME" \
        mlflow server \
        --backend-store-uri "$DB_URI" \
        --serve-artifacts \
        --artifacts-destination /mlflow/artifacts \
        --host 0.0.0.0 \
        --port 80 \
        --workers 1 \
        --allowed-hosts "*" \
        --cors-allowed-origins "http://192.0.2.10:5000,https://mlflow.example.com"
fi

# The public mlflow.example.com proxy resolves the `mlflow` container on
# this network. The standalone run script starts on homelab_default, so join
# the proxy network as well when it exists.
if docker network inspect proxy_manager >/dev/null 2>&1; then
    docker network connect proxy_manager mlflow 2>/dev/null || true
fi

echo "MLflow container status:"
docker ps -f name=mlflow

echo "MLflow deployment complete!"
