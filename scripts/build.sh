#!/bin/bash
source "$(dirname "$0")/common.sh"

docker build -t "$CONTAINER_NAME" ./app