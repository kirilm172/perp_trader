#!/bin/bash
source "$(dirname "$0")/common.sh"


if [ -f .env ]; then
    echo "Uploading .env file to server..."
    scp -P "$SSH_PORT" .env "$SSH_USER@$SSH_HOST:~/tradeGPT/.env"
fi

ssh -p "$SSH_PORT" "$SSH_USER"@"$SSH_HOST" "cd ~/tradeGPT && ./scripts/update_container.sh"
