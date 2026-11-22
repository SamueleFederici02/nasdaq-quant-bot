"""
Build Docker su OMV con gestione sicura dei log.
"""
import sys
import paramiko

SERVER_IP = "192.168.1.68"
SERVER_USER = "root"
SERVER_PASS = "admin"
REMOTE_DIR = "/root/nasdaq-quant-bot"

def run_build():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SERVER_IP, username=SERVER_USER, password=SERVER_PASS, timeout=10)
    
    print("[*] Avvio build Docker su OMV...")
    stdin, stdout, stderr = ssh.exec_command(f"cd {REMOTE_DIR} && docker compose build 2>&1")
    
    # Leggiamo lo stream byte per byte o con utf-8 sicuro
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            data = stdout.channel.recv(1024).decode("utf-8", errors="replace")
            sys.stdout.write(data)
            sys.stdout.flush()
            
    exit_status = stdout.channel.recv_exit_status()
    # Leggiamo residui
    data = stdout.read().decode("utf-8", errors="replace")
    if data:
        sys.stdout.write(data)
        sys.stdout.flush()
        
    ssh.close()
    if exit_status == 0:
        print("\n[+] DOCKER BUILD COMPLETATA CON SUCCESSO SU OMV!")
        return True
    else:
        print(f"\n[-] Errore build. Exit code: {exit_status}")
        return False

if __name__ == "__main__":
    run_build()
