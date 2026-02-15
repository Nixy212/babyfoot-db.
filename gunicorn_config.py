# Configuration Gunicorn pour Render
# worker_class threading = compatible Python 3.14, supporte WebSockets via Socket.IO

workers = 1
worker_class = 'gthread'
threads = 4
timeout = 120
keepalive = 75

accesslog = '-'
errorlog = '-'
loglevel = 'info'

bind = '0.0.0.0:10000'

max_requests = 1000
max_requests_jitter = 50
