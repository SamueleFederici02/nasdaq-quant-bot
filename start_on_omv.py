"""
Script per avviare il container su OMV e verificare che sia attivo.
"""
import time
import paramiko

SERVER_IP = "192.168.1.68"
SERVER_USER = "root"
SERVER_PASS = "admin"
REMOTE_DIR = "/root/nasdaq-quant-bot"

def start_container():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SERVER_IP, username=SERVER_USER, password=SERVER_PASS, timeout=10)
    
    print("[*] Avvio di: docker compose up -d --build su OMV...")
    stdin, stdout, stderr = ssh.exec_command(
        f"cd {REMOTE_DIR} && docker compose up -d --build",
        get_pty=True
    )
    
    # Leggiamo lo stream senza bloccarci
    for line in iter(stdout.readline, ""):
        print(line, end="")
        
    exit_status = stdout.channel.recv_exit_status()
    print(f"\n[i] docker compose up completato con codice: {exit_status}")

    time.sleep(3)
    # Verifichiamo che il container sia attivo
    print("[*] Controllo dello stato del container...")
    stdin, stdout, stderr = ssh.exec_command("docker ps --filter 'name=nasdaq_quant_bot' --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
    print(stdout.read().decode())

    # Controlliamo i primi log del bot sul server
    print("[*] Primi log del bot da Docker:")
    stdin, stdout, stderr = ssh.exec_command("docker logs --tail 30 nasdaq_quant_bot")
    print(stdout.read().decode())
    
    ssh.close()
    return exit_status == 0

if __name__ == "__main__":
    start_container()
