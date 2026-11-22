import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.1.68", username="root", password="admin", timeout=10)

cmd = """docker exec nasdaq_quant_bot python -c "
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, DEFAULT_SYMBOLS
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta
import pandas as pd

client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
req = StockBarsRequest(symbol_or_symbols=DEFAULT_SYMBOLS[:15], timeframe=TimeFrame.Hour, start=datetime.now()-timedelta(days=2))
bars = client.get_stock_bars(req).df

print('=== VARIAZIONI DI OGGI PER I TITOLI SCANSIONATI ===')
for sym in DEFAULT_SYMBOLS[:15]:
    if sym in bars.index:
        df_s = bars.loc[sym]
        if len(df_s) >= 2:
            open_p = df_s.iloc[0]['open']
            curr_p = df_s.iloc[-1]['close']
            pct = ((curr_p - open_p) / open_p) * 100
            print(f'{sym:6s}: Open ${open_p:.2f} -> Attuale ${curr_p:.2f} ({pct:+.2f}%)')
" """

stdin, stdout, stderr = ssh.exec_command(cmd)
print(stdout.read().decode("utf-8", errors="ignore").strip())
err = stderr.read().decode("utf-8", errors="ignore").strip()
if err:
    print("ERR:", err)

ssh.close()
