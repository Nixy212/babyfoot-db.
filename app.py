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
import collections
import time as _time

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')

# ── Secrets ──────────────────────────────────────────────────

_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    import secrets as _secrets
    _secret_key = _secrets.token_hex(32)
    logger.warning("SECRET_KEY non definie — cle aleatoire generee (sessions invalidees au redemarrage). Definissez SECRET_KEY dans Railway !")

app.secret_key = _secret_key
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = (
    os.environ.get('RAILWAY_ENVIRONMENT') is not None
    or os.environ.get('SESSION_COOKIE_SECURE', '').lower() == 'true'
)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_PATH'] = '/'
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 Mo max request body

# ── SocketIO ──────────────────────────────────────────────────

_ALLOWED_ORIGINS = os.environ.get('CORS_ORIGINS', '*')
socketio = SocketIO(
    app,
    cors_allowed_origins=_ALLOWED_ORIGINS,
    logger=False,
    engineio_logger=False,
    ping_timeout=45,
    ping_interval=20,
    async_mode="eventlet",
    manage_session=True,
    allow_upgrades=True,
    max_http_buffer_size=1_000_000,
)

# ── Service Worker ────────────────────────────────────────────

@app.route('/sw.js')
def service_worker():
    response = app.send_static_file('sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

# ── Headers HTTP globaux ──────────────────────────────────────

@app.after_request
def set_headers(response):
    if response.content_type and any(ct in response.content_type for ct in ['javascript', 'css', 'image', 'font']):
        response.headers['Cache-Control'] = 'public, max-age=604800, stale-while-revalidate=86400'
    elif response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response

@app.before_request
def handle_http_for_arduino():
    # Les endpoints Arduino sont accessibles en HTTP (pas de session Flask)
    if request.path.startswith('/api/arduino/'):
        return None
    # Redirection HTTP → HTTPS sauf en local
    if not request.is_secure:
        forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
        if forwarded_proto and forwarded_proto != 'https':
            if 'localhost' not in request.host and '127.0.0.1' not in request.host:
                secure_url = request.url.replace('http://', 'https://', 1)
                return redirect(secure_url, code=301)
    return None

# ── Base de donnees ───────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH', 'babyfoot.db')

# ── Etat global ───────────────────────────────────────────────

current_game = {
    "team1_score": 0, "team2_score": 0,
    "team1_players": [], "team2_players": [],
    "active": False, "started_by": None,
    "reserved_by": None, "started_at": None
}

active_lobby = {
    "host": None, "invited": [], "accepted": [],
    "declined": [], "team1": [], "team2": [], "active": False
}

team_swap_requests = {}
rematch_votes = {"team1": [], "team2": []}
rematch_no_votes = []         # Joueurs qui ont voté NON
servo_commands = {"servo1": [], "servo2": []}
rematch_pending = False       # True entre game_ended et le lancement du rematch
pending_invitations = {}      # username -> {from, timestamp}

# ── connected_users : sid -> username pour les handlers SocketIO ──
connected_users = {}

def get_socket_user():
    return connected_users.get(request.sid)

# ── Rate limiting login (anti brute-force) ────────────────────

_login_attempts = collections.defaultdict(list)  # ip -> [timestamps]
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 60

def check_rate_limit(ip):
    """Retourne True si l'IP est bloquee (trop de tentatives)."""
    now = _time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_WINDOW_SECONDS]
    if len(_login_attempts[ip]) >= LOGIN_MAX_ATTEMPTS:
        return True
    _login_attempts[ip].append(now)
    return False

# ── Verrou anti double-but (ESP32 HTTP + Socket simultanes) ──

_goal_processing = False

# ── Connexion DB ──────────────────────────────────────────────

def get_db_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # IMPORTANT : activer les foreign keys SQLite a chaque connexion
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

def row_to_dict(row):
    if row is None:
        return None
    return dict(row)

# ── Initialisation DB ─────────────────────────────────────────

def init_database():
    conn = get_db_connection()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(50) PRIMARY KEY,
                password VARCHAR(200) NOT NULL,
                total_goals INTEGER DEFAULT 0,
                total_games INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                nickname VARCHAR(50) DEFAULT NULL,
                bio VARCHAR(200) DEFAULT NULL,
                avatar_preset VARCHAR(10) DEFAULT NULL,
                avatar_url TEXT DEFAULT NULL,
                elo INTEGER DEFAULT 1000,
                role INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id SERIAL PRIMARY KEY,
                day VARCHAR(20) NOT NULL,
                time VARCHAR(30) NOT NULL,
                team1 TEXT NOT NULL DEFAULT '[]',
                team2 TEXT NOT NULL DEFAULT '[]',
                mode VARCHAR(10) DEFAULT '1v1',
                reserved_by VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                duration_minutes INTEGER DEFAULT 15,
                UNIQUE (start_time, reserved_by)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id SERIAL PRIMARY KEY,
                team1_players TEXT NOT NULL,
                team2_players TEXT NOT NULL,
                team1_score INTEGER NOT NULL,
                team2_score INTEGER NOT NULL,
                winner VARCHAR(10) NOT NULL,
                mode VARCHAR(10) DEFAULT '1v1',
                started_by VARCHAR(50),
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                total_goals INTEGER DEFAULT 0,
                total_games INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                nickname TEXT DEFAULT NULL,
                bio TEXT DEFAULT NULL,
                avatar_preset TEXT DEFAULT NULL,
                avatar_url TEXT DEFAULT NULL,
                elo INTEGER DEFAULT 1000,
                role INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                time TEXT NOT NULL,
                team1 TEXT NOT NULL DEFAULT '[]',
                team2 TEXT NOT NULL DEFAULT '[]',
                mode TEXT DEFAULT '1v1',
                reserved_by TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                start_time TEXT,
                end_time TEXT,
                duration_minutes INTEGER DEFAULT 15
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                score INTEGER NOT NULL,
                date TEXT DEFAULT (datetime('now'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team1_players TEXT NOT NULL,
                team2_players TEXT NOT NULL,
                team1_score INTEGER NOT NULL,
                team2_score INTEGER NOT NULL,
                winner TEXT NOT NULL,
                mode TEXT DEFAULT '1v1',
                started_by TEXT,
                date TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"DB initialisee ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")

def migrate_reservations_v2():
    """Ajoute les colonnes start_time, end_time, duration_minutes et la contrainte UNIQUE si elles n'existent pas."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("""
                ALTER TABLE reservations
                ADD COLUMN IF NOT EXISTS start_time TIMESTAMP,
                ADD COLUMN IF NOT EXISTS end_time TIMESTAMP,
                ADD COLUMN IF NOT EXISTS duration_minutes INTEGER DEFAULT 15
            """)
            # Ajouter la contrainte UNIQUE si elle n'existe pas encore
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'reservations_start_time_reserved_by_key'
                    ) THEN
                        ALTER TABLE reservations
                        ADD CONSTRAINT reservations_start_time_reserved_by_key
                        UNIQUE (start_time, reserved_by);
                    END IF;
                END $$;
            """)
        else:
            cur.execute("PRAGMA table_info(reservations)")
            cols = [row[1] for row in cur.fetchall()]
            if 'start_time' not in cols:
                cur.execute("ALTER TABLE reservations ADD COLUMN start_time TEXT")
            if 'end_time' not in cols:
                cur.execute("ALTER TABLE reservations ADD COLUMN end_time TEXT")
            if 'duration_minutes' not in cols:
                cur.execute("ALTER TABLE reservations ADD COLUMN duration_minutes INTEGER DEFAULT 15")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Migration v2: {e}")

