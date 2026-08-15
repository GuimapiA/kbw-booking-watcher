# Surveillance des réservations KBW (Yaoundé Deutsch B2)

## 1. Sites surveillés (targets.json)
Tous les sites/sessions à surveiller sont listés dans **`targets.json`**
à la racine du dépôt — c'est un simple fichier texte, pas besoin de
toucher au code Python pour en ajouter un. Voir les instructions en
haut de `check_booking.py` (section "AJOUTER UN NOUVEAU SITE") pour la
marche à suivre (même procédure DevTools que pour KBW, car même
système butlerapp).

## 2. Plusieurs destinataires
- **Telegram** : mets plusieurs `chat_id` séparés par une virgule dans
  le secret `TELEGRAM_CHAT_ID`, ex: `987654321, 123456789`
- **Email** : mets plusieurs adresses séparées par une virgule dans le
  secret `EMAIL_TO`, ex: `alice@example.com, bob@example.com`

## 3. La logique de détection est déjà en place
- Le script parcourt **toutes les pages** de résultats (pas juste la
  première), pour ne rater aucune session même après ajout d'un
  nouveau mois.
- Il compare l'état actuel de chaque session (`quantity_left`) à
  l'état précédent (`last_state.json`), et notifie dès qu'une session
  passe de "complet" (≤ 0 places) à "réservable" (> 0 places) — que ce
  soit une session existante qui se libère ou une toute nouvelle
  session qui apparaît. Cette logique tourne indépendamment pour
  chaque site listé dans `targets.json`.

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

---

## 4. Abonnement automatique Telegram (nouveau)

Plus besoin de récupérer manuellement chaque `chat_id` :

1. Partage simplement le lien de ton bot (`https://t.me/TonBotUsername`)
   à qui tu veux.
2. Dès qu'une personne clique sur **"Démarrer"** (ou envoie `/start`),
   `manage_subscribers.py` (qui tourne toutes les 5 min, voir
   `.github/workflows/subscribers.yml`) la détecte automatiquement, lui
   envoie un message de bienvenue chaleureux, et l'ajoute à
   `subscribers.json`.
3. Si quelqu'un veut se désabonner, il envoie `/stop` — il est retiré
   automatiquement.
4. `check_booking.py` lit `subscribers.json` à chaque vérification et
   inclut tous ces abonnés dans les notifications, en plus des
   `chat_id` fixes du secret `TELEGRAM_CHAT_ID` (optionnel désormais).

**Rien à configurer** pour cette partie : le secret `TELEGRAM_BOT_TOKEN`
déjà en place suffit.

## 5. Abonnement automatique par Email (Google Form)

1. Va sur https://forms.google.com → crée un nouveau formulaire.
2. Ajoute une seule question, type "Réponse courte", intitulée par
   exemple *"Ton adresse email pour recevoir les alertes KBW"*.
   (Active éventuellement la validation "doit être une adresse email"
   dans les options avancées de la question.)
3. Onglet **"Réponses"** du formulaire → clique sur l'icône verte
   Google Sheets pour créer la feuille de calcul liée.
4. Dans cette feuille Google Sheets : **Fichier → Partager → Publier
   sur le Web**.
5. Choisis la feuille concernée, format **"Valeurs séparées par des
   virgules (.csv)"** → **Publier**.
6. Copie le lien généré (ça ressemble à
   `https://docs.google.com/spreadsheets/d/e/.../pub?output=csv`).
7. Ajoute ce lien comme secret GitHub : nom `EMAIL_SHEET_CSV_URL`.
8. Partage l'URL du **formulaire** (pas la feuille) aux personnes
   concernées : `https://forms.gle/xxxxx`.

À chaque vérification, `check_booking.py` va lire ce CSV et notifier
tous les emails qui s'y trouvent, en plus de ceux du secret `EMAIL_TO`
(optionnel désormais).

## 6. Tableau de bord admin (GitHub Pages)

1. Sur GitHub, va dans **Settings → Pages**.
2. Sous "Build and deployment" → Source : **"Deploy from a branch"**.
3. Branche : **main**, dossier : **/docs** → **Save**.
4. Après 1-2 minutes, ton tableau de bord est disponible à une URL du
   type `https://TonUsername.github.io/kbw-booking-watcher/`.
5. Sur ton téléphone Android, ouvre ce lien dans Chrome → menu (⋮) →
   **"Ajouter à l'écran d'accueil"** — tu auras une icône comme une
   vraie app.

Le tableau de bord est **en lecture seule** (statut des examens,
nombre d'abonnés, dernière vérification) — pour ajouter/retirer un
examen, ça reste via `targets.json` sur GitHub (voir section 1).
