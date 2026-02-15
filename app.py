# MODIFICATIONS À APPORTER AU FICHIER app.py

# ═══════════════════════════════════════════════════════════════
# MODIFICATION 1 : Ligne 993 - Après game_started depuis lobby
# ═══════════════════════════════════════════════════════════════
# AVANT:
#     logger.info(f"Partie lancée depuis lobby par {username}")
#     socketio.emit('game_started', current_game, namespace='/')

# APRÈS:
    logger.info(f"Partie lancée depuis lobby par {username}")
    socketio.emit('game_started', current_game, namespace='/')
    # Ouvrir les deux servos au démarrage de la partie
    socketio.emit('servo1_unlock', {}, namespace='/')
    socketio.emit('servo2_unlock', {}, namespace='/')
    logger.info("🔓 Servos 1 et 2 déverrouillés au démarrage de la partie")


# ═══════════════════════════════════════════════════════════════
# MODIFICATION 2 : Ligne 1035 - Après game_started dans start_game
# ═══════════════════════════════════════════════════════════════
# AVANT:
#         logger.info(f"Partie démarrée par {username}")
#         socketio.emit('game_started', current_game, namespace='/')

# APRÈS:
        logger.info(f"Partie démarrée par {username}")
        socketio.emit('game_started', current_game, namespace='/')
        # Ouvrir les deux servos au démarrage de la partie
        socketio.emit('servo1_unlock', {}, namespace='/')
        socketio.emit('servo2_unlock', {}, namespace='/')
        logger.info("🔓 Servos 1 et 2 déverrouillés au démarrage de la partie")


# ═══════════════════════════════════════════════════════════════
# MODIFICATION 3 : Ligne 1212 - Après game_started dans rematch
# ═══════════════════════════════════════════════════════════════
# AVANT:
#         rematch_votes = {"team1": [], "team2": []}
#         socketio.emit('game_started', current_game, namespace='/')

# APRÈS:
        rematch_votes = {"team1": [], "team2": []}
        socketio.emit('game_started', current_game, namespace='/')
        # Ouvrir les deux servos au démarrage de la partie
        socketio.emit('servo1_unlock', {}, namespace='/')
        socketio.emit('servo2_unlock', {}, namespace='/')
        logger.info("🔓 Servos 1 et 2 déverrouillés au démarrage de la partie (rematch)")


# ═══════════════════════════════════════════════════════════════
# NOTE IMPORTANTE
# ═══════════════════════════════════════════════════════════════
# La logique de fermeture à 9 buts est DÉJÀ IMPLÉMENTÉE aux lignes 615-617 :
#     if current_game[f"{team}_score"] == 9:
#         servo_adverse = 'servo1' if team == 'team2' else 'servo2'
#         socketio.emit(f"{servo_adverse}_lock", {}, namespace="/")
#
# Cela signifie :
# - Quand l'équipe 2 marque 9 buts → servo1 (équipe 1) se ferme
# - Quand l'équipe 1 marque 9 buts → servo2 (équipe 2) se ferme
