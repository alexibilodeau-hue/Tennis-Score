# Bot Tennis Sherbrooke

Bot Discord qui gère les profils joueurs, les résultats de matchs (avec confirmation à deux), un classement Elo, et un questionnaire de niveau de départ.

## Commandes

- `/profil [membre]` - affiche un profil joueur
- `/modifier-profil` - met à jour objectif, préférence, niveau, âge
- `/disponibilite` - formulaire simple (jours + heure de début/fin) qui **remplace** tout ton horaire actuel
- `/dispo-maintenant` - annonce que tu es libre pour jouer dans les 3 prochaines heures (et publie une annonce publique)
- `/trouver-partenaire [quand] [preference] [secteur]` - trouve des joueurs dispo, triés par compatibilité (par défaut : maintenant)
- `/recherche-partenaire [message]` - publie une annonce dans le salon recherche-partenaire ; le premier qui clique sur **J'accepte la partie !** crée automatiquement un salon privé avec toi
- `/questionnaire` - estimation indicative du niveau (n'affecte plus l'Elo réel)
- `/resultat adversaire vainqueur score` - propose un résultat de match, l'adversaire doit confirmer/contester via boutons (puis évalue ton adversaire avec quelques tags optionnels)
- `/classement [categorie]` - Elo, Plus actif, Plus de partenaires, Plus apprécié, ou Plus de filleuls parrainés
- `/historique [membre]` - 5 derniers matchs confirmés
- `/badges [membre]` - badges débloqués (premier match, 10/50 matchs, 10 victoires, 20 partenaires, joueur social, séries, organisateur, joueur fiable, ambassadeur)
- `/stats [membre]` - parties jouées, taux de victoire, partenaires différents, série de semaines, partenaire favori, tie-breaks, dernier match, filleuls
- `/defi` - le défi de la semaine et si tu l'as complété
- `/objectifs` - tes 3 objectifs personnels (jouer cette semaine, atteindre Expert, 10 partenaires) et ta progression
- `/parrainage @parrain` - déclare qui t'a invité (donne +100 XP à ton parrain, badge Ambassadeur à 3 filleuls)
- `/terrains` - carte des terrains du coin (ajoutés par les mods avec `/ajouter-terrain`) avec bouton "Je suis ici"
- `/ajouter-terrain` - (Founder/Mod) ajoute un terrain à la carte

## Compatibilité et secteur

`/modifier-profil` inclut maintenant un choix de **secteur** (Nord, Sud, Est, Rock Forest, Lennoxville, Magog). `/trouver-partenaire` trie les résultats par un **score de compatibilité sur 100** qui combine niveau (Elo proche), secteur, objectif, disponibilités qui se recoupent vraiment, et le fait de ne jamais avoir joué ensemble.

## Annonces publiques et automatisations

- `/dispo-maintenant` (activer) publie aussi une annonce publique dans `#recherche-partenaire` avec un bouton **Je suis partant !** que n'importe qui peut cliquer pour se connecter avec toi.
- Après chaque match confirmé, en plus des tags de réputation, l'adversaire peut indiquer si l'autre joueur **s'est présenté ou non** - ça alimente une note de fiabilité visible dans `/profil`.
- Le 1er de chaque mois, le bot publie automatiquement dans `#recompenses` le palmarès du mois (plus actif, plus de nouveaux partenaires, plus longue série, plus apprécié).
- Chaque soir à 19h, si au moins 2 joueurs d'un même niveau sont disponibles, le bot envoie une notification dans le salon de ce niveau (`#partenaire-debutant/intermediaire/expert`).
- Chaque vendredi, le bot publie dans `#recherche-partenaire` une invitation à jouer le samedi matin (bouton pour s'inscrire) ; le dimanche, il publie les groupes/paires formés automatiquement.

## Elo de départ

Tout le monde commence à **0 Elo**, peu importe le niveau de tennis annoncé - il faut jouer pour grimper. Seul un Founder/Modérateur peut forcer manuellement l'Elo d'un membre via le bouton "Forcer l'Elo" dans `/modifier-profil` (cas exceptionnel).

## Rôles et salons par niveau

Le bot crée et assigne automatiquement 3 rôles selon l'Elo du joueur : **Débutant** (< 500), **Intermédiaire** (500-799), **Expert** (800+). Ces rôles se mettent à jour automatiquement après chaque match confirmé ou changement d'Elo forcé par un mod.

Une catégorie **TROUVER PARTENAIRE** est créée avec un salon privé par niveau (`#partenaire-debutant`, `#partenaire-intermediaire`, `#partenaire-expert`), visible uniquement par les membres ayant le rôle correspondant (+ Founder/Modérateur).

## Rappel hebdomadaire de disponibilité

Chaque lundi à 18h (heure du Québec), le bot envoie un message **en privé (DM)** à chaque membre avec un bouton qui ouvre directement le formulaire `/disponibilite`, sans avoir besoin de connaître une seule commande. Si les DM sont fermés, le message est envoyé dans l'espace personnel du membre à la place. Le bot garde une trace (`last_checkin`) pour n'envoyer qu'une seule fois par semaine, même si le service redémarre plusieurs fois dans la journée.

Limite à connaître : si le bot redémarre (redéploiement) entre l'envoi du message et le clic du membre, le bouton de ce message spécifique arrête de répondre (le membre peut alors relancer `/disponibilite` pour recommencer). Le prochain message hebdomadaire fonctionnera normalement.

## Réputation, badges et défis

Après chaque match confirmé, la personne qui confirme peut évaluer l'autre joueur (Ponctuel / Agréable / Bon niveau / Rejouerais avec lui) - visible dans `/profil`. Les badges sont calculés automatiquement à partir des statistiques (pas stockés en base). Le défi de la semaine ("joue avec un nouveau partenaire") est détecté automatiquement quand deux joueurs s'affrontent pour la première fois.

## Installation locale (pour tester)

```
pip install -r requirements.txt
cp .env.example .env
```

Remplis `.env` avec le token du bot et l'ID du serveur, puis :

```
python bot.py
```

## Déploiement 24/7 sur Railway (gratuit pour commencer)

1. Crée un compte sur https://railway.app (connecte-toi avec GitHub)
2. Mets ce dossier `tennis-bot` dans un repo GitHub (privé de préférence, car le `.env` ne doit jamais être commit)
3. Sur Railway : "New Project" -> "Deploy from GitHub repo" -> choisis ce repo
4. Dans l'onglet "Variables" du projet Railway, ajoute :
   - `BOT_TOKEN` = ton token de bot
   - `GUILD_ID` = 1520571160889397278
5. Railway va détecter `requirements.txt` et lancer automatiquement `python bot.py`
6. Vérifie les logs Railway : tu dois voir "Connecté en tant que ... - commandes synchronisées."

Le bot reste alors en ligne tout le temps, même quand ton ordinateur est fermé.

## Notes importantes

- La base de données est un simple fichier `tennis.db` (SQLite) créé automatiquement au démarrage. Sur Railway, ce fichier persiste tant que tu ne supprimes pas le service (pour une vraie persistance long terme, on pourra migrer vers une base Postgres plus tard si besoin).
- Le bot a besoin de l'intent par défaut seulement (pas besoin d'activer "Server Members Intent" pour ces commandes).
- Si tu changes les commandes plus tard, elles se resynchronisent automatiquement au redémarrage du bot (`on_ready`).
