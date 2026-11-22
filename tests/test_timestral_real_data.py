"""
Test del modulo TimestralEngine sui dati reali di mercato estratti da Alpaca.
Verifichiamo la previsione su GOOGL (dopo il crollo del 12%) e su AAPL.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from timestral_engine import TimestralEngine
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

def test_real_stocks():
    print("[*] Recupero dati storici da Alpaca...")
    alpaca = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    
    symbols = ["GOOGL", "AAPL"]
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Hour, # Usiamo candele orarie per avere maggior contesto recente
        start=datetime.now() - timedelta(days=20)
    )
    bars = alpaca.get_stock_bars(req).df

    print("[*] Inizializzazione Timestral Engine...")
    engine = TimestralEngine(model_id="mistralai/Timestral-209M")

    for sym in symbols:
        df_sym = bars.loc[sym]
        closes = df_sym['close'].tolist()
        print(f"\n=======================================================")
        print(f" ANALISI QUANTITATIVA TIMESTRAL PER: {sym} ({len(closes)} candele)")
        print(f"=======================================================")
        res = engine.predict_quantiles(closes, prediction_length=6)
        
        print(f" Ultimo Prezzo Battuto:       ${res['last_price']:.2f}")
        print(f" Previsione Mediana (q0.50):  ${res['predicted_median']:.2f} ({res['expected_return_pct']:+.2f}%)")
        print(f" Stop Loss Dinamico (q0.05):  ${res['dynamic_stop_loss']:.2f} (Distanza: -{res['stop_distance_pct']:.2f}%)")
        print(f" Target Rialzista (q0.95):    ${res['target_high']:.2f}")
        print(f" Trend Bullish?               {res['is_bullish']}")
        print(f" Rilevato Falling Knife?      {res['is_falling_knife']}")
        print(f"=======================================================")

if __name__ == "__main__":
    test_real_stocks()
