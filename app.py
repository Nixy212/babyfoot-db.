import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
from functools import wraps
import json
import bcrypt
import os
import logging
import traceback
import sys

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(**name**)

app = Flask(**name**, static_folder=‘static’, static_url_path=’/static’)
app.secret_key = os.environ.get(‘SECRET_KEY’, ‘babyfoot-secret-key-2024-change-me’)
app.config[‘PERMANENT_SESSION_LIFETIME’] = timedelta(days=7)
app.config[‘SESSION_COOKIE_SAMESITE’] = ‘Lax’
app.config[‘SESSION_COOKIE_SECURE’] = False
app.config[‘SESSION_COOKIE_HTTPONLY’] = True
app.config[‘SESSION_COOKIE_PATH’] = ‘/’
app.config[‘SESSION_REFRESH_EACH_REQUEST’] = True

# Configuration pour les fichiers statiques en production

app.config[‘SEND_FILE_MAX_AGE_DEFAULT’] = 31536000  # Cache 1 an pour les fichiers statiques

socketio = SocketIO(app, cors_allowed_origins=”*”, logger=True, engineio_logger=True,
ping_timeout=60, ping_interval=25, async_mode=“eventlet”, manage_session=False)

@app.before_request
def handle_http_for_arduino():
if request.path.startswith(’/api/arduino/’):
return None
if not request.is_secure:
forwarded_proto = request.headers.get(‘X-Forwarded-Proto’, ‘’)
if forwarded_proto and forwarded_proto != ‘https’:
if ‘localhost’ not in request.host and ‘127.0.0.1’ not in request.host:
secure_url = request.url.replace(‘http://’, ‘https://’, 1)
return redirect(secure_url, code=301)
return None

DATABASE_URL = os.environ.get(‘DATABASE_URL’)
if DATABASE_URL and DATABASE_URL.startswith(‘postgres://’):
DATABASE_URL = DATABASE_URL.replace(‘postgres://’, ‘postgresql://’, 1)

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
import psycopg2
import psycopg2.extras
else:
import sqlite3
DB_PATH = os.environ.get(‘DB_PATH’, ‘babyfoot.db’)

# ── État global ──────────────────────────────────────────────

current_game = {
“team1_score”: 0, “team2_score”: 0,
“team1_players”: [], “team2_players”: [],
“active”: False, “started_by”: None,
“reserved_by”: None, “started_at”: None
}

active_lobby = {
“host”: None, “invited”: [], “accepted”: [],
“declined”: [], “team1”: [], “team2”: [], “active”: False
}

team_swap_requests = {}
rematch_votes = {“team1”: [], “team2”: []}
servo_commands = {“servo1”: [], “servo2”: []}

# ── DB ───────────────────────────────────────────────────────

def get_db_connection():
if USE_POSTGRES:
return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
else:
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
return conn

def row_to_dict(row):
if row is None:
return None
return dict(row)

def init_database():
conn = get_db_connection()
cur = conn.cursor()
if USE_POSTGRES:
cur.execute(”””
CREATE TABLE IF NOT EXISTS users (
username VARCHAR(50) PRIMARY KEY,
password VARCHAR(200) NOT NULL,
total_goals INTEGER DEFAULT 0,
total_games INTEGER DEFAULT 0,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
“””)
cur.execute(”””
CREATE TABLE IF NOT EXISTS reservations (
id SERIAL PRIMARY KEY,
day VARCHAR(20) NOT NULL,
time VARCHAR(10) NOT NULL,
team1 TEXT[] NOT NULL,
team2 TEXT[] NOT NULL,
mode VARCHAR(10) DEFAULT ‘2v2’,
reserved_by VARCHAR(50) NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
UNIQUE(day, time)
)
“””)
cur.execute(”””
CREATE TABLE IF NOT EXISTS scores (
id SERIAL PRIMARY KEY,
username VARCHAR(50) NOT NULL,
score INTEGER NOT NULL,
date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
)
“””)
# Table games pour stocker les parties complètes
cur.execute(”””
CREATE TABLE IF NOT EXISTS games (
id SERIAL PRIMARY KEY,
team1_players TEXT NOT NULL,
team2_players TEXT NOT NULL,
team1_score INTEGER NOT NULL,
team2_score INTEGER NOT NULL,
winner VARCHAR(10) NOT NULL,
mode VARCHAR(10) DEFAULT ‘2v2’,
started_by VARCHAR(50),
date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
“””)
else:
cur.execute(”””
CREATE TABLE IF NOT EXISTS users (
username TEXT PRIMARY KEY,
password TEXT NOT NULL,
total_goals INTEGER DEFAULT 0,
total_games INTEGER DEFAULT 0,
created_at TEXT DEFAULT (datetime(‘now’))
)
“””)
cur.execute(”””
CREATE TABLE IF NOT EXISTS reservations (
id INTEGER PRIMARY KEY AUTOINCREMENT,
day TEXT NOT NULL,
time TEXT NOT NULL,
team1 TEXT NOT NULL,
team2 TEXT NOT NULL,
mode TEXT DEFAULT ‘2v2’,
reserved_by TEXT NOT NULL,
created_at TEXT DEFAULT (datetime(‘now’)),
UNIQUE(day, time)
)
“””)
cur.execute(”””
CREATE TABLE IF NOT EXISTS scores (
id INTEGER PRIMARY KEY AUTOINCREMENT,
username TEXT NOT NULL,
score INTEGER NOT NULL,
date TEXT DEFAULT (datetime(‘now’))
)
“””)
# Table games pour stocker les parties complètes
cur.execute(”””
CREATE TABLE IF NOT EXISTS games (
id INTEGER PRIMARY KEY AUTOINCREMENT,
team1_players TEXT NOT NULL,
team2_players TEXT NOT NULL,
team1_score INTEGER NOT NULL,
team2_score INTEGER NOT NULL,
winner TEXT NOT NULL,
mode TEXT DEFAULT ‘2v2’,
started_by TEXT,
date TEXT DEFAULT (datetime(‘now’))
)
“””)
conn.commit()
cur.close()
conn.close()
logger.info(f”✅ DB initialisée ({‘PostgreSQL’ if USE_POSTGRES else ‘SQLite’})”)

def seed_accounts():
accounts = [
(“alice”,“test123”), (“bob”,“test123”), (“charlie”,“test123”), (“diana”,“test123”),
(“Imran”,“imran2024”), (“Apoutou”,“admin123”), (“Hamara”,“admin123”), (“MDA”,“admin123”),
(“Joueur1”,“guest”), (“Joueur2”,“guest”), (“Joueur3”,“guest”), (“Joueur4”,“guest”),
]
try:
conn = get_db_connection()
cur = conn.cursor()
for username, password in accounts:
q = “SELECT username FROM users WHERE username = %s” if USE_POSTGRES else “SELECT username FROM users WHERE username = ?”
cur.execute(q, (username,))
if not cur.fetchone():
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
q2 = “INSERT INTO users (username, password, total_goals, total_games) VALUES (%s, %s, 0, 0)” if USE_POSTGRES else “INSERT INTO users (username, password, total_goals, total_games) VALUES (?, ?, 0, 0)”
cur.execute(q2, (username, hashed))
conn.commit()
cur.close()
conn.close()
except Exception as e:
logger.warning(f”Seed accounts: {e}”)

def cleanup_old_data():
try:
conn = get_db_connection()
cur = conn.cursor()
if USE_POSTGRES:
cur.execute(“DELETE FROM scores WHERE date < NOW() - INTERVAL ‘6 months’”)
cur.execute(“DELETE FROM games WHERE date < NOW() - INTERVAL ‘6 months’”)
cur.execute(“DELETE FROM reservations WHERE created_at < NOW() - INTERVAL ‘7 days’”)
else:
cur.execute(“DELETE FROM scores WHERE date < datetime(‘now’, ‘-6 months’)”)
cur.execute(“DELETE FROM games WHERE date < datetime(‘now’, ‘-6 months’)”)
cur.execute(“DELETE FROM reservations WHERE created_at < datetime(‘now’, ‘-7 days’)”)
conn.commit()
cur.close()
conn.close()
except Exception as e:
logger.error(f”Erreur cleanup: {e}”)

def schedule_cleanup():
import threading
cleanup_old_data()
threading.Timer(86400, schedule_cleanup).start()

def is_admin(username):
return username in [“Imran”, “Apoutou”, “Hamara”, “MDA”]

def is_guest_player(username):
return username in [“Joueur1”, “Joueur2”, “Joueur3”, “Joueur4”]
def handle_errors(f):
@wraps(f)
def decorated(*args, **kwargs):
try:
return f(*args, **kwargs)
except ValueError as e:
return jsonify({“success”: False, “message”: str(e)}), 400
except Exception as e:
logger.error(f”Erreur {f.**name**}: {e}\n{traceback.format_exc()}”)
return jsonify({“success”: False, “message”: “Erreur serveur”}), 500
return decorated

def validate_username(u):
if not u or not isinstance(u, str): raise ValueError(“Nom d’utilisateur requis”)
u = u.strip()
if len(u) < 3: raise ValueError(“Minimum 3 caractères”)
if len(u) > 20: raise ValueError(“Maximum 20 caractères”)
if not u.replace(’_’,’’).replace(’-’,’’).isalnum(): raise ValueError(“Lettres, chiffres, - et _ uniquement”)
return u

def validate_password(p):
if not p or not isinstance(p, str): raise ValueError(“Mot de passe requis”)
if len(p) < 6: raise ValueError(“Minimum 6 caractères”)
return p

# ── Pages ────────────────────────────────────────────────────

@app.route(”/”)
def index(): return render_template(“index.html”)

# ═══════════════════════════════════════════════════════════

# MODIFICATION 1 : Fonction has_active_reservation

# LIGNE 236-254 : REMPLACER COMPLÈTEMENT

# ═══════════════════════════════════════════════════════════

def has_active_reservation(username):
“”“Vérifie si l’utilisateur a une réservation ACTIVE maintenant”””
try:
conn = get_db_connection()
cur = conn.cursor()

```
    # Jour actuel
    today = datetime.now().strftime('%A')
    days_fr = {
        'Monday':'Lundi','Tuesday':'Mardi','Wednesday':'Mercredi',
        'Thursday':'Jeudi','Friday':'Vendredi','Saturday':'Samedi','Sunday':'Dimanche'
    }
    day_fr = days_fr.get(today, today)
    
    # Heure actuelle
    now = datetime.now()
    current_time = now.strftime('%H:%M')
    
    # Récupérer les réservations du jour
    q = "SELECT * FROM reservations WHERE reserved_by = %s AND day = %s" if USE_POSTGRES else "SELECT * FROM reservations WHERE reserved_by = ? AND day = ?"
    cur.execute(q, (username, day_fr))
    reservations = cur.fetchall()
    cur.close()
    conn.close()
    
    # Vérifier si une réservation est active maintenant
    for res in reservations:
        res_dict = row_to_dict(res)
        res_time = res_dict['time']  # Format: "14:00"
        
        # Parser l'heure de réservation
        res_hour, res_min = map(int, res_time.split(':'))
        res_datetime = now.replace(hour=res_hour, minute=res_min, second=0, microsecond=0)
        
        # Réservation dure 25 minutes
        res_end = res_datetime + timedelta(minutes=25)
        
        # Vérifier si on est dans le créneau
        if res_datetime <= now <= res_end:
            return True
    
    return False
    
except Exception as e:
    logger.error(f"Erreur has_active_reservation: {e}")
    return False
```

# ═══════════════════════════════════════════════════════════

# MODIFICATION 2 : Route suppression utilisateur

# AJOUTER APRÈS LA LIGNE 600 (après les autres routes /api/)

# ═══════════════════════════════════════════════════════════

@app.route(’/api/delete_user’, methods=[‘POST’])
@handle_errors
def delete_user():
“”“Supprime un utilisateur (admin seulement)”””
admin_username = session.get(‘username’)

```
# Vérifier que l'utilisateur est admin
if not is_admin(admin_username):
    return jsonify({"success": False, "message": "Accès refusé"}), 403

data = request.get_json()
username_to_delete = data.get('username')

if not username_to_delete:
    return jsonify({"success": False, "message": "Nom d'utilisateur requis"}), 400

# Empêcher de se supprimer soi-même
if username_to_delete == admin_username:
    return jsonify({"success": False, "message": "Vous ne pouvez pas vous supprimer vous-même"}), 400

# Empêcher de supprimer les comptes de test
protected_accounts = ['alice', 'bob', 'charlie', 'diana']
if username_to_delete in protected_accounts:
    return jsonify({"success": False, "message": f"Le compte '{username_to_delete}' est protégé"}), 400

try:
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Vérifier que l'utilisateur existe
    q_check = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
    cur.execute(q_check, (username_to_delete,))
    user = cur.fetchone()
    
    if not user:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Utilisateur introuvable"}), 404
    
    # Supprimer l'utilisateur (CASCADE va supprimer ses scores)
    q_delete = "DELETE FROM users WHERE username = %s" if USE_POSTGRES else "DELETE FROM users WHERE username = ?"
    cur.execute(q_delete, (username_to_delete,))
    
    # Supprimer ses réservations aussi
    q_res = "DELETE FROM reservations WHERE reserved_by = %s" if USE_POSTGRES else "DELETE FROM reservations WHERE reserved_by = ?"
    cur.execute(q_res, (username_to_delete,))
    
    conn.commit()
    cur.close()
    conn.close()
    
    logger.info(f"✅ Admin {admin_username} a supprimé le compte {username_to_delete}")
    
    return jsonify({
        "success": True,
        "message": f"Compte '{username_to_delete}' supprimé avec succès"
    })
    
except Exception as e:
    logger.error(f"Erreur suppression utilisateur: {e}")
    return jsonify({"success": False, "message": "Erreur serveur"}), 500
```

try:
init_database()
seed_accounts()
schedule_cleanup()
logger.info(“✅ Système initialisé”)
except Exception as e:
logger.error(f”Erreur init DB: {e}”)

@app.route(”/login”)
def login_page(): return render_template(“login.html”)
@app.route(”/register”)
def register_page(): return render_template(“register.html”)
@app.route(”/dashboard”)
def dashboard():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“dashboard.html”)
@app.route(”/reservation”)
def reservation():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“reservation.html”)
@app.route(”/lobby”)
def lobby_page():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“lobby.html”)
@app.route(”/admin”)
def admin_page():
if “username” not in session: return redirect(url_for(‘login_page’))
if not is_admin(session.get(‘username’)): return redirect(url_for(‘index’))
return render_template(“admin.html”)
@app.route(”/live-score”)
def live_score():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“live-score.html”)
@app.route(”/stats”)
def stats():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“stats.html”)
@app.route(”/top”)
def top():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“top.html”)
@app.route(”/scores”)
def scores():
if “username” not in session: return redirect(url_for(‘login_page’))
return render_template(“scores.html”)

