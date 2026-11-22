"""
Script per simulare un ciclo completo di trading (anche a mercati chiusi nel weekend).
Verifica l'intero flusso:
1. Connessione ad Alpaca
2. Lettura saldo e posizioni
3. Scan dei titoli Nasdaq-100
4. Previsione quantili con Timestral-209M
5. Validazione con News Guardian & Gemini Flash-Lite
6. Calcolo del Position Sizing all'1%
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from lumibot.brokers import Alpaca
from config import ALPACA_CONFIG
from strategy import NasdaqTimestralStrategy

def run_test_cycle():
    print("===================================================================")
    print("      TEST SIMULATO END-TO-END (UN SINGOLO CICLO COMPLETO)         ")
    print("===================================================================")
    
    broker = Alpaca(ALPACA_CONFIG)
    strategy = NasdaqTimestralStrategy(broker=broker)
    strategy.initialize()
    
    print("\n[*] Esecuzione del metodo on_trading_iteration()...")
    strategy.on_trading_iteration()
    print("\n[+] Ciclo completato con successo senza errori!")

if __name__ == "__main__":
    run_test_cycle()
