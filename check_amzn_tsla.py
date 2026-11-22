import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.1.68", username="root", password="admin", timeout=10)

cmd = """docker exec nasdaq_quant_bot python -c "
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
from timestral_engine import TimestralEngine
from news_guardian import NewsGuardian

client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
engine = TimestralEngine('mistralai/Timestral-209M')
guardian = NewsGuardian()

for sym in ['AMZN', 'TSLA', 'NVDA']:
    req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Hour, start=datetime.now()-timedelta(days=15))
    bars = client.get_stock_bars(req).df
    closes = bars.loc[sym]['close'].tolist() if sym in bars.index else bars['close'].tolist()
    t = engine.predict_quantiles(closes)
    n = guardian.evaluate_symbol_safety(sym)
    print(f'=== {sym} ===')
    print('  Timestral Bullish:', t['is_bullish'], 'Expected Return:', f'{t[\"expected_return_pct\"]:+.2f}%', 'Stop Dist:', f'{t[\"stop_distance_pct\"]:.2f}%')
    print('  News Guardian Safe:', n['safe_to_enter'], 'Veto:', n['veto_active'], 'Reason:', n['reason'])
" """

stdin, stdout, stderr = ssh.exec_command(cmd)
print(stdout.read().decode("utf-8", errors="ignore").strip())

ssh.close()
