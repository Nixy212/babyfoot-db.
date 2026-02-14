from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta
from functools import wraps
import json
import bcrypt
import os
import logging
import traceback

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler()])
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
    logger.info(f"Mode SQLite: {DB_PATH}")

current_game = {"team1_score": 0, "team2_score": 0, "team1_players": [], "team2_players": [], "active": False}
arduino_simulated = True

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
                logger.info(f"✅ Compte test créé: {username}")
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        logger.warning(f"Seed test accounts: {e}")

def seed_admin():
    """Créer le compte admin Imran avec accès total"""
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
        logger.warning(f"seed_admin: {e}")

def seed_admin_accounts():
    """Créer les comptes admin : Apoutou, Hamara, MDA avec mot de passe par défaut"""
    admin_accounts = [
        ("Apoutou", "admin123"),
        ("Hamara", "admin123"),
        ("MDA", "admin123")
    ]
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
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"seed_admin_accounts: {e}")

try:
    init_database()
    seed_test_accounts()
    seed_admin()
    seed_admin_accounts()  # Créer les nouveaux comptes admin
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

def is_admin(username):
    ADMIN_USERS = ["Imran", "Apoutou", "Hamara", "MDA"]
    return username in ADMIN_USERS

@app.route("/")
def index(): return render_template("index.html")

@app.route("/login")
def login_page():
    if "username" in session: return redirect(url_for('dashboard'))
    return render_template("login.html")

