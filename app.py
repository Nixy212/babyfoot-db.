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
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'babyfoot-secret-key-2024-change-me')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

socketio = SocketIO(app, cors_allowed_origins="*", logger=False, engineio_logger=False, ping_timeout=60, ping_interval=25)

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
else:
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH', 'babyfoot.db')

current_game = {
    "team1_score": 0,
    "team2_score": 0,
    "team1_players": [],
    "team2_players": [],
    "active": False,
    "started_by": None,
    "reserved_by": None,
    "started_at": None
}

active_lobby = {
    "host": None,
    "invited": [],
    "accepted": [],
    "declined": [],
    "team1": [],
    "team2": [],
    "active": False
}

team_swap_requests = {}
rematch_votes = {"team1": [], "team2": []}

def get_db_connection():
    if USE_POSTGRES:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
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
        cur.execute("CREATE TABLE IF NOT EXISTS users (username VARCHAR(50) PRIMARY KEY, password VARCHAR(200) NOT NULL, total_goals INTEGER DEFAULT 0, total_games INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("CREATE TABLE IF NOT EXISTS reservations (id SERIAL PRIMARY KEY, day VARCHAR(20) NOT NULL, time VARCHAR(10) NOT NULL, team1 TEXT[] NOT NULL, team2 TEXT[] NOT NULL, mode VARCHAR(10) DEFAULT '2v2', reserved_by VARCHAR(50) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(day, time))")
        cur.execute("CREATE TABLE IF NOT EXISTS scores (id SERIAL PRIMARY KEY, username VARCHAR(50) NOT NULL, score INTEGER NOT NULL, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE)")
    else:
        cur.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT NOT NULL, total_goals INTEGER DEFAULT 0, total_games INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')))")
        cur.execute("CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, time TEXT NOT NULL, team1 TEXT NOT NULL, team2 TEXT NOT NULL, mode TEXT DEFAULT '2v2', reserved_by TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')), UNIQUE(day, time))")
        cur.execute("CREATE TABLE IF NOT EXISTS scores (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, score INTEGER NOT NULL, date TEXT DEFAULT (datetime('now')))")
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"✅ DB initialisée ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")

def seed_test_accounts():
    test_accounts = [("alice","test123"),("bob","test123"),("charlie","test123"),("diana","test123")]
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for username, password in test_accounts:
            q = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
            cur.execute(q, (username,))
            if not cur.fetchone():
                hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                q2 = "INSERT INTO users (username, password, total_goals, total_games) VALUES (%s, %s, 0, 0)" if USE_POSTGRES else "INSERT INTO users (username, password, total_goals, total_games) VALUES (?, ?, 0, 0)"
                cur.execute(q2, (username, hashed))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"Seed test accounts: {e}")

def seed_admin():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        q = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
        cur.execute(q, ("Imran",))
        if not cur.fetchone():
            hashed = bcrypt.hashpw("imran2024".encode(), bcrypt.gensalt()).decode()
            q2 = "INSERT INTO users (username, password, total_goals, total_games) VALUES (%s, %s, 0, 0)" if USE_POSTGRES else "INSERT INTO users (username, password, total_goals, total_games) VALUES (?, ?, 0, 0)"
            cur.execute(q2, ("Imran", hashed))
            conn.commit()
            logger.info("✅ Compte admin Imran créé")
        cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"Seed admin: {e}")

def seed_admin_accounts():
    admin_accounts = [("Apoutou","admin123"),("Hamara","admin123"),("MDA","admin123")]
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        for username, password in admin_accounts:
            q = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
            cur.execute(q, (username,))
            if not cur.fetchone():
                hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                q2 = "INSERT INTO users (username, password, total_goals, total_games) VALUES (%s, %s, 0, 0)" if USE_POSTGRES else "INSERT INTO users (username, password, total_goals, total_games) VALUES (?, ?, 0, 0)"
                cur.execute(q2, (username, hashed))
                logger.info(f"✅ Compte admin créé: {username}")
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"Seed admin accounts: {e}")

def is_admin(username):
    admin_list = ["Imran", "Apoutou", "Hamara", "MDA"]
    return username in admin_list