def migrate_teams_to_text():
    """Corrige les colonnes mal typees dans reservations (PostgreSQL)."""
    if not USE_POSTGRES:
        return
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # --- Fix 1 : team1/team2 TEXT[] -> TEXT ---
        cur.execute("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_name = 'reservations' AND column_name IN ('team1', 'team2')
        """)
        cols = {row['column_name']: row['udt_name'] for row in cur.fetchall()}
        for col in ['team1', 'team2']:
            if col in cols and cols[col] != 'text':
                logger.info(f"Migration: {col} ({cols[col]}) -> TEXT")
                cur.execute(f"ALTER TABLE reservations ALTER COLUMN {col} TYPE TEXT USING {col}::text")
                cur.execute(f"ALTER TABLE reservations ALTER COLUMN {col} SET DEFAULT '[]'")
                logger.info(f"Migration {col} TEXT[] -> TEXT OK")

        # --- Fix 2 : colonne 'time' trop courte (VARCHAR(10) -> VARCHAR(30)) ---
        cur.execute("""
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'reservations' AND column_name = 'time'
        """)
        row = cur.fetchone()
        if row and row['character_maximum_length'] and row['character_maximum_length'] < 30:
            logger.info(f"Migration: colonne time VARCHAR({row['character_maximum_length']}) -> VARCHAR(30)")
            cur.execute("ALTER TABLE reservations ALTER COLUMN time TYPE VARCHAR(30)")
            logger.info("Migration time VARCHAR -> VARCHAR(30) OK")

        # --- Fix 3 : colonne 'mode' trop courte si besoin ---
        cur.execute("""
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'reservations' AND column_name = 'mode'
        """)
        row = cur.fetchone()
        if row and row['character_maximum_length'] and row['character_maximum_length'] < 20:
            logger.info(f"Migration: colonne mode VARCHAR({row['character_maximum_length']}) -> VARCHAR(20)")
            cur.execute("ALTER TABLE reservations ALTER COLUMN mode TYPE VARCHAR(20)")
            logger.info("Migration mode VARCHAR -> VARCHAR(20) OK")

        # --- Fix 4 : nouvelles colonnes profil utilisateur ---
        for col, definition in [
            ('nickname', 'VARCHAR(50)'),
            ('bio', 'VARCHAR(200)'),
            ('avatar_preset', 'VARCHAR(10)'),
            ('avatar_url', 'TEXT'),
            ('elo', 'INTEGER DEFAULT 1000'),
            ('role', 'INTEGER DEFAULT 0'),
        ]:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                conn.commit()
                logger.info(f"Migration: colonne users.{col} ajoutée")
            except Exception:
                conn.rollback()  # colonne existe déjà

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Migration schema: {e}")

def seed_accounts():
    # (username, password, role) — role: 0=user, 1=super_admin, 2=admin
    # Seuls les vrais comptes du club + guests physiques (pas de comptes test)
    accounts = [
        ("Imran", "imran2024", 1), ("Apoutou", "admin123", 2), ("Hamara", "admin123", 2), ("MDA", "admin123", 2),
        ("Joueur1", "guest", 0), ("Joueur2", "guest", 0), ("Joueur3", "guest", 0),
    ]
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for username, password, role in accounts:
            q = "SELECT username, role FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username, role FROM users WHERE username = ?"
            cur.execute(q, (username,))
            existing = row_to_dict(cur.fetchone())
            if not existing:
                hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                q2 = (
                    "INSERT INTO users (username, password, total_goals, total_games, role) VALUES (%s, %s, 0, 0, %s)"
                    if USE_POSTGRES else
                    "INSERT INTO users (username, password, total_goals, total_games, role) VALUES (?, ?, 0, 0, ?)"
                )
                cur.execute(q2, (username, hashed, role))
            elif existing.get('role') is None or existing.get('role') != role:
                # Mettre à jour le rôle si changé
                q3 = "UPDATE users SET role = %s WHERE username = %s" if USE_POSTGRES else "UPDATE users SET role = ? WHERE username = ?"
                cur.execute(q3, (role, username))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Seed accounts: {e}")

def cleanup_old_data():
    """Nettoie les donnees anciennes et les reservations expirees."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if USE_POSTGRES:
            cur.execute("DELETE FROM scores WHERE date < NOW() - INTERVAL '6 months'")
            cur.execute("DELETE FROM games WHERE date < NOW() - INTERVAL '6 months'")
            # Supprimer les reservations dont la fin est passee depuis plus de 1 jour
            cur.execute("DELETE FROM reservations WHERE end_time < NOW() - INTERVAL '1 day'")
        else:
            cur.execute("DELETE FROM scores WHERE date < datetime('now', '-6 months')")
            cur.execute("DELETE FROM games WHERE date < datetime('now', '-6 months')")
            # Supprimer les reservations dont la fin est passee depuis plus de 1 jour
            cur.execute("DELETE FROM reservations WHERE end_time < datetime('now', '-1 day')")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Erreur cleanup: {e}")

    # Nettoyer les invitations expirees (> 5 minutes)
    now = _time.time()
    expired = [u for u, inv in list(pending_invitations.items()) if now - inv.get('timestamp', 0) > 300]
    for u in expired:
        pending_invitations.pop(u, None)
    if expired:
        logger.info(f"Invitations expirees nettoyees: {expired}")

def schedule_cleanup():
    cleanup_old_data()
    def _loop():
        while True:
            eventlet.sleep(86400)
            cleanup_old_data()
    eventlet.spawn(_loop)

def schedule_zombie_game_cleanup():
    """Nettoie automatiquement les parties actives depuis plus de 2h (parties zombies)."""
    def _loop():
        while True:
            eventlet.sleep(300)
            try:
                if current_game.get('active') and current_game.get('started_at'):
                    started = datetime.fromisoformat(current_game['started_at'])
                    if datetime.now() - started > timedelta(hours=2):
                        logger.warning("Partie zombie detectee (>2h) — nettoyage automatique")
                        _reset_game_state()
                        socketio.emit('game_stopped', {'reason': 'timeout'}, namespace='/')
            except Exception as e:
                logger.error(f"Erreur zombie cleanup: {e}")
    eventlet.spawn(_loop)

# ── Helpers etat de jeu ───────────────────────────────────────

def _reset_game_state():
    """Reinitialise l'etat global d'une partie."""
    global current_game, rematch_votes, servo_commands, rematch_pending
    current_game = {
        "team1_score": 0, "team2_score": 0,
        "team1_players": [], "team2_players": [],
        "active": False, "started_by": None, "reserved_by": None,
        "started_at": None
    }
    rematch_votes = {"team1": [], "team2": []}
    rematch_pending = False
    servo_commands["servo1"].append("close")
    servo_commands["servo2"].append("close")

def _launch_rematch(game):
    """Lance un rematch. Centralise le code de relance."""
    global current_game, rematch_votes, rematch_no_votes, servo_commands, rematch_pending
    rematch_no_votes.clear()
    current_game = {
        "team1_score": 0, "team2_score": 0,
        "team1_players": game['team1_players'],
        "team2_players": game['team2_players'],
        "active": True,
        "started_by": game.get('started_by'),
        "reserved_by": game.get('reserved_by'),
        "started_at": datetime.now().isoformat()
    }
    rematch_votes = {"team1": [], "team2": []}
    rematch_pending = False
    servo_commands["servo1"].append("open")
    servo_commands["servo2"].append("open")
    socketio.emit('game_started', current_game, namespace='/')
    socketio.emit('servo1_unlock', {}, namespace='/')
    socketio.emit('servo2_unlock', {}, namespace='/')

# ── Roles ─────────────────────────────────────────────────────

# Cache roles pour eviter des requetes DB a chaque appel socket
_role_cache = {}

def _get_user_role(username):
    """Retourne le role depuis la DB avec cache memoire (0=user, 1=super_admin, 2=admin)."""
    if not username:
        return 0
    if username in _role_cache:
        return _role_cache[username]
    # Fallback hardcodé pour compatibilité (écrasé si en DB)
    hardcoded = {"Imran": 1, "Apoutou": 2, "Hamara": 2, "MDA": 2}
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        q = "SELECT role FROM users WHERE username = %s" if USE_POSTGRES else "SELECT role FROM users WHERE username = ?"
        cur.execute(q, (username,))
        row = row_to_dict(cur.fetchone())
        cur.close(); conn.close()
        if row is not None and row.get('role') is not None:
            role = int(row['role'])
        else:
            role = hardcoded.get(username, 0)
    except Exception:
        role = hardcoded.get(username, 0)
    _role_cache[username] = role
    return role

def invalidate_role_cache(username=None):
    if username:
        _role_cache.pop(username, None)
    else:
        _role_cache.clear()

def is_super_admin(username):
    """Classe 1 — role=1. Acces illimite."""
    return _get_user_role(username) == 1

def is_admin(username):
    """Classe 2 — role >= 1 (inclut super admin)."""
    return _get_user_role(username) >= 1

def is_guest_player(username):
    return username in ["Joueur1", "Joueur2", "Joueur3"]

# ── Decorateurs utilitaires ───────────────────────────────────

def handle_errors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        except Exception as e:
            logger.error(f"Erreur {f.__name__}: {e}\n{traceback.format_exc()}")
            return jsonify({"success": False, "message": "Erreur serveur"}), 500
    return decorated

def validate_username(u):
    if not u or not isinstance(u, str):
        raise ValueError("Nom d'utilisateur requis")
    u = u.strip()
    if len(u) < 3:
        raise ValueError("Minimum 3 caracteres")
    if len(u) > 20:
        raise ValueError("Maximum 20 caracteres")
    if not u.replace('_', '').replace('-', '').isalnum():
        raise ValueError("Lettres, chiffres, - et _ uniquement")
    return u

def validate_password(p):
    if not p or not isinstance(p, str):
        raise ValueError("Mot de passe requis")
    if len(p) < 6:
        raise ValueError("Minimum 6 caracteres")
    return p

# ── Reservation active ────────────────────────────────────────

def has_active_reservation(username):
    """Verifie si l'utilisateur a une reservation active en ce moment via start_time/end_time."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        now_iso = datetime.now().isoformat()
        if USE_POSTGRES:
            cur.execute(
                "SELECT id FROM reservations WHERE reserved_by = %s AND start_time <= %s AND end_time >= %s LIMIT 1",
                (username, now_iso, now_iso)
            )
        else:
            cur.execute(
                "SELECT id FROM reservations WHERE reserved_by = ? AND start_time <= ? AND end_time >= ? LIMIT 1",
                (username, now_iso, now_iso)
            )
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result is not None
    except Exception as e:
        logger.error(f"Erreur has_active_reservation: {e}")
        return False

# ── Initialisation au demarrage ───────────────────────────────

try:
    init_database()
    migrate_reservations_v2()
    migrate_teams_to_text()
    seed_accounts()
    schedule_cleanup()
    schedule_zombie_game_cleanup()
    logger.info("Systeme initialise")
except Exception as e:
    logger.error(f"Erreur init DB: {e}")

# ── Pages ─────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/register")
def register_page():
    return render_template("register.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("dashboard.html")

@app.route("/reservation")
def reservation():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("reservation.html")

@app.route("/lobby")
def lobby_page():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("lobby.html")

@app.route("/admin")
def admin_page():
    if "username" not in session:
        return redirect(url_for('login_page'))
    if not is_admin(session.get('username')):
        return redirect(url_for('index'))
    return render_template("admin.html")

@app.route("/live-score")
def live_score():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("live-score.html")

@app.route("/stats")
def stats():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("stats.html")

@app.route("/top")
def top():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("top.html")

@app.route("/scores")
def scores():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("scores.html")

# ── Health ────────────────────────────────────────────────────

@app.route("/health")
def health_check():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected", "timestamp": datetime.now().isoformat()}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route("/debug/static")
def debug_static():
    username = session.get('username')
    if not is_admin(username):
        return jsonify({"error": "Admin requis"}), 403
    static_path = os.path.join(app.root_path, 'static')
    files_info = {
        "static_folder": app.static_folder,
        "static_url_path": app.static_url_path,
        "static_path_exists": os.path.exists(static_path),
        "root_path": app.root_path
    }
    if os.path.exists(static_path):
        files_info["static_files"] = os.listdir(static_path)
    return jsonify(files_info), 200

@app.route("/debug/game")
def debug_game():
    username = session.get('username')
    if not is_admin(username):
        return jsonify({"error": "Admin requis"}), 403
    return jsonify({
        "current_game": current_game,
        "active_lobby": active_lobby,
        "rematch_votes": rematch_votes,
        "servo_commands": servo_commands,
    })

# ── Auth ──────────────────────────────────────────────────────

@app.route("/api/register", methods=["POST"])
@handle_errors
def api_register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Aucune donnee"}), 400
    username = validate_username(data.get("username", ""))
    password = validate_password(data.get("password", ""))
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
    cur.execute(q, (username,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Nom d'utilisateur deja pris"}), 409
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    q2 = (
        "INSERT INTO users (username, password) VALUES (%s, %s)"
        if USE_POSTGRES else
        "INSERT INTO users (username, password) VALUES (?, ?)"
    )
    cur.execute(q2, (username, hashed))
    conn.commit()
    cur.close()
    conn.close()
    session.permanent = True
    session['username'] = username
    return jsonify({"success": True, "is_admin": is_admin(username)})

@app.route("/api/login", methods=["POST"])
@handle_errors
def api_login():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    if check_rate_limit(client_ip):
        return jsonify({"success": False, "message": "Trop de tentatives, attendez 1 minute"}), 429
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Aucune donnee"}), 400
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or len(username) > 50 or not password:
        return jsonify({"success": False, "message": "Identifiants invalides"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    user = row_to_dict(cur.fetchone())
    cur.close()
    conn.close()
    if not user:
        return jsonify({"success": False, "message": "Utilisateur inconnu"}), 401
    if not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return jsonify({"success": False, "message": "Mot de passe incorrect"}), 401
    session.permanent = True
    session['username'] = username
    return jsonify({"success": True, "is_admin": is_admin(username)})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/current_user")
def current_user():
    username = session.get('username')
    if not username:
        return jsonify(None), 401
    admin_class = 1 if is_super_admin(username) else (2 if is_admin(username) else 0)
    # Charger les infos profil pour affichage global (nav avatar, surnom)
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        q = "SELECT nickname, avatar_preset, avatar_url FROM users WHERE username = %s" if USE_POSTGRES else "SELECT nickname, avatar_preset, avatar_url FROM users WHERE username = ?"
        cur.execute(q, (username,))
        prof = row_to_dict(cur.fetchone()) or {}
        cur.close(); conn.close()
    except Exception:
        prof = {}
    return jsonify({
        "username": username,
        "nickname": prof.get("nickname") or "",
        "avatar_preset": prof.get("avatar_preset") or "",
        "avatar_url": prof.get("avatar_url") or "",
        "is_admin": is_admin(username),
        "is_super_admin": is_super_admin(username),
        "admin_class": admin_class,
        "has_reservation": has_active_reservation(username),
    })

@app.route("/api/is_admin")
def api_is_admin():
    username = session.get('username')
    if not username:
        return jsonify({"is_admin": False, "is_super_admin": False, "admin_class": 0})
    return jsonify({
        "is_admin": is_admin(username),
        "is_super_admin": is_super_admin(username),
        "admin_class": 1 if is_super_admin(username) else (2 if is_admin(username) else 0),
    })

# ── Data ──────────────────────────────────────────────────────

@app.route("/reservations_all")
@handle_errors
def reservations_all():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT day, time, mode, reserved_by FROM reservations")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for row in rows:
        r = row_to_dict(row)
        day = r['day']
        time_val = r['time']
        if day not in result:
            result[day] = {}
        result[day][time_val] = {
            'reserved_by': r['reserved_by'],
            'mode': r.get('mode', '1v1')
        }
    return jsonify(result)

@app.route("/leaderboard")
@handle_errors
def leaderboard():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Exclure les comptes guest et calculer les victoires depuis la table games
        if USE_POSTGRES:
            cur.execute("""
                SELECT u.username, u.total_goals, u.total_games, u.elo,
                       COALESCE(w.wins, 0) as wins
                FROM users u
                LEFT JOIN (
                    SELECT p.username, COUNT(*) as wins
                    FROM (
                        SELECT json_array_elements_text(team1_players::json) as username, winner
                        FROM games WHERE winner = 'team1'
                        UNION ALL
                        SELECT json_array_elements_text(team2_players::json) as username, winner
                        FROM games WHERE winner = 'team2'
                    ) p
                    GROUP BY p.username
                ) w ON u.username = w.username
                WHERE u.username NOT IN ('Joueur1', 'Joueur2', 'Joueur3')
                ORDER BY u.elo DESC
            """)
        else:
            cur.execute("""
                SELECT username, total_goals, total_games, elo, 0 as wins
                FROM users
                WHERE username NOT IN ('Joueur1', 'Joueur2', 'Joueur3')
                ORDER BY elo DESC
            """)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/user_stats/<username>")
@handle_errors
def user_stats(username):
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    user = row_to_dict(cur.fetchone())
    if not user:
        cur.close()
        conn.close()
        accept_header = request.headers.get('Accept', '')
        if accept_header.startswith('text/html') or accept_header.startswith('application/xhtml'):
            return redirect(url_for('admin_page'))
        return jsonify(None), 404
    q2 = (
        "SELECT score, date FROM scores WHERE username = %s ORDER BY date DESC LIMIT 20"
        if USE_POSTGRES else
        "SELECT score, date FROM scores WHERE username = ? ORDER BY date DESC LIMIT 20"
    )
    cur.execute(q2, (username,))
    scores_rows = [row_to_dict(r) for r in cur.fetchall()]

    # Calculer buts marqués et buts pris depuis la table games
    goals_scored = 0
    goals_conceded = 0
    if USE_POSTGRES:
        cur.execute("SELECT team1_players, team2_players, team1_score, team2_score FROM games")
    else:
        cur.execute("SELECT team1_players, team2_players, team1_score, team2_score FROM games")
    for grow in cur.fetchall():
        gr = row_to_dict(grow)
        t1p = gr.get('team1_players', '[]')
        t2p = gr.get('team2_players', '[]')
        if isinstance(t1p, str):
            try: t1p = json.loads(t1p)
            except: t1p = []
        if isinstance(t2p, str):
            try: t2p = json.loads(t2p)
            except: t2p = []
        if username in t1p:
            goals_scored += int(gr.get('team1_score') or 0)
            goals_conceded += int(gr.get('team2_score') or 0)
        elif username in t2p:
            goals_scored += int(gr.get('team2_score') or 0)
            goals_conceded += int(gr.get('team1_score') or 0)

    cur.close()
    conn.close()
    total_games = user.get('total_games', 0)
    total_goals = user.get('total_goals', 0)
    stats_data = {
        "username": user['username'],
        "total_games": total_games,
        "total_goals": total_goals,
        "goals_scored": goals_scored,
        "goals_conceded": goals_conceded,
        "ratio": round(total_goals / total_games, 2) if total_games > 0 else 0,
        "best_score": max([s['score'] for s in scores_rows], default=0),
        "average_score": round(sum([s['score'] for s in scores_rows]) / len(scores_rows), 2) if scores_rows else 0,
        "recent_scores": scores_rows,
    }
    accept_header = request.headers.get('Accept', '')
    is_browser_nav = accept_header.startswith('text/html') or accept_header.startswith('application/xhtml')
    if is_browser_nav:
        current_u = session.get('username')
        # Admin peut voir n'importe qui, un joueur peut voir ses propres stats
        if is_admin(current_u) or current_u == username:
            return render_template('stats.html', user_stats=stats_data, target_username=username)
        else:
            return redirect(url_for('dashboard'))
    return jsonify(stats_data)

@app.route("/scores_all")
@handle_errors
def scores_all():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM games")
    count_row = cur.fetchone()
    total = (count_row[0] if isinstance(count_row, tuple) else list(count_row.values())[0]) if count_row else 0
    cur.execute("SELECT * FROM games ORDER BY date DESC LIMIT 100")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for row in rows:
        r = row_to_dict(row)
        t1 = r.get('team1_players', '[]')
        t2 = r.get('team2_players', '[]')
        if isinstance(t1, str):
            try:
                t1 = json.loads(t1)
            except Exception:
                t1 = [t1]
        if isinstance(t2, str):
            try:
                t2 = json.loads(t2)
            except Exception:
                t2 = [t2]
        r['team1_players'] = t1
        r['team2_players'] = t2
        if 'date' in r and hasattr(r['date'], 'isoformat'):
            r['date'] = r['date'].isoformat()
        elif 'date' not in r or r['date'] is None:
            r['date'] = ''
        result.append(r)
    return jsonify({"games": result, "total": total})

@app.route("/admin/reset_database", methods=["POST"])
def admin_reset_database():
    username = session.get('username')
    if not is_super_admin(username):
        return jsonify({"success": False, "message": "Reserve a l'administrateur principal (classe 1)"}), 403
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM scores")
        cur.execute("DELETE FROM reservations")
        cur.execute("DELETE FROM games")
        cur.execute("DELETE FROM users")
        conn.commit()
        cur.close()
        conn.close()
        seed_accounts()
        return jsonify({"success": True, "message": "Base de donnees reinitialisee"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/delete_user', methods=['POST'])
@handle_errors
def delete_user():
    admin_username = session.get('username')
    if not is_super_admin(admin_username):
        return jsonify({"success": False, "message": "Reserve a l'administrateur principal (classe 1)"}), 403
    data = request.get_json()
    username_to_delete = data.get('username')
    if not username_to_delete:
        return jsonify({"success": False, "message": "Nom d'utilisateur requis"}), 400
    if username_to_delete == admin_username:
        return jsonify({"success": False, "message": "Vous ne pouvez pas vous supprimer vous-meme"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    q_check = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
    cur.execute(q_check, (username_to_delete,))
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        return jsonify({"success": False, "message": "Utilisateur introuvable"}), 404
    # Supprimer reservations d'abord (scores supprimés par CASCADE)
    q_res = "DELETE FROM reservations WHERE reserved_by = %s" if USE_POSTGRES else "DELETE FROM reservations WHERE reserved_by = ?"
    cur.execute(q_res, (username_to_delete,))
    q_delete = "DELETE FROM users WHERE username = %s" if USE_POSTGRES else "DELETE FROM users WHERE username = ?"
    cur.execute(q_delete, (username_to_delete,))
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Admin {admin_username} a supprime le compte {username_to_delete}")
    invalidate_role_cache(username_to_delete)
    return jsonify({"success": True, "message": f"Compte '{username_to_delete}' supprime avec succes"})

@app.route('/api/set_user_role', methods=['POST'])
@handle_errors
def set_user_role():
    """Permet au super admin de changer le rôle d'un utilisateur."""
    admin_username = session.get('username')
    if not is_super_admin(admin_username):
        return jsonify({"success": False, "message": "Réservé au super admin"}), 403
    data = request.get_json()
    target = data.get('username')
    role = data.get('role')
    if not target or role not in [0, 1, 2]:
        return jsonify({"success": False, "message": "Paramètres invalides (role: 0=user, 1=super_admin, 2=admin)"}), 400
    if target == admin_username:
        return jsonify({"success": False, "message": "Vous ne pouvez pas changer votre propre rôle"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    q = "UPDATE users SET role = %s WHERE username = %s" if USE_POSTGRES else "UPDATE users SET role = ? WHERE username = ?"
    cur.execute(q, (role, target))
    if cur.rowcount == 0:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Utilisateur introuvable"}), 404
    conn.commit(); cur.close(); conn.close()
    invalidate_role_cache(target)
    logger.info(f"{admin_username} a changé le rôle de {target} → {role}")
    return jsonify({"success": True, "message": f"Rôle de {target} mis à jour"})

# ── Reservations ──────────────────────────────────────────────

@app.route("/save_reservation", methods=["POST"])
@handle_errors
def save_reservation():
    """Ancien endpoint de reservation par jour/heure (conserve pour compatibilite)."""
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Donnees manquantes"}), 400
    day = data.get("day")
    time_val = data.get("time")
    team1 = data.get("team1", [])
    team2 = data.get("team2", [])
    mode = data.get("mode", "1v1")
    reserved_by = session.get("username", "unknown")
    if not day or not time_val:
        return jsonify({"success": False, "message": "Jour et heure requis"}), 400

    # Valider que le jour est aujourd'hui ou demain seulement
    days_map = {
        'Lundi': 0, 'Mardi': 1, 'Mercredi': 2, 'Jeudi': 3,
        'Vendredi': 4, 'Samedi': 5, 'Dimanche': 6
    }
    today_wd = datetime.now().weekday()
    tomorrow_wd = (today_wd + 1) % 7
    target_wd = days_map.get(day)
    if target_wd is None or target_wd not in [today_wd, tomorrow_wd]:
        return jsonify({"success": False, "message": "Reservation limitee a aujourd'hui et demain"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if not is_admin(reserved_by):
            q_count = "SELECT COUNT(*) as cnt FROM reservations WHERE reserved_by = %s" if USE_POSTGRES else "SELECT COUNT(*) as cnt FROM reservations WHERE reserved_by = ?"
            cur.execute(q_count, (reserved_by,))
            count_row = row_to_dict(cur.fetchone())
            user_total = int(count_row.get('cnt') or count_row.get('count') or 0)
            q_existing_mine = (
                "SELECT id FROM reservations WHERE day = %s AND time = %s AND reserved_by = %s"
                if USE_POSTGRES else
                "SELECT id FROM reservations WHERE day = ? AND time = ? AND reserved_by = ?"
            )
            cur.execute(q_existing_mine, (day, time_val, reserved_by))
            is_update = cur.fetchone() is not None
            if not is_update and user_total >= 3:
                return jsonify({"success": False, "message": "Maximum 3 reservations par joueur"}), 400
        q_check = (
            "SELECT reserved_by FROM reservations WHERE day = %s AND time = %s"
            if USE_POSTGRES else
            "SELECT reserved_by FROM reservations WHERE day = ? AND time = ?"
        )
        cur.execute(q_check, (day, time_val))
        existing = cur.fetchone()
        if existing:
            existing_dict = row_to_dict(existing)
            if existing_dict['reserved_by'] != reserved_by and not is_admin(reserved_by):
                return jsonify({"success": False, "message": f"Ce creneau est deja reserve par {existing_dict['reserved_by']}"}), 409
        if USE_POSTGRES:
            cur.execute("DELETE FROM reservations WHERE day = %s AND time = %s", (day, time_val))
        else:
            cur.execute("DELETE FROM reservations WHERE day = ? AND time = ?", (day, time_val))

        # Calculer start_time / end_time
        import re as _re
        start_iso_val = None
        end_iso_val = None
        try:
            match = _re.search(r'(\d{1,2}):(\d{2})', time_val)
            if match:
                h, m = int(match.group(1)), int(match.group(2))
                base = datetime.now()
                diff = (target_wd - base.weekday()) % 7
                base = base + timedelta(days=diff)
                start_dt = base.replace(hour=h, minute=m, second=0, microsecond=0)
                end_dt = start_dt + timedelta(minutes=15)
                start_iso_val = start_dt.isoformat()
                end_iso_val = end_dt.isoformat()
        except Exception:
            pass

        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO reservations (day, time, team1, team2, mode, reserved_by, start_time, end_time) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (day, time_val, json.dumps(team1), json.dumps(team2), mode, reserved_by, start_iso_val, end_iso_val)
            )
        else:
            cur.execute(
                "INSERT INTO reservations (day, time, team1, team2, mode, reserved_by, start_time, end_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (day, time_val, json.dumps(team1), json.dumps(team2), mode, reserved_by, start_iso_val, end_iso_val)
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return jsonify({"success": True})

@app.route("/cancel_reservation", methods=["POST"])
@handle_errors
def cancel_reservation():
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True)
    day = data.get("day")
    time_val = data.get("time")
    username = session.get("username")
    conn = get_db_connection()
    cur = conn.cursor()
    if is_admin(username):
        q = "DELETE FROM reservations WHERE day = %s AND time = %s" if USE_POSTGRES else "DELETE FROM reservations WHERE day = ? AND time = ?"
        cur.execute(q, (day, time_val))
    else:
        q = (
            "DELETE FROM reservations WHERE day = %s AND time = %s AND reserved_by = %s"
            if USE_POSTGRES else
            "DELETE FROM reservations WHERE day = ? AND time = ? AND reserved_by = ?"
        )
        cur.execute(q, (day, time_val, username))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": bool(deleted)})

@app.route("/api/cancel_reservation_v2", methods=["POST"])
@handle_errors
def cancel_reservation_v2():
    """Annuler une reservation par son id."""
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True) or {}
    res_id = data.get("id")
    username = session["username"]
    if not res_id:
        return jsonify({"success": False, "message": "ID requis"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    if is_admin(username):
        q = "DELETE FROM reservations WHERE id = %s" if USE_POSTGRES else "DELETE FROM reservations WHERE id = ?"
        cur.execute(q, (res_id,))
    else:
        q = (
            "DELETE FROM reservations WHERE id = %s AND reserved_by = %s"
            if USE_POSTGRES else
            "DELETE FROM reservations WHERE id = ? AND reserved_by = ?"
        )
        cur.execute(q, (res_id, username))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": bool(deleted)})

@app.route("/api/reserve_and_lobby", methods=["POST"])
@handle_errors
def reserve_and_lobby():
    """Reserver maintenant et creer le lobby avec l'utilisateur comme hote."""
    global active_lobby
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True) or {}
    try:
        duration = int(data.get("duration", 15))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Duree invalide"}), 400
    mode = data.get("mode", "1v1")
    username = session["username"]
    if duration not in [5, 10, 15]:
        return jsonify({"success": False, "message": "Duree invalide (5, 10 ou 15 min)"}), 400
    now = datetime.now()
    start_time = now
    end_time = now + timedelta(minutes=duration)
    result = _do_reservation(username, start_time, end_time, duration, mode)
    result_data = result.get_json() if hasattr(result, 'get_json') else {}
    if result_data and result_data.get("success"):
        # Bloquer si une partie est en cours (sauf super admin)
        if current_game.get('active') and not is_super_admin(username):
            return jsonify({"success": False, "message": "Une partie est en cours — impossible de créer un lobby"}), 400
        # Creer le lobby avec l'utilisateur comme hote
        if active_lobby.get('active'):
            socketio.emit('lobby_cancelled', {}, namespace='/')
        active_lobby = {
            "host": username, "invited": [],
            "accepted": [username], "declined": [],
            "team1": [username], "team2": [], "active": True
        }
        socketio.emit('lobby_created', {'host': username, 'invited': []}, namespace='/')
        return jsonify({"success": True, "redirect": "/lobby"})
    return result

@app.route("/api/reserve_now", methods=["POST"])
@handle_errors
def reserve_now():
    """Reserver maintenant pour X minutes (5, 10 ou 15)."""
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True) or {}
    try:
        duration = int(data.get("duration", 15))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Duree invalide"}), 400
    mode = data.get("mode", "1v1")
    username = session["username"]
    if duration not in [5, 10, 15]:
        return jsonify({"success": False, "message": "Duree invalide (5, 10 ou 15 min)"}), 400
    now = datetime.now()
    start_time = now
    end_time = now + timedelta(minutes=duration)
    return _do_reservation(username, start_time, end_time, duration, mode)

@app.route("/api/reserve_plan", methods=["POST"])
@handle_errors
def reserve_plan():
    """Planifier une reservation a une heure precise (aujourd'hui ou demain uniquement)."""
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True) or {}
    start_str = data.get("start_time")
    try:
        duration = int(data.get("duration", 15))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Duree invalide"}), 400
    mode = data.get("mode", "1v1")
    username = session["username"]
    if duration not in [5, 10, 15]:
        return jsonify({"success": False, "message": "Duree invalide (5, 10 ou 15 min)"}), 400
    if not start_str:
        return jsonify({"success": False, "message": "Heure de debut requise"}), 400
    try:
        if 'T' in start_str:
            start_time = datetime.fromisoformat(start_str)
        else:
            date_str = data.get("date", datetime.now().date().isoformat())
            start_time = datetime.fromisoformat(f"{date_str}T{start_str}:00")
    except Exception:
        return jsonify({"success": False, "message": "Format d'heure invalide"}), 400
    now = datetime.now()
    # Refuser les reservations dans le passe (sauf admin)
    if not is_admin(username) and start_time < now - timedelta(minutes=1):
        return jsonify({"success": False, "message": "Impossible de reserver dans le passe"}), 400
    # Refuser si au-dela de demain (uniquement aujourd'hui et demain) — sauf admin
    if not is_admin(username):
        max_date = (now + timedelta(days=2)).date()
        if start_time.date() >= max_date:
            return jsonify({"success": False, "message": "Reservation limitee a aujourd'hui et demain"}), 400
    end_time = start_time + timedelta(minutes=duration)
    return _do_reservation(username, start_time, end_time, duration, mode)

def _do_reservation(username, start_time, end_time, duration, mode):
    """Logique commune de reservation avec verification anti-chevauchement."""
    now = datetime.now()
    # Double verification : seulement aujourd'hui et demain (sauf admin)
    if not is_admin(username):
        max_date = (now + timedelta(days=2)).date()
        if start_time.date() >= max_date:
            return jsonify({"success": False, "message": "Reservation limitee a aujourd'hui et demain"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        start_iso = start_time.isoformat()
        end_iso = end_time.isoformat()

        # Verifier le quota (sauf admin)
        if not is_admin(username):
            if USE_POSTGRES:
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM reservations WHERE reserved_by = %s AND end_time > %s",
                    (username, now.isoformat())
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM reservations WHERE reserved_by = ? AND end_time > ?",
                    (username, now.isoformat())
                )
            row = row_to_dict(cur.fetchone()) or {}
            if int(row.get('cnt') or row.get('count') or 0) >= 3:
                return jsonify({"success": False, "message": "Maximum 3 reservations actives"}), 400

        # Verifier le chevauchement
        if USE_POSTGRES:
            cur.execute(
                "SELECT reserved_by FROM reservations WHERE start_time < %s AND end_time > %s",
                (end_iso, start_iso)
            )
        else:
            cur.execute(
                "SELECT reserved_by FROM reservations WHERE start_time < ? AND end_time > ?",
                (end_iso, start_iso)
            )
        conflict = cur.fetchone()
        if conflict:
            c = row_to_dict(conflict)
            if c['reserved_by'] != username and not is_admin(username):
                return jsonify({"success": False, "message": f"Ce creneau chevauche une reservation de {c['reserved_by']}"}), 409

        # Champs compatibilite
        days_fr = {
            'Monday': 'Lundi', 'Tuesday': 'Mardi', 'Wednesday': 'Mercredi',
            'Thursday': 'Jeudi', 'Friday': 'Vendredi', 'Saturday': 'Samedi', 'Sunday': 'Dimanche'
        }
        day_fr = days_fr.get(start_time.strftime('%A'), start_time.strftime('%A'))
        time_str = start_time.strftime('%H:%M')
        end_str = end_time.strftime('%H:%M')
        time_display = f"{time_str} - {end_str}"

        if USE_POSTGRES:
            cur.execute("""
                INSERT INTO reservations (day, time, team1, team2, mode, reserved_by, start_time, end_time, duration_minutes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (start_time, reserved_by) DO NOTHING
            """, (day_fr, time_display, json.dumps([]), json.dumps([]), mode, username, start_iso, end_iso, duration))
        else:
            # Verifier doublon exact
            cur.execute(
                "SELECT id FROM reservations WHERE start_time = ? AND reserved_by = ?",
                (start_iso, username)
            )
            if cur.fetchone():
                return jsonify({"success": False, "message": "Vous avez deja une reservation a cette heure"}), 409
            cur.execute("""
                INSERT INTO reservations (day, time, team1, team2, mode, reserved_by, start_time, end_time, duration_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (day_fr, time_display, json.dumps([]), json.dumps([]), mode, username, start_iso, end_iso, duration))
        conn.commit()
        return jsonify({
            "success": True,
            "start": start_iso,
            "end": end_iso,
            "duration": duration,
            "time_display": time_display
        })
    finally:
        cur.close()
        conn.close()

@app.route("/users_list")
@handle_errors
def users_list():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT username, nickname, avatar_preset, avatar_url, elo FROM users ORDER BY username ASC")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return jsonify([{
        "username": row_to_dict(r)['username'],
        "nickname": row_to_dict(r).get('nickname') or "",
        "avatar_preset": row_to_dict(r).get('avatar_preset') or "",
        "avatar_url": row_to_dict(r).get('avatar_url') or "",
        "elo": row_to_dict(r).get('elo') or 1000,
    } for r in rows])

@app.route("/api/current_game")
def api_current_game():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    return jsonify(current_game)

@app.route("/api/has_active_game")
def api_has_active_game():
    username = session.get('username')
    if not username:
        return jsonify({"error": "Non connecte"}), 401
    return jsonify({
        "has_active_game": current_game.get('active', False),
        "game_data": current_game if current_game.get('active') else None,
        "is_admin": is_admin(username),
        "has_reservation": has_active_reservation(username),
    })

@app.route("/api/active_lobby")
def api_active_lobby():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    return jsonify(active_lobby)

@app.route("/api/online_users")
def api_online_users():
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    # Dédupliquer : un user peut avoir plusieurs tabs ouvertes
    online = list(set(connected_users.values()))
    return jsonify({"online": online})

@app.route("/api/public_stats")
@handle_errors
def api_public_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM games")
    row = row_to_dict(cur.fetchone())
    total_games = int(row.get('cnt') or row.get('count') or 0)
    cur.execute("SELECT COUNT(*) as cnt FROM users WHERE total_games > 0")
    row2 = row_to_dict(cur.fetchone())
    active_players = int(row2.get('cnt') or row2.get('count') or 0)
    cur.close()
    conn.close()
    return jsonify({
        "total_games": total_games,
        "active_players": active_players,
        "avg_duration_minutes": 15,
    })

@app.route("/reservations_today")
@handle_errors
def reservations_today():
    if "username" not in session:
        return jsonify([])
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        today = datetime.now().strftime('%A')
        days_fr = {
            'Monday': 'Lundi', 'Tuesday': 'Mardi', 'Wednesday': 'Mercredi',
            'Thursday': 'Jeudi', 'Friday': 'Vendredi', 'Saturday': 'Samedi', 'Sunday': 'Dimanche'
        }
        day_fr = days_fr.get(today, today)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%A')
        day_fr_tomorrow = days_fr.get(tomorrow, tomorrow)
        if USE_POSTGRES:
            cur.execute(
                "SELECT id, day, time, mode, reserved_by, start_time, end_time, duration_minutes FROM reservations WHERE day = %s ORDER BY time ASC LIMIT 5",
                (day_fr,)
            )
            today_rows = [row_to_dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT id, day, time, mode, reserved_by, start_time, end_time, duration_minutes FROM reservations WHERE day = %s ORDER BY time ASC LIMIT 5",
                (day_fr_tomorrow,)
            )
            tomorrow_rows = [row_to_dict(r) for r in cur.fetchall()]
        else:
            cur.execute(
                "SELECT id, day, time, mode, reserved_by, start_time, end_time, duration_minutes FROM reservations WHERE day = ? ORDER BY time ASC LIMIT 5",
                (day_fr,)
            )
            today_rows = [row_to_dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT id, day, time, mode, reserved_by, start_time, end_time, duration_minutes FROM reservations WHERE day = ? ORDER BY time ASC LIMIT 5",
                (day_fr_tomorrow,)
            )
            tomorrow_rows = [row_to_dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()
    return jsonify(today_rows + tomorrow_rows)

@app.route("/api/babyfoot_status")
@handle_errors
def babyfoot_status():
    """Retourne l'etat actuel du babyfoot : libre ou occupe + prochaines reservations."""
    if "username" not in session:
        return jsonify({"error": "Non connecte"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        now = datetime.now()
        today = now.date()
        tomorrow = (now + timedelta(days=1)).date()
        if USE_POSTGRES:
            cur.execute("""
                SELECT id, day, time, mode, reserved_by, start_time, end_time, duration_minutes
                FROM reservations
                WHERE start_time >= %s AND start_time < %s
                ORDER BY start_time ASC
            """, (today.isoformat(), (tomorrow + timedelta(days=1)).isoformat()))
        else:
            cur.execute("""
                SELECT id, day, time, mode, reserved_by, start_time, end_time, duration_minutes
                FROM reservations
                WHERE start_time >= ? AND start_time < ?
                ORDER BY start_time ASC
            """, (today.isoformat(), (tomorrow + timedelta(days=1)).isoformat()))
        rows = [row_to_dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    now_str = now.isoformat()
    current = None
    upcoming = []
    for r in rows:
        # Serialiser les datetime PostgreSQL en string si necessaire
        for field in ('start_time', 'end_time'):
            if r.get(field) and hasattr(r[field], 'isoformat'):
                r[field] = r[field].isoformat()
        st = r.get('start_time', '')
        et = r.get('end_time', '')
        if st and et:
            if st <= now_str <= et:
                current = r
            elif st > now_str:
                upcoming.append(r)

    return jsonify({
        "is_free": current is None,
        "current": current,
        "upcoming": upcoming[:5],
        "all_today": [r for r in rows if str(r.get('start_time', ''))[:10] == today.isoformat()],
        "all_tomorrow": [r for r in rows if str(r.get('start_time', ''))[:10] == tomorrow.isoformat()],
        "server_time": now_str,
    })

# ── ELO helpers ───────────────────────────────────────────────────────────
def compute_elo(winner_elo, loser_elo, k=40):
    """
    Formule ELO adaptée au baby-foot.
    K=40 (plus dynamique qu'aux échecs) pour que les ELO bougent vite.
    En 1v1 classique babyfoot sur 10 buts : chaque victoire compte vraiment.
    """
    expected_w = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    return (
        max(800, round(winner_elo + k * (1 - expected_w))),
        max(800, round(loser_elo  + k * (0 - (1 - expected_w))))
    )

def elo_tier(elo):
    """Niveaux ELO adaptés au baby-foot (départ à 1000, K=40)."""
    if elo >= 1500: return ("Maître 🏆", "🏆", 1500, 9999)
    if elo >= 1350: return ("Expert 💎", "💎", 1350, 1499)
    if elo >= 1200: return ("Confirmé ⚡", "⚡", 1200, 1349)
    if elo >= 1050: return ("Intermédiaire 🔥", "🔥", 1050, 1199)
    if elo >= 950:  return ("Débutant+ 🌱", "🌱", 950, 1049)
    return ("Débutant 🎮", "🎮", 800, 949)

ELO_TIERS_FRONTEND = [
    {"name": "Débutant 🎮",        "icon": "🎮", "min": 800,  "max": 949,  "desc": "Tu apprends les bases, chaque partie compte !"},
    {"name": "Débutant+ 🌱",       "icon": "🌱", "min": 950,  "max": 1049, "desc": "La technique commence à se voir, continue !"},
    {"name": "Intermédiaire 🔥",   "icon": "🔥", "min": 1050, "max": 1199, "desc": "Tu contrôles le jeu et tu gagnes régulièrement."},
    {"name": "Confirmé ⚡",         "icon": "⚡", "min": 1200, "max": 1349, "desc": "Adversaire redoutable — tout le monde le sait."},
    {"name": "Expert 💎",           "icon": "💎", "min": 1350, "max": 1499, "desc": "Top 5 du club, sérieusement."},
    {"name": "Maître 🏆",           "icon": "🏆", "min": 1500, "max": 9999, "desc": "Imbattable. La table te appartient."},
]

@app.route("/api/profile", methods=["GET"])
def api_get_profile():
    username = session.get("username")
    if not username:
        return jsonify({"error": "Non connecté"}), 401
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    user = row_to_dict(cur.fetchone())
    cur.close(); conn.close()
    if not user:
        return jsonify({"error": "Introuvable"}), 404
    elo = user.get("elo") or 1000
    tier_name, tier_icon, tier_min, tier_max = elo_tier(elo)
    return jsonify({
        "username": user["username"],
        "nickname": user.get("nickname") or "",
        "bio": user.get("bio") or "",
        "avatar_preset": user.get("avatar_preset") or "",
        "avatar_url": user.get("avatar_url") or "",
        "elo": elo,
        "elo_tier": tier_name,
        "elo_icon": tier_icon,
        "elo_tier_min": tier_min,
        "elo_tier_max": tier_max,
        "elo_tiers": ELO_TIERS_FRONTEND,
    })

@app.route("/api/profile", methods=["POST"])
def api_update_profile():
    username = session.get("username")
    if not username:
        return jsonify({"error": "Non connecté"}), 401
    data = request.get_json()
    nickname = (data.get("nickname") or "").strip()[:50]
    bio = (data.get("bio") or "").strip()[:120]
    avatar_preset = (data.get("avatar_preset") or "").strip()[:10]
    conn = get_db_connection()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute("UPDATE users SET nickname=%s, bio=%s, avatar_preset=%s WHERE username=%s",
                    (nickname or None, bio or None, avatar_preset or None, username))
    else:
        cur.execute("UPDATE users SET nickname=?, bio=?, avatar_preset=? WHERE username=?",
                    (nickname or None, bio or None, avatar_preset or None, username))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True, "message": "Profil mis à jour"})

@app.route("/api/upload_avatar", methods=["POST"])
def api_upload_avatar():
    import base64, os, uuid as _uuid
    username = session.get("username")
    if not username:
        return jsonify({"error": "Non connecté"}), 401
    data = request.get_json()
    img_data = data.get("image", "")
    if not img_data.startswith("data:image/"):
        return jsonify({"error": "Image invalide"}), 400
    # Validation taille côté serveur (max 2 Mo en base64 ≈ 2.7 Mo de string)
    MAX_B64_LEN = 13_600_000  # 10 Mo en base64 ≈ 13.3 Mo string
    header, b64 = img_data.split(",", 1)
    if len(b64) > MAX_B64_LEN:
        return jsonify({"error": "Image trop grande (max 10 Mo)"}), 413
    # Vérifier type autorisé
    if not any(t in header for t in ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif']):
        return jsonify({"error": "Format non supporté (jpg, png, webp, gif)"}), 400
    avatars_dir = os.path.join(os.path.dirname(__file__), "static", "avatars")
    os.makedirs(avatars_dir, exist_ok=True)
    ext = "png" if "png" in header else "webp" if "webp" in header else "gif" if "gif" in header else "jpg"
    filename = f"{username}_{_uuid.uuid4().hex[:8]}.{ext}"
    decoded = base64.b64decode(b64)
    with open(os.path.join(avatars_dir, filename), "wb") as f:
        f.write(decoded)
    avatar_url = f"/static/avatars/{filename}"
    # Supprimer l'ancien avatar si existant
    conn = get_db_connection()
    cur = conn.cursor()
    q_old = "SELECT avatar_url FROM users WHERE username = %s" if USE_POSTGRES else "SELECT avatar_url FROM users WHERE username = ?"
    cur.execute(q_old, (username,))
    old_row = row_to_dict(cur.fetchone())
    if old_row and old_row.get("avatar_url"):
        old_path = os.path.join(os.path.dirname(__file__), old_row["avatar_url"].lstrip("/"))
        try:
            if os.path.exists(old_path):
                os.remove(old_path)
        except Exception:
            pass
    if USE_POSTGRES:
        cur.execute("UPDATE users SET avatar_url=%s, avatar_preset=NULL WHERE username=%s", (avatar_url, username))
    else:
        cur.execute("UPDATE users SET avatar_url=?, avatar_preset=NULL WHERE username=?", (avatar_url, username))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True, "avatar_url": avatar_url})

@app.route("/settings")
def settings_page():
    if "username" not in session:
        return redirect(url_for('login_page'))
    return render_template("settings.html")

@app.route("/api/change_password", methods=["POST"])
@handle_errors
def api_change_password():
    if "username" not in session:
        return jsonify({"success": False, "message": "Non authentifie"}), 401
    data = request.get_json(silent=True) or {}
    username = session["username"]
    current_pw = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    if not current_pw or not new_pw:
        return jsonify({"success": False, "message": "Champs requis"}), 400
    try:
        new_pw = validate_password(new_pw)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT password FROM users WHERE username = %s" if USE_POSTGRES else "SELECT password FROM users WHERE username = ?"
    cur.execute(q, (username,))
    row = row_to_dict(cur.fetchone())
    if not row:
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Utilisateur introuvable"}), 404
    if not bcrypt.checkpw(current_pw.encode(), row["password"].encode()):
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Mot de passe actuel incorrect"}), 401
    hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    q2 = "UPDATE users SET password = %s WHERE username = %s" if USE_POSTGRES else "UPDATE users SET password = ? WHERE username = ?"
    cur.execute(q2, (hashed, username))
    conn.commit()
    cur.close(); conn.close()
    logger.info(f"Mot de passe change pour {username}")
    return jsonify({"success": True, "message": "Mot de passe mis à jour avec succès"})

@app.route("/stats/<username>")
@handle_errors
def stats_by_username(username):
    return user_stats(username)


# ── Arduino HTTP endpoints ────────────────────────────────────

arduino_last_goal_time = {}

@app.route("/api/arduino/status", methods=["GET"])
def api_arduino_status():
    """Etat complet pour l'ESP32 (sync au demarrage + poll)."""
    active = current_game.get("active", False)
    t1 = current_game.get("team1_score", 0)
    t2 = current_game.get("team2_score", 0)
    servo1_expected = "open" if (active and t1 < 9) else "close"
    servo2_expected = "open" if (active and t2 < 9) else "close"
    return jsonify({
        "game_active":     active,
        "team1_score":     t1,
        "team2_score":     t2,
        "servo1_expected": servo1_expected,
        "servo2_expected": servo2_expected,
        "started_by":      current_game.get("started_by"),
        "team1_players":   current_game.get("team1_players", []),
        "team2_players":   current_game.get("team2_players", []),
    })

@app.route("/api/arduino/commands", methods=["GET"])
def api_arduino_commands():
    global servo_commands
    now = _time.time()
    if not hasattr(api_arduino_commands, 'last_poll'):
        api_arduino_commands.last_poll = 0
    if now - api_arduino_commands.last_poll > 30:
        # L'ESP32 n'a pas poll depuis 30s → probablement redemarrage, nettoyer la queue
        servo_commands["servo1"].clear()
        servo_commands["servo2"].clear()
        logger.info("Queue servos nettoyee (reboot ESP32 detecte)")
    api_arduino_commands.last_poll = now
    cmd1 = servo_commands["servo1"].pop(0) if servo_commands["servo1"] else "none"
    cmd2 = servo_commands["servo2"].pop(0) if servo_commands["servo2"] else "none"
    return jsonify({"servo1": cmd1, "servo2": cmd2})

@app.route("/api/arduino/servo", methods=["POST"])
def api_arduino_servo():
    """Controle direct des servos via HTTP (utilise le secret Arduino, pas la session)."""
    global servo_commands
    data = request.get_json(silent=True) or {}
    ARDUINO_SECRET = os.environ.get("ARDUINO_SECRET", "babyfoot-arduino-secret-2024")
    # Accepter soit le secret Arduino soit une session admin
    username = session.get('username') if request.cookies.get('session') else None
    if data.get("secret") != ARDUINO_SECRET and not is_admin(username):
        return jsonify({"success": False, "message": "Non autorise"}), 403
    servo = data.get("servo")
    action = data.get("action")
    if servo not in ["servo1", "servo2"] or action not in ["open", "close"]:
        return jsonify({"success": False, "message": "Parametres invalides"}), 400
    servo_commands[servo].clear()
    servo_commands[servo].append(action)
    return jsonify({"success": True, "servo": servo, "action": action})

def _get_arduino_secret():
    """Retourne le secret Arduino. Leve une erreur si non defini en production."""
    secret = os.environ.get("ARDUINO_SECRET")
    if not secret:
        logger.warning("ARDUINO_SECRET non defini — utilisez la variable d'environnement Railway !")
        secret = "babyfoot-arduino-secret-2024"
    return secret

@app.route("/api/arduino/goal", methods=["POST"])
def api_arduino_goal():
    global current_game, _goal_processing, rematch_pending
    data = request.get_json(silent=True) or {}
    ARDUINO_SECRET = _get_arduino_secret()
    if data.get("secret") != ARDUINO_SECRET:
        return jsonify({"success": False, "message": "Secret invalide"}), 403
    now = _time.time()
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    if client_ip in arduino_last_goal_time and now - arduino_last_goal_time[client_ip] < 1:
        return jsonify({"success": False, "message": "Trop rapide"}), 429
    arduino_last_goal_time[client_ip] = now
    if not current_game.get("active"):
        return jsonify({"success": False, "message": "Aucune partie en cours", "game_active": False}), 200
    team = data.get("team")
    if team not in ["team1", "team2"]:
        return jsonify({"success": False, "message": "Equipe invalide"}), 400
    if _goal_processing:
        return jsonify({"success": False, "message": "Traitement en cours"}), 429
    _goal_processing = True
    try:
        current_game[f"{team}_score"] += 1
        if current_game[f"{team}_score"] == 9:
            # A 9 buts : verrouiller la balle adverse (avertissement)
            servo_adverse = 'servo1' if team == 'team2' else 'servo2'
            servo_commands[servo_adverse].append('close')
            socketio.emit(f"{servo_adverse}_lock", {}, namespace="/")
        if current_game[f"{team}_score"] >= 10:
            current_game["winner"] = team
            current_game["active"] = False
            servo_commands["servo1"].append("close")
            servo_commands["servo2"].append("close")
            try:
                save_game_results(current_game)
            except Exception as e:
                logger.error(f"Erreur sauvegarde: {e}")
            socketio.emit("game_ended", current_game, namespace="/")
            rematch_pending = True
            socketio.emit("rematch_prompt", {}, namespace="/")
            return jsonify({"success": True, "game_ended": True, "winner": team})
        else:
            socketio.emit("score_updated", current_game, namespace="/")
            return jsonify({
                "success": True,
                "game_ended": False,
                "scores": {
                    "team1": current_game["team1_score"],
                    "team2": current_game["team2_score"]
                }
            })
    finally:
        _goal_processing = False

# ── SocketIO handlers ─────────────────────────────────────────

@socketio.on('connect')
def handle_connect():
    global rematch_pending
    username = session.get('username', 'Anonymous')
    connected_users[request.sid] = username
    logger.info(f"WS connecte: {username} ({request.sid})")

    # Partie active → recuperation en cours de jeu
    if current_game.get('active'):
        join_room('game')
        emit('game_recovery', current_game)
    # Partie terminée et popup victoire pas encore fermée → rejouer game_ended SEULEMENT si l'user était dans la partie
    elif current_game.get('winner') and not current_game.get('active'):
        user_in_game = (
            username in current_game.get('team1_players', []) or
            username in current_game.get('team2_players', []) or
            is_admin(username) or
            username == current_game.get('started_by')
        )
        if user_in_game:
            emit('game_ended', current_game)
            if rematch_pending:
                emit('rematch_prompt', {})

    # Invitation lobby en attente → la renvoyer
    if username in pending_invitations:
        inv = pending_invitations[username]
        if _time.time() - inv.get('timestamp', 0) < 300:
            emit('lobby_invitation', {'from': inv['from'], 'to': username})

@socketio.on('disconnect')
def handle_disconnect():
    connected_users.pop(request.sid, None)
    logger.info(f"WS deconnecte: {request.sid}")

@socketio.on('create_lobby')
def handle_create_lobby(data):
    global active_lobby
    username = get_socket_user()
    if not is_admin(username) and not has_active_reservation(username):
        emit('error', {'message': 'Seuls admins/reservateurs peuvent creer un lobby'})
        return
    # Bloquer si une partie est en cours (sauf super admin)
    if current_game.get('active') and not is_super_admin(username):
        emit('error', {'message': 'Une partie est en cours — impossible de créer un lobby'})
        return
    # Si un lobby est déjà actif, seul Imran peut en créer un nouveau (annule l'ancien)
    if active_lobby.get('active') and not is_super_admin(username):
        emit('error', {'message': 'Un lobby est déjà en cours — seul Imran peut le remplacer'})
        return
    if active_lobby.get('active'):
        socketio.emit('lobby_cancelled', {}, namespace='/')
    invited_users = data.get('invited', [])
    active_lobby = {
        "host": username, "invited": invited_users,
        "accepted": [username], "declined": [],
        "team1": [username], "team2": [], "active": True
    }
    socketio.emit('lobby_created', {'host': username, 'invited': invited_users}, namespace='/')
    for user in invited_users:
        socketio.emit('lobby_invitation', {'from': username, 'to': user}, namespace='/')

@socketio.on('invite_to_lobby')
def handle_invite_to_lobby(data):
    global active_lobby
    username = get_socket_user()
    invited_user = data.get('user')
    if username != active_lobby['host'] and not is_admin(username):
        emit('error', {'message': "Seul l'hote ou un admin peut inviter"})
        return
    if len(active_lobby['accepted']) + len(active_lobby['invited']) >= 4:
        emit('error', {'message': 'Lobby complet'})
        return
    already_in = (
        invited_user in active_lobby['invited'] or
        invited_user in active_lobby['accepted'] or
        invited_user in active_lobby['team1'] or
        invited_user in active_lobby['team2']
    )
    if already_in:
        return
    pending_invitations[invited_user] = {'from': active_lobby['host'], 'timestamp': _time.time()}
    if is_guest_player(invited_user):
        active_lobby['accepted'].append(invited_user)
        # Équipes fixes : Joueur1→team2, Joueur2→team1, Joueur3→team2
        guest_team_map = {'Joueur1': 'team2', 'Joueur2': 'team1', 'Joueur3': 'team2'}
        target_team = guest_team_map.get(invited_user, 'team2')
        if len(active_lobby[target_team]) < 2:
            active_lobby[target_team].append(invited_user)
        else:
            active_lobby['accepted'].remove(invited_user)
            emit('error', {'message': 'Equipes completes'})
            return
    else:
        active_lobby['invited'].append(invited_user)
        socketio.emit('lobby_invitation', {'from': active_lobby['host'], 'to': invited_user}, namespace='/')
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('accept_lobby')
def handle_accept_lobby():
    global active_lobby
    username = get_socket_user()
    if username in active_lobby['team1'] or username in active_lobby['team2']:
        return
    if username not in active_lobby['invited']:
        return
    active_lobby['invited'].remove(username)
    if username not in active_lobby['accepted']:
        active_lobby['accepted'].append(username)
    t1, t2 = len(active_lobby['team1']), len(active_lobby['team2'])
    if t1 < 2 and t1 <= t2:
        active_lobby['team1'].append(username)
    elif t2 < 2:
        active_lobby['team2'].append(username)
    else:
        emit('error', {'message': 'Equipes completes'})
        active_lobby['accepted'].remove(username)
        active_lobby['invited'].append(username)
        return
    pending_invitations.pop(username, None)
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('decline_lobby')
def handle_decline_lobby():
    global active_lobby
    username = get_socket_user()
    if username not in active_lobby['invited']:
        return
    active_lobby['invited'].remove(username)
    if username not in active_lobby['declined']:
        active_lobby['declined'].append(username)
    pending_invitations.pop(username, None)
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('request_join_lobby')
def handle_request_join_lobby():
    global active_lobby
    import uuid as _uuid
    username = get_socket_user()
    if not username:
        return
    if not active_lobby['active']:
        emit('error', {'message': 'Aucun lobby actif'})
        return
    host = active_lobby['host']
    if username == host:
        return
    already_in = (
        username in active_lobby.get('invited', []) or
        username in active_lobby.get('accepted', [])
    )
    if already_in:
        emit('error', {'message': 'Vous etes deja dans ce lobby'})
        return
    request_id = str(_uuid.uuid4())[:8]
    if 'join_requests' not in active_lobby:
        active_lobby['join_requests'] = {}
    active_lobby['join_requests'][request_id] = {'from': username}
    socketio.emit('join_request', {
        'from': username,
        'host': host,
        'request_id': request_id
    }, namespace='/')

@socketio.on('accept_join_request')
def handle_accept_join_request(data):
    global active_lobby
    host = get_socket_user()
    from_user = data.get('from')
    request_id = data.get('request_id')
    if not active_lobby['active']:
        return
    if host != active_lobby['host'] and not is_admin(host):
        emit('error', {'message': 'Seul l hote peut accepter les demandes'})
        return
    join_requests = active_lobby.get('join_requests', {})
    if request_id not in join_requests:
        return
    join_requests.pop(request_id, None)
    if from_user not in active_lobby['invited'] and from_user not in active_lobby['accepted']:
        active_lobby['invited'].append(from_user)
        pending_invitations[from_user] = {'from': active_lobby['host'], 'timestamp': _time.time()}
    socketio.emit('join_request_result', {
        'accepted': True,
        'host': host,
        'from': from_user
    }, namespace='/')
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('decline_join_request')
def handle_decline_join_request(data):
    global active_lobby
    host = get_socket_user()
    from_user = data.get('from')
    request_id = data.get('request_id')
    if not active_lobby['active']:
        return
    join_requests = active_lobby.get('join_requests', {})
    join_requests.pop(request_id, None)
    socketio.emit('join_request_result', {
        'accepted': False,
        'host': host,
        'from': from_user
    }, namespace='/')

@socketio.on('request_team_swap')
def handle_request_team_swap(data):
    from_user = get_socket_user()
    to_user = data.get('with')
    request_id = f"{from_user}_{to_user}"
    team_swap_requests[request_id] = {'from': from_user, 'to': to_user}
    socketio.emit('team_swap_request', {'from': from_user, 'to': to_user, 'request_id': request_id}, namespace='/')

@socketio.on('accept_team_swap')
def handle_accept_team_swap(data):
    global active_lobby
    request_id = data.get('request_id')
    if request_id not in team_swap_requests:
        return
    swap = team_swap_requests.pop(request_id)
    fu, tu = swap['from'], swap['to']
    if fu in active_lobby['team1'] and tu in active_lobby['team2']:
        active_lobby['team1'].remove(fu)
        active_lobby['team2'].remove(tu)
        active_lobby['team1'].append(tu)
        active_lobby['team2'].append(fu)
    elif fu in active_lobby['team2'] and tu in active_lobby['team1']:
        active_lobby['team2'].remove(fu)
        active_lobby['team1'].remove(tu)
        active_lobby['team2'].append(tu)
        active_lobby['team1'].append(fu)
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('decline_team_swap')
def handle_decline_team_swap(data):
    request_id = data.get('request_id')
    if request_id in team_swap_requests:
        team_swap_requests.pop(request_id)

@socketio.on('kick_from_lobby')
def handle_kick_from_lobby(data):
    global active_lobby
    username = get_socket_user()
    kicked_user = data.get('user')
    if username != active_lobby['host'] and not is_admin(username):
        emit('error', {'message': "Seul l'hote ou un admin peut exclure"})
        return
    if kicked_user == active_lobby['host']:
        emit('error', {'message': "Impossible d'exclure l'hote"})
        return
    for lst in ['invited', 'accepted', 'team1', 'team2']:
        if kicked_user in active_lobby[lst]:
            active_lobby[lst].remove(kicked_user)
    pending_invitations.pop(kicked_user, None)
    socketio.emit('kicked_from_lobby', {'kicked_user': kicked_user}, namespace='/')
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('cancel_lobby')
def handle_cancel_lobby():
    global active_lobby
    username = get_socket_user()
    if username != active_lobby['host'] and not is_admin(username):
        emit('error', {'message': "Seul l'hote ou un admin peut annuler"})
        return
    for user in list(active_lobby.get('invited', [])):
        pending_invitations.pop(user, None)
    active_lobby = {
        "host": None, "invited": [], "accepted": [],
        "declined": [], "team1": [], "team2": [], "active": False
    }
    socketio.emit('lobby_cancelled', {}, namespace='/')

@socketio.on('start_game_from_lobby')
def handle_start_game_from_lobby():
    global current_game, active_lobby, rematch_votes, servo_commands
    username = get_socket_user()
    if username != active_lobby['host'] and not is_admin(username):
        emit('error', {'message': "Seul l'hote ou un admin peut lancer"})
        return
    if len(active_lobby['accepted']) < 2:
        emit('error', {'message': 'Au moins 2 joueurs requis'})
        return
    current_game = {
        "team1_score": 0, "team2_score": 0,
        "team1_players": active_lobby['team1'],
        "team2_players": active_lobby['team2'],
        "active": True,
        "started_by": username,
        "reserved_by": username if has_active_reservation(username) else None,
        "started_at": datetime.now().isoformat()
    }
    active_lobby = {
        "host": None, "invited": [], "accepted": [],
        "declined": [], "team1": [], "team2": [], "active": False
    }
    rematch_votes = {"team1": [], "team2": []}
    servo_commands["servo1"].append("open")
    servo_commands["servo2"].append("open")
    socketio.emit('game_started', current_game, namespace='/')
    socketio.emit('servo1_unlock', {}, namespace='/')
    socketio.emit('servo2_unlock', {}, namespace='/')

@socketio.on('start_game')
def handle_start_game(data):
    global current_game, rematch_votes, servo_commands
    try:
        username = get_socket_user()
        if not username:
            emit('error', {'message': 'Non authentifié'})
            return
        if not is_admin(username) and not has_active_reservation(username):
            emit('error', {'message': 'Reservation active ou admin requis'})
            return
        team1 = [p for p in data.get('team1', []) if p and p.strip()]
        team2 = [p for p in data.get('team2', []) if p and p.strip()]
        if not team1 or not team2:
            emit('error', {'message': 'Chaque equipe doit avoir au moins un joueur'})
            return
        if current_game.get('active'):
            emit('error', {'message': 'Une partie est deja en cours'})
            return
        current_game = {
            "team1_score": 0, "team2_score": 0,
            "team1_players": team1, "team2_players": team2,
            "active": True, "started_by": username,
            "reserved_by": username if has_active_reservation(username) else None,
            "started_at": datetime.now().isoformat()
        }
        rematch_votes = {"team1": [], "team2": []}
        servo_commands["servo1"].append("open")
        servo_commands["servo2"].append("open")
        socketio.emit('game_started', current_game, namespace='/')
        socketio.emit('servo1_unlock', {}, namespace='/')
        socketio.emit('servo2_unlock', {}, namespace='/')
    except Exception as e:
        logger.error(f"Erreur start_game: {e}")
        emit('error', {'message': str(e)})

@socketio.on('unlock_servo1')
def handle_unlock_servo1():
    global servo_commands
    username = get_socket_user()
    if not is_admin(username):
        emit('error', {'message': 'Admin requis'})
        return
    servo_commands["servo1"].clear()
    servo_commands["servo1"].append("open")
    socketio.emit('servo1_unlock', {}, namespace='/')
    def relock():
        eventlet.sleep(5.0)
        servo_commands["servo1"].clear()
        servo_commands["servo1"].append("close")
        socketio.emit('servo1_lock', {}, namespace='/')
    eventlet.spawn(relock)

@socketio.on('unlock_servo2')
def handle_unlock_servo2():
    global servo_commands
    username = get_socket_user()
    if not is_admin(username):
        emit('error', {'message': 'Admin requis'})
        return
    servo_commands["servo2"].clear()
    servo_commands["servo2"].append("open")
    socketio.emit('servo2_unlock', {}, namespace='/')
    def relock():
        eventlet.sleep(5.0)
        servo_commands["servo2"].clear()
        servo_commands["servo2"].append("close")
        socketio.emit('servo2_lock', {}, namespace='/')
    eventlet.spawn(relock)

@socketio.on('stop_game')
def handle_stop_game():
    global current_game, rematch_votes, servo_commands, rematch_pending
    username = get_socket_user()
    can_stop = is_admin(username) or current_game.get('started_by') == username
    if not can_stop:
        emit('error', {'message': "Seul l'admin ou l'hote de la partie peut l'arreter"})
        return
    _reset_game_state()
    socketio.emit('game_stopped', {}, namespace='/')
    socketio.emit('servo1_lock', {}, namespace='/')
    socketio.emit('servo2_lock', {}, namespace='/')

@socketio.on('update_score')
def handle_score(data):
    global current_game, rematch_pending
    try:
        username = get_socket_user()
        if not username:
            emit('error', {'message': 'Non authentifié'})
            return
        if not current_game.get('active'):
            emit('error', {'message': 'Aucune partie en cours'})
            return
        can_control = is_admin(username)
        if not can_control:
            emit('error', {'message': "Seul un admin peut ajouter des points"})
            return
        team = data.get('team')
        if team not in ['team1', 'team2']:
            emit('error', {'message': 'Equipe invalide'})
            return
        current_game[f"{team}_score"] += 1
        if current_game[f"{team}_score"] >= 10:
            current_game['winner'] = team
            current_game['active'] = False
            try:
                save_game_results(current_game)
            except Exception as e:
                logger.error(f"Save error: {e}")
            socketio.emit('game_ended', current_game, namespace='/')
            rematch_pending = True
            socketio.emit('rematch_prompt', {}, namespace='/')
        else:
            socketio.emit('score_updated', current_game, namespace='/')
            emit('score_ack', {'team': team, 'score': current_game[f"{team}_score"]})
    except Exception as e:
        logger.error(f"Erreur update_score: {e}")
        emit('error', {'message': str(e)})

@socketio.on('vote_rematch')
def handle_vote_rematch(data):
    global rematch_votes, rematch_no_votes, current_game, servo_commands, rematch_pending
    username = get_socket_user()
    if not username:
        return
    all_players = list(current_game.get('team1_players', [])) + list(current_game.get('team2_players', []))
    host = current_game.get('started_by')

    if data.get('vote') == 'no':
        # Enregistrer le NON
        if username not in rematch_no_votes:
            rematch_no_votes.append(username)
        yes_count = len(rematch_votes['team1']) + len(rematch_votes['team2'])
        no_count = len(rematch_no_votes)
        # Envoyer update des votes à tout le monde
        socketio.emit('rematch_vote_update', {
            'yes': yes_count, 'no': no_count,
            'total': len(all_players),
            'no_player': username
        }, namespace='/')
        # Notifier l'hôte pour qu'il décide : remplacer ou quitter
        socketio.emit('host_replace_or_quit', {
            'declined_player': username,
            'host': host
        }, namespace='/')
        return

    # Vote OUI
    team = None
    if username in current_game.get('team1_players', []):
        team = 'team1'
    elif username in current_game.get('team2_players', []):
        team = 'team2'
    elif is_admin(username) or username == host:
        # Admin/hôte forcent le rematch
        _launch_rematch(current_game)
        return
    if not team:
        emit('error', {'message': 'Pas dans cette partie'})
        return
    if username not in rematch_votes[team]:
        rematch_votes[team].append(username)
    yes_count = len(rematch_votes['team1']) + len(rematch_votes['team2'])
    no_count = len(rematch_no_votes)
    socketio.emit('rematch_vote_update', {
        'yes': yes_count, 'no': no_count,
        'total': len(all_players)
    }, namespace='/')
    # Lancer uniquement si tous les joueurs qui N'ONT PAS voté NON ont voté OUI
    t1_needed = [p for p in current_game.get('team1_players', []) if p not in rematch_no_votes]
    t2_needed = [p for p in current_game.get('team2_players', []) if p not in rematch_no_votes]
    t1_yes = [p for p in rematch_votes['team1'] if p not in rematch_no_votes]
    t2_yes = [p for p in rematch_votes['team2'] if p not in rematch_no_votes]
    if len(t1_yes) >= len(t1_needed) and len(t2_yes) >= len(t2_needed) and (t1_needed or t2_needed):
        rematch_no_votes.clear()
        _launch_rematch(current_game)

@socketio.on('host_quit_rematch')
def handle_host_quit_rematch():
    global rematch_votes, rematch_no_votes, rematch_pending
    username = get_socket_user()
    host = current_game.get('started_by')
    if username != host and not is_admin(username):
        return
    rematch_votes = {"team1": [], "team2": []}
    rematch_no_votes = []
    rematch_pending = False
    socketio.emit('rematch_cancelled', {}, namespace='/')

@socketio.on('reset_game')
def handle_reset():
    global current_game, rematch_votes, servo_commands, rematch_pending
    username = get_socket_user()
    if not is_admin(username):
        emit('error', {'message': 'Admin requis'})
        return
    _reset_game_state()
    socketio.emit('game_reset', current_game, namespace='/')

@socketio.on('arduino_goal')
def handle_arduino_goal(data):
    global current_game, servo_commands, _goal_processing, rematch_pending
    ARDUINO_SECRET = _get_arduino_secret()
    if data.get('secret') != ARDUINO_SECRET:
        emit('error', {'message': 'Secret invalide'})
        return
    if not hasattr(handle_arduino_goal, 'last_goal_time'):
        handle_arduino_goal.last_goal_time = {}
    now = _time.time()
    # Anti double-but : 2 secondes minimum entre deux buts du meme sid
    last = handle_arduino_goal.last_goal_time.get(request.sid, 0)
    if now - last < 2:
        return
    handle_arduino_goal.last_goal_time[request.sid] = now
    if not current_game.get('active'):
        return
    team = data.get('team')
    if team not in ['team1', 'team2']:
        return
    if _goal_processing:
        return
    _goal_processing = True
    try:
        current_game[f"{team}_score"] += 1
        if current_game[f"{team}_score"] == 9:
            servo_adverse = 'servo1' if team == 'team2' else 'servo2'
            servo_commands[servo_adverse].append('close')
            socketio.emit(f'{servo_adverse}_lock', {}, namespace='/')
        if current_game[f"{team}_score"] >= 10:
            current_game['winner'] = team
            current_game['active'] = False
            servo_commands["servo1"].append("close")
            servo_commands["servo2"].append("close")
            try:
                save_game_results(current_game)
            except Exception as e:
                logger.error(f"Erreur sauvegarde: {e}")
            socketio.emit('game_ended', current_game, namespace='/')
            rematch_pending = True
            socketio.emit('rematch_prompt', {}, namespace='/')
        else:
            socketio.emit('score_updated', current_game, namespace='/')
    finally:
        _goal_processing = False

@socketio.on('arduino_ping')
def handle_arduino_ping(data):
    emit('arduino_pong', {'status': 'ok'})

@socketio.on('get_game_state')
def handle_get_game_state(data):
    emit('game_state', {
        'active': current_game.get('active', False),
        'team1_score': current_game.get('team1_score', 0),
        'team2_score': current_game.get('team2_score', 0),
        'team1_players': current_game.get('team1_players', []),
        'team2_players': current_game.get('team2_players', []),
    })

# ── Sauvegarde des resultats ──────────────────────────────────

def save_game_results(game):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            winner_team = game.get('winner', 'team1')
            t1_players = game.get('team1_players', [])
            t2_players = game.get('team2_players', [])
            if isinstance(t1_players, str):
                try:
                    t1_players = json.loads(t1_players)
                except Exception:
                    t1_players = []
            if isinstance(t2_players, str):
                try:
                    t2_players = json.loads(t2_players)
                except Exception:
                    t2_players = []
            all_players = t1_players + t2_players
            real_players = [p for p in all_players if not is_guest_player(p)]
            t1_score = game.get("team1_score", 0)
            t2_score = game.get("team2_score", 0)
            total_players = len(t1_players) + len(t2_players)
            mode = '2v2' if total_players >= 4 else '1v1'
            # Charger les ELO actuels
            elos = {}
            for player in real_players:
                q = "SELECT elo FROM users WHERE username = %s" if USE_POSTGRES else "SELECT elo FROM users WHERE username = ?"
                cur.execute(q, (player,))
                row = cur.fetchone()
                elos[player] = (row_to_dict(row) or {}).get('elo') or 1000

            # Calculer les nouveaux ELO (gagnants vs perdants, paire par paire)
            winners = [p for p in real_players if (p in t1_players and winner_team == 'team1') or (p in t2_players and winner_team == 'team2')]
            losers  = [p for p in real_players if p not in winners]
            new_elos = dict(elos)
            if winners and losers:
                avg_w = sum(elos.get(p, 1000) for p in winners) / len(winners)
                avg_l = sum(elos.get(p, 1000) for p in losers)  / len(losers)
                new_w, new_l = compute_elo(avg_w, avg_l)
                delta_w = new_w - avg_w
                delta_l = new_l - avg_l
                for p in winners: new_elos[p] = max(0, round(elos.get(p, 1000) + delta_w))
                for p in losers:  new_elos[p] = max(0, round(elos.get(p, 1000) + delta_l))

            for player in real_players:
                player_score = t1_score if player in t1_players else t2_score
                new_elo = new_elos.get(player, elos.get(player, 1000))
                if USE_POSTGRES:
                    cur.execute("UPDATE users SET total_games = total_games + 1, elo = %s WHERE username = %s", (new_elo, player))
                    if player_score > 0:
                        cur.execute("INSERT INTO scores (username, score) VALUES (%s, %s)", (player, player_score))
                        cur.execute("UPDATE users SET total_goals = total_goals + %s WHERE username = %s", (player_score, player))
                else:
                    cur.execute("UPDATE users SET total_games = total_games + 1, elo = ? WHERE username = ?", (new_elo, player))
                    if player_score > 0:
                        cur.execute("INSERT INTO scores (username, score) VALUES (?, ?)", (player, player_score))
                        cur.execute("UPDATE users SET total_goals = total_goals + ? WHERE username = ?", (player_score, player))
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
            logger.info("Resultats sauvegardes (users + scores + games)")

            # ── Émettre les changements ELO + message félicitations ──
            elo_changes = []
            for player in real_players:
                old_elo = elos.get(player, 1000)
                new_elo = new_elos.get(player, old_elo)
                delta = new_elo - old_elo
                old_tier = elo_tier(old_elo)[0]
                new_tier = elo_tier(new_elo)[0]
                tier_up = (old_tier != new_tier and delta > 0)
                tier_down = (old_tier != new_tier and delta < 0)
                is_winner = player in winners
                elo_changes.append({
                    "player": player,
                    "old_elo": old_elo,
                    "new_elo": new_elo,
                    "delta": delta,
                    "is_winner": is_winner,
                    "tier_up": tier_up,
                    "tier_down": tier_down,
                    "new_tier": new_tier,
                })
            socketio.emit('elo_updated', {"changes": elo_changes}, namespace='/')
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"Erreur save_game_results: {e}")

# ── Point d'entree WSGI ───────────────────────────────────────
# Commande : gunicorn --config gunicorn_config.py app:app
