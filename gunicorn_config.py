import os

workers = 1
worker_class = 'eventlet'
timeout = 120
keepalive = 75
bind = f"0.0.0.0:{os.environ.get('PORT', 5000)}"

# Logs
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# Configuration pour les fichiers statiques
raw_env = [
    'PYTHONUNBUFFERED=1',
]

