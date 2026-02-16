#!/bin/bash

# Script de démarrage pour Railway

echo "🚀 Démarrage de l'application Baby-Foot..."

# Vérifier que le dossier static existe
if [ ! -d "static" ]; then
    echo "❌ ERREUR: Le dossier static n'existe pas!"
    exit 1
fi

echo "✅ Dossier static trouvé"
ls -la static/

# Démarrer gunicorn avec la configuration appropriée
exec gunicorn \
    --worker-class eventlet \
    -w 1 \
    --bind 0.0.0.0:${PORT:-5000} \
    --timeout 120 \
    --keepalive 75 \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    app:app
