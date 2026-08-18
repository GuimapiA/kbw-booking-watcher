#!/usr/bin/env python3
"""
Surveille une ou plusieurs pages de réservation butlerapp (KBW, etc.)
et notifie par Email + Telegram + notification bureau (Windows) dès
qu'une session devient réservable (nouvelle session, ou place qui se
libère sur une session existante).

=========================
 AJOUTER UN NOUVEAU SITE
=========================
Tous les sites suivis sont listés dans le fichier targets.json (à la
racine du dépôt). Pour en ajouter un :

  1. Ouvre la page du site dans Chrome/Edge/Firefox.
  2. F12 -> onglet "Réseau"/"Network" -> filtre "Fetch/XHR"
     -> "Preserve log" coché -> recharge la page (F5).
  3. Cherche la requête qui renvoie du JSON avec des champs comme
     "course_timespans", "places_left", "quantity_left" (même
     structure que KBW, puisque c'est le même système butlerapp).
  4. Copie l'URL complète de cette requête (onglet Headers -> Request
     URL).
  5. Ajoute une nouvelle entrée dans targets.json :
       {
         "name": "Nom affiché dans les notifications",
         "page_url": "URL de la page pour affichage humain",
         "api_url": "URL de l'API trouvée à l'étape 4"
       }
  6. Commit -> c'est tout, le script surveillera ce site en plus des
     autres dès la prochaine exécution (pas besoin de toucher au code
     Python ni aux secrets).
"""

import csv
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

import requests

# ============ CONFIGURATION ============

TARGETS_FILE = Path(__file__).parent / "targets.json"
STATE_FILE = Path(__file__).parent / "last_state.json"

# --- Fenêtre horaire de surveillance (heure du Cameroun, UTC+1 toute
# l'année, pas de changement d'heure d'été) ---
CAMEROON_UTC_OFFSET_HOURS = 1
ACTIVE_HOUR_START = 7   # inclus
ACTIVE_HOUR_END = 18    # exclu (donc actif jusqu'à 17:59 heure du Cameroun)

# --- Notifications : mets True/False selon ce que tu veux activer ---
ENABLE_EMAIL = True
ENABLE_TELEGRAM = True
ENABLE_DESKTOP = True  # uniquement utile si le script tourne sur ton PC Windows

# --- Email (SMTP) ---
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER") or ""          # adresse d'envoi
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or ""  # mot de passe d'application

# Plusieurs destinataires possibles, séparés par des virgules,
# ex: "alice@example.com, bob@example.com"
EMAIL_TO_RAW = os.environ.get("EMAIL_TO") or ""
EMAIL_RECIPIENTS = [e.strip() for e in EMAIL_TO_RAW.split(",") if e.strip()]

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""

# Destinataires "fixes" (secret GitHub, optionnel), en plus des abonnés
# dynamiques dans subscribers.json
TELEGRAM_CHAT_ID_RAW = os.environ.get("TELEGRAM_CHAT_ID") or ""
STATIC_TELEGRAM_CHAT_IDS = [c.strip() for c in TELEGRAM_CHAT_ID_RAW.split(",") if c.strip()]

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"

# URL CSV publique du Google Sheet lié au Google Form (emails), optionnel
EMAIL_SHEET_CSV_URL = os.environ.get("EMAIL_SHEET_CSV_URL") or ""


def get_telegram_chat_ids() -> list:
    """Fusionne les chat_id fixes (secret) et les abonnés dynamiques."""
    ids = set(STATIC_TELEGRAM_CHAT_IDS)
    if SUBSCRIBERS_FILE.exists():
        try:
            data = json.loads(SUBSCRIBERS_FILE.read_text())
            ids.update(data.get("telegram", {}).keys())
        except Exception as e:
            print(f"[Abonnés Telegram] erreur de lecture: {e}")
    return list(ids)


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_email_recipients() -> list:
    """
    Fusionne les emails fixes (secret) et ceux du Google Form (CSV).
    Filtre les entrées qui ne ressemblent pas à une adresse email
    valide (utile car la validation native du formulaire n'est pas
    toujours disponible/fiable).
    """
    emails = set(EMAIL_RECIPIENTS)
    if EMAIL_SHEET_CSV_URL:
        try:
            resp = requests.get(EMAIL_SHEET_CSV_URL, timeout=20)
            resp.raise_for_status()
            for line in resp.text.splitlines()[1:]:  # ignore l'en-tête
                for cell in line.split(","):
                    cell = cell.strip().strip('"')
                    if EMAIL_REGEX.match(cell):
                        emails.add(cell)
                    elif "@" in cell:
                        print(f"[Emails Google Form] adresse ignorée (invalide): {cell}")
        except Exception as e:
            print(f"[Emails Google Form] erreur de lecture: {e}")
    return list(emails)


