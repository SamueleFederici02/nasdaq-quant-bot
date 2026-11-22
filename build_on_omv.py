"""
Script per avviare la build dell'immagine Docker sul server OMV.
"""
import paramiko

SERVER_IP = "192.168.1.68"
SERVER_USER = "root"
SERVER_PASS = "admin"
REMOTE_DIR = "/root/nasdaq-quant-bot"

def build():
    print(f"[*] Connessione SSH a {SERVER_USER}@{SERVER_IP} per avviare la build Docker...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(SERVER_IP, username=SERVER_USER, password=SERVER_PASS, timeout=10)
        print("[+] Connesso! Avvio di: docker compose build...")
        
        # Eseguiamo il build e stampiamo l'output
        stdin, stdout, stderr = ssh.exec_command(f"cd {REMOTE_DIR} && docker compose build")
        for line in iter(stdout.readline, ""):
            print(line, end="")
            
        exit_status = stdout.channel.recv_exit_status()
        if exit_status == 0:
            print("\n[+] BUILD DOCKER COMPLETATA CON SUCCESSO SUL SERVER OMV!")
            return True
        else:
            print(f"\n[-] Errore durante la build (Exit code {exit_status}):")
            print(stderr.read().decode())
            return False

    except Exception as e:
        print(f"[-] Errore SSH: {e}")
        return False
    finally:
        ssh.close()

if __name__ == "__main__":
    build()
