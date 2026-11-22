"""
Script di verifica della connessione ad Alpaca Paper Trading.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import ALPACA_CONFIG

def test_connection():
    api_key = ALPACA_CONFIG["API_KEY"]
    secret_key = ALPACA_CONFIG["API_SECRET"]
    paper = ALPACA_CONFIG["PAPER"]

    if not api_key or api_key == "INSERISCI_QUI_LA_TUA_API_KEY_ID":
        print("[-] ERRORE: Chiavi Alpaca non impostate nel file .env!")
        print("    Apri il file .env e inserisci la tua API KEY e SECRET KEY.")
        return False

    try:
        from alpaca.trading.client import TradingClient
        print("[*] Connessione ad Alpaca in corso...")
        trading_client = TradingClient(api_key, secret_key, paper=paper)
        account = trading_client.get_account()
        
        print("\n=======================================================")
        print(" [+] CONNESSIONE AD ALPACA PAPER RIUSCITA CON SUCCESSO! ")
        print("=======================================================")
        print(f" Account ID:       {account.id}")
        print(f" Stato Account:    {account.status}")
        print(f" Valuta:           {account.currency}")
        print(f" Liquidita' Cash:  ${float(account.cash):,.2f}")
        print(f" Potere d'Acquisto: ${float(account.buying_power):,.2f}")
        print(f" Valore Portafoglio: ${float(account.portfolio_value):,.2f}")
        print("=======================================================\n")
        return True
    except Exception as e:
        print(f"\n[-] ERRORE durante la connessione: {e}\n")
        return False

if __name__ == "__main__":
    test_connection()
