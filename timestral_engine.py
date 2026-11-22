"""
Modulo Timestral Engine: Modello Foundation per Serie Temporali di Mistral AI.
Calcola previsioni probabilistiche a 21 quantili per identificare trend e Stop Loss Dinamici.
"""
import logging
from typing import Dict, Any, List, Optional
import numpy as np
import torch

from timestral import TimestralForecaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TimestralEngine")

class TimestralEngine:
    def __init__(self, model_id: str = "mistralai/Timestral-209M", device: str = "cpu"):
        self.model_id = model_id
        self.device = device
        self.forecaster: Optional[TimestralForecaster] = None
        self._load_model()

    def _load_model(self):
        """Carica il modello da Hugging Face su CPU."""
        logger.info(f"[*] Caricamento del modello {self.model_id} su {self.device}...")
        try:
            self.forecaster = TimestralForecaster.from_pretrained(
                self.model_id,
                device=self.device
            )
            logger.info(f"[+] Modello {self.model_id} caricato con successo!")
        except Exception as e:
            logger.error(f"[-] Errore caricamento {self.model_id}: {e}")
            logger.info("[*] Tentativo di fallback con versione leggera Timestral-28M...")
            self.model_id = "mistralai/Timestral-28M"
            self.forecaster = TimestralForecaster.from_pretrained(
                self.model_id,
                device=self.device
            )
            logger.info(f"[+] Fallback {self.model_id} completato con successo!")

    def predict_quantiles(
        self, 
        prices: List[float], 
        prediction_length: int = 5,
        max_skew_ratio_long: float = 2.0,
        min_skew_ratio_short: float = 0.75
    ) -> Dict[str, Any]:
        """
        Dato uno storico di prezzi di chiusura, prevede i quantili futuri.
        Restituisce il pavimento statistico (q0.05), la mediana (q0.50) e il tetto (q0.95),
        integrando Path Consistency Check (Anti-Fossa) e Asymmetric Quantile Skewness Filter.
        """
        if not self.forecaster:
            raise RuntimeError("Modello Timestral non inizializzato.")

        if len(prices) < 30:
            raise ValueError("Servono almeno 30 punti storici per un'inferenza affidabile.")

        # Timestral richiede un tensore con forma: (batch_size, num_variates, time_steps)
        context_np = np.array(prices, dtype=np.float32)
        context_tensor = torch.from_numpy(context_np).reshape(1, 1, -1).to(self.device)

        with torch.no_grad():
            forecast = self.forecaster.forecast(
                context=context_tensor,
                prediction_length=prediction_length
            )

        # Estraiamo i quantili desiderati: 0.05, 0.50 (mediana), 0.95
        q05 = forecast.quantile(0.05).squeeze().cpu().numpy()
        q50 = forecast.median.squeeze().cpu().numpy()
        q95 = forecast.quantile(0.95).squeeze().cpu().numpy()

        last_price = prices[-1]
        predicted_median_end = float(q50[-1]) if q50.ndim > 0 else float(q50)
        dynamic_stop_long = float(q05[0]) if q05.ndim > 0 else float(q05)
        dynamic_stop_short = float(q95[0]) if q95.ndim > 0 else float(q95)
        target_high = float(q95[-1]) if q95.ndim > 0 else float(q95)
        target_low = float(q05[-1]) if q05.ndim > 0 else float(q05)

        # Assicuriamoci che lo stop loss short sia effettivamente sopra il prezzo
        if dynamic_stop_short <= last_price:
            dynamic_stop_short = last_price * 1.012

        # Calcolo del trend atteso e distanze di stop
        expected_return_pct = ((predicted_median_end - last_price) / last_price) * 100
        stop_dist_long_pct = ((last_price - dynamic_stop_long) / last_price) * 100
        stop_dist_short_pct = ((dynamic_stop_short - last_price) / last_price) * 100

        # Rilevamento anomalie estreme:
        # Per i LONG: se il titolo è in caduta libera (-3.0% o candela -5%), evitiamo di comprare il coltello che cade.
        # Per gli SHORT: un trend ribassista (< -0.5%) è perfetto per vendere allo scoperto; evitiamo solo spike anomali rialzisti (short squeeze).
        is_falling_knife = (expected_return_pct < -3.0) or (prices[-1] < prices[-2] * 0.95)
        is_short_squeeze = (expected_return_pct > 3.0) or (prices[-1] > prices[-2] * 1.05)

        # -----------------------------------------------------------------
        # AFFINAMENTO 2: PATH CONSISTENCY CHECK (ANTI-FOSSA / EARLY DIP/SPIKE)
        # -----------------------------------------------------------------
        # Verifichiamo che la traiettoria mediana q50 nei primi 3 step (t+1, t+2, t+3)
        # non collassi verso lo stop loss prima di rimontare (falsa promessa o dip che prende lo stop)
        q50_list = [float(x) for x in (q50 if q50.ndim > 0 else [q50])]
        early_steps = q50_list[:min(3, len(q50_list))]

        # Per LONG: la flessione iniziale non deve scendere oltre l'80% dello stop e la tendenza finale deve superare il primo step
        min_early = min(early_steps) if early_steps else last_price
        early_drop_pct = max(0.0, ((last_price - min_early) / last_price) * 100)
        path_consistent_long = (early_drop_pct <= stop_dist_long_pct * 0.8) and (predicted_median_end > q50_list[0])

        # Per SHORT: lo spike iniziale non deve salire oltre l'80% dello stop e la tendenza finale deve scendere sotto il primo step
        max_early = max(early_steps) if early_steps else last_price
        early_spike_pct = max(0.0, ((max_early - last_price) / last_price) * 100)
        path_consistent_short = (early_spike_pct <= stop_dist_short_pct * 0.8) and (predicted_median_end < q50_list[0])

        # -----------------------------------------------------------------
        # AFFINAMENTO 3: ASYMMETRIC QUANTILE SKEWNESS FILTER (FAT-TAIL RISK)
        # -----------------------------------------------------------------
        # Dispersione Upside (q95 - q50) vs Downside (q50 - q05) a fine orizzonte
        upside_disp = max(0.001, target_high - predicted_median_end)
        downside_disp = max(0.001, predicted_median_end - target_low)
        skewness_ratio = round(downside_disp / upside_disp, 2)  # D / U

        # Per LONG: la coda ribassista non deve essere eccessivamente sbilanciata rispetto al potenziale rialzista (D/U <= max_skew_ratio_long, default 2.0)
        skew_ok_long = (skewness_ratio <= max_skew_ratio_long)

        # Per SHORT: la coda ribassista deve essere rilevante (D/U >= min_skew_ratio_short, default 0.75, ovvero U <= 1.33 * D)
        skew_ok_short = (skewness_ratio >= min_skew_ratio_short)

        is_bullish = (expected_return_pct > 0.5) and not is_falling_knife and path_consistent_long and skew_ok_long
        is_bearish = (expected_return_pct < -0.5) and not is_short_squeeze and path_consistent_short and skew_ok_short

        return {
            "last_price": last_price,
            "predicted_median": round(predicted_median_end, 2),
            "dynamic_stop_loss": round(dynamic_stop_long, 2),
            "dynamic_stop_loss_short": round(dynamic_stop_short, 2),
            "target_high": round(target_high, 2),
            "target_low": round(target_low, 2),
            "expected_return_pct": round(expected_return_pct, 2),
            "stop_distance_pct": round(stop_dist_long_pct, 2),
            "stop_distance_short_pct": round(stop_dist_short_pct, 2),
            "is_bullish": is_bullish,
            "is_bearish": is_bearish,
            "is_falling_knife": is_falling_knife,
            "is_short_squeeze": is_short_squeeze,
            "path_consistent_long": path_consistent_long,
            "path_consistent_short": path_consistent_short,
            "early_drop_pct": round(early_drop_pct, 2),
            "early_spike_pct": round(early_spike_pct, 2),
            "skewness_ratio": skewness_ratio,
            "skew_ok_long": skew_ok_long,
            "skew_ok_short": skew_ok_short,
            "raw_quantiles": {
                "q05": [round(float(x), 2) for x in (q05 if q05.ndim > 0 else [q05])],
                "q50": [round(float(x), 2) for x in (q50 if q50.ndim > 0 else [q50])],
                "q95": [round(float(x), 2) for x in (q95 if q95.ndim > 0 else [q95])],
            }
        }

if __name__ == "__main__":
    # Test sintetico rapido
    print("[*] Inizializzazione test TimestralEngine...")
    engine = TimestralEngine(model_id="mistralai/Timestral-209M")
    sample_prices = [100.0 + np.sin(i / 5.0) * 5.0 + (i * 0.1) for i in range(60)]
    result = engine.predict_quantiles(sample_prices)
    print("\nRisultato Previsione Quantili:")
    for k, v in result.items():
        if k != "raw_quantiles":
            print(f"  {k}: {v}")
