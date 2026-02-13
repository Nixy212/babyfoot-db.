# 🚀 GUIDE DÉPLOIEMENT RENDER AVEC POSTGRESQL

## ✅ CHANGEMENTS APPORTÉS

### Avant (JSON) ❌
- Fichiers JSON dans `/data`
- Données **perdues à chaque redémarrage**
- Pas de sauvegardes automatiques

### Après (PostgreSQL) ✅
- Base de données PostgreSQL
- Données **persistantes**
- Sauvegardes automatiques Render
- **GRATUIT** sur Render

## 📋 ÉTAPES DE DÉPLOIEMENT

### 1️⃣ Créer la base de données PostgreSQL

1. Va sur [Render.com](https://render.com)
2. Clique sur **"New +"** → **"PostgreSQL"**
3. Configure :
   - **Name** : `babyfoot-db` (ou autre nom)
   - **Database** : `babyfoot`
   - **User** : (auto-généré)
   - **Region** : Choisis le plus proche
   - **Plan** : **FREE** 
4. Clique sur **"Create Database"**
5. **IMPORTANT** : Copie l'**Internal Database URL** (commence par `postgres://`)

### 2️⃣ Créer le Web Service

1. Sur Render, clique **"New +"** → **"Web Service"**
2. Connecte ton dépôt GitHub
3. Configure :
   - **Name** : `babyfoot-club`
   - **Environment** : `Python 3`
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app`
   - **Plan** : **FREE**

### 3️⃣ Configurer les variables d'environnement

Dans les **Environment Variables** de ton Web Service, ajoute :

```
DATABASE_URL = postgres://... (colle l'URL de l'étape 1)
SECRET_KEY = ton-secret-key-super-secure-ici
```

**IMPORTANT** : Si l'URL commence par `postgres://`, elle sera automatiquement convertie en `postgresql://`

### 4️⃣ Déployer

1. Clique sur **"Create Web Service"**
2. Attends le déploiement (2-3 minutes)
3. Les tables seront créées automatiquement au premier lancement ! ✅

## 🗄️ STRUCTURE DE LA BASE DE DONNÉES

### Table `users`
```sql
username VARCHAR(50) PRIMARY KEY
password VARCHAR(100)
total_points INTEGER
total_games INTEGER
created_at TIMESTAMP
```

### Table `reservations`
```sql
id SERIAL PRIMARY KEY
day VARCHAR(20)
time VARCHAR(10)
team1 TEXT[]
team2 TEXT[]
mode VARCHAR(10)
reserved_by VARCHAR(50)
created_at TIMESTAMP
UNIQUE(day, time)
```

### Table `scores`
```sql
id SERIAL PRIMARY KEY
username VARCHAR(50) FOREIGN KEY → users(username)
score INTEGER
date TIMESTAMP
```

## 🔧 MODIFICATIONS DU CODE

### `app.py`
- ✅ Remplace `load_json()` / `save_json()` par des requêtes SQL
- ✅ Utilise `psycopg2` pour PostgreSQL
- ✅ Auto-initialisation des tables au démarrage
- ✅ Gestion d'erreurs PostgreSQL (`IntegrityError`, etc.)

### `requirements.txt`
- ➕ `psycopg2-binary==2.9.9` (driver PostgreSQL)

### Pas de changements
- ❌ `main.js` - inchangé
- ❌ `templates/` - inchangés
- ❌ `static/` - inchangé
- ❌ API routes - **même format JSON en réponse**

## ✅ AVANTAGES POSTGRESQL

| Feature | JSON (Avant) | PostgreSQL (Après) |
|---------|--------------|-------------------|
| Données persistantes | ❌ Perdues au redémarrage | ✅ Toujours sauvegardées |
| Sauvegardes auto | ❌ Non | ✅ Quotidiennes (Render) |
| Transactions | ❌ Non | ✅ ACID compliant |
| Requêtes complexes | ❌ Difficile | ✅ SQL puissant |
| Scalabilité | ❌ Limitée | ✅ Millions de lignes |
| Coût | Gratuit | ✅ Gratuit (plan FREE) |

## 🧪 TESTER LA BASE DE DONNÉES

### Vérifier la connexion
```bash
# Sur Render, va dans les logs de ton Web Service
# Tu devrais voir :
✅ Base de données initialisée
```

### Tester l'API Health Check
```bash
curl https://ton-app.onrender.com/health
```

Réponse attendue :
```json
{
  "status": "healthy",
  "timestamp": "2024-02-04T19:30:00",
  "checks": {
    "database": "OK"
  }
}
```

## 🔄 MIGRATION DES DONNÉES (si tu en as déjà)

Si tu as des utilisateurs/réservations dans des fichiers JSON :

### Script de migration (à exécuter localement)

```python
import json
import psycopg2

# Connexion à la DB Render
DATABASE_URL = "postgres://..."  # Ton URL Render
conn = psycopg2.connect(DATABASE_URL.replace('postgres://', 'postgresql://', 1))
cur = conn.cursor()

# Migrer users.json
with open('data/users.json') as f:
    users = json.load(f)
    for username, data in users.items():
        cur.execute(
            "INSERT INTO users (username, password, total_points, total_games) VALUES (%s, %s, %s, %s)",
            (username, data['password'], data['total_points'], data['total_games'])
        )

# Migrer reservations.json
with open('data/reservations.json') as f:
    reservations = json.load(f)
    for day, times in reservations.items():
        for time, data in times.items():
            cur.execute(
                "INSERT INTO reservations (day, time, team1, team2, mode, reserved_by) VALUES (%s, %s, %s, %s, %s, %s)",
                (day, time, data['team1'], data['team2'], data['mode'], data['reserved_by'])
            )

# Migrer scores.json
with open('data/scores.json') as f:
    scores = json.load(f)
    for username, user_scores in scores.items():
        for score in user_scores:
            cur.execute(
                "INSERT INTO scores (username, score, date) VALUES (%s, %s, %s)",
                (username, score['score'], score['date'])
            )

conn.commit()
cur.close()
conn.close()
print("✅ Migration terminée !")
```

## 🛠️ COMMANDES UTILES

### Accéder à la base de données
1. Va sur Render → Ta database → Onglet **"Connect"**
2. Utilise l'outil **PSQL** ou **pgAdmin**

### Voir toutes les tables
```sql
\dt
```

### Voir les utilisateurs
```sql
SELECT * FROM users;
```

### Compter les réservations
```sql
SELECT COUNT(*) FROM reservations;
```

### Supprimer toutes les données (DANGER ⚠️)
```sql
TRUNCATE users CASCADE;
TRUNCATE reservations CASCADE;
TRUNCATE scores CASCADE;
```

## 📊 MONITORING

### Logs Render
- Va sur ton Web Service → **Logs**
- Cherche les messages :
  - `✅ Base de données initialisée`
  - `✅ Connexion: username`
  - `✅ Réservation: ...`
  - `⚽ But team1: ...`

### Métriques PostgreSQL
- Va sur ta Database → **Metrics**
- Surveille :
  - Nombre de connexions
  - Taille de la base
  - Requêtes par seconde

## 🚨 DÉPANNAGE

### Erreur "relation does not exist"
→ Les tables ne sont pas créées. Redémarre le Web Service.

### Erreur "could not connect to server"
→ Vérifie que `DATABASE_URL` est bien configuré dans les variables d'environnement.

### Erreur "password authentication failed"
→ Vérifie que tu utilises l'**Internal Database URL** (pas l'External).

### Les données disparaissent
→ Si tu utilises encore l'ancien code avec JSON, elles vont disparaître. Utilise la version PostgreSQL !

## ✅ CHECKLIST FINALE

- [ ] PostgreSQL database créée sur Render
- [ ] Web Service créé et connecté au repo
- [ ] `DATABASE_URL` configurée dans les variables d'environnement
- [ ] `SECRET_KEY` configurée
- [ ] Build réussi
- [ ] Logs montrent "✅ Base de données initialisée"
- [ ] `/health` retourne `{"checks": {"database": "OK"}}`
- [ ] Je peux créer un compte
- [ ] Je peux me connecter
- [ ] Les données persistent après redémarrage

## 🎉 C'EST TOUT !

Ton application est maintenant **production-ready** avec :
- ✅ Données persistantes
- ✅ Sauvegardes automatiques
- ✅ Scalable
- ✅ Gratuit
- ✅ Professionnel

Bon jeu de babyfoot ! ⚽🎮
