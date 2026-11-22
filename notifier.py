"""
Modulo Notificatore Webhook n8n -> Telegram.
Invia eventi di trading (Acquisti, Vendite, Stop Loss, Break-Even) e Report Giornalieri
al workflow n8n attivo sul server, che li recapita all'istante su Telegram.
"""
import logging
import requests

logger = logging.getLogger("NasdaqNotifier")

N8N_WEBHOOK_LOCAL = "http://192.168.1.68:5678/webhook/nasdaq-trade"
N8N_HOST_HEADER = "charming-naturals-moral-stops.trycloudflare.com"
N8N_WEBHOOK_PUBLIC = "https://charming-naturals-moral-stops.trycloudflare.com/webhook/nasdaq-trade"

def send_notification(message: str) -> bool:
    """Invia un messaggio formattato HTML a n8n per il recapito su Telegram."""
    payload = {"message": message}
    
    # Tentativo 1: Rete locale OMV con Header Host
    try:
        resp = requests.post(
            N8N_WEBHOOK_LOCAL,
            json=payload,
            headers={"Host": N8N_HOST_HEADER},
            timeout=5
        )
        if resp.status_code == 200:
            logger.info("[+] Notifica Telegram recapitata con successo via n8n locale!")
            return True
    except Exception as e:
        logger.debug(f"Invio locale fallito: {e}")

    # Tentativo 2: Tunnel Cloudflare
    try:
        resp = requests.post(
            N8N_WEBHOOK_PUBLIC,
            json=payload,
            timeout=8
        )
        if resp.status_code == 200:
            logger.info("[+] Notifica Telegram recapitata con successo via tunnel Cloudflare!")
            return True
    except Exception as e:
        logger.warning(f"[-] Impossibile recapitare notifica Telegram: {e}")

    return False

if __name__ == "__main__":
    send_notification("🧪 Test diretto da notifier.py!")
