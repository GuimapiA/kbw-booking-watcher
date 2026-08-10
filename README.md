# Surveillance des réservations KBW (Yaoundé Deutsch B2)

## 1. URL de l'API (déjà configurée)
L'URL de l'API et la logique de détection sont déjà en place dans
`check_booking.py` :
- Le script parcourt **toutes les pages** de résultats (pas juste la
  première), pour ne rater aucune session même après ajout d'un
  nouveau mois.
- Il compare l'état actuel de chaque session (`quantity_left`) à
  l'état précédent (`last_state.json`), et notifie dès qu'une session
  passe de "complet" (≤ 0 places) à "réservable" (> 0 places) — que ce
  soit une session existante qui se libère ou une toute nouvelle
  session qui apparaît.

## 2. Créer le bot Telegram (5 min)
1. Dans Telegram, cherche **@BotFather**, envoie `/newbot`, suis les
   étapes -> tu obtiens un **token** (`TELEGRAM_BOT_TOKEN`).
2. Envoie un message à ton nouveau bot (n'importe quoi).
3. Va sur `https://api.telegram.org/bot<TON_TOKEN>/getUpdates` dans
   un navigateur -> tu verras ton `chat.id` (`TELEGRAM_CHAT_ID`).

## 3. Configurer l'email (si tu utilises Gmail)
Crée un **mot de passe d'application** Gmail (pas ton mot de passe
normal) : Compte Google -> Sécurité -> Validation en 2 étapes ->
Mots de passe des applications.

## 4A. Option PC (Windows) — Planificateur de tâches
1. Installe Python : https://python.org
2. `pip install -r requirements.txt`
3. Renseigne les variables (`API_URL`, `SMTP_USER`, etc.) soit
   directement dans le script, soit comme variables d'environnement
   Windows.
4. Ouvre le "Planificateur de tâches" -> Créer une tâche de base ->
   déclencheur "toutes les 15 minutes" -> action:
   `python C:\chemin\vers\check_booking.py`
   

## 4B. Option cloud (fonctionne même PC/téléphone éteints) — recommandé
1. Crée un dépôt GitHub (privé de préférence) et mets-y ces fichiers.
2. Dans le dépôt : Settings -> Secrets and variables -> Actions ->
   ajoute chaque valeur (`API_URL`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`,
   `SMTP_HOST`, `SMTP_PORT`) comme "Repository secret".
3. Le fichier `.github/workflows/check.yml` est déjà prêt : GitHub
   exécutera le script automatiquement toutes les 15 minutes, gratuit.
4. Les notifications Telegram/Email arriveront directement sur ton
   téléphone (l'appli Telegram/ta boîte mail), sans rien installer
   sur Android.

## Notes
- La notification "bureau" (`ENABLE_DESKTOP`) ne fonctionne que si le
  script tourne sur ton PC — inutile/ignorée sur GitHub Actions.
- Le script ne notifie que lors du **passage** de "fermé" à "ouvert"
  (pas à chaque exécution), grâce à `last_state.json`.
- Le cron est réglé sur `*/5 * * * *` (toutes les 5 minutes). C'est
  déjà le minimum pratique fiable sur GitHub Actions — en dessous, les
  déclenchements peuvent être retardés par la charge de GitHub, donc
  descendre plus bas n'apporterait rien de garanti. 

