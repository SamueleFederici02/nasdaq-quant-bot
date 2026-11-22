import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.1.68", username="root", password="admin", timeout=10)

cmd = "docker exec nasdaq_quant_bot python -c \"from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER; from alpaca.trading.client import TradingClient; c = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER); print('VALORE:', c.get_account().portfolio_value); print('CASH:', c.get_account().cash); [print(p.symbol, p.qty, 'Entry:', p.avg_entry_price, 'Now:', p.current_price, 'PL:', p.unrealized_pl) for p in c.get_all_positions()]\""

stdin, stdout, stderr = ssh.exec_command(cmd)
print("--- OUTPUT DA DOCKER SU OMV ---")
print(stdout.read().decode("utf-8", errors="ignore").strip())
err = stderr.read().decode("utf-8", errors="ignore").strip()
if err:
    print("ERR:", err)

ssh.close()