# --- Notifications Push (Web Push) ---
PUSH_SHEET_CSV_URL = os.environ.get("PUSH_SHEET_CSV_URL") or ""
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY") or ""
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY") or ""
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL") or ""


def get_push_subscriptions() -> list:
    """
    Récupère les abonnements Web Push stockés via le Google Form dédié
    (même principe que les emails). Chaque ligne du CSV contient un
    JSON (l'objet PushSubscription du navigateur) dans une cellule.
    """
    subs = []
    if not PUSH_SHEET_CSV_URL:
        return subs
    try:
        resp = requests.get(PUSH_SHEET_CSV_URL, timeout=20)
        resp.raise_for_status()
        reader = csv.reader(resp.text.splitlines())
        rows = list(reader)
        for row in rows[1:]:  # ignore l'en-tête
            for cell in row:
                cell = cell.strip()
                if cell.startswith("{") and '"endpoint"' in cell:
                    try:
                        subs.append(json.loads(cell))
                    except Exception:
                        pass
    except Exception as e:
        print(f"[Push] erreur de lecture des abonnements: {e}")
    # Déduplique par endpoint (un même appareil peut réapparaître)
    seen = set()
    unique_subs = []
    for s in subs:
        endpoint = s.get("endpoint")
        if endpoint and endpoint not in seen:
            seen.add(endpoint)
            unique_subs.append(s)
    return unique_subs


def notify_push(title: str, body: str, url: str = "./"):
    subs = get_push_subscriptions()
    if not (subs and VAPID_PRIVATE_KEY and VAPID_CLAIMS_EMAIL):
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("[Push] bibliothèque pywebpush non installée, notifications push ignorées.")
        return

    payload = json.dumps({"title": title, "body": body, "url": url})
    sent, failed = 0, 0
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_CLAIMS_EMAIL}"},
            )
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[Push] échec d'envoi: {e}")
    print(f"[Push] envoyé à {sent} appareil(s), {failed} échec(s).")

# ============ CIBLES À SURVEILLER ============


def load_targets() -> list:
    if not TARGETS_FILE.exists():
        raise RuntimeError(f"Fichier introuvable: {TARGETS_FILE}")
    return json.loads(TARGETS_FILE.read_text())


def is_within_active_window() -> bool:
    """
    Vrai si l'heure actuelle (convertie en heure du Cameroun) est dans
    la fenêtre de surveillance [ACTIVE_HOUR_START, ACTIVE_HOUR_END).
    """
    now_utc = datetime.now(timezone.utc)
    cameroon_hour = (now_utc.hour + CAMEROON_UTC_OFFSET_HOURS) % 24
    return ACTIVE_HOUR_START <= cameroon_hour < ACTIVE_HOUR_END


# ============ LOGIQUE DE DÉTECTION ============


def _url_with_page(url: str, page: int) -> str:
    """Retourne la même URL avec le paramètre page=<page>."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query["page"] = [str(page)]
    new_query = urlencode(query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def fetch_page(api_url: str, page: int) -> dict:
    url = _url_with_page(api_url, page)
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.json()


def fetch_all_timespans(api_url: str) -> dict:
    """
    Récupère TOUTES les pages (pas seulement la première) pour ne
    manquer aucune session, même celles ajoutées au-delà de la
    pagination initiale (ex: nouveau mois ouvert).
    """
    page = 1
    combined = {}
    while True:
        data = fetch_page(api_url, page)
        combined.update(extract_timespans(data))

        pagination = data["body"].get("pagination", {}).get("body", {})
        last_page = pagination.get("lastPage", 1)
        if page >= last_page:
            break
        page += 1
    return combined


def extract_timespans(data: dict) -> dict:
    """
    Extrait, pour chaque session (course_timespan) trouvée dans la
    réponse, un petit résumé:
    {id: {"quantity_left": int, "label": str, "book_url": str}}

    Le bouton de réservation du site est actif quand quantity_left > 0
    (places_left suit la même valeur dans les données observées).
    """
    entities = data["body"]["entities"]["body"]

    result = {}
    for ts in entities.get("course_timespans", []):
        attrs = ts["attributes"]
        presented = ts.get("presented", {})
        result[attrs["id"]] = {
            "quantity_left": attrs.get("quantity_left", attrs.get("places_left", -1)),
            "label": presented.get("titleReplaced", "") + " (" + attrs.get("shortcut", "") + ")",
            "book_url": presented.get("apiBookUrl", ""),
        }
    return result


def load_last_state() -> dict:
    """Retourne { target_name: { ts_id: {...} } }"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def detect_newly_bookable(previous: dict, current: dict) -> list:
    """
    Retourne la liste des sessions qui viennent de devenir réservables
    (quantity_left passe de <= 0 (ou inexistant) à > 0).
    Chaque élément est un dict {"label": str, "book_url": str}.
    """
    newly_open = []
    for ts_id, info in current.items():
        ts_id_str = str(ts_id)
        prev_info = previous.get(ts_id_str)
        prev_qty = prev_info["quantity_left"] if prev_info else None
        curr_qty = info["quantity_left"]

        was_open = prev_qty is not None and prev_qty > 0
        is_open = curr_qty > 0

        if is_open and not was_open:
            newly_open.append({
                "label": info["label"] or f"session #{ts_id}",
                "book_url": info.get("book_url", ""),
            })

    return newly_open


