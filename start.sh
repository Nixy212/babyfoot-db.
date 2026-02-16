#!/bin/bash
set -e

echo "🚀 Starting Baby-Foot application..."

# Lancer gunicorn
exec gunicorn \
    --worker-class eventlet \
    -w 1 \
    --bind 0.0.0.0:${PORT:-8080} \
    --timeout 120 \
    --keepalive 75 \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    app:app