# ── Health ───────────────────────────────────────────────────

@app.route(”/health”)
def health_check():
try:
conn = get_db_connection()
cur = conn.cursor()
cur.execute(“SELECT 1”)
cur.close()
conn.close()
return jsonify({“status”: “healthy”, “database”: “connected”, “timestamp”: datetime.now().isoformat()}), 200
except Exception as e:
return jsonify({“status”: “unhealthy”, “error”: str(e)}), 500

@app.route(”/debug/static”)
def debug_static():
“”“Route de debug pour vérifier que les fichiers statiques sont accessibles”””
import os
static_path = os.path.join(app.root_path, ‘static’)
files_info = {
“static_folder”: app.static_folder,
“static_url_path”: app.static_url_path,
“static_path_exists”: os.path.exists(static_path),
“root_path”: app.root_path
}
if os.path.exists(static_path):
files_info[“static_files”] = os.listdir(static_path)
return jsonify(files_info), 200

@app.route(”/debug/game”)
def debug_game():
return jsonify({
“current_game”: current_game,
“active_lobby”: active_lobby,
“rematch_votes”: rematch_votes,
“servo_commands”: servo_commands
})

# ── Auth ─────────────────────────────────────────────────────

@app.route(”/api/register”, methods=[“POST”])
@handle_errors
def api_register():
data = request.get_json(silent=True)
if not data: return jsonify({“success”: False, “message”: “Aucune donnée”}), 400
username = validate_username(data.get(“username”, “”))
password = validate_password(data.get(“password”, “”))
conn = get_db_connection()
cur = conn.cursor()
q = “SELECT username FROM users WHERE username = %s” if USE_POSTGRES else “SELECT username FROM users WHERE username = ?”
cur.execute(q, (username,))
if cur.fetchone():
cur.close(); conn.close()
return jsonify({“success”: False, “message”: “Nom d’utilisateur déjà pris”}), 409
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
q2 = “INSERT INTO users (username, password) VALUES (%s, %s)” if USE_POSTGRES else “INSERT INTO users (username, password) VALUES (?, ?)”
cur.execute(q2, (username, hashed))
conn.commit(); cur.close(); conn.close()
# ✅ Créer la session automatiquement après inscription
session.permanent = True
session[‘username’] = username
return jsonify({“success”: True, “is_admin”: is_admin(username)})

