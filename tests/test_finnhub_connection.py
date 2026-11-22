"""
Script di verifica della connessione a Finnhub API.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import FINNHUB_API_KEY

def test_finnhub():
    if not FINNHUB_API_KEY or FINNHUB_API_KEY == "INSERISCI_QUI_LA_TUA_FINNHUB_KEY":
        print("[-] ERRORE: Chiave FINNHUB_API_KEY non impostata nel file .env!")
        return False

    try:
        import finnhub
        print("[*] Connessione a Finnhub in corso...")
        client = finnhub.Client(api_key=FINNHUB_API_KEY)
        
        # Test 1: Quotazione AAPL
        quote = client.quote('AAPL')
        print("\n=======================================================")
        print(" [+] CONNESSIONE A FINNHUB RIUSCITA CON SUCCESSO! ")
        print("=======================================================")
        print(f" Test Quotazione AAPL: Prezzo Attuale = ${quote['c']}, Max Giorno = ${quote['h']}, Min Giorno = ${quote['l']}")
        
        # Test 2: Ultime Notizie
        news = client.company_news('AAPL', _from="2026-10-25", to="2026-10-31")
        print(f" Test News Feed AAPL: Recuperati {len(news)} articoli recenti")
        if news:
            print(f" Ultima news: \"{news[0]['headline']}\"")
        print("=======================================================\n")
        return True
    except Exception as e:
        print(f"\n[-] ERRORE durante il test Finnhub: {e}\n")
        return False

if __name__ == "__main__":
    test_finnhub()
