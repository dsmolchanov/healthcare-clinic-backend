#!/bin/bash

# Start the Healthcare Backend Server
set -a # automatically export all variables

# Get the directory of the script to robustly locate other files
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Load environment variables
export $(cat ../.env | grep -v '^#' | xargs)
ENV_FILE="$SCRIPT_DIR/../.env"
if [ -f "$ENV_FILE" ]; then
    echo "📋 Loading environment variables from $ENV_FILE"
    source "$ENV_FILE"
else
    echo "⚠️  Warning: .env file not found at $ENV_FILE. Server may not start correctly."
fi
set +a

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
VENV_PATH="$SCRIPT_DIR/venv/bin/activate"
if [ -f "$VENV_PATH" ]; then
    echo "🐍 Activating Python virtual environment"
    source "$VENV_PATH"
fi

echo "Starting Healthcare Backend on port 8000..."
echo "WhatsApp webhook will be available at: http://localhost:8000/webhooks/whatsapp"
echo ""

# Start the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