# ============ NOTIFICATIONS ============


def notify_email(subject: str, body: str):
    recipients = get_email_recipients()
    if not (ENABLE_EMAIL and SMTP_USER and SMTP_PASSWORD and recipients):
        return
    sent, failed = 0, 0
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            for recipient in recipients:
                try:
                    msg = MIMEText(body)
                    msg["Subject"] = subject
                    msg["From"] = SMTP_USER
                    msg["To"] = recipient
                    server.send_message(msg)
                    sent += 1
                except Exception as e:
                    failed += 1
                    print(f"[Email] échec pour {recipient}: {e}")
    except Exception as e:
        print(f"[Email] erreur de connexion SMTP: {e}")
        return
    print(f"[Email] envoyé à {sent} destinataire(s), {failed} échec(s).")


def notify_telegram(text: str):
    chat_ids = get_telegram_chat_ids()
    if not (ENABLE_TELEGRAM and TELEGRAM_BOT_TOKEN and chat_ids):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok_count = 0
    for chat_id in chat_ids:
        try:
            requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
            ok_count += 1
        except Exception as e:
            print(f"[Telegram] erreur pour chat_id={chat_id}: {e}")
    print(f"[Telegram] envoyé à {ok_count}/{len(chat_ids)} destinataire(s).")


def notify_desktop(title: str, message: str):
    if not ENABLE_DESKTOP:
        return
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=15)
        print("[Bureau] notification affichée.")
    except Exception as e:
        # Pas grave si ça échoue (ex: pas sous Windows, ou plyer absent)
        print(f"[Bureau] notification ignorée: {e}")


def notify_newly_open(target_name: str, page_url: str, sessions: list):
    title = f"🎉 Réservation OUVERTE - {target_name}"
    lines = []
    for s in sessions:
        line = f"- {s['label']}"
        if s.get("book_url"):
            line += f"\n  👉 Réserver : {s['book_url']}"
        lines.append(line)

    body = (
        f"{title}\n\nSession(s) désormais réservable(s) :\n"
        + "\n".join(lines)
        + f"\n\nPage complète : {page_url}\n\nVa réserver vite !"
    )
    notify_email(title, body)
    notify_telegram(body)
    notify_desktop(title, body)

    # Le push reste volontairement vague (pas de détail sur la session
    # précise), juste de quoi prévenir vite fait.
    notify_push(
        "🎓 Une place s'est libérée !",
        f"Vérifie vite : {target_name}",
        url=page_url,
    )


DASHBOARD_REFRESH_MINUTES = 30  # ne réécrire docs/status.json (et donc
                                 # déclencher un redéploiement Pages) que
                                 # tous les 30 min max, pour éviter les
                                 # déploiements qui se bousculent