@app.route(”/api/login”, methods=[“POST”])
@handle_errors
def api_login():
data = request.get_json(silent=True)
username = data.get(“username”, “”).strip()
password = data.get(“password”, “”)
conn = get_db_connection()
cur = conn.cursor()
q = “SELECT * FROM users WHERE username = %s” if USE_POSTGRES else “SELECT * FROM users WHERE username = ?”
cur.execute(q, (username,))
user = row_to_dict(cur.fetchone())
cur.close(); conn.close()
if not user: return jsonify({“success”: False, “message”: “Utilisateur inconnu”}), 401
if not bcrypt.checkpw(password.encode(), user[“password”].encode()):
return jsonify({“success”: False, “message”: “Mot de passe incorrect”}), 401
session.permanent = True
session[‘username’] = username
return jsonify({“success”: True, “is_admin”: is_admin(username)})

@app.route(”/api/logout”, methods=[“POST”])
def api_logout():
session.clear()
return jsonify({“success”: True})

@app.route(”/current_user”)
def current_user():
username = session.get(‘username’)
if not username: return jsonify(None), 401
return jsonify({
“username”: username,
“is_admin”: is_admin(username),
“has_reservation”: has_active_reservation(username)
})

@app.route(”/api/is_admin”)
def api_is_admin():
username = session.get(‘username’)
if not username: return jsonify({“is_admin”: False})
return jsonify({“is_admin”: is_admin(username)})

# ── Data ─────────────────────────────────────────────────────

@app.route(”/reservations_all”)
@handle_errors
def reservations_all():
“””
Retourne un dict {jour: {heure: {reserved_by, mode}}}
pour que reservation.html puisse afficher les créneaux pris
“””
conn = get_db_connection()
cur = conn.cursor()
cur.execute(“SELECT day, time, mode, reserved_by FROM reservations”)
rows = cur.fetchall()
cur.close(); conn.close()

```
result = {}
for row in rows:
    r = row_to_dict(row)
    day = r['day']
    time = r['time']
    if day not in result:
        result[day] = {}
    result[day][time] = {
        'reserved_by': r['reserved_by'],
        'mode': r.get('mode', '2v2')
    }
return jsonify(result)
```

