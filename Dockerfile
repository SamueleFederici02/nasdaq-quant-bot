FROM python:3.10-slim

WORKDIR /app

# Dipendenze di sistema e git per compilazione leggera
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Aggiornamento pip
RUN pip install --no-cache-dir --upgrade pip

# Installazione PyTorch versione CPU ottimizzata
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Installazione del modello Timestral da GitHub
RUN pip install --no-cache-dir git+https://github.com/mistralai/timestral.git

# Installazione dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia dei sorgenti
COPY . .

# Output in tempo reale non bufferizzato
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
