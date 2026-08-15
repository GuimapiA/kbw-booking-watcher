#!/usr/bin/env python3
"""
Gère automatiquement les abonnés Telegram du bot :
- Détecte les nouveaux utilisateurs qui envoient un message (ex: /start)
  -> les ajoute à subscribers.json + leur envoie un message de bienvenue
- Détecte /stop -> les retire de la liste + confirme la désinscription
- Garde en mémoire (subscribers.json -> _meta.last_update_id) jusqu'où on
  a déjà lu, pour ne jamais retraiter deux fois le même message.

Ce fichier est indépendant de check_booking.py : il tourne sur son
propre planning (voir .github/workflows/subscribers.yml), plus
fréquent, pour accueillir les gens rapidement.
"""

import json
import os
import random
from pathlib import Path

import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"

WELCOME_MESSAGES = [
    "🎉 Bienvenue ! Je suis le bot de surveillance des examens KBW "
    "(Deutsch B1/B2 à Yaoundé et Douala). Dès qu'une place se libère "
    "quelque part, tu seras le premier au courant ! 📚🇩🇪\n\n"
    "Envoie /stop à tout moment si tu veux te désabonner.",

    "👋 Salut et bienvenue dans la team ! À partir de maintenant, je "
    "veille sur les réservations d'examens B1/B2 (Yaoundé & Douala) "
    "pour toi. Dès qu'une session s'ouvre, tu reçois l'alerte "
    "immédiatement 🚀🎓\n\n"
    "(Pour arrêter les notifications, envoie /stop.)",

    "🤗 Content de t'avoir ici ! Je surveille en continu les places "
    "disponibles pour les examens de Deutsch B1/B2 à Yaoundé et Douala. "
    "Prépare-toi, ça peut aller vite quand une place se libère ⚡📖\n\n"
    "Tape /stop si tu veux te désinscrire un jour.",
]

GOODBYE_MESSAGE = (
    "👋 C'est noté, tu ne recevras plus de notifications. Bon courage "
    "pour la suite, et n'hésite pas à revenir avec /start si tu changes "
    "d'avis !"
)


def load_subscribers() -> dict:
    if SUBSCRIBERS_FILE.exists():
        try:
            return json.loads(SUBSCRIBERS_FILE.read_text())
        except Exception:
            pass
    return {"telegram": {}, "_meta": {"last_update_id": 0}}


def save_subscribers(data: dict):
    SUBSCRIBERS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def send_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception as e:
        print(f"[Telegram] erreur d'envoi à {chat_id}: {e}")


def get_updates(offset: int) -> list:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    resp = requests.get(url, params={"offset": offset + 1, "timeout": 0}, timeout=20)
    resp.raise_for_status()
    return resp.json().get("result", [])


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN non configuré, rien à faire.")
        return

    data = load_subscribers()
    data.setdefault("telegram", {})
    data.setdefault("_meta", {"last_update_id": 0})

    last_update_id = data["_meta"].get("last_update_id", 0)
    updates = get_updates(last_update_id)

    if not updates:
        print("Aucun nouveau message Telegram.")
        return

    for update in updates:
        data["_meta"]["last_update_id"] = update["update_id"]

        message = update.get("message")
        if not message:
            continue

        chat = message.get("chat", {})
        chat_id = str(chat.get("id"))
        text = (message.get("text") or "").strip().lower()
        display_name = chat.get("first_name") or chat.get("username") or chat_id

        if text.startswith("/stop"):
            if chat_id in data["telegram"]:
                del data["telegram"][chat_id]
                send_message(chat_id, GOODBYE_MESSAGE)
                print(f"Désabonné: {display_name} ({chat_id})")
            continue

        # Nouveau venu (ou message quelconque d'un chat_id inconnu) -> on l'abonne
        if chat_id not in data["telegram"]:
            data["telegram"][chat_id] = {
                "name": display_name,
                "joined": message.get("date"),
            }
            welcome = random.choice(WELCOME_MESSAGES)
            send_message(chat_id, welcome)
            print(f"Nouvel abonné: {display_name} ({chat_id})")

    save_subscribers(data)
    print(f"Total abonnés Telegram: {len(data['telegram'])}")


if __name__ == "__main__":
    main()
