# 📤 Instructions pour uploader sur GitHub

## Étape 1 : Créer un repository sur GitHub

1. Allez sur https://github.com
2. Cliquez sur le bouton "+" en haut à droite → "New repository"
3. Donnez un nom à votre repo (ex: `babyfoot-test`)
4. Choisissez Public ou Private
5. **NE COCHEZ PAS** "Add a README file" (on en a déjà un)
6. Cliquez sur "Create repository"

## Étape 2 : Uploader votre projet

### Option A : Via l'interface web (SIMPLE)

1. Sur la page de votre nouveau repo, cliquez sur "uploading an existing file"
2. Glissez-déposez TOUS les fichiers de ce dossier
3. Ajoutez un message de commit (ex: "Premier commit")
4. Cliquez sur "Commit changes"

### Option B : Via Git en ligne de commande (RECOMMANDÉ)

Ouvrez un terminal dans ce dossier et exécutez :

```bash
# Initialiser Git
git init

# Ajouter tous les fichiers
git add .

# Créer le premier commit
git commit -m "Premier commit - Application babyfoot"

# Renommer la branche en main
git branch -M main

# Lier à votre repo GitHub (REMPLACEZ avec votre URL)
git remote add origin https://github.com/VOTRE-NOM/babyfoot-test.git

# Pousser vers GitHub
git push -u origin main
```

## ✅ C'est fait !

Votre projet est maintenant sur GitHub. Vous pouvez le partager avec l'URL :
`https://github.com/VOTRE-NOM/babyfoot-test`

## 🔄 Pour mettre à jour plus tard

```bash
git add .
git commit -m "Description des modifications"
git push
```
