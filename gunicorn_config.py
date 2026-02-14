# Configuration Gunicorn pour Render
# Optimisé pour WebSockets (Socket.IO)

import multiprocessing

# Workers
workers = 2  # Nombre de workers (2 pour le plan gratuit)
worker_class = 'gevent'  # Nécessaire pour Socket.IO
worker_connections = 1000  # Connexions simultanées par worker

# Timeouts
timeout = 120  # Timeout de 2 minutes
keepalive = 5  # Connexions keep-alive

# Logging
accesslog = '-'  # Logs d'accès vers stdout
errorlog = '-'   # Logs d'erreur vers stdout
loglevel = 'info'

# Server mechanics
bind = '0.0.0.0:10000'  # Port par défaut Render
preload_app = True  # Charger l'app avant de forker

# Performance
max_requests = 1000  # Redémarrer worker après 1000 requêtes
max_requests_jitter = 50  # Ajouter du jitter pour éviter redémarrage simultané
