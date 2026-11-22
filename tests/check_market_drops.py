import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import FINNHUB_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import finnhub

def analyze_drops():
    alpaca = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    fh = finnhub.Client(api_key=FINNHUB_API_KEY)
    
    symbols = ["GOOGL", "IBM"]
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=7)
    )
    bars = alpaca.get_stock_bars(req).df
    print("\n--- DATI DI PREZZO ULTIMI 5 GIORNI ---")
    for sym in symbols:
        df_sym = bars.loc[sym]
        print(f"\n{sym}:")
        for idx, row in df_sym.iterrows():
            pct = 0.0
            print(f"  {idx.strftime('%Y-%m-%d')}: Close ${row['close']:.2f} | Vol: {int(row['volume']):,}")

    print("\n--- NOTIZIE RECENTI GOOGL (28-30 Ottobre) ---")
    goog_news = fh.company_news("GOOGL", _from="2026-10-28", to="2026-10-30")
    for n in goog_news[:5]:
        dt = datetime.fromtimestamp(n['datetime']).strftime('%Y-%m-%d %H:%M')
        print(f"  [{dt}] {n['headline']}")

if __name__ == "__main__":
    analyze_drops()