def has_active_reservation(username):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        today = datetime.now().strftime('%A')
        days_fr = {
            'Monday': 'Lundi',
            'Tuesday': 'Mardi', 
            'Wednesday': 'Mercredi',
            'Thursday': 'Jeudi',
            'Friday': 'Vendredi',
            'Saturday': 'Samedi',
            'Sunday': 'Dimanche'
        }
        day_fr = days_fr.get(today, today)
        
        q = "SELECT * FROM reservations WHERE reserved_by = %s AND day = %s" if USE_POSTGRES else "SELECT * FROM reservations WHERE reserved_by = ? AND day = ?"
        cur.execute(q, (username, day_fr))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result is not None
    except Exception as e:
        logger.error(f"Erreur has_active_reservation: {e}")
        return False

try:
    init_database()
    seed_test_accounts()
    seed_admin()
    seed_admin_accounts()
except Exception as e:
    logger.error(f"Erreur init DB: {e}")

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
    if not u or not isinstance(u, str): raise ValueError("Nom d'utilisateur requis")
    u = u.strip()
    if len(u) < 3: raise ValueError("Minimum 3 caractères")
    if len(u) > 20: raise ValueError("Maximum 20 caractères")
    if not u.replace('_','').replace('-','').isalnum(): raise ValueError("Lettres, chiffres, - et _ uniquement")
    return u

def validate_password(p):
    if not p or not isinstance(p, str): raise ValueError("Mot de passe requis")
    if len(p) < 6: raise ValueError("Minimum 6 caractères")
    return p

@app.route("/")
def index(): return render_template("index.html")

@app.route("/login")
def login_page(): return render_template("login.html")

