import os
from pathlib import Path
from dotenv import load_dotenv

# Carica le variabili d'ambiente dal file .env se presente
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Credenziali Alpaca Markets (Paper Trading)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_API_SECRET") or os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "True").lower() in ("true", "1", "yes")
ALPACA_ENDPOINT = os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")

ALPACA_CONFIG = {
    "API_KEY": ALPACA_API_KEY,
    "API_SECRET": ALPACA_SECRET_KEY,
    "PAPER": ALPACA_PAPER,
    "ENDPOINT": ALPACA_ENDPOINT
}

# Credenziali Google Gemini (AI Studio)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Credenziali Finnhub
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# Parametri di Gestione del Rischio (Money Management & Portfolio Risk)
RISK_CONFIG = {
    "MAX_RISK_PER_TRADE": 0.01,       # 1% del capitale a rischio per operazione Long
    "MAX_RISK_PER_SHORT": 0.005,      # 0.5% del capitale a rischio per operazione Short (dimezzato)
    "MAX_POSITION_CAP": 0.20,         # Massimo 20% del capitale allocato su un singolo titolo Long
    "MAX_SHORT_CAP": 0.10,            # Massimo 10% del capitale allocato su un singolo titolo Short
    "MIN_CASH_BUFFER": 0.30,          # Almeno il 30% del portafoglio mantenuto in liquidità
    "MAX_OPEN_POSITIONS": 4,          # Massimo 4 posizioni aperte contemporaneamente
    "MAX_PORTFOLIO_VAR_PCT": 0.025,   # Massimo 2.5% del capitale di VaR 95% a 1 giorno per l'intero portafoglio
    "MAX_CLUSTER_POSITIONS": 2,       # Massimo 2 posizioni aperte contemporaneamente sullo stesso cluster di settore
    "CORRELATION_THRESHOLD": 0.65,    # Soglia correlazione elevata che attiva il ridimensionamento prudenziale (40% cut)
    "CLOSE_POSITIONS_AT_EOD": True,          # Chiudi tutte le posizioni speculative prima della fine della seduta per azzerare il rischio overnight (Flat overnight)
    "EOD_CLOSE_TIME_EST": "15:50",           # Orario di chiusura forzata in orario di New York (15:50 EST / 21:50 CET, 10 min prima della chiusura)
    "EOD_NO_NEW_POSITIONS_EST": "15:45",     # Nessuna nuova operazione aperta dopo le 15:45 EST (21:45 CET)
    "PROFIT_REENTRY_COOLDOWN_MIN": 15,       # Minuti di attesa prima di rientrare sullo stesso titolo dopo uscita in profitto/break-even (anti-flicker)
    "CRYPTO_SIZE_MULTIPLIER": 0.70,          # Riduzione prudenziale del 30% sulla size dei titoli crypto-fintech (MSTR, COIN, HOOD) ad altissimo beta
    "ANTI_EXHAUSTION_ATR_LIMIT": 3.2,        # Non inseguire ingressi se il prezzo dista più di 3.2x ATR orari dall'apertura (filtro anti-molla tesa / squeeze)
    "MARKET_OPEN_CALM_BUFFER_MIN": 10,       # Minuti di attesa dall'apertura di Wall Street (9:30-9:40 EST / 15:30-15:40 CET) prima di aprire nuovi trade (filtro anti-whipsaw di campana)
    "ACCELERATED_TENAGLIA_R_MULT": 2.0,      # Moltiplicatore R di profitto oltre il quale scatta la Tenaglia Accelerata (stringe il trailing stop per blindare l'85% del picco)
    "TIMESTRAL_LOOKBACK_DAYS": 28,           # Lookback storico candele orarie (28 giorni = 1 mese di borsa per contesto strutturale)
    "MAX_SKEW_RATIO_LONG": 2.0,              # Massimo rapporto Downside/Upside (D/U <= 2.0) per autorizzare ingressi LONG
    "MIN_SKEW_RATIO_SHORT": 0.75,            # Minimo rapporto Downside/Upside (D/U >= 0.75) per autorizzare ingressi SHORT
    "V_RECOVERY_BOUNCE_PCT": 0.90,           # Rimbalzo minimo dai minimi intraday di QQQ (+0.90%) per attivare il V-Shape Recovery Circuit Breaker
    "SHORT_MAX_QQQ_INTRADAY_PCT": 0.10,      # Massimo intraday di QQQ (+0.10%) oltre il quale qualsiasi operazione SHORT viene categoricamente vietata
    "SHORT_MIN_STOCK_INTRADAY_PCT": -0.50,   # Minimo ribasso intraday del titolo (-0.50%) richiesto per autorizzare uno SHORT (vietato shortare titoli verdi o piatti)
}

# Mappatura Cluster Settoriali per Anti-Concentrazione
SECTOR_CLUSTERS = {
    "SEMICONDUCTORS": ["NVDA", "AMD", "AVGO", "TSM", "ARM", "QCOM", "ASML", "SMH"],
    "BIG_TECH_SOFTWARE": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "ORCL", "NFLX", "PLTR", "SNOW", "DDOG", "CRWD", "PANW", "APP"],
    "CRYPTO_FINTECH": ["COIN", "MSTR", "HOOD", "PYPL"],
    "CONSUMER_DIVERSIFIED": ["TSLA", "COST", "LLY", "JPM", "UBER", "SPY", "QQQ"]
}

def get_symbol_cluster(symbol: str) -> str:
    """Restituisce il cluster settoriale di appartenenza per un dato ticker."""
    for cluster, symbols in SECTOR_CLUSTERS.items():
        if symbol in symbols:
            return cluster
    return "OTHER"

# Panieri Operativi per lo Scanner del Bot
WATCHLIST_CONFIG = {
    # 1. Benchmark e Macro ETF (Sentiment Generale & Chipmakers)
    "BENCHMARKS": ["QQQ", "SPY", "SMH"],

    # 2. Titani della Tecnologia & Monopoli Semiconduttori (Mega-Caps)
    "TECH_TITANS": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", 
        "TSLA", "AVGO", "TSM", "AMD", "ASML", "ORCL", "QCOM", "NFLX"
    ],

    # 3. Stabilizzatori Non-Tech (Diversificazione solida & Difensivi)
    "STABILIZERS": ["COST", "LLY", "JPM", "UBER"],

    # 4. Hyper-Growth & Nuove Promesse AI/Fintech/Cyber (Alta Volatilita')
    "GROWTH_STARS": [
        "PLTR", "ARM", "CRWD", "PANW", "COIN", 
        "APP", "SNOW", "HOOD", "MSTR", "DDOG"
    ]
}

# Unione complessiva per lo scanner di trading (~31 titoli ultra-liquidi)
DEFAULT_SYMBOLS = (
    WATCHLIST_CONFIG["BENCHMARKS"] +
    WATCHLIST_CONFIG["TECH_TITANS"] +
    WATCHLIST_CONFIG["STABILIZERS"] +
    WATCHLIST_CONFIG["GROWTH_STARS"]
)
