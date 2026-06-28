# Bot Tennis Sherbrooke

Bot Discord qui gere les profils joueurs, les resultats de matchs (avec confirmation a deux), un classement Elo, et un questionnaire de niveau de depart.

## Commandes

- `/profil [membre]` - affiche un profil joueur
- `/modifier-profil` - met a jour objectif, preference, dispo, age
- `/questionnaire` - estime le niveau Elo de depart (uniquement avant le premier match)
- `/resultat adversaire vainqueur score` - propose un resultat de match, l'adversaire doit confirmer/contester via boutons
- `/classement` - top des membres par Elo
- `/historique [membre]` - 5 derniers matchs confirmes

## Installation locale (pour tester)

```
pip install -r requirements.txt
cp .env.example .env
```

Remplis `.env` avec le token du bot et l'ID du serveur, puis :

```
python bot.py
```

## Deploiement 24/7 sur Railway (gratuit pour commencer)

1. Cree un compte sur https://railway.app (connecte-toi avec GitHub)
2. Mets ce dossier `tennis-bot` dans un repo GitHub (prive de preference, car le `.env` ne doit jamais etre commit)
3. Sur Railway : "New Project" -> "Deploy from GitHub repo" -> choisis ce repo
4. Dans l'onglet "Variables" du projet Railway, ajoute :
   - `BOT_TOKEN` = ton token de bot
   - `GUILD_ID` = 1520571160889397278
5. Railway va detecter `requirements.txt` et lancer automatiquement `python bot.py`
6. Verifie les logs Railway : tu dois voir "Connecte en tant que ... - commandes synchronisees."

Le bot reste alors en ligne tout le temps, meme quand ton ordinateur est ferme.

## Notes importantes

- La base de donnees est un simple fichier `tennis.db` (SQLite) cree automatiquement au demarrage. Sur Railway, ce fichier persiste tant que tu ne supprimes pas le service (pour une vraie persistance long terme, on pourra migrer vers une base Postgres plus tard si besoin).
- Le bot a besoin de l'intent par defaut seulement (pas besoin d'activer "Server Members Intent" pour ces commandes).
- Si tu changes les commandes plus tard, elles se resynchronisent automatiquement au redemarrage du bot (`on_ready`).