@app.route(”/leaderboard”)
@handle_errors
def leaderboard():
conn = get_db_connection()
cur = conn.cursor()
cur.execute(“SELECT username, total_goals, total_games FROM users ORDER BY total_goals DESC LIMIT 10”)
rows = cur.fetchall()
cur.close(); conn.close()
return jsonify([row_to_dict(r) for r in rows])

@app.route(”/user_stats/<username>”)
@handle_errors
def user_stats(username):
“”“Stats utilisateur - HTML pour navigation, JSON pour API”””
conn = get_db_connection()
cur = conn.cursor()
q = “SELECT * FROM users WHERE username = %s” if USE_POSTGRES else “SELECT * FROM users WHERE username = ?”
cur.execute(q, (username,))
user = row_to_dict(cur.fetchone())
if not user:
cur.close(); conn.close()
if ‘text/html’ in request.accept_mimetypes:
return redirect(url_for(‘admin’))
return jsonify(None), 404

```
q2 = "SELECT score, date FROM scores WHERE username = %s ORDER BY date DESC LIMIT 20" if USE_POSTGRES else "SELECT score, date FROM scores WHERE username = ? ORDER BY date DESC LIMIT 20"
cur.execute(q2, (username,))
scores_rows = [row_to_dict(r) for r in cur.fetchall()]
cur.close(); conn.close()

total_games = user.get('total_games', 0)
total_goals = user.get('total_goals', 0)

stats_data = {
    "username": user['username'],
    "total_games": total_games,
    "total_goals": total_goals,
    "ratio": round(total_goals / total_games, 2) if total_games > 0 else 0,
    "best_score": max([s['score'] for s in scores_rows], default=0),
    "average_score": round(sum([s['score'] for s in scores_rows]) / len(scores_rows), 2) if scores_rows else 0,
    "recent_scores": scores_rows
}

# Si la requête accepte HTML (navigateur), retourner la page
if 'text/html' in request.accept_mimetypes:
    current_user = session.get('username')
    if is_admin(current_user):
        # Utiliser le template stats.html avec les données de l'utilisateur spécifique
        return render_template('stats.html', user_stats=stats_data, target_username=username)
    else:
        return redirect(url_for('dashboard'))

# Sinon retourner JSON (pour fetch)
return jsonify(stats_data)
```

@app.route(”/scores_all”)
@handle_errors
def scores_all():
“”“Retourne les parties complètes depuis la table games”””
conn = get_db_connection()
cur = conn.cursor()
cur.execute(“SELECT * FROM games ORDER BY date DESC LIMIT 50”)
rows = cur.fetchall()
cur.close(); conn.close()

```
result = []
for row in rows:
    r = row_to_dict(row)
    # Parser les players (JSON string en SQLite, array en PostgreSQL)
    t1 = r.get('team1_players', '[]')
    t2 = r.get('team2_players', '[]')
    if isinstance(t1, str):
        try: t1 = json.loads(t1)
        except: t1 = [t1]
    if isinstance(t2, str):
        try: t2 = json.loads(t2)
        except: t2 = [t2]
    r['team1_players'] = t1
    r['team2_players'] = t2
    result.append(r)
return jsonify(result)
```

@app.route(”/admin/reset_database”, methods=[“POST”])
def admin_reset_database():
username = session.get(‘username’)
if not is_admin(username):
return jsonify({“success”: False, “message”: “Admin requis”}), 403
try:
conn = get_db_connection()
cur = conn.cursor()
cur.execute(“DELETE FROM scores”)
cur.execute(“DELETE FROM reservations”)
cur.execute(“DELETE FROM games”)
cur.execute(“DELETE FROM users”)
conn.commit(); cur.close(); conn.close()
seed_accounts()
return jsonify({“success”: True, “message”: “Base de données réinitialisée”})
except Exception as e:
return jsonify({“success”: False, “message”: str(e)}), 500

@app.route(”/save_reservation”, methods=[“POST”])
@handle_errors
def save_reservation():
if “username” not in session:
return jsonify({“success”: False, “message”: “Non authentifié”}), 401
data = request.get_json(silent=True)
day = data.get(“day”)
time = data.get(“time”)
team1 = data.get(“team1”, [])
team2 = data.get(“team2”, [])
mode = data.get(“mode”, “1v1”)
reserved_by = session.get(“username”, “unknown”)
if not day or not time:
return jsonify({“success”: False, “message”: “Jour et heure requis”}), 400
conn = get_db_connection()
cur = conn.cursor()
if USE_POSTGRES:
cur.execute(“DELETE FROM reservations WHERE day = %s AND time = %s”, (day, time))
cur.execute(
“INSERT INTO reservations (day, time, team1, team2, mode, reserved_by) VALUES (%s, %s, %s, %s, %s, %s)”,
(day, time, team1, team2, mode, reserved_by)
)
else:
cur.execute(“DELETE FROM reservations WHERE day = ? AND time = ?”, (day, time))
cur.execute(
“INSERT INTO reservations (day, time, team1, team2, mode, reserved_by) VALUES (?, ?, ?, ?, ?, ?)”,
(day, time, json.dumps(team1), json.dumps(team2), mode, reserved_by)
)
conn.commit(); cur.close(); conn.close()
return jsonify({“success”: True})

@app.route(”/cancel_reservation”, methods=[“POST”])
@handle_errors
def cancel_reservation():
if “username” not in session:
return jsonify({“success”: False, “message”: “Non authentifié”}), 401
data = request.get_json(silent=True)
day = data.get(“day”)
time = data.get(“time”)
username = session.get(“username”)
conn = get_db_connection()
cur = conn.cursor()
q = “DELETE FROM reservations WHERE day = %s AND time = %s AND reserved_by = %s” if USE_POSTGRES else “DELETE FROM reservations WHERE day = ? AND time = ? AND reserved_by = ?”
cur.execute(q, (day, time, username))
deleted = cur.rowcount
conn.commit(); cur.close(); conn.close()
return jsonify({“success”: bool(deleted)})

@app.route(”/users_list”)
@handle_errors
def users_list():
conn = get_db_connection()
cur = conn.cursor()
cur.execute(“SELECT username FROM users ORDER BY username ASC”)
rows = cur.fetchall()
cur.close(); conn.close()
return jsonify([row_to_dict(r)[‘username’] for r in rows])

@app.route(”/api/current_game”)
def api_current_game():
return jsonify(current_game)

@app.route(”/api/has_active_game”)
def api_has_active_game():
username = session.get(‘username’)
return jsonify({
“has_active_game”: current_game.get(‘active’, False),
“game_data”: current_game if current_game.get(‘active’) else None,
“is_admin”: is_admin(username) if username else False,
“has_reservation”: has_active_reservation(username) if username else False
})

