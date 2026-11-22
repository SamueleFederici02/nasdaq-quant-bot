"""
Lancia la build in background su OMV e monitora il log.
"""
import time
import paramiko

SERVER_IP = "192.168.1.68"
SERVER_USER = "root"
SERVER_PASS = "admin"
REMOTE_DIR = "/root/nasdaq-quant-bot"

def launch():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SERVER_IP, username=SERVER_USER, password=SERVER_PASS, timeout=10)
    
    # Rimuoviamo eventuale vecchio log e avviamo con nohup
    print("[*] Avvio docker compose build con nohup sul server OMV...")
    ssh.exec_command(f"rm -f {REMOTE_DIR}/build.log")
    ssh.exec_command(f"cd {REMOTE_DIR} && nohup docker compose build > {REMOTE_DIR}/build.log 2>&1 &")
    
    print("[+] Build avviata in background sul server OMV!")
    print("[*] Monitoraggio del file build.log in corso...")
    
    finished = False
    for _ in range(60): # Fino a 5-6 minuti di monitoraggio
        time.sleep(5)
        stdin, stdout, stderr = ssh.exec_command(f"tail -n 10 {REMOTE_DIR}/build.log")
        tail = stdout.read().decode("utf-8", errors="ignore").strip()
        
        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'docker.*build' | grep -v grep")
        ps = stdout.read().decode("utf-8", errors="ignore").strip()
        
        if not ps:
            print("\n[+] Processo di build terminato!")
            stdin, stdout, stderr = ssh.exec_command(f"tail -n 25 {REMOTE_DIR}/build.log")
            final_log = stdout.read().decode("utf-8", errors="ignore").strip()
            print("--- LOG FINALI DI BUILD ---")
            print(final_log)
            finished = True
            break
        else:
            print(".", end="", flush=True)
            
    ssh.close()
    return finished

if __name__ == "__main__":
    launch()
