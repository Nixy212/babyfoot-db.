# Configuration Gunicorn pour Render
# Configuration simple compatible avec tous les déploiements

# Workers
workers = 1  # Un seul worker pour le plan gratuit
worker_class = 'sync'  # Worker synchrone standard
threads = 4  # 4 threads par worker pour gérer les connexions

# Timeouts
timeout = 120  # Timeout de 2 minutes
keepalive = 5  # Connexions keep-alive

# Logging
accesslog = '-'  # Logs d'accès vers stdout
errorlog = '-'   # Logs d'erreur vers stdout
loglevel = 'info'

# Server mechanics
bind = '0.0.0.0:10000'  # Port par défaut Render

# Performance
max_requests = 1000  # Redémarrer worker après 1000 requêtes
max_requests_jitter = 50  # Ajouter du jitter pour éviter redémarrage simultané