@app.route(”/api/active_lobby”)
def api_active_lobby():
return jsonify(active_lobby)

# ── Stats publiques ───────────────────────────────────────────

@app.route(”/api/public_stats”)
@handle_errors
def api_public_stats():
“”“Stats publiques corrigées pour la page d’accueil”””
conn = get_db_connection()
cur = conn.cursor()

```
# Nombre de parties réelles depuis la table games
cur.execute("SELECT COUNT(*) as cnt FROM games")
row = row_to_dict(cur.fetchone())
total_games = int(row.get('cnt') or 0)

# Joueurs ayant au moins 1 partie
cur.execute("SELECT COUNT(*) as cnt FROM users WHERE total_games > 0")
row2 = row_to_dict(cur.fetchone())
active_players = int(row2.get('cnt') or 0)

cur.close()
conn.close()
return jsonify({
    "total_games": total_games,
    "active_players": active_players,
    "avg_duration_minutes": 15
})
```

# ── Réservations dashboard ────────────────────────────────────

@app.route(”/reservations_today”)
@handle_errors
def reservations_today():
“”“Réservations du jour + lendemain pour le dashboard”””
if “username” not in session:
return jsonify([])
conn = get_db_connection()
cur = conn.cursor()
today = datetime.now().strftime(’%A’)
days_fr = {
‘Monday’: ‘Lundi’, ‘Tuesday’: ‘Mardi’, ‘Wednesday’: ‘Mercredi’,
‘Thursday’: ‘Jeudi’, ‘Friday’: ‘Vendredi’, ‘Saturday’: ‘Samedi’, ‘Sunday’: ‘Dimanche’
}
day_fr = days_fr.get(today, today)
tomorrow = (datetime.now() + timedelta(days=1)).strftime(’%A’)
day_fr_tomorrow = days_fr.get(tomorrow, tomorrow)
if USE_POSTGRES:
cur.execute(
“SELECT day, time, mode, reserved_by FROM reservations WHERE day = %s OR day = %s ORDER BY time ASC LIMIT 5”,
(day_fr, day_fr_tomorrow)
)
else:
cur.execute(
“SELECT day, time, mode, reserved_by FROM reservations WHERE day = ? OR day = ? ORDER BY time ASC LIMIT 5”,
(day_fr, day_fr_tomorrow)
)
rows = [row_to_dict(r) for r in cur.fetchall()]
cur.close()
conn.close()
return jsonify(rows)

@app.route(”/stats/<username>”)
@handle_errors
def stats_by_username(username):
return user_stats(username)

# ── Arduino HTTP endpoints ────────────────────────────────────

arduino_last_goal_time = {}

@app.route(”/api/arduino/status”, methods=[“GET”])
def api_arduino_status():
return jsonify({
“game_active”: current_game.get(“active”, False),
“team1_score”: current_game.get(“team1_score”, 0),
“team2_score”: current_game.get(“team2_score”, 0),
})

@app.route(”/api/arduino/commands”, methods=[“GET”])
def api_arduino_commands():
global servo_commands
import time
now = time.time()
if not hasattr(api_arduino_commands, ‘last_poll’):
api_arduino_commands.last_poll = 0
if now - api_arduino_commands.last_poll > 10:
servo_commands[“servo1”].clear()
servo_commands[“servo2”].clear()
logger.info(“🧹 Queue servos nettoyée (reboot ESP32 détecté)”)
api_arduino_commands.last_poll = now
cmd1 = servo_commands[“servo1”].pop(0) if servo_commands[“servo1”] else “none”
cmd2 = servo_commands[“servo2”].pop(0) if servo_commands[“servo2”] else “none”
return jsonify({“servo1”: cmd1, “servo2”: cmd2})

@app.route(”/api/arduino/servo”, methods=[“POST”])
def api_arduino_servo():
global servo_commands
username = session.get(‘username’)
if not is_admin(username):
return jsonify({“success”: False, “message”: “Admin requis”}), 403
data = request.get_json(silent=True) or {}
servo = data.get(“servo”)
action = data.get(“action”)
if servo not in [“servo1”, “servo2”] or action not in [“open”, “close”]:
return jsonify({“success”: False, “message”: “Paramètres invalides”}), 400
servo_commands[servo] = action
return jsonify({“success”: True, “servo”: servo, “action”: action})

@app.route(”/api/arduino/goal”, methods=[“POST”])
def api_arduino_goal():
global current_game
data = request.get_json(silent=True) or {}
ARDUINO_SECRET = os.environ.get(“ARDUINO_SECRET”, “babyfoot-arduino-secret-2024”)
if data.get(“secret”) != ARDUINO_SECRET:
return jsonify({“success”: False, “message”: “Secret invalide”}), 403
import time
now = time.time()
client_ip = request.remote_addr
if client_ip in arduino_last_goal_time and now - arduino_last_goal_time[client_ip] < 1:
return jsonify({“success”: False, “message”: “Trop rapide”}), 429
arduino_last_goal_time[client_ip] = now
if not current_game.get(“active”):
return jsonify({“success”: False, “message”: “Aucune partie en cours”, “game_active”: False}), 200
team = data.get(“team”)
if team not in [“team1”, “team2”]:
return jsonify({“success”: False, “message”: “Équipe invalide”}), 400
current_game[f”{team}_score”] += 1
if current_game[f”{team}_score”] == 9:
servo_adverse = ‘servo1’ if team == ‘team2’ else ‘servo2’
servo_commands[servo_adverse].append(‘close’)
socketio.emit(f”{servo_adverse}_lock”, {}, namespace=”/”)
if current_game[f”{team}_score”] >= 10:
current_game[“winner”] = team
current_game[“active”] = False
servo_commands[“servo1”].append(“close”)
servo_commands[“servo2”].append(“close”)
try: save_game_results(current_game)
except Exception as e: logger.error(f”Erreur sauvegarde: {e}”)
socketio.emit(“game_ended”, current_game, namespace=”/”)
import threading
def ask_rematch():
import time; time.sleep(2)
socketio.emit(“rematch_prompt”, {}, namespace=”/”)
threading.Thread(target=ask_rematch, daemon=True).start()
return jsonify({“success”: True, “game_ended”: True, “winner”: team})
else:
socketio.emit(“score_updated”, current_game, namespace=”/”)
return jsonify({
“success”: True, “game_ended”: False,
“scores”: {“team1”: current_game[“team1_score”], “team2”: current_game[“team2_score”]}
})
@socketio.on(‘connect’)
def handle_connect():
username = session.get(‘username’, ‘Anonymous’)
logger.info(f”WS connecté: {username} ({request.sid})”)
if current_game.get(‘active’):
join_room(‘game’)
emit(‘game_recovery’, current_game)

