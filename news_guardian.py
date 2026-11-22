"""
Modulo News Guardian: Scudo News, Calendario Trimestrali e Veto d'Emergenza.
Utilizza Finnhub per i dati e Google Gemini Flash-Lite per l'analisi NLP ultra-veloce.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import finnhub
from google import genai
from config import FINNHUB_API_KEY, GEMINI_API_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("NewsGuardian")

# Parole chiave che attivano l'analisi approfondita di Gemini
CRITICAL_KEYWORDS = [
    # Negativi / Shock
    "investigation", "antitrust", "doj", "probe", "subpoena", 
    "downgrade", "misses", "missed", "cuts guidance", "lowers guidance", 
    "warning", "lawsuit", "resigns", "fraud", "sec", "plunges", "slumps",
    # Positivi / Catalizzatori
    "beats", "record revenue", "raises guidance", "upgrade", 
    "partnership", "acquisition", "soars", "jumps"
]

class NewsGuardian:
    def __init__(self):
        self.finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None
        self.gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.model_name = "gemini-flash-lite-latest"
        self._earnings_cache: Dict[str, Any] = {} # cache_key -> (expiry_dt, result)
        self._safety_cache: Dict[str, Any] = {}   # cache_key -> (expiry_dt, result)

    def check_earnings_calendar(self, symbol: str, days_ahead: int = 1) -> Dict[str, Any]:
        """
        Verifica se il titolo ha una trimestrale in programma nei prossimi X giorni.
        Se c'e' una trimestrale imminente, scatta la regola dell'Earnings Blackout.
        Cache TTL: 4 ore.
        """
        cache_key = f"{symbol}_{days_ahead}"
        now = datetime.now()
        if cache_key in self._earnings_cache:
            exp, res = self._earnings_cache[cache_key]
            if now < exp:
                return res

        if not self.finnhub_client:
            return {"has_earnings_soon": False, "days_until": 999, "reason": "Finnhub non configurato"}

        today = datetime.now().date()
        future_date = today + timedelta(days=days_ahead)

        try:
            earnings = self.finnhub_client.earnings_calendar(
                _from=today.strftime("%Y-%m-%d"),
                to=future_date.strftime("%Y-%m-%d"),
                symbol=symbol
            )
            events = earnings.get("earningsCalendar", [])
            if events:
                event_date_str = events[0].get("date")
                event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
                days_until = (event_date - today).days
                hour = events[0].get("hour", "amc") # bmo = before open, amc = after close
                logger.warning(f"[!] EARNINGS ALERT per {symbol}: Trimestrale prevista il {event_date_str} ({hour}) [tra {days_until} giorni]")
                res = {
                    "has_earnings_soon": True,
                    "date": event_date_str,
                    "days_until": days_until,
                    "hour": hour,
                    "reason": f"Trimestrale imminente ({event_date_str}, {days_until}g)"
                }
            else:
                res = {"has_earnings_soon": False, "days_until": 999, "reason": "Nessuna trimestrale nei prossimi giorni"}
            self._earnings_cache[cache_key] = (now + timedelta(hours=4), res)
            return res
        except Exception as e:
            logger.error(f"Errore controllo calendario trimestrali per {symbol}: {e}")
            return {"has_earnings_soon": False, "days_until": 999, "reason": str(e)}

    def check_hard_exit_pre_earnings(self, symbol: str) -> Dict[str, Any]:
        """
        Verifica se una posizione aperta deve essere categoricamente chiusa
        perché la trimestrale è oggi pomeriggio (AMC) o domani mattina (BMO).
        Evita categoricamente di detenere il titolo durante l'annuncio.
        """
        chk = self.check_earnings_calendar(symbol, days_ahead=1)
        if chk.get("has_earnings_soon"):
            days = chk.get("days_until", 999)
            hour = chk.get("hour", "amc")
            if days == 0 or (days == 1 and hour == "bmo"):
                return {
                    "must_exit": True,
                    "reason": f"HARD EXIT PRE-EARNINGS: Trimestrale prevista il {chk.get('date')} ({hour})"
                }
        return {"must_exit": False, "reason": "Nessuna trimestrale imminente"}

    def fetch_recent_news(self, symbol: str, days_back: int = 2) -> List[Dict[str, Any]]:
        """Recupera le notizie recenti per il ticker da Finnhub."""
        if not self.finnhub_client:
            return []

        today = datetime.now().date()
        from_date = today - timedelta(days=days_back)
        try:
            news = self.finnhub_client.company_news(
                symbol,
                _from=from_date.strftime("%Y-%m-%d"),
                to=today.strftime("%Y-%m-%d")
            )
            return news[:15] # Prendiamo le prime 15 notizie piu' rilevanti
        except Exception as e:
            logger.error(f"Errore recupero news per {symbol}: {e}")
            return []

    def filter_critical_news(self, news_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filtra le notizie cercando parole chiave ad alto impatto per evitare chiamate inutili all'IA."""
        critical = []
        for item in news_list:
            headline = item.get("headline", "").lower()
            summary = item.get("summary", "").lower()
            text = f"{headline} {summary}"
            if any(kw in text for kw in CRITICAL_KEYWORDS):
                critical.append(item)
        return critical

    def analyze_news_with_gemini(self, symbol: str, headlines: List[str]) -> Dict[str, Any]:
        """
        Invia i titoli critici a Gemini Flash-Lite per ottenere uno score strutturato e l'eventuale veto.
        """
        if not self.gemini_client:
            return {"sentiment": 0.0, "emergency_exit": False, "reason": "Gemini non configurato"}

        joined_headlines = "\n- ".join(headlines[:5])
        prompt = f"""Sei un analista di trading quantitativo. Valuta l'impatto di queste recenti notizie per il titolo {symbol}:
- {joined_headlines}

Rispondi ESCLUSIVAMENTE con un JSON valido con questa struttura esatta:
{{
    "sentiment": <numero float tra -1.0 (disastroso) e +1.0 (ottimo)>,
    "emergency_exit": <true se c'è un crollo imminente/indagine grave/dimissioni/trimestrale disastrosa, altrimenti false>,
    "reason": "<massimo 1 frase concisa che spiega la motivazione>"
}}"""

        try:
            response = self.gemini_client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            raw_text = response.text.strip()
            # Pulizia markdown se presente
            if raw_text.startswith("```json"):
                raw_text = raw_text.replace("```json", "", 1)
            if raw_text.endswith("```"):
                raw_text = raw_text.rstrip("```")
            return json.loads(raw_text.strip())
        except Exception as e:
            logger.error(f"Errore analisi Gemini per {symbol}: {e}")
            return {"sentiment": 0.0, "emergency_exit": False, "reason": f"Errore parsing: {e}"}

    def evaluate_symbol_safety(self, symbol: str, side: str = "long") -> Dict[str, Any]:
        """
        Valutazione complessiva di sicurezza per il titolo (Long o Short).
        Combina il controllo delle trimestrali e l'analisi qualitativa delle notizie.
        Cache TTL: 15 minuti (per evitare quota limit / 429 su Gemini e Finnhub).
        """
        cache_key = f"{symbol}_{side.lower()}"
        now = datetime.now()
        if cache_key in self._safety_cache:
            exp, res = self._safety_cache[cache_key]
            if now < exp:
                return res

        logger.info(f"[*] Valutazione di sicurezza News & Trimestrali per {symbol} (lato {side.upper()})...")

        # 1. Controllo Earnings Blackout (1 giorno per Long per permettere il run-up, 3 giorni per Short)
        days_ahead = 3 if side.lower() == "short" else 1
        earnings_check = self.check_earnings_calendar(symbol, days_ahead=days_ahead)
        if earnings_check.get("has_earnings_soon"):
            res = {
                "symbol": symbol,
                "safe_to_enter": False,
                "emergency_exit_required": False,
                "sentiment_score": 0.0,
                "veto_active": True,
                "reason": f"VETO TRIMESTRALE ({side.upper()}): {earnings_check['reason']}"
            }
            self._safety_cache[cache_key] = (now + timedelta(minutes=15), res)
            return res

        # 2. Recupero News e Filtro Parole Chiave
        news = self.fetch_recent_news(symbol, days_back=2)
        critical_news = self.filter_critical_news(news)

        if not critical_news:
            res = {
                "symbol": symbol,
                "safe_to_enter": True,
                "emergency_exit_required": False,
                "sentiment_score": 0.0,
                "veto_active": False,
                "reason": "Nessuna notizia critica o rumor ad alto impatto nelle ultime 48h"
            }
            self._safety_cache[cache_key] = (now + timedelta(minutes=15), res)
            return res

        # 3. Analisi Approfondita con Gemini Flash-Lite
        headlines = [item.get("headline", "") for item in critical_news]
        analysis = self.analyze_news_with_gemini(symbol, headlines)
        
        sentiment = analysis.get("sentiment", 0.0)
        emergency_exit = analysis.get("emergency_exit", False)
        reason = analysis.get("reason", "")

        # Se andiamo Long: non vogliamo sentiment negativo
        # Se andiamo Short: non vogliamo sentiment fortemente positivo (es. buyout, upgrade massivo)
        if side.lower() == "short":
            is_safe = (sentiment <= 0.30)
        else:
            is_safe = (sentiment >= -0.25) and not emergency_exit

        res = {
            "symbol": symbol,
            "safe_to_enter": is_safe,
            "emergency_exit_required": emergency_exit if side.lower() == "long" else (sentiment > 0.60),
            "sentiment_score": sentiment,
            "veto_active": not is_safe,
            "reason": f"Gemini [{sentiment:+.2f}]: {reason}"
        }
        self._safety_cache[cache_key] = (now + timedelta(minutes=15), res)
        return res

if __name__ == "__main__":
    guardian = NewsGuardian()
    for test_ticker in ["AAPL", "IBM", "GOOGL"]:
        res = guardian.evaluate_symbol_safety(test_ticker)
        print(f"\nRisultato per {test_ticker}:")
        print(json.dumps(res, indent=2))
