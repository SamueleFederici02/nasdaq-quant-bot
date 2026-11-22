import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("192.168.1.68", username="root", password="admin", timeout=10)

print("--- STATO CONTAINER ---")
stdin, stdout, stderr = ssh.exec_command('docker ps --filter name=nasdaq_quant_bot --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"')
print(stdout.read().decode("utf-8", errors="ignore").strip())

print("\n--- LOGS CONTAINER (NASDAQ QUANT BOT) ---")
stdin, stdout, stderr = ssh.exec_command('docker logs --tail 35 nasdaq_quant_bot')
print(stdout.read().decode("utf-8", errors="ignore").strip())

ssh.close()
