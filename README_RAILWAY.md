# 🚀 Déploiement Railway — Baby-Foot Club

## Variables d'environnement Railway

| Variable | Valeur | Description |
|----------|--------|-------------|
| `SECRET_KEY` | (générer un secret aléatoire) | Clé de session Flask |
| `DATABASE_URL` | (automatique avec PostgreSQL plugin) | URL de la base de données |
| `PORT` | (automatique Railway) | Port d'écoute |

## Instructions Railway Free

1. Créez un nouveau projet Railway
2. Connectez votre repo GitHub
3. **Ajoutez un plugin PostgreSQL** : New > Database > PostgreSQL
   - Railway copie automatiquement `DATABASE_URL` dans l'environnement
4. Ajoutez la variable `SECRET_KEY` = une longue chaîne aléatoire
5. Le déploiement est automatique !

## Sans PostgreSQL (SQLite local)

Si vous ne configurez pas `DATABASE_URL`, l'app utilise SQLite automatiquement.
⚠️ Sur Railway free, les fichiers sont effacés à chaque redémarrage.
**Utilisez PostgreSQL pour la persistance des données.**

## Health check

Vérifiez que tout fonctionne : `https://votre-app.railway.app/health`