def write_dashboard_status(targets: list, all_state: dict, error_targets: set):
    """
    Écrit docs/status.json, consommé par le tableau de bord web
    (docs/index.html, servi par GitHub Pages).

    Limité à une écriture toutes les DASHBOARD_REFRESH_MINUTES minutes
    pour éviter de déclencher un redéploiement GitHub Pages à chaque
    exécution du script (ce qui surchargeait la file de déploiement).
    """
    docs_dir = Path(__file__).parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    status_file = docs_dir / "status.json"

    if status_file.exists():
        try:
            previous = json.loads(status_file.read_text())
            last_check = datetime.fromisoformat(previous["last_check_utc"])
            elapsed_minutes = (datetime.now(timezone.utc) - last_check).total_seconds() / 60
            if elapsed_minutes < DASHBOARD_REFRESH_MINUTES:
                print(
                    f"[Tableau de bord] pas encore rafraîchi "
                    f"(dernière mise à jour il y a {elapsed_minutes:.0f} min, "
                    f"seuil: {DASHBOARD_REFRESH_MINUTES} min)."
                )
                return
        except Exception:
            pass  # fichier corrompu/absent -> on le réécrit normalement

    target_statuses = []
    for target in targets:
        name = target["name"]
        sessions = all_state.get(name, {})
        open_count = sum(1 for s in sessions.values() if s.get("quantity_left", -1) > 0)
        target_statuses.append({
            "name": name,
            "page_url": target.get("page_url", target.get("api_url", "")),
            "session_count": len(sessions),
            "open_count": open_count,
            "error": name in error_targets,
        })

    telegram_count = 0
    if SUBSCRIBERS_FILE.exists():
        try:
            telegram_count = len(json.loads(SUBSCRIBERS_FILE.read_text()).get("telegram", {}))
        except Exception:
            pass

    email_count = len(get_email_recipients())

    status = {
        "last_check_utc": datetime.now(timezone.utc).isoformat(),
        "targets": target_statuses,
        "subscriber_counts": {
            "telegram": telegram_count,
            "email": email_count,
        },
    }
    (docs_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False))

    # --- Données admin (liste nominative, page protégée par mot de passe) ---
    telegram_names = []
    if SUBSCRIBERS_FILE.exists():
        try:
            sub_data = json.loads(SUBSCRIBERS_FILE.read_text())
            telegram_names = [v.get("name", k) for k, v in sub_data.get("telegram", {}).items()]
        except Exception:
            pass

    admin_data = {
        "telegram": telegram_names,
        "emails": get_email_recipients(),
    }
    (docs_dir / "admin-data.json").write_text(json.dumps(admin_data, indent=2, ensure_ascii=False))


# ============ MAIN ============


def main():
    # Mode test : force l'envoi d'une notification Telegram/Email
    # factice, sans aller chercher les vraies données. Utile pour
    # vérifier que les identifiants fonctionnent correctement.
    if os.environ.get("FORCE_TEST_NOTIFY") == "true":
        print("Mode TEST activé : envoi d'une notification factice...")
        notify_newly_open(
            "[TEST] Cible factice",
            "https://example.com",
            [{"label": "[TEST] Session factice 12.08.2026 8:00 Uhr", "book_url": "https://example.com/bookcart?ftimespans=0"}],
        )
        print("Notification de test envoyée (si les identifiants sont corrects).")
        return

    if not is_within_active_window() and os.environ.get("FORCE_CHECK") != "true":
        now_utc = datetime.now(timezone.utc)
        cameroon_hour = (now_utc.hour + CAMEROON_UTC_OFFSET_HOURS) % 24
        print(
            f"Hors fenêtre de surveillance (heure actuelle au Cameroun: {cameroon_hour}h, "
            f"fenêtre active: {ACTIVE_HOUR_START}h-{ACTIVE_HOUR_END}h). Aucune vérification effectuée."
        )
        return

    targets = load_targets()
    all_state = load_last_state()
    new_all_state = {}
    any_error = False
    error_targets = set()

    for target in targets:
        name = target["name"]
        api_url = target["api_url"]
        page_url = target.get("page_url", api_url)

        print(f"--- Vérification: {name} ---")
        try:
            current = fetch_all_timespans(api_url)
        except Exception as e:
            print(f"Erreur pour '{name}': {e}")
            any_error = True
            error_targets.add(name)
            # on garde l'ancien état de cette cible pour ne pas perdre
            # sa mémoire à cause d'une erreur réseau ponctuelle
            new_all_state[name] = all_state.get(name, {})
            continue

        previous = all_state.get(name, {})
        newly_open = detect_newly_bookable(previous, current)

        print(f"{len(current)} session(s) suivie(s). {len(newly_open)} nouvellement réservable(s).")

        if newly_open:
            print(f">> Changement détecté pour '{name}' ! Envoi des notifications...")
            notify_newly_open(name, page_url, newly_open)

        new_all_state[name] = {str(k): v for k, v in current.items()}

    save_state(new_all_state)
    write_dashboard_status(targets, new_all_state, error_targets)

    if any_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