@app.route("/register")
def register_page():
    if "username" in session: return redirect(url_for('dashboard'))
    return render_template("register.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("dashboard.html", username=session.get('username'))

@app.route("/reservation")
def reservation_page():
    if "username" not in session: return redirect(url_for('login_page'))
    return render_template("reservation.html")

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
        return jsonify({"success": False, "message": "Ce nom d'utilisateur est déjà pris"}), 409
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    q = "INSERT INTO users (username, password, total_goals, total_games) VALUES (%s, %s, 0, 0)" if USE_POSTGRES else "INSERT INTO users (username, password, total_goals, total_games) VALUES (?, ?, 0, 0)"
    cur.execute(q, (username, hashed))
    conn.commit(); cur.close(); conn.close()
    session["username"] = username
    session.permanent = True
    return jsonify({"success": True})

@app.route("/api/login", methods=["POST"])
@handle_errors
def api_login():
    data = request.get_json(silent=True)
    if not data: return jsonify({"success": False, "message": "Aucune donnée"}), 400
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password: return jsonify({"success": False, "message": "Identifiants manquants"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: return jsonify({"success": False, "message": "Identifiants incorrects"}), 401
    user = row_to_dict(row)
    if bcrypt.checkpw(password.encode(), user['password'].encode()):
        session["username"] = username
        session.permanent = True
        return jsonify({"success": True, "is_admin": is_admin(username)})
    return jsonify({"success": False, "message": "Identifiants incorrects"}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/is_admin")
def api_is_admin():
    username = session.get("username", "")
    return jsonify({"is_admin": is_admin(username)})

@app.route("/all_users")
@handle_errors
def get_users():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users ORDER BY username")
    users = [row[0] for row in cur.fetchall()]
    cur.close(); conn.close()
    return jsonify(users)

@app.route("/current_user")
def get_current(): return jsonify({"username": session.get("username", "")})

@app.route("/reservations_all")
@handle_errors
def get_res():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM reservations ORDER BY created_at DESC")
    rows = cur.fetchall(); cur.close(); conn.close()
    result = {}
    for row in rows:
        r = row_to_dict(row)
        day, time = r['day'], r['time']
        if day not in result: result[day] = {}
        t1, t2 = r['team1'], r['team2']
        if isinstance(t1, str):
            try: t1 = json.loads(t1)
            except: t1 = [t1]
        if isinstance(t2, str):
            try: t2 = json.loads(t2)
            except: t2 = [t2]
        result[day][time] = {"time": time, "team1": t1, "team2": t2, "mode": r['mode'], "reserved_by": r['reserved_by']}
    return jsonify(result)

@app.route("/reserve_slot", methods=["POST"])
@handle_errors
def reserve():
    if "username" not in session: return jsonify({"success": False, "message": "Non authentifié"}), 401
    data = request.get_json(silent=True)
    if not data: return jsonify({"success": False, "message": "Aucune donnée"}), 400
    day, time = data.get("day"), data.get("time")
    team1 = [p for p in data.get("team1", []) if p and str(p).strip()]
    team2 = [p for p in data.get("team2", []) if p and str(p).strip()]
    mode = data.get("mode", "2v2")
    reserved_by = session.get("username", "unknown")
    if not day or not time: return jsonify({"success": False, "message": "Jour et heure requis"}), 400
    # Les équipes peuvent être vides (définies lors du lancement)
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

@app.route("/scores_all")
@handle_errors
def get_scores():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, score, date FROM scores ORDER BY date DESC")
    rows = cur.fetchall(); cur.close(); conn.close()
    result = {}
    for row in rows:
        r = row_to_dict(row)
        u = r['username']
        if u not in result: result[u] = []
        result[u].append({"score": r['score'], "date": str(r['date'])})
    return jsonify(result)

@app.route("/user_stats/<username>")
@handle_errors
def user_stats(username):
    conn = get_db_connection()
    cur = conn.cursor()
    q = "SELECT * FROM users WHERE username = %s" if USE_POSTGRES else "SELECT * FROM users WHERE username = ?"
    cur.execute(q, (username,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return jsonify({"total_games": 0, "total_goals": 0, "ratio": 0, "best_score": 0, "average_score": 0, "recent_scores": []})
    user = row_to_dict(row)
    q = "SELECT score, date FROM scores WHERE username = %s ORDER BY date DESC LIMIT 20" if USE_POSTGRES else "SELECT score, date FROM scores WHERE username = ? ORDER BY date DESC LIMIT 20"
    cur.execute(q, (username,))
    score_rows = [row_to_dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    vals = [s['score'] for s in score_rows]
    total_goals = user.get('total_goals', 0)
    total_games = user.get('total_games', 0)
    ratio = round(total_goals / total_games, 2) if total_games > 0 else 0
    return jsonify({
        "total_games": total_games,
        "total_goals": total_goals,
        "ratio": ratio,
        "best_score": max(vals) if vals else 0,
        "average_score": round(sum(vals)/len(vals), 1) if vals else 0,
        "recent_scores": [{"score": s['score'], "date": str(s['date'])} for s in score_rows]
    })

@app.route("/leaderboard")
@handle_errors
def leaderboard():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username, total_goals, total_games FROM users ORDER BY total_goals DESC LIMIT 10")
    rows = cur.fetchall(); cur.close(); conn.close()
    result = []
    for r in rows:
        row_dict = row_to_dict(r)
        total_goals = row_dict.get('total_goals', 0)
        total_games = row_dict.get('total_games', 0)
        ratio = round(total_goals / total_games, 2) if total_games > 0 else 0
        result.append({'username': row_dict['username'], 'total_goals': total_goals, 'total_games': total_games, 'ratio': ratio})
    return jsonify(result)

@app.route("/arduino/unlock", methods=["POST"])
@handle_errors
def arduino_unlock():
    if "username" not in session: return jsonify({"success": False, "message": "Non authentifié"}), 401
    username = session.get("username")
    # Émet le signal SocketIO vers l'ESP32
    socketio.emit('servo_unlock', {})
    logger.info(f"Déverrouillage servo via HTTP par {username}")
    return jsonify({"success": True, "message": "Balle déverrouillée !"})

@app.route("/arduino/status")
def arduino_status(): return jsonify({"simulated": arduino_simulated})

@socketio.on('connect')
def handle_connect():
    emit('connection_response', {'status': 'connected', 'client_id': request.sid})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"WS déconnecté: {request.sid}")

@socketio.on('start_game')
def handle_start_game(data):
    global current_game
    try:
        team1 = [p for p in data.get('team1', []) if p and p.strip()]
        team2 = [p for p in data.get('team2', []) if p and p.strip()]
        if not team1 or not team2:
            emit('error', {'message': 'Chaque équipe doit avoir au moins un joueur'}); return
        if current_game.get('active'):
            emit('error', {'message': 'Une partie est déjà en cours'}); return
        current_game = {"team1_score": 0, "team2_score": 0, "team1_players": team1, "team2_players": team2, "active": True, "started_at": datetime.now().isoformat()}
        emit('game_started', current_game, broadcast=True)
    except Exception as e:
        emit('error', {'message': str(e)})

@socketio.on('update_score')
def handle_score(data):
    global current_game
    try:
        if not current_game.get('active'):
            emit('error', {'message': 'Aucune partie en cours'}); return
        team = data.get('team')
        if team not in ['team1', 'team2']:
            emit('error', {'message': 'Équipe invalide'}); return
        current_game[f"{team}_score"] += 1
        if current_game[f"{team}_score"] >= 10:
            current_game['winner'] = team
            current_game['active'] = False
            try: save_game_results(current_game)
            except Exception as e: logger.error(f"Save error: {e}")
            emit('game_ended', current_game, broadcast=True)
            # Fermer le servo automatiquement après 3 secondes
            import threading
            def close_servo():
                import time
                time.sleep(3)
                socketio.emit('servo_lock', {}, broadcast=True)
                logger.info("Servo fermé automatiquement à la fin de la partie")
            threading.Thread(target=close_servo, daemon=True).start()
        else:
            emit('score_updated', current_game, broadcast=True)
    except Exception as e:
        emit('error', {'message': str(e)})

def save_game_results(game):
    conn = get_db_connection()
    cur = conn.cursor()
    wt = game.get('winner')
    if not wt: return
    team1_score   = game.get('team1_score', 0)
    team2_score   = game.get('team2_score', 0)
    team1_players = game.get('team1_players', [])
    team2_players = game.get('team2_players', [])
    team1_goals_per_player = team1_score / len(team1_players) if team1_players else 0
    team2_goals_per_player = team2_score / len(team2_players) if team2_players else 0
    for p in team1_players:
        if p and p.strip():
            if USE_POSTGRES:
                cur.execute("INSERT INTO scores (username, score) VALUES (%s, %s)", (p, team1_score))
                cur.execute("UPDATE users SET total_goals = total_goals + %s, total_games = total_games + 1 WHERE username = %s", (int(team1_goals_per_player), p))
            else:
                cur.execute("INSERT INTO scores (username, score) VALUES (?, ?)", (p, team1_score))
                cur.execute("UPDATE users SET total_goals = total_goals + ?, total_games = total_games + 1 WHERE username = ?", (int(team1_goals_per_player), p))
    for p in team2_players:
        if p and p.strip():
            if USE_POSTGRES:
                cur.execute("INSERT INTO scores (username, score) VALUES (%s, %s)", (p, team2_score))
                cur.execute("UPDATE users SET total_goals = total_goals + %s, total_games = total_games + 1 WHERE username = %s", (int(team2_goals_per_player), p))
            else:
                cur.execute("INSERT INTO scores (username, score) VALUES (?, ?)", (p, team2_score))
                cur.execute("UPDATE users SET total_goals = total_goals + ?, total_games = total_games + 1 WHERE username = ?", (int(team2_goals_per_player), p))
    conn.commit(); cur.close(); conn.close()

@socketio.on('reset_game')
def handle_reset():
    global current_game
    current_game = {"team1_score": 0, "team2_score": 0, "team1_players": [], "team2_players": [], "active": False}
    emit('game_reset', current_game, broadcast=True)

@socketio.on('abandon_game')
def handle_abandon():
    global current_game
    if not session.get('username'):
        emit('error', {'message': 'Non authentifié'}); return
    current_game = {"team1_score": 0, "team2_score": 0, "team1_players": [], "team2_players": [], "active": False}
    emit('game_abandoned', {}, broadcast=True)
    logger.info(f"Partie abandonnée par {session.get('username')}")

@app.route('/api/game_status')
def game_status():
    return jsonify(current_game)

@app.route('/api/force_reset', methods=['POST'])
def force_reset():
    global current_game
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Non authentifié'}), 401
    current_game = {"team1_score": 0, "team2_score": 0, "team1_players": [], "team2_players": [], "active": False}
    socketio.emit('game_abandoned', {})
    logger.info(f"Force reset par {session.get('username')}")
    return jsonify({'success': True})

@socketio.on('arduino_goal')
def handle_arduino_goal(data): handle_score({'team': data.get('team')})

@socketio.on('unlock_servo')
def handle_unlock_servo():
    username = session.get('username')
    if not username:
        emit('error', {'message': 'Non authentifié'}); return
    # Admin peut déverrouiller à tout moment
    if not is_admin(username) and current_game.get('active'):
        emit('error', {'message': 'La partie est encore en cours'}); return
    emit('servo_unlock', {}, broadcast=True)
    logger.info(f"Déverrouillage servo par {username}")

@socketio.on('ping')
def handle_ping(): emit('pong')

@app.route("/health")
def health():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close(); conn.close()
        db_status = "OK"
    except Exception as e:
        db_status = f"ERROR: {e}"
    return jsonify({"status": "healthy" if db_status == "OK" else "unhealthy", "db": db_status, "db_type": "PostgreSQL" if USE_POSTGRES else "SQLite"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
