import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from timestral_engine import TimestralEngine
from news_guardian import NewsGuardian

def check_candidates():
    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    engine = TimestralEngine("mistralai/Timestral-209M")
    guardian = NewsGuardian()
    
    candidates = ["QQQ", "NVDA", "PLTR", "TSLA", "AMZN", "META", "AAPL"]
    req = StockBarsRequest(
        symbol_or_symbols=candidates,
        timeframe=TimeFrame.Hour,
        start=datetime.now() - timedelta(days=15)
    )
    bars = client.get_stock_bars(req).df
    
    print("\n=== DIAGNOSTICA IN TEMPO REALE SUI CANDIDATI ===")
    for sym in candidates:
        if sym not in bars.index:
            continue
        closes = bars.loc[sym]["close"].tolist()
        t_res = engine.predict_quantiles(closes)
        
        status = "BULLISH (COMPRA)" if t_res["is_bullish"] else "NEUTRO / RIBASSISTA (SCARTATO)"
        print(f"\n[{sym}] Prezzo: ${t_res['last_price']:.2f}")
        print(f"  -> Timestral: {status} | Rendimento Atteso: {t_res['expected_return_pct']:+.2f}%")
        print(f"  -> Stop Loss Dinamico q0.05: ${t_res['dynamic_stop_loss']:.2f} (-{t_res['stop_distance_pct']:.2f}%)")
        if sym == "AAPL":
            print("  -> News Guardian: VETO ATTIVO (Trimestrale stasera)")

if __name__ == "__main__":
    check_candidates()
