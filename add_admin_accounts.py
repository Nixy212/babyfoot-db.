#!/usr/bin/env python3
"""
Script pour ajouter les comptes admin : Apoutou, Hamara, MDA
Mot de passe par défaut pour tous : "admin123"
(À changer après la première connexion)
"""

import bcrypt
import os
import sys

# Import des fonctions de connexion DB depuis app.py
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    def get_db_connection():
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
else:
    import sqlite3
    def get_db_connection():
        conn = sqlite3.connect('babyfoot.db')
        conn.row_factory = sqlite3.Row
        return conn

# Nouveaux comptes admin à créer
ADMIN_ACCOUNTS = [
    {"username": "Apoutou", "password": "admin123"},
    {"username": "Hamara", "password": "admin123"},
    {"username": "MDA", "password": "admin123"}
]

def create_admin_accounts():
    """Créer les comptes admin dans la base de données"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    created_count = 0
    
    for account in ADMIN_ACCOUNTS:
        username = account["username"]
        password = account["password"]
        
        # Vérifier si le compte existe déjà
        q = "SELECT username FROM users WHERE username = %s" if USE_POSTGRES else "SELECT username FROM users WHERE username = ?"
        cur.execute(q, (username,))
        
        if cur.fetchone():
            print(f"⚠️  Le compte '{username}' existe déjà - ignoré")
            continue
        
        # Hasher le mot de passe
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        
        # Créer le compte
        q = "INSERT INTO users (username, password, total_goals, total_games) VALUES (%s, %s, 0, 0)" if USE_POSTGRES else "INSERT INTO users (username, password, total_goals, total_games) VALUES (?, ?, 0, 0)"
        cur.execute(q, (username, hashed))
        
        print(f"✅ Compte admin créé : {username}")
        created_count += 1
    
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"\n🎉 {created_count} nouveau(x) compte(s) admin créé(s) !")
    print("\n📝 Informations de connexion :")
    print("   Username : Apoutou, Hamara, ou MDA")
    print("   Password : admin123")
    print("\n⚠️  IMPORTANT : Changez ces mots de passe après la première connexion !")

if __name__ == "__main__":
    try:
        print("🔧 Création des comptes admin...")
        print("=" * 50)
        create_admin_accounts()
    except Exception as e:
        print(f"\n❌ Erreur : {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
