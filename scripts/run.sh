#!/bin/bash
source "$(dirname "$0")/common.sh"

echo "Force removing container '$CONTAINER_NAME'..."
docker stop "$CONTAINER_NAME"
docker rm -f "$CONTAINER_NAME"

echo "Starting new container '$CONTAINER_NAME'..."
docker run -d --env-file ./app/.env --name "$CONTAINER_NAME" "$CONTAINER_NAME"

if [ $? -eq 0 ]; then
    echo "Container '$CONTAINER_NAME' started successfully."
else
    echo "Failed to start container '$CONTAINER_NAME'. Please check Docker logs for more information."
    exit 1
fi