@socketio.on(‘disconnect’)
def handle_disconnect():
logger.info(f”WS déconnecté: {request.sid}”)

@socketio.on(‘create_lobby’)
def handle_create_lobby(data):
global active_lobby
username = session.get(‘username’)
if not is_admin(username) and not has_active_reservation(username):
emit(‘error’, {‘message’: ‘Seuls admins/réservateurs peuvent créer un lobby’}); return
if active_lobby.get(‘active’):
socketio.emit(‘lobby_cancelled’, {}, namespace=’/’)
invited_users = data.get(‘invited’, [])
active_lobby = {
“host”: username, “invited”: invited_users,
“accepted”: [username], “declined”: [],
“team1”: [username], “team2”: [], “active”: True
}
socketio.emit(‘lobby_created’, {‘host’: username, ‘invited’: invited_users}, namespace=’/’)
for user in invited_users:
socketio.emit(‘lobby_invitation’, {‘from’: username, ‘to’: user}, namespace=’/’)

@socketio.on(‘invite_to_lobby’)
def handle_invite_to_lobby(data):
global active_lobby
username = session.get(‘username’)
invited_user = data.get(‘user’)
if username != active_lobby[‘host’] and not is_admin(username):
emit(‘error’, {‘message’: “Seul l’hôte ou un admin peut inviter”}); return
if len(active_lobby[‘accepted’]) + len(active_lobby[‘invited’]) >= 4:
emit(‘error’, {‘message’: ‘Lobby complet’}); return
if invited_user in active_lobby[‘invited’] or invited_user in active_lobby[‘accepted’]: return
if is_guest_player(invited_user):
active_lobby[‘accepted’].append(invited_user)
t1, t2 = len(active_lobby[‘team1’]), len(active_lobby[‘team2’])
if t1 < 2 and t1 <= t2: active_lobby[‘team1’].append(invited_user)
elif t2 < 2: active_lobby[‘team2’].append(invited_user)
else: active_lobby[‘invited’].append(invited_user)
socketio.emit(‘lobby_update’, active_lobby, namespace=’/’)
else:
active_lobby[‘invited’].append(invited_user)
socketio.emit(‘lobby_invitation’, {‘from’: active_lobby[‘host’], ‘to’: invited_user}, namespace=’/’)
socketio.emit(‘lobby_update’, active_lobby, namespace=’/’)

@socketio.on(‘accept_lobby’)
def handle_accept_lobby():
global active_lobby
username = session.get(‘username’)
if username in active_lobby[‘team1’] or username in active_lobby[‘team2’]: return
if username not in active_lobby[‘invited’]: return
active_lobby[‘invited’].remove(username)
if username not in active_lobby[‘accepted’]: active_lobby[‘accepted’].append(username)
t1, t2 = len(active_lobby[‘team1’]), len(active_lobby[‘team2’])
if t1 < 2 and t1 <= t2: active_lobby[‘team1’].append(username)
elif t2 < 2: active_lobby[‘team2’].append(username)
else:
emit(‘error’, {‘message’: ‘Équipes complètes’})
active_lobby[‘accepted’].remove(username)
active_lobby[‘invited’].append(username)
return
socketio.emit(‘lobby_update’, active_lobby, namespace=’/’)

@socketio.on(‘decline_lobby’)
def handle_decline_lobby():
global active_lobby
username = session.get(‘username’)
if username not in active_lobby[‘invited’]: return
active_lobby[‘invited’].remove(username)
if username not in active_lobby[‘declined’]: active_lobby[‘declined’].append(username)
socketio.emit(‘lobby_update’, active_lobby, namespace=’/’)

@socketio.on(‘request_team_swap’)
def handle_request_team_swap(data):
from_user = session.get(‘username’)
to_user = data.get(‘with’)
request_id = f”{from_user}_{to_user}”
team_swap_requests[request_id] = {‘from’: from_user, ‘to’: to_user}
socketio.emit(‘team_swap_request’, {‘from’: from_user, ‘to’: to_user, ‘request_id’: request_id}, namespace=’/’)

@socketio.on(‘accept_team_swap’)
def handle_accept_team_swap(data):
global active_lobby
request_id = data.get(‘request_id’)
if request_id not in team_swap_requests: return
swap = team_swap_requests.pop(request_id)
fu, tu = swap[‘from’], swap[‘to’]
if fu in active_lobby[‘team1’] and tu in active_lobby[‘team2’]:
active_lobby[‘team1’].remove(fu); active_lobby[‘team2’].remove(tu)
active_lobby[‘team1’].append(tu); active_lobby[‘team2’].append(fu)
elif fu in active_lobby[‘team2’] and tu in active_lobby[‘team1’]:
active_lobby[‘team2’].remove(fu); active_lobby[‘team1’].remove(tu)
active_lobby[‘team2’].append(tu); active_lobby[‘team1’].append(fu)
socketio.emit(‘lobby_update’, active_lobby, namespace=’/’)

@socketio.on(‘decline_team_swap’)
def handle_decline_team_swap(data):
request_id = data.get(‘request_id’)
if request_id in team_swap_requests: team_swap_requests.pop(request_id)

@socketio.on(‘kick_from_lobby’)
def handle_kick_from_lobby(data):
global active_lobby
username = session.get(‘username’)
kicked_user = data.get(‘user’)
if username != active_lobby[‘host’] and not is_admin(username):
emit(‘error’, {‘message’: “Seul l’hôte ou un admin peut exclure”}); return
if kicked_user == active_lobby[‘host’]:
emit(‘error’, {‘message’: “Impossible d’exclure l’hôte”}); return
for lst in [‘invited’, ‘accepted’, ‘team1’, ‘team2’]:
if kicked_user in active_lobby[lst]: active_lobby[lst].remove(kicked_user)
socketio.emit(‘kicked_from_lobby’, {‘kicked_user’: kicked_user}, namespace=’/’)
socketio.emit(‘lobby_update’, active_lobby, namespace=’/’)

@socketio.on(‘cancel_lobby’)
def handle_cancel_lobby():
global active_lobby
username = session.get(‘username’)
if username != active_lobby[‘host’] and not is_admin(username):
emit(‘error’, {‘message’: “Seul l’hôte ou un admin peut annuler”}); return
active_lobby = {
“host”: None, “invited”: [], “accepted”: [],
“declined”: [], “team1”: [], “team2”: [], “active”: False
}
socketio.emit(‘lobby_cancelled’, {}, namespace=’/’)

