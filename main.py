"""
File principale di avvio per il Bot di Trading Quantitativo Nasdaq-100.
Esecuzione in locale sul tuo PC (modalità accendi-spegni con CTRL+C).
"""
import sys
import logging
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.append(str(ROOT_DIR))

from lumibot.brokers import Alpaca
from lumibot.traders import Trader

from config import ALPACA_CONFIG
from strategy import NasdaqTimestralStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT_DIR / "data" / "trading_bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("MainRunner")

def run():
    print("""
===================================================================
     NASDAQ-100 QUANTITATIVE TRADING BOT (PAPER TRADING DEMO)
      Cervello: Timestral-209M (Mistral AI) su CPU
      Scudo: Gemini Flash-Lite & Calendario Trimestrali
      Money Management: Rischio Max 1% per Trade
===================================================================
[*] Avvio del sistema in modalità accendi-spegni...
[*] Premi CTRL + C in qualsiasi momento per fermare il bot in sicurezza.
===================================================================
""")
    
    # Verifica credenziali
    if not ALPACA_CONFIG["API_KEY"] or ALPACA_CONFIG["API_KEY"] == "INSERISCI_QUI_LA_TUA_API_KEY_ID":
        print("[-] ERRORE CRITICO: Configura prima le API Keys nel file .env!")
        return

    try:
        # Inizializza il broker Alpaca Paper Trading
        logger.info("[*] Connessione al broker Alpaca Paper Trading...")
        broker = Alpaca(ALPACA_CONFIG)

        # Inizializza la strategia quantitativa
        strategy = NasdaqTimestralStrategy(broker=broker)

        # Inizializza il trader orchestratore di Lumibot
        trader = Trader()
        trader.add_strategy(strategy)

        logger.info("[+] Trader avviato! Inizio del ciclo continuo...")
        trader.run_all()

    except KeyboardInterrupt:
        print("\n\n[*] Chiusura del bot richiesta dall'utente (CTRL+C)...")
        print("[+] Bot arrestato in sicurezza senza lasciare ordini pendenti.")
    except Exception as e:
        logger.critical(f"[-] Errore fatale durante l'esecuzione: {e}", exc_info=True)

if __name__ == "__main__":
    run()
