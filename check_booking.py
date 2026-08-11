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

import json
import os
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
EMAIL_TO_RAW = os.environ.get("EMAIL_TO") or SMTP_USER
EMAIL_RECIPIENTS = [e.strip() for e in EMAIL_TO_RAW.split(",") if e.strip()]

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""

# Plusieurs chat_id possibles, séparés par des virgules,
# ex: "6804016670, 123456789"
TELEGRAM_CHAT_ID_RAW = os.environ.get("TELEGRAM_CHAT_ID") or ""
TELEGRAM_CHAT_IDS = [c.strip() for c in TELEGRAM_CHAT_ID_RAW.split(",") if c.strip()]

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
    if not (ENABLE_EMAIL and SMTP_USER and SMTP_PASSWORD and EMAIL_RECIPIENTS):
        return
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            for recipient in EMAIL_RECIPIENTS:
                msg = MIMEText(body)
                msg["Subject"] = subject
                msg["From"] = SMTP_USER
                msg["To"] = recipient
                server.send_message(msg)
        print(f"[Email] envoyé à {len(EMAIL_RECIPIENTS)} destinataire(s).")
    except Exception as e:
        print(f"[Email] erreur: {e}")


def notify_telegram(text: str):
    if not (ENABLE_TELEGRAM and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ok_count = 0
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
            ok_count += 1
        except Exception as e:
            print(f"[Telegram] erreur pour chat_id={chat_id}: {e}")
    print(f"[Telegram] envoyé à {ok_count}/{len(TELEGRAM_CHAT_IDS)} destinataire(s).")


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

    if not is_within_active_window():
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

    if any_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