@app.route("/register")
def register_page(): return render_template("register.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("dashboard.html")

@app.route("/reservation")
def reservation():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("reservation.html")

@app.route("/lobby")
def lobby_page():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("lobby.html")

@app.route("/live-score")
def live_score():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("live-score.html")

@app.route("/stats")
def stats():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("stats.html")

@app.route("/top")
def top():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("top.html")

@app.route("/scores")
def scores():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("scores.html")

@app.route("/debug-socketio")
def debug_socketio_page():
    return render_template("debug-socketio.html")

@app.route("/debug/game")
def debug_game():
    global current_game, active_lobby
    return jsonify({
        "current_game": current_game,
        "active_lobby": active_lobby,
        "team_swap_requests": team_swap_requests,
        "rematch_votes": rematch_votes,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/api/register", methods=["POST"])
@handle_errors
def api_register():
    data = request.get_json(silent=True)
    if not data: return jsonify({"success": False, "message": "Aucune donnée"}), 400
    username = validate_username(data.get("username", ""))
    password = validate_password(data.get("password", ""))
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
    cur.execute(q, (username,))
    if cur.fetchone():
        cur.close(); conn.close()
        return jsonify({"success": False, "message": "Nom d'utilisateur déjà pris"}), 409
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    q2 = "INSERT INTO users (username, password) VALUES (%s, %s)" if USE_POSTGRES else "INSERT INTO users (username, password) VALUES (?, ?)"
    cur.execute(q2, (username, hashed))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

@app.route("/api/login", methods=["POST"])
@handle_errors
def api_login():
    data = request.get_json(silent=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user: return jsonify({"success": False, "message": "Utilisateur inconnu"}), 401
    user_dict = row_to_dict(user)
    if not bcrypt.checkpw(password.encode(), user_dict["password"].encode()):
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
    if not username: return jsonify(None), 401
    return jsonify({
        "username": username,
        "is_admin": is_admin(username),
        "has_reservation": has_active_reservation(username)
    })

@app.route("/reservations_all")
@handle_errors
def reservations_all():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM reservations ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/leaderboard")
@handle_errors
def leaderboard():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, total_goals, total_games FROM users ORDER BY total_goals DESC LIMIT 10")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/user_stats/<username>")
@handle_errors
def user_stats(username):
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user: return jsonify(None), 404
    return jsonify(row_to_dict(user))

@app.route("/api/is_admin")
def api_is_admin():
    username = session.get('username')
    if not username: return jsonify({"is_admin": False})
    return jsonify({"is_admin": is_admin(username)})

@app.route("/save_reservation", methods=["POST"])
@handle_errors
def save_reservation():
    if "username" not in session: return jsonify({"success": False, "message": "Non authentifié"}), 401
    data = request.get_json(silent=True)
    day, time = data.get("day"), data.get("time")
    team1, team2 = data.get("team1", []), data.get("team2", [])
    mode = data.get("mode", "2v2")
    reserved_by = session.get("username", "unknown")
    if not day or not time: return jsonify({"success": False, "message": "Jour et heure requis"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute("DELETE FROM reservations WHERE day = %s AND time = %s", (day, time))
        cur.execute("INSERT INTO reservations (day, time, team1, team2, mode, reserved_by) VALUES (%s, %s, %s, %s, %s, %s)", (day, time, team1, team2, mode, reserved_by))
    else:
        cur.execute("DELETE FROM reservations WHERE day = ? AND time = ?", (day, time))
        cur.execute("INSERT INTO reservations (day, time, team1, team2, mode, reserved_by) VALUES (?, ?, ?, ?, ?, ?)", (day, time, json.dumps(team1), json.dumps(team2), mode, reserved_by))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

@app.route("/cancel_reservation", methods=["POST"])
@handle_errors
def cancel_reservation():
    if "username" not in session: return jsonify({"success": False, "message": "Non authentifié"}), 401
    data = request.get_json(silent=True)
    day, time = data.get("day"), data.get("time")
    username = session.get("username")
    conn = get_db_connection()
    cur = conn.cursor()
    q = "DELETE FROM reservations WHERE day = %s AND time = %s AND reserved_by = %s" if USE_POSTGRES else "DELETE FROM reservations WHERE day = ? AND time = ? AND reserved_by = ?"
    cur.execute(q, (day, time, username))
    deleted = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": bool(deleted)})

@app.route("/users_list")
@handle_errors
def users_list():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users ORDER BY username ASC")
    rows = cur.fetchall()
    cur.close(); conn.close()
    users = [row_to_dict(r)['username'] for r in rows]
    return jsonify(users)

@app.route("/scores_all")
@handle_errors
def scores_all():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM scores ORDER BY date DESC LIMIT 50")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/api/current_game")
def api_current_game():
    global current_game
    return jsonify(current_game)

@app.route("/api/has_active_game")
def api_has_active_game():
    global current_game
    return jsonify({
        "has_active_game": current_game.get('active', False),
        "game_data": current_game if current_game.get('active') else None
    })

@app.route("/api/active_lobby")
def api_active_lobby():
    global active_lobby
    return jsonify(active_lobby)

@socketio.on('connect')
def handle_connect():
    username = session.get('username', 'Anonymous')
    logger.info(f"WS connecté: {username} ({request.sid})")
    if current_game.get('active'):
        join_room('game')
        emit('game_recovery', current_game)
    if active_lobby.get('active'):
        join_room('lobby')
        emit('lobby_update', active_lobby)

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"WS déconnecté: {request.sid}")

@socketio.on('create_lobby')
def handle_create_lobby(data):
    global active_lobby
    username = session.get('username')
    
    if not is_admin(username) and not has_active_reservation(username):
        emit('error', {'message': 'Seuls admins/réservateurs peuvent créer un lobby'})
        return
    
    invited_users = data.get('invited', [])
    
    active_lobby = {
        "host": username,
        "invited": invited_users,
        "accepted": [username],
        "declined": [],
        "team1": [username],
        "team2": [],
        "active": True
    }
    
    logger.info(f"Lobby créé par {username}, invités: {invited_users}")
    
    socketio.emit('lobby_created', {
        'host': username,
        'invited': invited_users
    }, namespace='/')
    
    for user in invited_users:
        socketio.emit('lobby_invitation', {
            'from': username,
            'to': user
        }, namespace='/')


@socketio.on('invite_to_lobby')
def handle_invite_to_lobby(data):
    global active_lobby
    invited_user = data.get('user')
    
    if invited_user and invited_user not in active_lobby['invited']:
        active_lobby['invited'].append(invited_user)
        
        socketio.emit('lobby_invitation', {
            'from': active_lobby['host'],
            'to': invited_user
        }, namespace='/')
@socketio.on('accept_lobby')
def handle_accept_lobby():
    global active_lobby
    username = session.get('username')
    
    if username not in active_lobby['invited']:
        return
    
    if username not in active_lobby['accepted']:
        active_lobby['accepted'].append(username)
        
        if len(active_lobby['team1']) <= len(active_lobby['team2']):
            active_lobby['team1'].append(username)
        else:
            active_lobby['team2'].append(username)
    
    logger.info(f"{username} a accepté le lobby")
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('decline_lobby')
def handle_decline_lobby():
    global active_lobby
    username = session.get('username')
    
    if username not in active_lobby['invited']:
        return
    
    if username not in active_lobby['declined']:
        active_lobby['declined'].append(username)
    
    logger.info(f"{username} a refusé le lobby")
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('request_team_swap')
def handle_request_team_swap(data):
    global team_swap_requests
    from_user = session.get('username')
    to_user = data.get('with')
    
    request_id = f"{from_user}_{to_user}"
    team_swap_requests[request_id] = {
        'from': from_user,
        'to': to_user
    }
    
    socketio.emit('team_swap_request', {
        'from': from_user,
        'to': to_user,
        'request_id': request_id
    }, namespace='/')

@socketio.on('accept_team_swap')
def handle_accept_team_swap(data):
    global active_lobby, team_swap_requests
    request_id = data.get('request_id')
    
    if request_id not in team_swap_requests:
        return
    
    swap = team_swap_requests.pop(request_id)
    from_user = swap['from']
    to_user = swap['to']
    
    if from_user in active_lobby['team1'] and to_user in active_lobby['team2']:
        active_lobby['team1'].remove(from_user)
        active_lobby['team2'].remove(to_user)
        active_lobby['team1'].append(to_user)
        active_lobby['team2'].append(from_user)
    elif from_user in active_lobby['team2'] and to_user in active_lobby['team1']:
        active_lobby['team2'].remove(from_user)
        active_lobby['team1'].remove(to_user)
        active_lobby['team2'].append(to_user)
        active_lobby['team1'].append(from_user)
    
    logger.info(f"Échange équipe: {from_user} ↔ {to_user}")
    socketio.emit('lobby_update', active_lobby, namespace='/')

@socketio.on('decline_team_swap')
def handle_decline_team_swap(data):
    global team_swap_requests
    request_id = data.get('request_id')
    
    if request_id in team_swap_requests:
        team_swap_requests.pop(request_id)

@socketio.on('cancel_lobby')
def handle_cancel_lobby():
    global active_lobby
    username = session.get('username')
    
    if username != active_lobby['host'] and not is_admin(username):
        emit('error', {'message': 'Seul l\'hôte ou un admin peut annuler'})
        return
    
    active_lobby = {
        "host": None,
        "invited": [],
        "accepted": [],
        "declined": [],
        "team1": [],
        "team2": [],
        "active": False
    }
    
    logger.info(f"Lobby annulé par {username}")
    socketio.emit('lobby_cancelled', {}, namespace='/')

@socketio.on('start_game_from_lobby')
def handle_start_game_from_lobby():
    global current_game, active_lobby, rematch_votes
    username = session.get('username')
    
    if username != active_lobby['host'] and not is_admin(username):
        emit('error', {'message': 'Seul l\'hôte ou un admin peut lancer'})
        return
    
    if len(active_lobby['accepted']) < 2:
        emit('error', {'message': 'Au moins 2 joueurs requis'})
        return
    
    reserved_by = None
    if has_active_reservation(username):
        reserved_by = username
    
    current_game = {
        "team1_score": 0,
        "team2_score": 0,
        "team1_players": active_lobby['team1'],
        "team2_players": active_lobby['team2'],
        "active": True,
        "started_by": username,
        "reserved_by": reserved_by,
        "started_at": datetime.now().isoformat()
    }
    
    active_lobby = {
        "host": None,
        "invited": [],
        "accepted": [],
        "declined": [],
        "team1": [],
        "team2": [],
        "active": False
    }
    
    rematch_votes = {"team1": [], "team2": []}
    
    logger.info(f"Partie lancée depuis lobby par {username}")
    socketio.emit('game_started', current_game, namespace='/')

@socketio.on('start_game')
def handle_start_game(data):
    global current_game, rematch_votes
    
    try:
        username = session.get('username', '')
        
        if not is_admin(username) and not has_active_reservation(username):
            emit('error', {'message': 'Vous devez avoir une réservation active ou être admin'})
            return
        
        team1 = [p for p in data.get('team1', []) if p and p.strip()]
        team2 = [p for p in data.get('team2', []) if p and p.strip()]
        
        if not team1 or not team2:
            emit('error', {'message': 'Chaque équipe doit avoir au moins un joueur'})
            return
        
        if current_game.get('active'):
            emit('error', {'message': 'Une partie est déjà en cours'})
            return
        
        reserved_by = None
        if has_active_reservation(username):
            reserved_by = username
        
        current_game = {
            "team1_score": 0,
            "team2_score": 0,
            "team1_players": team1,
            "team2_players": team2,
            "active": True,
            "started_by": username,
            "reserved_by": reserved_by,
            "started_at": datetime.now().isoformat()
        }
        
        rematch_votes = {"team1": [], "team2": []}
        
        logger.info(f"Partie démarrée par {username}")
        socketio.emit('game_started', current_game, namespace='/')
    
    except Exception as e:
        logger.error(f"Erreur start_game: {e}")
        emit('error', {'message': str(e)})

@socketio.on('unlock_servo')
def handle_unlock_servo():
    username = session.get('username')
    
    if not username:
        emit('error', {'message': 'Non authentifié'})
        return
    
    can_unlock = is_admin(username) or (current_game.get('reserved_by') == username)
    
    if not can_unlock:
        emit('error', {'message': 'Seuls admins et réservateur peuvent débloquer'})
        return
    
    logger.info(f"Déverrouillage servo par {username}")
    socketio.emit('servo_unlock', {}, namespace='/')

@socketio.on('stop_game')
def handle_stop_game():
    global current_game, rematch_votes
    
    username = session.get('username')
    
    if not is_admin(username):
        emit('error', {'message': 'Seuls les admins peuvent arrêter'})
        return
    
    logger.info(f"Partie arrêtée par admin {username}")
    
    current_game = {
        "team1_score": 0,
        "team2_score": 0,
        "team1_players": [],
        "team2_players": [],
        "active": False,
        "started_by": None,
        "reserved_by": None
    }
    
    rematch_votes = {"team1": [], "team2": []}
    
    socketio.emit('game_stopped', {}, namespace='/')
    socketio.emit('servo_lock', {}, namespace='/')

@socketio.on('update_score')
def handle_score(data):
    global current_game
    
    try:
        if not current_game.get('active'):
            emit('error', {'message': 'Aucune partie en cours'})
            return
        
        team = data.get('team')
        if team not in ['team1', 'team2']:
            emit('error', {'message': 'Équipe invalide'})
            return
        
        current_game[f"{team}_score"] += 1
        logger.info(f"Score: Team1={current_game['team1_score']} Team2={current_game['team2_score']}")
        
        if current_game[f"{team}_score"] >= 10:
            current_game['winner'] = team
            current_game['active'] = False
            
            logger.info(f"Victoire de {team} !")
            
            try:
                save_game_results(current_game)
            except Exception as e:
                logger.error(f"Save error: {e}")
            
            socketio.emit('game_ended', current_game, namespace='/')
            
            import threading
            def ask_rematch():
                import time
                time.sleep(2)
                socketio.emit('rematch_prompt', {}, namespace='/')
            threading.Thread(target=ask_rematch, daemon=True).start()
        else:
            socketio.emit('score_updated', current_game, namespace='/')
    
    except Exception as e:
        logger.error(f"Erreur update_score: {e}")
        emit('error', {'message': str(e)})

@socketio.on('vote_rematch')
def handle_vote_rematch(data):
    global rematch_votes, current_game
    
    username = session.get('username')
    vote = data.get('vote')
    
    if vote == 'no':
        logger.info(f"{username} a voté NON pour le rematch")
        socketio.emit('rematch_cancelled', {}, namespace='/')
        rematch_votes = {"team1": [], "team2": []}
        return
    
    team = None
    if username in current_game.get('team1_players', []):
        team = 'team1'
    elif username in current_game.get('team2_players', []):
        team = 'team2'
    
    if not team:
        emit('error', {'message': 'Pas dans cette partie'})
        return
    
    if username not in rematch_votes[team]:
        rematch_votes[team].append(username)
    
    logger.info(f"{username} a voté OUI pour le rematch")
    
    team1_all = len(rematch_votes['team1']) == len(current_game['team1_players'])
    team2_all = len(rematch_votes['team2']) == len(current_game['team2_players'])
    
    if team1_all and team2_all:
        logger.info("Rematch lancé !")
        
        current_game = {
            "team1_score": 0,
            "team2_score": 0,
            "team1_players": current_game['team1_players'],
            "team2_players": current_game['team2_players'],
            "active": True,
            "started_by": current_game.get('started_by'),
            "reserved_by": current_game.get('reserved_by'),
            "started_at": datetime.now().isoformat()
        }
        
        rematch_votes = {"team1": [], "team2": []}
        socketio.emit('game_started', current_game, namespace='/')

def save_game_results(game):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        winner_team = game.get('winner', 'team1')
        winners = game.get(f"{winner_team}_players", [])
        losers_team = 'team2' if winner_team == 'team1' else 'team1'
        losers = game.get(f"{losers_team}_players", [])
        
        for player in winners + losers:
            q_update = "UPDATE users SET total_games = total_games + 1 WHERE username = %s" if USE_POSTGRES else "UPDATE users SET total_games = total_games + 1 WHERE username = ?"
            cur.execute(q_update, (player,))
        
        winner_score = game.get(f"{winner_team}_score", 0)
        for player in winners:
            q_goals = "UPDATE users SET total_goals = total_goals + %s WHERE username = %s" if USE_POSTGRES else "UPDATE users SET total_goals = total_goals + ? WHERE username = ?"
            cur.execute(q_goals, (winner_score, player))
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Résultats sauvegardés")
    
    except Exception as e:
        logger.error(f"Erreur save_game_results: {e}")

@socketio.on('reset_game')
def handle_reset():
    global current_game, rematch_votes
    username = session.get('username')
    
    if not is_admin(username):
        emit('error', {'message': 'Seuls les admins peuvent reset'})
        return
    
    current_game = {
        "team1_score": 0,
        "team2_score": 0,
        "team1_players": [],
        "team2_players": [],
        "active": False
    }
    
    rematch_votes = {"team1": [], "team2": []}
    socketio.emit('game_reset', current_game, namespace='/')
    logger.info(f"Partie reset par {username}")

@socketio.on('arduino_goal')
def handle_arduino_goal(data):
    global current_game
    
    logger.info(f"🤖 Arduino BUT reçu - Data: {data}")
    logger.info(f"   Match actif: {current_game.get('active', False)}")
    logger.info(f"   Scores actuels: T1={current_game.get('team1_score', 0)} T2={current_game.get('team2_score', 0)}")
    
    try:
        if not current_game.get('active'):
            logger.warning("❌ But ignoré - Aucune partie en cours")
            return
        
        team = data.get('team')
        
        if team not in ['team1', 'team2']:
            logger.warning(f"❌ Équipe invalide: {team}")
            return
        
        current_game[f"{team}_score"] += 1
        
        logger.info(f"✅ BUT VALIDÉ ! Nouveau score: T1={current_game['team1_score']} T2={current_game['team2_score']}")
        
        if current_game[f"{team}_score"] >= 10:
            current_game['winner'] = team
            current_game['active'] = False
            
            logger.info(f"🏆 VICTOIRE DE {team} !")
            
            try:
                save_game_results(current_game)
                logger.info("💾 Résultats sauvegardés")
            except Exception as e:
                logger.error(f"Erreur sauvegarde: {e}")
            
            socketio.emit('game_ended', current_game, namespace='/')
            
            import threading
            def lock_and_rematch():
                import time
                time.sleep(2)
                socketio.emit('servo_lock', {}, namespace='/')
                logger.info("🔒 Servo verrouillé")
                time.sleep(1)
                socketio.emit('rematch_prompt', {}, namespace='/')
            threading.Thread(target=lock_and_rematch, daemon=True).start()
        
        else:
            socketio.emit('score_updated', current_game, namespace='/')
            logger.info("📊 Score diffusé")
    
    except Exception as e:
        logger.error(f"❌ ERREUR arduino_goal: {e}")
        logger.error(traceback.format_exc())

@socketio.on('arduino_ping')
def handle_arduino_ping(data):
    socketio.emit('arduino_pong', {'status': 'ok'}, namespace='/')

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
