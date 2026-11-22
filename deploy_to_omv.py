"""
Script di deployment automatico su server OpenMediaVault (Debian Linux).
Trasferisce tutti i file necessari tramite SFTP e verifica la presenza di Docker.
"""
import os
import sys
from pathlib import Path
import paramiko

SERVER_IP = "192.168.1.68"
SERVER_USER = "root"
SERVER_PASS = "admin"
REMOTE_DIR = "/root/nasdaq-quant-bot"

LOCAL_DIR = Path(__file__).resolve().parent

FILES_TO_UPLOAD = [
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "config.py",
    "strategy.py",
    "timestral_engine.py",
    "news_guardian.py",
    "main.py",
    ".env"
]

def deploy():
    print(f"[*] Connessione SSH a {SERVER_USER}@{SERVER_IP}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_IP, username=SERVER_USER, password=SERVER_PASS, timeout=10)
        print("[+] Connessione SSH riuscita con successo!")
        
        # 1. Verifica ambiente Docker sul server
        stdin, stdout, stderr = ssh.exec_command("docker --version && docker compose version")
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        print(f"[i] Stato Docker sul server:\n{out}")
        if err and "version" not in err.lower():
            print(f"[!] Warning Docker: {err}")

        # 2. Creazione cartella remota
        print(f"[*] Creazione cartella remota: {REMOTE_DIR} e {REMOTE_DIR}/data...")
        ssh.exec_command(f"mkdir -p {REMOTE_DIR}/data")
        
        # 3. Trasferimento file via SFTP
        print("[*] Avvio trasferimento file via SFTP...")
        sftp = ssh.open_sftp()
        for filename in FILES_TO_UPLOAD:
            local_path = LOCAL_DIR / filename
            remote_path = f"{REMOTE_DIR}/{filename}"
            if local_path.exists():
                print(f"  -> Uploading {filename}...")
                sftp.put(str(local_path), remote_path)
            else:
                print(f"  [!] File locale non trovato: {filename}")
        
        sftp.close()
        print("[+] Tutti i file sono stati trasferiti con successo!")

        # 4. Verifica elenco file sul server
        stdin, stdout, stderr = ssh.exec_command(f"ls -la {REMOTE_DIR}")
        print(f"\n[+] Contenuto della cartella {REMOTE_DIR} su OMV:\n{stdout.read().decode().strip()}")
        
        ssh.close()
        return True

    except Exception as e:
        print(f"[-] Errore durante il deploy: {e}")
        return False

if __name__ == "__main__":
    deploy()
