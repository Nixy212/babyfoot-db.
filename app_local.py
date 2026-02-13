# Version LOCAL simplifiée - Sans PostgreSQL
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit
from datetime import datetime, timedelta
from pathlib import Path
import json
import bcrypt

app = Flask(__name__)
app.secret_key = 'local-test-key'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

USERS_FILE = "data/users.json"
RESERVATIONS_FILE = "data/reservations.json"
SCORES_FILE = "data/scores.json"

current_game = {"team1_score": 0, "team2_score": 0, "team1_players": [], "team2_players": [], "active": False}

def load_json(f):
    if Path(f).exists():
        with open(f, "r", encoding='utf-8') as file:
            try: return json.load(file)
            except: return {}
    return {}

def save_json(f, d):
    Path(f).parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding='utf-8') as file:
        json.dump(d, file, indent=4, ensure_ascii=False)

@app.route("/")
def index(): return render_template("index.html")

@app.route("/login")
def login_page(): return render_template("login.html")

@app.route("/register")
def register_page(): return render_template("register.html")

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
def api_register():
    data = request.json
    u, p = data.get("username"), data.get("password")
    if not u or not p: return jsonify({"success": False, "message": "Champs vides"})
    users = load_json(USERS_FILE)
    if u in users: return jsonify({"success": False, "message": "Nom déjà pris"})
    users[u] = {"password": bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode(), "total_points": 0, "total_games": 0}
    save_json(USERS_FILE, users)
    session["username"] = u
    return jsonify({"success": True})

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json
    users = load_json(USERS_FILE)
    u, p = data.get("username"), data.get("password")
    if u in users and bcrypt.checkpw(p.encode(), users[u]["password"].encode()):
        session["username"] = u
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Identifiants incorrects"})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/all_users")
def get_users(): return jsonify(list(load_json(USERS_FILE).keys()))

@app.route("/current_user")
def get_current(): return jsonify({"username": session.get("username", "")})

@app.route("/reservations_all")
def get_res(): return jsonify(load_json(RESERVATIONS_FILE))

@app.route("/reserve_slot", methods=["POST"])
def reserve():
    data = request.json
    res = load_json(RESERVATIONS_FILE)
    day, time = data.get("day"), data.get("time")
    if day not in res: res[day] = {}
    res[day][time] = {"time": time, "team1": data.get("team1", []), "team2": data.get("team2", []), "mode": data.get("mode", "2v2")}
    save_json(RESERVATIONS_FILE, res)
    return jsonify({"success": True})

@app.route("/scores_all")
def get_scores(): return jsonify(load_json(SCORES_FILE))

@app.route("/user_stats/<username>")
def user_stats(username):
    scores, users = load_json(SCORES_FILE), load_json(USERS_FILE)
    user_scores, user_data = scores.get(username, []), users.get(username, {})
    vals = [s.get('score', 0) if isinstance(s, dict) else s for s in user_scores]
    return jsonify({"total_games": user_data.get("total_games", 0), "total_points": user_data.get("total_points", 0), 
                    "best_score": max(vals) if vals else 0, "average_score": round(sum(vals)/len(vals), 1) if vals else 0})

@app.route("/leaderboard")
def leaderboard():
    users = load_json(USERS_FILE)
    lb = [{"username": u, "total_points": d.get("total_points", 0), "total_games": d.get("total_games", 0)} for u, d in users.items()]
    return jsonify(sorted(lb, key=lambda x: x['total_points'], reverse=True)[:10])

@socketio.on('connect')
def handle_connect(): emit('connection_response', {'status': 'connected'})

@socketio.on('start_game')
def handle_start_game(data):
    global current_game
    current_game = {"team1_score": 0, "team2_score": 0, "team1_players": data.get('team1', []), 
                    "team2_players": data.get('team2', []), "active": True}
    emit('game_started', current_game, broadcast=True)

@socketio.on('update_score')
def handle_score(data):
    global current_game
    if not current_game.get('active'): return
    team = data.get('team')
    if team in ['team1', 'team2']:
        current_game[f"{team}_score"] += 1
        if current_game[f"{team}_score"] >= 10:
            current_game['winner'] = team
            current_game['active'] = False
            scores, users = load_json(SCORES_FILE), load_json(USERS_FILE)
            for player in current_game[f"{team}_players"]:
                if player and player.strip():
                    if player not in scores: scores[player] = []
                    scores[player].append({"score": current_game[f"{team}_score"], "date": datetime.now().isoformat()})
                    if player in users:
                        users[player]["total_points"] = users[player].get("total_points", 0) + current_game[f"{team}_score"]
                        users[player]["total_games"] = users[player].get("total_games", 0) + 1
            save_json(SCORES_FILE, scores)
            save_json(USERS_FILE, users)
            emit('game_ended', current_game, broadcast=True)
        else:
            emit('score_updated', current_game, broadcast=True)

@socketio.on('reset_game')
def handle_reset():
    global current_game
    current_game = {"team1_score": 0, "team2_score": 0, "team1_players": [], "team2_players": [], "active": False}
    emit('game_reset', current_game, broadcast=True)

if __name__ == "__main__":
    print("🚀 Serveur local: http://127.0.0.1:5000")
    socketio.run(app, host="127.0.0.1", port=5000, debug=True)