@socketio.on(‘start_game_from_lobby’)
def handle_start_game_from_lobby():
global current_game, active_lobby, rematch_votes, servo_commands
username = session.get(‘username’)
if username != active_lobby[‘host’] and not is_admin(username):
emit(‘error’, {‘message’: “Seul l’hôte ou un admin peut lancer”}); return
if len(active_lobby[‘accepted’]) < 2:
emit(‘error’, {‘message’: ‘Au moins 2 joueurs requis’}); return
current_game = {
“team1_score”: 0, “team2_score”: 0,
“team1_players”: active_lobby[‘team1’],
“team2_players”: active_lobby[‘team2’],
“active”: True, “started_by”: username,
“reserved_by”: username if has_active_reservation(username) else None,
“started_at”: datetime.now().isoformat()
}
active_lobby = {
“host”: None, “invited”: [], “accepted”: [],
“declined”: [], “team1”: [], “team2”: [], “active”: False
}
rematch_votes = {“team1”: [], “team2”: []}
servo_commands[“servo1”].append(“open”)
servo_commands[“servo2”].append(“open”)
socketio.emit(‘game_started’, current_game, namespace=’/’)
socketio.emit(‘servo1_unlock’, {}, namespace=’/’)
socketio.emit(‘servo2_unlock’, {}, namespace=’/’)

@socketio.on(‘start_game’)
def handle_start_game(data):
global current_game, rematch_votes, servo_commands
try:
username = session.get(‘username’, ‘’)
if not is_admin(username) and not has_active_reservation(username):
emit(‘error’, {‘message’: ‘Réservation active ou admin requis’}); return
team1 = [p for p in data.get(‘team1’, []) if p and p.strip()]
team2 = [p for p in data.get(‘team2’, []) if p and p.strip()]
if not team1 or not team2:
emit(‘error’, {‘message’: ‘Chaque équipe doit avoir au moins un joueur’}); return
if current_game.get(‘active’):
emit(‘error’, {‘message’: ‘Une partie est déjà en cours’}); return
current_game = {
“team1_score”: 0, “team2_score”: 0,
“team1_players”: team1, “team2_players”: team2,
“active”: True, “started_by”: username,
“reserved_by”: username if has_active_reservation(username) else None,
“started_at”: datetime.now().isoformat()
}
rematch_votes = {“team1”: [], “team2”: []}
servo_commands[“servo1”].append(“open”)
servo_commands[“servo2”].append(“open”)
socketio.emit(‘game_started’, current_game, namespace=’/’)
socketio.emit(‘servo1_unlock’, {}, namespace=’/’)
socketio.emit(‘servo2_unlock’, {}, namespace=’/’)
except Exception as e:
logger.error(f”Erreur start_game: {e}”)
emit(‘error’, {‘message’: str(e)})

@socketio.on(‘unlock_servo1’)
def handle_unlock_servo1():
global servo_commands
username = session.get(‘username’)
if not is_admin(username):
emit(‘error’, {‘message’: ‘Admin requis’}); return
servo_commands[“servo1”].clear()
servo_commands[“servo1”].append(“open”)
socketio.emit(‘servo1_unlock’, {}, namespace=’/’)
import threading
def relock():
import time; time.sleep(3.0)
servo_commands[“servo1”].clear()
servo_commands[“servo1”].append(“close”)
socketio.emit(‘servo1_lock’, {}, namespace=’/’)
threading.Thread(target=relock, daemon=True).start()

@socketio.on(‘unlock_servo2’)
def handle_unlock_servo2():
global servo_commands
username = session.get(‘username’)
if not is_admin(username):
emit(‘error’, {‘message’: ‘Admin requis’}); return
servo_commands[“servo2”].clear()
servo_commands[“servo2”].append(“open”)
socketio.emit(‘servo2_unlock’, {}, namespace=’/’)
import threading
def relock():
import time; time.sleep(3.0)
servo_commands[“servo2”].clear()
servo_commands[“servo2”].append(“close”)
socketio.emit(‘servo2_lock’, {}, namespace=’/’)
threading.Thread(target=relock, daemon=True).start()

@socketio.on(‘stop_game’)
def handle_stop_game():
global current_game, rematch_votes, servo_commands
username = session.get(‘username’)
if not is_admin(username):
emit(‘error’, {‘message’: ‘Admin requis’}); return
current_game = {
“team1_score”: 0, “team2_score”: 0,
“team1_players”: [], “team2_players”: [],
“active”: False, “started_by”: None, “reserved_by”: None
}
rematch_votes = {“team1”: [], “team2”: []}
servo_commands[“servo1”].append(“close”)
servo_commands[“servo2”].append(“close”)
socketio.emit(‘game_stopped’, {}, namespace=’/’)
socketio.emit(‘servo1_lock’, {}, namespace=’/’)
socketio.emit(‘servo2_lock’, {}, namespace=’/’)

@socketio.on(‘update_score’)
def handle_score(data):
global current_game
try:
if not current_game.get(‘active’):
emit(‘error’, {‘message’: ‘Aucune partie en cours’}); return
team = data.get(‘team’)
if team not in [‘team1’, ‘team2’]:
emit(‘error’, {‘message’: ‘Équipe invalide’}); return
current_game[f”{team}_score”] += 1
if current_game[f”{team}_score”] >= 10:
current_game[‘winner’] = team
current_game[‘active’] = False
try: save_game_results(current_game)
except Exception as e: logger.error(f”Save error: {e}”)
socketio.emit(‘game_ended’, current_game, namespace=’/’)
import threading
def ask_rematch():
import time; time.sleep(2)
socketio.emit(‘rematch_prompt’, {}, namespace=’/’)
threading.Thread(target=ask_rematch, daemon=True).start()
else:
socketio.emit(‘score_updated’, current_game, namespace=’/’)
except Exception as e:
logger.error(f”Erreur update_score: {e}”)
emit(‘error’, {‘message’: str(e)})

