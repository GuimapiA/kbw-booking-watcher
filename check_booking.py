#!/usr/bin/env python3
"""
Surveille la page de réservation KBW (Yaoundé Deutsch B2) et notifie
par Email + Telegram + notification bureau (Windows) dès qu'un
changement d'état est détecté (ex: réservation ouverte).

=========================
 ÉTAPE 1 - TROUVER L'API
=========================
Le site est une appli JavaScript (butlerapp) : le HTML brut ne contient
pas les cours, ils sont chargés via une requête AJAX après le
chargement de la page. Pour trouver cette requête :

  1. Ouvre la page dans Chrome/Firefox :
     https://kbw-personal-services.butlerapp2.de/demo#/courses?fcourses=yaounde_deutsch_b2
  2. Ouvre les outils développeur (F12) -> onglet "Réseau" / "Network"
  3. Filtre sur "Fetch/XHR"
  4. Recharge la page (F5)
  5. Cherche une requête qui renvoie du JSON contenant le mot
     "yaounde_deutsch_b2" ou des infos de cours (souvent une URL du
     style https://kbw-personal-services.butlerapp2.de/api/... ou
     .../rest/... ou .../ajax/...)
  6. Clique dessus -> copie l'URL complète -> colle-la dans API_URL
     ci-dessous.
  7. Regarde la réponse JSON (onglet "Réponse"/"Preview") : repère le
     champ qui indique que la réservation est ouverte (ex: "bookable":
     true, "status": "open", "available_seats": 12, "state": "closed"...)
     -> adapte la fonction is_booking_open() plus bas en conséquence.

Si tu ne trouves pas d'API exploitable, utilise plutôt la variante
Playwright (check_booking_playwright.py, fournie séparément) qui
simule un vrai navigateur.
"""

import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

import requests

# ============ CONFIGURATION ============

# URL de la page (affichage humain, utilisée dans les messages de notif)
PAGE_URL = "https://kbw-personal-services.butlerapp2.de/demo#/courses?fcourses=yaounde_deutsch_b2"

# --> METS ICI l'URL exacte trouvée dans l'onglet Réseau (Request URL)
# Elle doit renvoyer le JSON "response_type": "response_collection", "name": "event_data"
API_URL = os.environ.get("API_URL") or (
    "https://kbw-personal-services.butlerapp2.de/api/book"
    "?exclude=trainer,appointments,extra_prices,forms&page=1&perPage=10&fcourses=yaounde_deutsch_b2"
)

# Nom technique du cours à filtrer (vu dans le JSON: courses[0].attributes.name)
COURSE_NAME = "yaounde_deutsch_b2"

# Fichier local qui garde en mémoire le dernier état connu (id -> quantity_left)
STATE_FILE = Path(__file__).parent / "last_state.json"

# --- Notifications : mets True/False selon ce que tu veux activer ---
ENABLE_EMAIL = True
ENABLE_TELEGRAM = True
ENABLE_DESKTOP = True  # uniquement utile si le script tourne sur ton PC Windows

# --- Email (SMTP) ---
SMTP_HOST = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER") or ""       # ton adresse email d'envoi
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or ""  # mot de passe d'application (pas ton mdp normal)
EMAIL_TO = os.environ.get("EMAIL_TO") or SMTP_USER  # destinataire

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or ""

# ============ LOGIQUE DE DÉTECTION ============


def _url_with_page(url: str, page: int) -> str:
    """Retourne la même URL avec le paramètre page=<page>."""
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query["page"] = [str(page)]
    new_query = urlencode(query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def fetch_page(page: int) -> dict:
    if not API_URL:
        raise RuntimeError(
            "API_URL n'est pas configurée. Va chercher l'URL dans l'onglet "
            "Réseau des DevTools (voir instructions en haut du fichier)."
        )
    url = _url_with_page(API_URL, page)
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.json()


def fetch_all_timespans() -> dict:
    """
    Récupère TOUTES les pages (pas seulement la première) pour ne
    manquer aucune session, même celles ajoutées au-delà de la
    pagination initiale (ex: nouveau mois ouvert).
    """
    page = 1
    combined = {}
    while True:
        data = fetch_page(page)
        combined.update(extract_timespans(data))

        pagination = data["body"].get("pagination", {}).get("body", {})
        last_page = pagination.get("lastPage", 1)
        if page >= last_page:
            break
        page += 1
    return combined


def extract_timespans(data: dict) -> dict:
    """
    Extrait, pour chaque session (course_timespan) du cours COURSE_NAME,
    un petit résumé: {id: {"quantity_left": int, "label": str}}

    Le bouton de réservation du site est actif quand quantity_left > 0
    (places_left suit la même valeur dans les données observées).
    """
    entities = data["body"]["entities"]["body"]

    # ID du cours correspondant à COURSE_NAME
    course_ids = {
        c["attributes"]["id"]
        for c in entities.get("courses", [])
        if c["attributes"].get("name") == COURSE_NAME
    }

    result = {}
    for ts in entities.get("course_timespans", []):
        related_course_ids = set(ts.get("related", {}).get("courses", {}).get("value", []))
        if course_ids and not (related_course_ids & course_ids):
            continue  # cette session n'appartient pas au cours qu'on surveille

        attrs = ts["attributes"]
        result[attrs["id"]] = {
            "quantity_left": attrs.get("quantity_left", attrs.get("places_left", -1)),
            "label": ts.get("presented", {}).get("titleReplaced", "")
            + " (" + attrs.get("shortcut", "") + ")",
        }
    return result


def load_last_state() -> dict:
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
            newly_open.append(info["label"] or f"session #{ts_id}")

    return newly_open


# ============ NOTIFICATIONS ============


def notify_email(subject: str, body: str):
    if not (ENABLE_EMAIL and SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print("[Email] envoyé.")
    except Exception as e:
        print(f"[Email] erreur: {e}")


def notify_telegram(text: str):
    if not (ENABLE_TELEGRAM and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
        print("[Telegram] envoyé.")
    except Exception as e:
        print(f"[Telegram] erreur: {e}")


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


def notify_newly_open(sessions: list):
    title = "🎉 Réservation OUVERTE - Yaoundé Deutsch B2"
    body = (
        f"{title}\n\nSession(s) désormais réservable(s) :\n"
        + "\n".join(f"- {s}" for s in sessions)
        + f"\n\n{PAGE_URL}\n\nVa réserver vite !"
    )
    notify_email(title, body)
    notify_telegram(body)
    notify_desktop(title, body)


# ============ MAIN ============


def main():
    try:
        current = fetch_all_timespans()
    except Exception as e:
        print(f"Erreur lors de la récupération/lecture des données: {e}")
        sys.exit(1)

    previous = load_last_state()

    newly_open = detect_newly_bookable(previous, current)

    print(f"{len(current)} session(s) suivie(s). {len(newly_open)} nouvellement réservable(s).")

    if newly_open:
        print(">> Changement détecté ! Envoi des notifications...")
        notify_newly_open(newly_open)

    # Sauvegarde l'état courant pour la prochaine comparaison
    save_state({str(k): v for k, v in current.items()})


if __name__ == "__main__":
    main()
