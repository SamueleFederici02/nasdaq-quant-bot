"""
Modulo Adaptive Learner & Contextual Bandits per il Nasdaq-100 Quant Bot.
Apprende continuamente dai risultati delle operazioni (PnL, MFE, MAE) per:
1. Calibrare i pesi di allocazione per ticker Q(symbol) - Contextual Bandits.
2. Auto-regolare dinamicamente i parametri di rischio (ATR cushion, soglia Break-Even).
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("AdaptiveLearner")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "data" / "adaptive_state.json"

DEFAULT_STATE = {
    "version": "1.0",
    "updated_at": datetime.now().isoformat(),
    "ticker_q_scores": {
        "PLTR": 0.35,
        "MSFT": 0.20,
        "ORCL": 0.20,
        "QCOM": -0.15,
        "APP": -0.30
    },
    "meta_params": {
        "atr_multiplier": 1.5,
        "min_stop_pct": 1.8,
        "break_even_pct": 0.80,
        "break_even_protect_pct": 0.25
    },
    "active_peaks": {
        "PLTR": 176.45
    },
    "trade_history": []
}

def compute_infinite_profit_lock(
    entry_price: float,
    peak_price: float,
    is_short: bool = False,
    min_trigger_pct: float = 0.80
) -> Dict[str, Any]:
    """
    Algoritmo Cricchetto Profit-Lock ad Alta Frequenza e Scaglioni Progressivi Infiniti.
    Calcola lo stop progressivo che blinda porzioni crescenti di guadagno in modo serrato:
      - Sotto 0.80%: inattivo (si usa lo stop iniziale basato su ATR)
      - Tra 0.80% e 1.29% (Tier 1): blinda +0.25% (Break-Even iniziale, rischio azzerato)
      - Tra 1.30% e 1.79% (Tier 2): blinda +0.70% (Primi profitti monetizzati al sicuro)
      - Tra 1.80% e 2.39% (Tier 3): blinda +1.20% (Consolidamento guadagno)
      - Tra 2.40% e 2.99% (Tier 4): blinda +1.80%
      - Tra 3.00% e 3.79% (Tier 5): blinda +2.40%
      - Per guadagni >= 3.80% (Tier 6+ all'infinito):
        blinda (gain_pct - 0.60)% lasciando un respiro serrato di soli 0.6% dal picco massimo!
    """
    if is_short:
        gain_pct = ((entry_price - peak_price) / entry_price) * 100 if entry_price > 0 else 0.0
    else:
        gain_pct = ((peak_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0

    if gain_pct < min_trigger_pct:
        return {"active": False, "tier": 0, "gain_pct": round(gain_pct, 2), "lock_pct": 0.0, "stop_price": 0.0}

    if gain_pct < 1.30:
        tier = 1
        lock_pct = 0.25
    elif gain_pct < 1.80:
        tier = 2
        lock_pct = 0.70
    elif gain_pct < 2.40:
        tier = 3
        lock_pct = 1.20
    elif gain_pct < 3.00:
        tier = 4
        lock_pct = 1.80
    elif gain_pct < 3.80:
        tier = 5
        lock_pct = 2.40
    else:
        tier = 5 + int((gain_pct - 3.80) / 0.5) + 1
        lock_pct = min(gain_pct - 0.60, round(2.40 + (gain_pct - 3.80) * 0.85, 2))

    if is_short:
        stop_price = round(entry_price * (1.0 - lock_pct / 100.0), 2)
    else:
        stop_price = round(entry_price * (1.0 + lock_pct / 100.0), 2)

    return {
        "active": True,
        "tier": tier,
        "gain_pct": round(gain_pct, 2),
        "lock_pct": round(lock_pct, 2),
        "stop_price": stop_price
    }

class AdaptiveLearner:
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = Path(state_file)
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Carica lo stato persistente dal file JSON o inizializza con i default."""
        try:
            if self.state_file.exists():
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Verifica chiavi minime
                    if "ticker_q_scores" in data and "meta_params" in data:
                        return data
        except Exception as e:
            logger.warning(f"Errore lettura stato adattivo: {e}. Inizializzazione default.")

        # Inizializza cartella e file di default
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._save_state(DEFAULT_STATE)
        return DEFAULT_STATE.copy()

    def _save_state(self, state: Dict[str, Any] = None):
        """Salva lo stato adattivo su file JSON."""
        if state is None:
            state = self.state
        state["updated_at"] = datetime.now().isoformat()
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Errore salvataggio stato adattivo: {e}")

    def get_ticker_multiplier(self, symbol: str) -> float:
        """
        Calcola il moltiplicatore di posizione (Punto B: Contextual Bandits).
        Restituisce un valore continuo tra 0.40x e 1.30x.
        - Q > 0 (vincite recenti): aumenta la size per sfruttare il momentum del titolo.
        - Q < 0 (perdite recenti): riduce la size prudentemente (MAI zero, niente panchina forzata!).
        """
        q = self.state["ticker_q_scores"].get(symbol, 0.0)
        # Formula: 1.0 + (Q * 0.5), limitato tra 0.40 e 1.30
        mult = 1.0 + (q * 0.5)
        return max(0.40, min(1.30, round(mult, 2)))

    def get_meta_params(self) -> Dict[str, float]:
        """Restituisce i parametri di trading adattivi auto-calibrati (Punto C)."""
        return self.state.get("meta_params", DEFAULT_STATE["meta_params"]).copy()

    def get_peak_price(self, symbol: str, default: float) -> float:
        """Restituisce il prezzo di picco memorizzato per la posizione."""
        peaks = self.state.setdefault("active_peaks", {})
        return peaks.get(symbol, default)

    def update_peak_price(self, symbol: str, current_price: float, is_short: bool = False) -> float:
        """Aggiorna il picco favorevole massimo della posizione (MFE) e lo persiste."""
        peaks = self.state.setdefault("active_peaks", {})
        current_peak = peaks.get(symbol, current_price)
        new_peak = min(current_peak, current_price) if is_short else max(current_peak, current_price)
        if new_peak != current_peak or symbol not in peaks:
            peaks[symbol] = new_peak
            self._save_state()
        return new_peak

    def clear_peak_price(self, symbol: str):
        """Rimuove il picco memorizzato alla chiusura della posizione."""
        peaks = self.state.setdefault("active_peaks", {})
        if symbol in peaks:
            peaks.pop(symbol, None)
            self._save_state()

    def update_adverse_price(self, symbol: str, current_price: float, is_short: bool = False) -> float:
        """Aggiorna la massima escursione avversa della posizione (MAE) e la persiste."""
        adverses = self.state.setdefault("active_adverses", {})
        current_adv = adverses.get(symbol, current_price)
        new_adv = max(current_adv, current_price) if is_short else min(current_adv, current_price)
        if new_adv != current_adv or symbol not in adverses:
            adverses[symbol] = new_adv
            self._save_state()
        return new_adv

    def clear_adverse_price(self, symbol: str):
        """Rimuove l'avverso memorizzato alla chiusura della posizione."""
        adverses = self.state.setdefault("active_adverses", {})
        if symbol in adverses:
            adverses.pop(symbol, None)
            self._save_state()

    def record_trade_result(
        self,
        symbol: str,
        side: str,
        pnl_pct: float,
        pnl_dollars: float,
        exit_reason: str,
        peak_profit_pct: float = 0.0,
        mae_pct: float = 0.0
    ):
        """
        Funzione di aggiornamento del Reinforcement Learning con Sortino Reward & MFE/MAE:
        1. Calcola il Capture Ratio (efficienza di cattura del trend).
        2. Penalizza il reward per Downside Deviation (Sortino Penalty) se il trade ha subito forte sofferenza prima di chiudersi.
        3. Aggiorna il punteggio Q del ticker con Exponential Moving Average.
        4. Auto-calibra i parametri meta (ATR multiplier e soglia Break-Even).
        """
        # Calcolo Capture Ratio (PnL Realizzato vs MFE)
        capture_ratio = 0.0
        if pnl_pct > 0 and peak_profit_pct > 0:
            capture_ratio = round(min(1.0, max(0.0, pnl_pct / peak_profit_pct)), 2)

        # Calcolo Sortino Downside Penalty
        downside_penalty = 0.0
        if mae_pct < -0.5:
            # Penalità proporzionale alla sofferenza oltre -0.5%
            downside_excess = abs(mae_pct) - 0.5
            downside_penalty = min(0.50, round(downside_excess * 0.15, 3))

        logger.info(f"[*] ADAPTIVE LEARNING: Ricevuto esito trade {symbol} ({pnl_pct:+.2f}%, ${pnl_dollars:+.2f}, motivo: {exit_reason}) | MFE: {peak_profit_pct:+.2f}% | MAE: {mae_pct:+.2f}% | Capture: {capture_ratio*100:.0f}%")
        
        # 1. Aggiornamento Punteggio Q del Titolo con Sortino Reward
        if pnl_pct >= 0:
            base_reward = min(1.0, pnl_pct / 2.0)
            normalized_reward = max(0.0, base_reward - downside_penalty)
            if downside_penalty > 0:
                logger.info(f"[*] SORTINO PENALTY per {symbol}: reward ridotto da {base_reward:+.2f} a {normalized_reward:+.2f} causa MAE ({mae_pct:+.2f}%)")
        else:
            normalized_reward = max(-1.0, pnl_pct / 2.0)

        current_q = self.state["ticker_q_scores"].get(symbol, 0.0)
        decay = 0.85 # Tasso di memoria / decadimento verso la media
        new_q = (current_q * decay) + (normalized_reward * (1.0 - decay))
        self.state["ticker_q_scores"][symbol] = round(new_q, 3)

        # 2. Auto-Calibrazione Parametri Meta (Self-Tuning MFE / MAE)
        meta = self.state["meta_params"]

        # Caso A: Uscita per Stop Loss e perdita netta
        if pnl_pct < 0 and exit_reason == "stop_loss":
            # Se prendiamo stop loss frequenti, allarghiamo il cuscinetto di respiro (anti-whipsaw)
            meta["atr_multiplier"] = min(2.2, round(meta["atr_multiplier"] + 0.05, 2))
            meta["min_stop_pct"] = min(2.5, round(meta["min_stop_pct"] + 0.05, 2))
            logger.info(f"[+] SELF-TUNING: Aumentato respiro ATR a {meta['atr_multiplier']}x (Min stop: {meta['min_stop_pct']}%) per contrastare volatilità")

        # Caso B: Il trade era salito bene (MFE >= +0.8%) ma poi ha ritracciato in perdita/pareggio
        if peak_profit_pct >= 0.8 and pnl_pct <= 0.2:
            meta["break_even_pct"] = max(0.75, round(meta["break_even_pct"] - 0.05, 2))
            logger.info(f"[+] SELF-TUNING: Anticipata soglia Break-Even a +{meta['break_even_pct']}% per congelare i profitti più tempestivamente")

        # Caso C: Trade con ottimo guadagno (> +1.5%) e alto capture ratio
        if pnl_pct > 1.5 and capture_ratio >= 0.50:
            if meta["atr_multiplier"] > 1.5:
                meta["atr_multiplier"] = max(1.5, round(meta["atr_multiplier"] - 0.03, 2))

        # 3. Registra evento nello storico con metriche MFE/MAE
        history_entry = {
            "symbol": symbol,
            "side": side,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_dollars": round(pnl_dollars, 2),
            "exit_reason": exit_reason,
            "peak_profit_pct": round(peak_profit_pct, 2), # MFE
            "max_adverse_pct": round(mae_pct, 2),         # MAE
            "capture_ratio": capture_ratio,
            "q_score_after": round(new_q, 3),
            "timestamp": datetime.now().isoformat()
        }
        self.state["trade_history"].append(history_entry)
        if len(self.state["trade_history"]) > 50:
            self.state["trade_history"] = self.state["trade_history"][-50:]

        self._save_state()
        logger.info(f"[+] ADAPTIVE LEARNING: Stato aggiornato! Q({symbol}) = {new_q:+.3f} (Moltiplicatore Size: {self.get_ticker_multiplier(symbol):.2f}x)")

if __name__ == "__main__":
    learner = AdaptiveLearner()
    print("Ticker Multipliers:")
    for s in ["PLTR", "MSFT", "APP", "ORCL", "NVDA"]:
        print(f"  {s}: Q={learner.state['ticker_q_scores'].get(s, 0.0):+.3f} -> Multiplier: {learner.get_ticker_multiplier(s):.2f}x")
    print("\nMeta Parameters:")
    print(learner.get_meta_params())