@socketio.on(‘vote_rematch’)
def handle_vote_rematch(data):
global rematch_votes, current_game, servo_commands
username = session.get(‘username’)
if data.get(‘vote’) == ‘no’:
socketio.emit(‘rematch_cancelled’, {}, namespace=’/’)
rematch_votes = {“team1”: [], “team2”: []}
return
team = None
if username in current_game.get(‘team1_players’, []): team = ‘team1’
elif username in current_game.get(‘team2_players’, []): team = ‘team2’
if not team: emit(‘error’, {‘message’: ‘Pas dans cette partie’}); return
if username not in rematch_votes[team]: rematch_votes[team].append(username)
if len(rematch_votes[‘team1’]) == len(current_game[‘team1_players’]) and   
len(rematch_votes[‘team2’]) == len(current_game[‘team2_players’]):
current_game = {
“team1_score”: 0, “team2_score”: 0,
“team1_players”: current_game[‘team1_players’],
“team2_players”: current_game[‘team2_players’],
“active”: True, “started_by”: current_game.get(‘started_by’),
“reserved_by”: current_game.get(‘reserved_by’),
“started_at”: datetime.now().isoformat()
}
rematch_votes = {“team1”: [], “team2”: []}
servo_commands[“servo1”].append(“open”)
servo_commands[“servo2”].append(“open”)
socketio.emit(‘game_started’, current_game, namespace=’/’)
socketio.emit(‘servo1_unlock’, {}, namespace=’/’)
socketio.emit(‘servo2_unlock’, {}, namespace=’/’)

@socketio.on(‘reset_game’)
def handle_reset():
global current_game, rematch_votes, servo_commands
username = session.get(‘username’)
if not is_admin(username):
emit(‘error’, {‘message’: ‘Admin requis’}); return
current_game = {
“team1_score”: 0, “team2_score”: 0,
“team1_players”: [], “team2_players”: [], “active”: False
}
rematch_votes = {“team1”: [], “team2”: []}
servo_commands[“servo1”].append(“close”)
servo_commands[“servo2”].append(“close”)
socketio.emit(‘game_reset’, current_game, namespace=’/’)

@socketio.on(‘arduino_goal’)
def handle_arduino_goal(data):
global current_game, servo_commands
ARDUINO_SECRET = os.environ.get(‘ARDUINO_SECRET’, ‘babyfoot-arduino-secret-2024’)
if data.get(‘secret’) != ARDUINO_SECRET:
emit(‘error’, {‘message’: ‘Secret invalide’}); return
if not hasattr(handle_arduino_goal, ‘last_goal_time’):
handle_arduino_goal.last_goal_time = {}
import time
now = time.time()
if request.sid in handle_arduino_goal.last_goal_time and   
now - handle_arduino_goal.last_goal_time[request.sid] < 2: return
handle_arduino_goal.last_goal_time[request.sid] = now
if not current_game.get(‘active’): return
team = data.get(‘team’)
if team not in [‘team1’, ‘team2’]: return
current_game[f”{team}_score”] += 1
if current_game[f”{team}_score”] == 9:
servo_adverse = ‘servo1’ if team == ‘team2’ else ‘servo2’
servo_commands[servo_adverse].append(‘close’)
socketio.emit(f’{servo_adverse}_lock’, {}, namespace=’/’)
if current_game[f”{team}_score”] >= 10:
current_game[‘winner’] = team
current_game[‘active’] = False
servo_commands[“servo1”].append(“close”)
servo_commands[“servo2”].append(“close”)
try: save_game_results(current_game)
except Exception as e: logger.error(f”Erreur sauvegarde: {e}”)
socketio.emit(‘game_ended’, current_game, namespace=’/’)
import threading
def ask_rematch_delayed():
import time; time.sleep(2)
socketio.emit(‘rematch_prompt’, {}, namespace=’/’)
threading.Thread(target=ask_rematch_delayed, daemon=True).start()
else:
socketio.emit(‘score_updated’, current_game, namespace=’/’)

@socketio.on(‘arduino_ping’)
def handle_arduino_ping(data):
emit(‘arduino_pong’, {‘status’: ‘ok’})

@socketio.on(‘get_game_state’)
def handle_get_game_state(data):
emit(‘game_state’, {
‘active’: current_game.get(‘active’, False),
‘team1_score’: current_game.get(‘team1_score’, 0),
‘team2_score’: current_game.get(‘team2_score’, 0),
‘team1_players’: current_game.get(‘team1_players’, []),
‘team2_players’: current_game.get(‘team2_players’, []),
})

def save_game_results(game):
“”“Sauvegarde les résultats dans users, scores ET games”””
try:
conn = get_db_connection()
cur = conn.cursor()
winner_team = game.get(‘winner’, ‘team1’)
losers_team = ‘team2’ if winner_team == ‘team1’ else ‘team1’
t1_players = game.get(‘team1_players’, [])
t2_players = game.get(‘team2_players’, [])

```
    # Parser si string JSON
    if isinstance(t1_players, str):
        try: t1_players = json.loads(t1_players)
        except: t1_players = []
    if isinstance(t2_players, str):
        try: t2_players = json.loads(t2_players)
        except: t2_players = []

    all_players = t1_players + t2_players
    real_players = [p for p in all_players if not is_guest_player(p)]
    t1_score = game.get("team1_score", 0)
    t2_score = game.get("team2_score", 0)

    # Détecter le mode
    total_players = len(t1_players) + len(t2_players)
    mode = '2v2' if total_players >= 4 else '1v1'

    # Mettre à jour les stats des joueurs réels
    for player in real_players:
        player_score = t1_score if player in t1_players else t2_score
        if USE_POSTGRES:
            cur.execute("UPDATE users SET total_games = total_games + 1 WHERE username = %s", (player,))
            cur.execute("INSERT INTO scores (username, score) VALUES (%s, %s)", (player, player_score))
            cur.execute("UPDATE users SET total_goals = total_goals + %s WHERE username = %s", (player_score, player))
        else:
            cur.execute("UPDATE users SET total_games = total_games + 1 WHERE username = ?", (player,))
            cur.execute("INSERT INTO scores (username, score) VALUES (?, ?)", (player, player_score))
            cur.execute("UPDATE users SET total_goals = total_goals + ? WHERE username = ?", (player_score, player))

    # Enregistrer la partie complète dans games
    t1_json = json.dumps(t1_players)
    t2_json = json.dumps(t2_players)
    if USE_POSTGRES:
        cur.execute(
            "INSERT INTO games (team1_players, team2_players, team1_score, team2_score, winner, mode, started_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (t1_json, t2_json, t1_score, t2_score, winner_team, mode, game.get('started_by'))
        )
    else:
        cur.execute(
            "INSERT INTO games (team1_players, team2_players, team1_score, team2_score, winner, mode, started_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (t1_json, t2_json, t1_score, t2_score, winner_team, mode, game.get('started_by'))
        )

    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ Résultats sauvegardés (users + scores + games)")
except Exception as e:
    logger.error(f"Erreur save_game_results: {e}")
```

if **name** == “**main**”:
socketio.run(app, host=“0.0.0.0”, port=int(os.environ.get(“PORT”, 5000)), debug=False)