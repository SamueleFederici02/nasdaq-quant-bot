"""
Strategia Quantitativa Nasdaq-100: Timestral-209M + Scudo News + Money Management (Regola 1%).
Implementata su framework Lumibot per esecuzione pulita e robusta su Alpaca Paper.
"""
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
import pandas as pd
import numpy as np

from lumibot.strategies.strategy import Strategy
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import ALPACA_CONFIG, RISK_CONFIG, DEFAULT_SYMBOLS, get_symbol_cluster
from timestral_engine import TimestralEngine
from news_guardian import NewsGuardian
from notifier import send_notification
from adaptive_learner import AdaptiveLearner, compute_infinite_profit_lock

logger = logging.getLogger("NasdaqTimestralStrategy")

def compute_atr(df_sym, period: int = 14, default_pct: float = 0.018) -> float:
    """Calcola l'Average True Range (ATR) orario per garantire un cuscinetto di respiro anti-rumore."""
    try:
        highs = df_sym["high"].tolist()
        lows = df_sym["low"].tolist()
        closes = df_sym["close"].tolist()
        curr_price = float(closes[-1])
        tr_list = []
        n = min(period + 1, len(closes))
        for i in range(1, n):
            h = float(highs[-i])
            l = float(lows[-i])
            prev_c = float(closes[-i - 1])
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
        if tr_list:
            atr = sum(tr_list) / len(tr_list)
            return max(atr, curr_price * default_pct)
        return curr_price * default_pct
    except Exception:
        closes = df_sym["close"].tolist() if "close" in df_sym else [100.0]
        return float(closes[-1]) * default_pct

class NasdaqTimestralStrategy(Strategy):
    parameters = {
        "symbols": DEFAULT_SYMBOLS,
        "sleeptime": "30S",                                  # Frequenza del ciclo di monitoraggio
        "risk_pct": RISK_CONFIG["MAX_RISK_PER_TRADE"],       # 1% capitale a rischio Long
        "short_risk_pct": RISK_CONFIG["MAX_RISK_PER_SHORT"], # 0.5% capitale a rischio Short
        "max_cap": RISK_CONFIG["MAX_POSITION_CAP"],          # Cap 20% su singolo titolo Long
        "short_max_cap": RISK_CONFIG["MAX_SHORT_CAP"],       # Cap 10% su singolo titolo Short
        "cash_buffer": RISK_CONFIG["MIN_CASH_BUFFER"],       # Almeno 30% in cash
        "max_positions": RISK_CONFIG["MAX_OPEN_POSITIONS"],  # Massimo 4 titoli contemporanei
        "max_portfolio_var_pct": RISK_CONFIG.get("MAX_PORTFOLIO_VAR_PCT", 0.025), # 2.5% VaR 95% 1-day budget
        "max_cluster_positions": RISK_CONFIG.get("MAX_CLUSTER_POSITIONS", 2),     # max 2 posizioni per cluster di settore
        "correlation_threshold": RISK_CONFIG.get("CORRELATION_THRESHOLD", 0.65),  # soglia correlazione elevata (taglio 40% size)
        "close_positions_at_eod": RISK_CONFIG.get("CLOSE_POSITIONS_AT_EOD", True), # Flat cash overnight anti-gap
        "eod_close_time_est": RISK_CONFIG.get("EOD_CLOSE_TIME_EST", "15:50"),       # 15:50 EST (21:50 CET)
        "eod_no_new_positions_est": RISK_CONFIG.get("EOD_NO_NEW_POSITIONS_EST", "15:45"), # Blocco ingressi pre-close
        "profit_reentry_cooldown_min": RISK_CONFIG.get("PROFIT_REENTRY_COOLDOWN_MIN", 15), # Minuti di pausa post gain/BE
        "crypto_size_multiplier": RISK_CONFIG.get("CRYPTO_SIZE_MULTIPLIER", 0.70),          # Riduzione size crypto/high beta
        "anti_exhaustion_atr_limit": RISK_CONFIG.get("ANTI_EXHAUSTION_ATR_LIMIT", 3.2),    # Limite max estensione da open
        "market_open_calm_buffer_min": RISK_CONFIG.get("MARKET_OPEN_CALM_BUFFER_MIN", 10), # Buffer di calma nei primi 10 min di campana (15:30-15:40 CET)
        "accelerated_tenaglia_r_mult": RISK_CONFIG.get("ACCELERATED_TENAGLIA_R_MULT", 2.0), # Trigger >2R per Tenaglia Accelerata (lock 85% picco)
        "timestral_lookback_days": RISK_CONFIG.get("TIMESTRAL_LOOKBACK_DAYS", 28),         # Lookback storico candele orarie (28 giorni = 1 mese di borsa per contesto strutturale)
        "max_skew_ratio_long": RISK_CONFIG.get("MAX_SKEW_RATIO_LONG", 2.0),               # Max Downside/Upside ratio per LONG
        "min_skew_ratio_short": RISK_CONFIG.get("MIN_SKEW_RATIO_SHORT", 0.75),             # Min Downside/Upside ratio per SHORT
    }

    def initialize(self):
        """Inizializzazione una tantum: carica IA, scudo news e client dati."""
        logger.info("[*] Inizializzazione strategia Nasdaq-100 Quant...")
        self.sleeptime = self.parameters["sleeptime"]
        
        # 1. Carica il motore predittivo Timestral-209M su CPU
        self.timestral = TimestralEngine(model_id="mistralai/Timestral-209M")
        
        # 2. Carica lo scudo news (Calendario Trimestrali + Gemini Flash-Lite)
        self.news_guardian = NewsGuardian()
        
        # 3. Client Alpaca per recupero candele storiche
        self.alpaca_data = StockHistoricalDataClient(
            ALPACA_CONFIG["API_KEY"],
            ALPACA_CONFIG["API_SECRET"]
        )
        
        # Dizionario per tracciare stop loss dinamici, prezzi di carico e orari aggiornamento
        self.active_stops: Dict[str, float] = {}
        self.entry_prices: Dict[str, float] = {}
        self.last_stop_update: Dict[str, datetime] = {}
        self.pending_order_times: Dict[str, datetime] = {}
        self.ticker_cooldowns: Dict[str, datetime] = {}
        self.peak_profits: Dict[str, float] = {}
        self.max_adverse_excursions: Dict[str, float] = {}
        self.notified_tiers: Dict[str, int] = {}
        self.notified_tenaglia: Dict[str, bool] = {}
        
        # Spunto 1: Macro Regime Switch QQQ con Isteresi & Debounce
        self.last_macro_regime: str = "NEUTRAL"
        self.last_macro_regime_change: datetime = datetime.now() - timedelta(minutes=60)
        self.last_macro_check: datetime = datetime.min
        self.current_macro_info: Dict[str, Any] = {"regime": "NEUTRAL", "allow_long": True, "allow_short": True, "qqq_intraday_pct": 0.0}

        # Batch Market Trends & Portfolio Risk Metrics (Covariance & Quality Momentum)
        self.daily_sma20: Dict[str, float] = {}
        self.rs_scores: Dict[str, float] = {}
        self.quality_rs_scores: Dict[str, float] = {}
        self.daily_returns_df: pd.DataFrame = pd.DataFrame()
        self.corr_matrix: pd.DataFrame = pd.DataFrame()
        self.cov_matrix: pd.DataFrame = pd.DataFrame()
        self.last_batch_trend_update: datetime = datetime.min

        # Modulo Adaptive Learner (Contextual Bandits + Self-Tuning Parametri)
        self.learner = AdaptiveLearner()
        logger.info("[+] Strategia inizializzata e pronta per il monitoraggio!")

    def evaluate_macro_regime(self) -> Dict[str, Any]:
        """
        Macro Regime Switch su QQQ (Nasdaq-100 ETF) con Isteresi (Deadband) & Debounce Temporale.
        Evita il continuo flickering/whipsaw tra LONG e SHORT:
        - Per entrare in BULLISH: QQQ >= +0.70% e sopra EMA20 (oppure >= +1.2%).
        - Per USCIRE da BULLISH a NEUTRAL: QQQ deve scendere sotto +0.20% oppure sotto EMA20 con <= +0.35%.
        - Per entrare in BEARISH: QQQ <= -0.80% e sotto EMA20 (oppure <= -1.2%).
        - Per USCIRE da BEARISH a NEUTRAL: QQQ deve salire sopra -0.20% oppure sopra EMA20 con >= -0.35%.
        - Cooldown minimo tra cambi di regime: 15 minuti (salvo flash-crash <= -1.8%).
        """
        now = datetime.now()
        if (now - self.last_macro_check).total_seconds() < 120 and self.current_macro_info:
            return self.current_macro_info

        try:
            req_qqq = StockBarsRequest(
                symbol_or_symbols="QQQ",
                timeframe=TimeFrame.Hour,
                start=now - timedelta(days=6)
            )
            df_q_raw = self.alpaca_data.get_stock_bars(req_qqq).df
            df_q = df_q_raw.loc["QQQ"] if "QQQ" in df_q_raw.index else df_q_raw
            if len(df_q) >= 20:
                q_closes = df_q["close"].tolist()
                ema20 = float(pd.Series(q_closes).ewm(span=20, adjust=False).mean().iloc[-1])
                curr_q = float(q_closes[-1])
                dist_ema = ((curr_q - ema20) / ema20) * 100
                day_open = float(df_q["open"].iloc[-min(7, len(df_q))])
                intraday_pct = ((curr_q - day_open) / day_open) * 100

                curr_regime = self.last_macro_regime
                candidate_regime = curr_regime

                # 1. Logica di Isteresi (Banda Morta / Deadband)
                if curr_regime == "RISK_ON_BULLISH":
                    if intraday_pct <= -0.80 and curr_q < ema20:
                        candidate_regime = "RISK_OFF_BEARISH"
                    elif intraday_pct < 0.20 or (curr_q < ema20 and intraday_pct < 0.35):
                        candidate_regime = "NEUTRAL"
                    else:
                        candidate_regime = "RISK_ON_BULLISH"
                elif curr_regime == "RISK_OFF_BEARISH":
                    if intraday_pct >= 0.70 and curr_q > ema20:
                        candidate_regime = "RISK_ON_BULLISH"
                    elif intraday_pct > -0.20 or (curr_q > ema20 and intraday_pct > -0.35):
                        candidate_regime = "NEUTRAL"
                    else:
                        candidate_regime = "RISK_OFF_BEARISH"
                else: # NEUTRAL
                    if intraday_pct <= -1.2 or (curr_q < ema20 and intraday_pct <= -0.80):
                        candidate_regime = "RISK_OFF_BEARISH"
                    elif intraday_pct >= +1.2 or (curr_q > ema20 and intraday_pct >= +0.70):
                        candidate_regime = "RISK_ON_BULLISH"
                    else:
                        candidate_regime = "NEUTRAL"

                # 2. Debounce / Cooldown temporale (almeno 15 minuti tra cambi, salvo flash crash <= -1.8%)
                time_since_switch = (now - self.last_macro_regime_change).total_seconds()
                is_emergency_crash = (intraday_pct <= -1.8)

                target_regime = curr_regime
                if candidate_regime != curr_regime:
                    if time_since_switch >= 900 or is_emergency_crash:
                        target_regime = candidate_regime
                    else:
                        rem_m = int((900 - time_since_switch) / 60)
                        logger.info(f"[*] Macro Regime: cambio candidato {curr_regime} -> {candidate_regime} in attesa di conferma debounce ({rem_m}m rimanenti)")
                        target_regime = curr_regime

                allow_long = (target_regime != "RISK_OFF_BEARISH")
                allow_short = (target_regime != "RISK_ON_BULLISH")

                macro_info = {
                    "regime": target_regime,
                    "allow_long": allow_long,
                    "allow_short": allow_short,
                    "qqq_curr": round(curr_q, 2),
                    "qqq_ema20": round(ema20, 2),
                    "qqq_dist_ema_pct": round(dist_ema, 2),
                    "qqq_intraday_pct": round(intraday_pct, 2)
                }

                if target_regime != self.last_macro_regime:
                    logger.warning(f"[CAMBIO REGIME MACRO CONFERMATO] Passaggio da {self.last_macro_regime} a {target_regime} (QQQ: ${curr_q:.2f}, Intraday: {intraday_pct:+.2f}%, vs EMA20: {dist_ema:+.2f}%)")
                    regime_icon = "🔴" if target_regime == "RISK_OFF_BEARISH" else ("🟢" if target_regime == "RISK_ON_BULLISH" else "⚪")
                    perm_str = "SOLO SHORT autorizzati (LONG bloccati)" if target_regime == "RISK_OFF_BEARISH" else ("SOLO LONG autorizzati (SHORT bloccati)" if target_regime == "RISK_ON_BULLISH" else "Bidirezionale (LONG & SHORT)")
                    try:
                        send_notification(
                            f"{regime_icon} <b>CAMBIO REGIME MACRO NASDAQ ({target_regime})</b>\n\n"
                            f"• Indice QQQ: <b>${curr_q:.2f}</b> ({intraday_pct:+.2f}% intraday)\n"
                            f"• Distanza da EMA20 oraria: <b>{dist_ema:+.2f}%</b>\n"
                            f"• Politica Operativa: <b>{perm_str}</b>\n"
                            f"• <i>Stabilità garantita da filtro isteresi & debounce anti-whipsaw</i>"
                        )
                    except Exception:
                        pass
                    self.last_macro_regime = target_regime
                    self.last_macro_regime_change = now

                self.current_macro_info = macro_info
                self.last_macro_check = now
                return macro_info
        except Exception as ex:
            logger.warning(f"Impossibile valutare Macro Regime QQQ: {ex}")

        return self.current_macro_info

    def update_batch_market_data(self, symbols: List[str]):
        """
        Recupera in un'unica chiamata batch le candele Daily e calcola:
        1. SMA20 Daily per ciascun titolo (Trend Alignment).
        2. Matrice di Correlazione e Covarianza a 30 giorni per il Portfolio VaR.
        3. Quality Momentum normalizzato per ATR (Alpha Zoo Volatility-Adjusted Momentum).
        Aggiornato al massimo una volta ogni 30 minuti per minimizzare overhead di rete.
        """
        now = datetime.now()
        if (now - self.last_batch_trend_update).total_seconds() < 1800 and self.daily_sma20:
            return

        if not symbols:
            return

        try:
            req_symbols = list(set(symbols + ["QQQ"]))
            req_daily = StockBarsRequest(
                symbol_or_symbols=req_symbols,
                timeframe=TimeFrame.Day,
                start=now - timedelta(days=45)
            )
            df_daily = self.alpaca_data.get_stock_bars(req_daily).df
            if df_daily.empty:
                return

            # Calcolo rendimento benchmark QQQ su 5 giorni
            qqq_ret_5d = 0.0
            if "QQQ" in df_daily.index:
                q_bars = df_daily.loc["QQQ"]
                if len(q_bars) >= 5:
                    p_start = float(q_bars["close"].iloc[-5])
                    p_now = float(q_bars["close"].iloc[-1])
                    qqq_ret_5d = ((p_now - p_start) / p_start) * 100

            # Costruzione DataFrame dei rendimenti per Matrice di Covarianza & Correlazione
            try:
                pivoted = df_daily.reset_index().pivot(index='timestamp', columns='symbol', values='close')
                self.daily_returns_df = pivoted.pct_change().dropna()
                if not self.daily_returns_df.empty:
                    self.corr_matrix = self.daily_returns_df.corr()
                    self.cov_matrix = self.daily_returns_df.cov()
            except Exception as e_cov:
                logger.debug(f"Errore calcolo matrici di rischio: {e_cov}")

            for s in symbols:
                if s in df_daily.index:
                    s_bars = df_daily.loc[s]
                    s_len = len(s_bars)
                    if s_len >= 20:
                        self.daily_sma20[s] = float(s_bars["close"].iloc[-20:].mean())
                    elif s_len > 0:
                        self.daily_sma20[s] = float(s_bars["close"].mean())

                    # Calcolo ATR Daily 14 periodi
                    if s_len >= 14:
                        tr_list = []
                        for k in range(max(1, s_len - 14), s_len):
                            h = float(s_bars["high"].iloc[k])
                            l = float(s_bars["low"].iloc[k])
                            prev_c = float(s_bars["close"].iloc[k - 1])
                            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                            tr_list.append(tr)
                        atr14 = float(sum(tr_list) / len(tr_list)) if tr_list else 1.0
                    else:
                        atr14 = 1.0

                    if s_len >= 5:
                        s_start = float(s_bars["close"].iloc[-5])
                        s_now = float(s_bars["close"].iloc[-1])
                        s_ret_5d = ((s_now - s_start) / s_start) * 100
                        raw_rs = s_ret_5d - qqq_ret_5d
                        self.rs_scores[s] = raw_rs

                        # Volatility-Adjusted Momentum: normalizzato per ATR%
                        norm_atr_pct = (atr14 / s_now) * 100 if s_now > 0 else 2.0
                        self.quality_rs_scores[s] = raw_rs / max(norm_atr_pct, 0.5)
                    else:
                        self.rs_scores[s] = 0.0
                        self.quality_rs_scores[s] = 0.0

            self.last_batch_trend_update = now
            logger.info(f"[*] Aggiornato trend macro e matrici di rischio su {len(self.daily_sma20)} titoli (SMA20, Quality RS, Covarianza)")
        except Exception as e:
            logger.warning(f"Errore aggiornamento batch market data: {e}")

    def estimate_portfolio_var(self, weights_dict: Dict[str, float], portfolio_val: float) -> tuple:
        """
        Calcola il Value at Risk parametrico 1-Day 95% del portafoglio dato un dizionario {symbol: dollar_value}.
        Restituisce (var_pct, var_dollars).
        """
        if not weights_dict or self.cov_matrix.empty:
            total_gross = sum(abs(v) for v in weights_dict.values())
            var_dollars = total_gross * 0.025
            return ((var_dollars / portfolio_val * 100 if portfolio_val > 0 else 2.5), var_dollars)

        valid_symbols = [s for s in weights_dict.keys() if s in self.cov_matrix.columns]
        if not valid_symbols:
            total_gross = sum(abs(v) for v in weights_dict.values())
            var_dollars = total_gross * 0.025
            return ((var_dollars / portfolio_val * 100 if portfolio_val > 0 else 2.5), var_dollars)

        w_vec = np.array([weights_dict[s] for s in valid_symbols], dtype=float)
        cov_sub = self.cov_matrix.loc[valid_symbols, valid_symbols].values

        portfolio_var_dollar_sq = float(np.dot(w_vec, np.dot(cov_sub, w_vec)))
        if portfolio_var_dollar_sq < 0:
            portfolio_var_dollar_sq = 0.0
        portfolio_std_dollars = np.sqrt(portfolio_var_dollar_sq)

        # 95% One-tailed Normal Z = 1.645
        var_dollars = 1.645 * portfolio_std_dollars
        var_pct = (var_dollars / portfolio_val) * 100 if portfolio_val > 0 else 0.0
        return (var_pct, var_dollars)

    def rank_candidates_by_relative_strength(self, symbols: List[str], is_bearish_regime: bool) -> List[str]:
        """
        Classifica di Forza Relativa Cross-Sectional e Volatility-Adjusted Momentum vs QQQ.
        - Se regime BEARISH: ordina i candidati dal più debole (quality_rs più basso) al più forte.
        - Se regime BULLISH/NEUTRALE: ordina dal più forte (quality_rs più alto) al più debole.
        """
        if not symbols:
            return []

        try:
            self.update_batch_market_data(symbols)
            sorted_symbols = sorted(symbols, key=lambda s: self.quality_rs_scores.get(s, self.rs_scores.get(s, 0.0)), reverse=(not is_bearish_regime))
            top_sym = sorted_symbols[0]
            top_q = self.quality_rs_scores.get(top_sym, 0.0)
            top_raw = self.rs_scores.get(top_sym, 0.0)
            bot_sym = sorted_symbols[-1]
            bot_q = self.quality_rs_scores.get(bot_sym, 0.0)
            bot_raw = self.rs_scores.get(bot_sym, 0.0)
            logger.info(f"[*] Ranking Quality Momentum: {len(symbols)} titoli (Top: {top_sym} [Q-Score: {top_q:+.2f}, RS: {top_raw:+.2f}%], Bottom: {bot_sym} [Q-Score: {bot_q:+.2f}, RS: {bot_raw:+.2f}%])")
            return sorted_symbols
        except Exception as e:
            logger.warning(f"Errore calcolo ranking forza relativa: {e}. Uso ordine predefinito.")
            return symbols

    def on_trading_iteration(self):
        """
        Ciclo principale eseguito in automatico ogni 'sleeptime' secondi.
        """
        logger.info("\n--- [CICLO SENTINELLA NASDAQ-100] ---")
        portfolio_val = self.get_portfolio_value()
        cash = self.get_cash()
        positions = self.get_positions()
        pos_symbols = [p.symbol for p in positions] if positions else []

        logger.info(f"Portafoglio: ${portfolio_val:,.2f} | Liquidita': ${cash:,.2f} | Posizioni Aperte: {len(pos_symbols)} {pos_symbols}")

        # ---------------------------------------------------------------------
        # 0. CONTROLLO END-OF-DAY CASH SWEEP (FLAT OVERNIGHT ANTI-GAP)
        # ---------------------------------------------------------------------
        close_eod_enabled = self.parameters.get("close_positions_at_eod", True)
        if close_eod_enabled:
            try:
                from zoneinfo import ZoneInfo
                now_ny = datetime.now(ZoneInfo("America/New_York"))
            except Exception:
                from datetime import timezone
                now_ny = datetime.now(timezone.utc) - timedelta(hours=5)

            sweep_hour, sweep_min = 15, 50
            try:
                t_parts = self.parameters.get("eod_close_time_est", "15:50").split(":")
                sweep_hour, sweep_min = int(t_parts[0]), int(t_parts[1])
            except Exception:
                pass

            is_eod_sweep_active = (now_ny.weekday() < 5) and (
                (now_ny.hour == sweep_hour and now_ny.minute >= sweep_min) or (now_ny.hour >= 16)
            )

            if is_eod_sweep_active:
                if positions:
                    logger.warning(
                        f"🌙 [END-OF-DAY CASH SWEEP] Orario NY: {now_ny.strftime('%H:%M:%S')} EST "
                        f"(>= {sweep_hour:02d}:{sweep_min:02d}). Avvio liquidazione forzata di tutte le "
                        f"posizioni aperte ({len(positions)}) per azzerare il rischio overnight!"
                    )
                    for pos in positions:
                        symbol = pos.symbol
                        qty = abs(pos.quantity)
                        is_short = pos.quantity < 0
                        curr_price = self.get_last_price(symbol) or float(getattr(pos, "current_price", getattr(pos, "avg_entry_price", 0.0)))
                        entry_price = self.entry_prices.get(symbol, float(getattr(pos, "avg_entry_price", curr_price)))

                        # 1. Cancella preventivamente gli ordini attivi/stop loss su Alpaca per liberare le quote
                        active_orders = [o for o in self.get_orders() if getattr(o, "symbol", "") == symbol and hasattr(o, "is_active") and o.is_active()]
                        for o in active_orders:
                            try:
                                self.cancel_order(o)
                            except Exception:
                                pass
                        if active_orders:
                            time.sleep(1.0)

                        # 2. Invia ordine di chiusura a mercato
                        close_side = "buy" if is_short else "sell"
                        try:
                            close_order = self.create_order(symbol, quantity=qty, side=close_side)
                            self.submit_order(close_order)
                            logger.info(f"[EOD SWEEP ORDER] Inviato ordine di chiusura {close_side.upper()} per {qty} azioni di {symbol}")
                        except Exception as close_err:
                            logger.error(f"[EOD SWEEP ERROR] Errore chiusura {symbol}: {close_err}")
                            continue

                        # 3. Calcolo PnL ed emissione notifica Telegram
                        if is_short:
                            pnl_dollars = (entry_price - curr_price) * qty
                            pnl_pct = ((entry_price - curr_price) / entry_price) * 100 if entry_price > 0 else 0.0
                        else:
                            pnl_dollars = (curr_price - entry_price) * qty
                            pnl_pct = ((curr_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0

                        pnl_icon = "🟢" if pnl_dollars >= 0 else "🔴"
                        pnl_sign = "+" if pnl_dollars >= 0 else ""
                        liq_val = qty * curr_price

                        try:
                            send_notification(
                                f"🌙 <b>END-OF-DAY CASH SWEEP (FLAT OVERNIGHT)</b>\n\n"
                                f"• Titolo: <b>{symbol}</b> ({'SHORT (Buy to cover)' if is_short else 'LONG (Vendita)'})\n"
                                f"• Azioni liquidate: <b>{qty}</b>\n"
                                f"• Prezzo Carico: <b>${entry_price:.2f}</b>\n"
                                f"• Prezzo Uscita: <b>${curr_price:.2f}</b>\n"
                                f"• Controvalore Liquidato: <b>${liq_val:,.2f}</b>\n"
                                f"• Risultato Trade: {pnl_icon} <b>{pnl_sign}${pnl_dollars:,.2f} ({pnl_pct:+.2f}%)</b>\n"
                                f"• Orario New York: <b>{now_ny.strftime('%H:%M:%S')} EST</b> (21:50 CET)\n"
                                f"• <i>Liquidazione automatica di fine seduta per azzerare al 100% il rischio gap notturno (Portafoglio 100% Cash overnight).</i>"
                            )
                        except Exception:
                            pass

                        # 4. Aggiorna Adaptive Learner e pulisci stato in memoria
                        peak_fav = self.peak_profits.pop(symbol, 0.0)
                        mae_adv = self.max_adverse_excursions.pop(symbol, 0.0)
                        try:
                            self.learner.record_trade_result(
                                symbol=symbol,
                                side="short" if is_short else "long",
                                pnl_pct=pnl_pct,
                                pnl_dollars=pnl_dollars,
                                exit_reason="eod_cash_sweep",
                                peak_profit_pct=peak_fav,
                                mae_pct=mae_adv
                            )
                        except Exception as l_err:
                            logger.warning(f"Errore learner in EOD sweep: {l_err}")

                        self.active_stops.pop(symbol, None)
                        self.entry_prices.pop(symbol, None)
                        self.last_stop_update.pop(symbol, None)
                        self.notified_tiers.pop(symbol, None)
                        self.notified_tenaglia.pop(symbol, None)
                        try:
                            self.learner.clear_peak_price(symbol)
                            self.learner.clear_adverse_price(symbol)
                        except Exception:
                            pass

                    logger.info("🌙 [END-OF-DAY CASH SWEEP] Tutte le posizioni sono state liquidate. Portafoglio flat al 100%.")
                    return
                else:
                    logger.info(f"🌙 [END-OF-DAY CASH SWEEP] Orario NY: {now_ny.strftime('%H:%M:%S')} EST. Portafoglio già 100% flat (nessuna posizione aperta). Nessuna nuova operazione.")
                    return

        # ---------------------------------------------------------------------
        # 1. WATCHDOG POSIZIONI APERTE (Cricchetto Long & Short / Ratchet)
        # ---------------------------------------------------------------------
        for pos in positions:
            symbol = pos.symbol
            curr_price = self.get_last_price(symbol)
            if not curr_price:
                continue

            is_short = pos.quantity < 0
            qty = abs(pos.quantity)

            # Recupero o stima del prezzo di carico
            entry_price = self.entry_prices.get(symbol, 0.0)
            if entry_price == 0.0:
                entry_price = float(getattr(pos, "avg_entry_price", getattr(pos, "holdings_cost", curr_price)))
                self.entry_prices[symbol] = entry_price

            # Traccia MFE e prezzo di picco persistente
            peak_price = self.learner.update_peak_price(symbol, curr_price, is_short=is_short)
            fav_pct = ((entry_price - curr_price) / entry_price) * 100 if is_short else ((curr_price - entry_price) / entry_price) * 100
            self.peak_profits[symbol] = max(self.peak_profits.get(symbol, fav_pct), fav_pct)

            # Traccia MAE e prezzo avverso persistente
            self.learner.update_adverse_price(symbol, curr_price, is_short=is_short)
            self.max_adverse_excursions[symbol] = min(self.max_adverse_excursions.get(symbol, fav_pct), fav_pct)

            # Spunto 3: Verifica e sincronizzazione Emergency Stop Catastrofico su Alpaca (-4.5%)
            has_active_stop_order = any(getattr(o, "symbol", "") == symbol and getattr(o, "stop_price", None) is not None and hasattr(o, "is_active") and o.is_active() for o in self.get_orders())
            if not has_active_stop_order and entry_price > 0:
                try:
                    emerg_side = "sell" if not is_short else "buy"
                    emerg_stop_price = round(entry_price * 0.955 if not is_short else entry_price * 1.045, 2)
                    emerg_order = self.create_order(symbol, quantity=qty, side=emerg_side, stop_price=emerg_stop_price)
                    self.submit_order(emerg_order)
                    logger.info(f"[EMERGENCY STOP ALPACA] Inserita protezione catastrofica server-side per {symbol}: Stop a ${emerg_stop_price:.2f} ({emerg_side.upper()})")
                except Exception as e_err:
                    logger.warning(f"Errore registrazione Emergency Stop per {symbol}: {e_err}")

            stop_price = self.active_stops.get(symbol, 0.0)

            # Auto-Recovery se assente
            if stop_price == 0.0:
                try:
                    lookback_days = self.parameters.get("timestral_lookback_days", 28)
                    req_bars = StockBarsRequest(
                        symbol_or_symbols=symbol,
                        timeframe=TimeFrame.Hour,
                        start=datetime.now() - timedelta(days=lookback_days)
                    )
                    b_df = self.alpaca_data.get_stock_bars(req_bars).df
                    df_sym_b = b_df.loc[symbol] if symbol in b_df.index else b_df
                    sym_closes = df_sym_b["close"].tolist()
                    t_eval = self.timestral.predict_quantiles(sym_closes, prediction_length=6)
                    atr = compute_atr(df_sym_b, period=14, default_pct=0.018)
                    cushion = max(curr_price * 0.018, 1.5 * atr)

                    if is_short:
                        raw_stop = t_eval["dynamic_stop_loss_short"]
                        stop_price = round(max(raw_stop, curr_price + cushion), 2)
                    else:
                        raw_stop = t_eval["dynamic_stop_loss"]
                        stop_price = round(min(raw_stop, curr_price - cushion), 2)

                    self.active_stops[symbol] = stop_price
                    self.last_stop_update[symbol] = datetime.now()
                    dist_pct = abs(curr_price - stop_price) / curr_price * 100
                    logger.info(f"[+] Stop Dinamico con RESPIRO ripristinato per {symbol} ({'SHORT' if is_short else 'LONG'}): ${stop_price:.2f} (Distanza: {dist_pct:.2f}%, Cushion: ${cushion:.2f})")
                except Exception as ex:
                    logger.warning(f"Impossibile ricalcolare stop per {symbol}: {ex}")

            # Cricchetto Profit-Lock a Scaglioni Infiniti (+1%, +2%, +3%, +4%, ... +N% all'infinito)
            meta_p = self.learner.get_meta_params()
            be_min_trigger = meta_p.get("break_even_pct", 1.0)
            lock_eval = compute_infinite_profit_lock(
                entry_price=entry_price,
                peak_price=peak_price,
                is_short=is_short,
                min_trigger_pct=be_min_trigger
            )

            if lock_eval["active"]:
                new_lock_stop = lock_eval["stop_price"]
                tier = lock_eval["tier"]
                lock_pct = lock_eval["lock_pct"]

                is_tighter = (new_lock_stop < stop_price) if is_short else (new_lock_stop > stop_price)
                if stop_price == 0.0 or is_tighter:
                    old_stop = stop_price
                    stop_price = new_lock_stop
                    self.active_stops[symbol] = stop_price

                    last_notified_tier = self.notified_tiers.get(symbol, 0)
                    if tier > last_notified_tier:
                        self.notified_tiers[symbol] = tier
                        locked_dollars = abs(new_lock_stop - entry_price) * qty
                        pnl_sign = "+" if lock_pct >= 0 else ""
                        try:
                            if is_short:
                                label_peak = "Prezzo Minimo Raggiunto"
                                label_stop = "Nuovo Tetto Blindato"
                                ratchet_msg = "Il cricchetto abbassa il tetto protettivo blindando profitti crescenti!"
                            else:
                                label_peak = "Prezzo Massimo Raggiunto"
                                label_stop = "Nuovo Pavimento Blindato"
                                ratchet_msg = "Il cricchetto alza il pavimento protettivo blindando profitti crescenti!"

                            send_notification(
                                f"🛡️ <b>PROFIT-LOCK SCAGLIONE #{tier} (+{lock_eval['gain_pct']:.2f}%)</b>\n\n"
                                f"• Titolo: <b>{symbol}</b> ({'SHORT' if is_short else 'LONG'})\n"
                                f"• Prezzo Carico: ${entry_price:.2f}\n"
                                f"• {label_peak}: <b>${peak_price:.2f}</b> (+{lock_eval['gain_pct']:.2f}%)\n"
                                f"• {label_stop}: <b>${new_lock_stop:.2f}</b> (Blindato: {pnl_sign}{lock_pct:.2f}%)\n"
                                f"• Guadagno Minimo Blindato: <b>+${locked_dollars:,.2f}</b>\n"
                                f"• <i>{ratchet_msg}</i>"
                            )
                        except Exception:
                            pass
                    logger.info(f"[PROFIT-LOCK INFINITO] {symbol} Scaglione #{tier} (+{lock_eval['gain_pct']:.2f}%): Stop fissato a ${stop_price:.2f} (Lock: +{lock_pct}%)")

            # Spunto: Tenaglia Accelerata sui Super-Gain (> 2R / Max Capture Ratio)
            initial_risk_pct = meta_p.get("min_stop_pct", 2.5)
            r_threshold = initial_risk_pct * self.parameters.get("accelerated_tenaglia_r_mult", 2.0)
            if fav_pct >= r_threshold and entry_price > 0:
                tenaglia_lock_pct = round(fav_pct * 0.85, 2)
                tenaglia_stop = round(entry_price * (1.0 - tenaglia_lock_pct / 100.0) if is_short else entry_price * (1.0 + tenaglia_lock_pct / 100.0), 2)
                is_tenaglia_tighter = (tenaglia_stop < stop_price) if is_short else (tenaglia_stop > stop_price)
                if stop_price == 0.0 or is_tenaglia_tighter:
                    stop_price = tenaglia_stop
                    self.active_stops[symbol] = stop_price
                    if not self.notified_tenaglia.get(symbol, False):
                        self.notified_tenaglia[symbol] = True
                        locked_dollars = abs(tenaglia_stop - entry_price) * qty
                        try:
                            send_notification(
                                f"⚡ <b>TENAGLIA ACCELERATA ATTIVATA (&gt;{self.parameters.get('accelerated_tenaglia_r_mult', 2.0):.1f}R SUPER-GAIN)</b>\n\n"
                                f"• Titolo: <b>{symbol}</b> ({'SHORT' if is_short else 'LONG'})\n"
                                f"• Guadagno di Picco: <b>+{fav_pct:.2f}%</b> (&gt;{fav_pct/initial_risk_pct:.1f}R)\n"
                                f"• Nuovo Stop a Tenaglia: <b>${tenaglia_stop:.2f}</b> (Blindato: <b>+{tenaglia_lock_pct:.2f}%</b>)\n"
                                f"• Profitto Minimo Blindato: <b>+${locked_dollars:,.2f}</b>\n"
                                f"• <i>La tenaglia si stringe blindando l'85% del picco massimo per massimizzare il Capture Ratio!</i>"
                            )
                        except Exception:
                            pass
                    logger.info(f"[TENAGLIA ACCELERATA] {symbol} supera {r_threshold:.1f}% (+{fav_pct:.2f}%): Stop a ${stop_price:.2f} (Lock: +{tenaglia_lock_pct}%)")

            # Cricchetto Ratchet (ogni 5 minuti)
            now = datetime.now()
            last_update = self.last_stop_update.get(symbol, datetime.min)
            if (now - last_update).total_seconds() >= 300:
                try:
                    lookback_days = self.parameters.get("timestral_lookback_days", 28)
                    req_bars = StockBarsRequest(
                        symbol_or_symbols=symbol,
                        timeframe=TimeFrame.Hour,
                        start=datetime.now() - timedelta(days=lookback_days)
                    )
                    b_df = self.alpaca_data.get_stock_bars(req_bars).df
                    df_sym_b = b_df.loc[symbol] if symbol in b_df.index else b_df
                    sym_closes = df_sym_b["close"].tolist()
                    t_eval = self.timestral.predict_quantiles(sym_closes, prediction_length=6)
                    self.last_stop_update[symbol] = now
                    atr = compute_atr(df_sym_b, period=14, default_pct=0.018)
                    min_cushion = max(curr_price * 0.012, 1.0 * atr)

                    meta_p = self.learner.get_meta_params()
                    be_trigger = meta_p.get("break_even_pct", 1.0) / 100.0
                    be_protect = meta_p.get("break_even_protect_pct", 0.2) / 100.0

                    if is_short:
                        # SHORT: lo stop è un tetto, può solo SCENDERE
                        new_roof = t_eval["dynamic_stop_loss_short"]
                        if new_roof < stop_price and (new_roof - curr_price) >= min_cushion:
                            logger.info(f"[CRICCHETTO SHORT] Abbassato Stop per {symbol}: da ${stop_price:.2f} a ${new_roof:.2f} (Prezzo: ${curr_price:.2f})")
                            stop_price = new_roof
                            self.active_stops[symbol] = stop_price

                        # Break-Even Short adattivo
                        if entry_price > 0 and curr_price <= entry_price * (1.0 - be_trigger):
                            be_stop = round(entry_price * (1.0 - be_protect), 2)
                            if be_stop < stop_price:
                                logger.info(f"[BREAK-EVEN SHORT] Blindato profitto short {symbol}: Stop a ${be_stop:.2f} (Entry: ${entry_price:.2f})")
                                stop_price = be_stop
                                self.active_stops[symbol] = stop_price
                                try:
                                    send_notification(
                                        f"🛡️ <b>BREAK-EVEN ATTIVATO (SHORT)</b>\n\n"
                                        f"• Titolo: <b>{symbol}</b>\n"
                                        f"• Prezzo Attuale: <b>${curr_price:.2f}</b> (+{be_trigger*100:.1f}% di profitto)\n"
                                        f"• Prezzo di Vendita: ${entry_price:.2f}\n"
                                        f"• Nuovo Stop Protettivo: <b>${be_stop:.2f}</b>\n"
                                        f"• <i>Rischio trade azzerato al 100%!</i>"
                                    )
                                except Exception:
                                    pass
                    else:
                        # LONG: lo stop è un pavimento, può solo SALIRE
                        new_floor = t_eval["dynamic_stop_loss"]
                        if new_floor > stop_price and (curr_price - new_floor) >= min_cushion:
                            logger.info(f"[CRICCHETTO LONG] Alzato Stop per {symbol}: da ${stop_price:.2f} a ${new_floor:.2f} (Prezzo: ${curr_price:.2f})")
                            stop_price = new_floor
                            self.active_stops[symbol] = stop_price

                        # Break-Even Long adattivo
                        if entry_price > 0 and curr_price >= entry_price * (1.0 + be_trigger):
                            be_stop = round(entry_price * (1.0 + be_protect), 2)
                            if be_stop > stop_price:
                                logger.info(f"[BREAK-EVEN LONG] Blindato trade {symbol}: Stop a ${be_stop:.2f} (Entry: ${entry_price:.2f})")
                                stop_price = be_stop
                                self.active_stops[symbol] = stop_price
                                try:
                                    send_notification(
                                        f"🛡️ <b>BREAK-EVEN ATTIVATO (LONG)</b>\n\n"
                                        f"• Titolo: <b>{symbol}</b>\n"
                                        f"• Prezzo Attuale: <b>${curr_price:.2f}</b> (+{be_trigger*100:.1f}% di profitto)\n"
                                        f"• Prezzo di Carico: ${entry_price:.2f}\n"
                                        f"• Nuovo Stop Protettivo: <b>${be_stop:.2f}</b>\n"
                                        f"• <i>Rischio trade azzerato al 100%!</i>"
                                    )
                                except Exception:
                                    pass
                except Exception as ex:
                    logger.warning(f"Errore aggiornamento cricchetto per {symbol}: {ex}")

            # Controllo violazione Stop Dinamico
            stop_hit = False
            if is_short and stop_price > 0 and curr_price >= stop_price:
                stop_hit = True
                logger.warning(f"[!] STOP LOSS COLPITO (SHORT) per {symbol}: Prezzo ${curr_price:.2f} >= Stop ${stop_price:.2f}")
                # Spunto 3: Cancella preventivamente Emergency Stop per liberare le quote su Alpaca
                active_orders = [o for o in self.get_orders() if getattr(o, "symbol", "") == symbol and hasattr(o, "is_active") and o.is_active()]
                for o in active_orders:
                    try:
                        self.cancel_order(o)
                    except Exception:
                        pass
                if active_orders:
                    time.sleep(1.0)
                close_order = self.create_order(symbol, quantity=qty, side="buy") # Buy to cover
                self.submit_order(close_order)
            elif not is_short and stop_price > 0 and curr_price <= stop_price:
                stop_hit = True
                logger.warning(f"[!] STOP LOSS COLPITO (LONG) per {symbol}: Prezzo ${curr_price:.2f} <= Stop ${stop_price:.2f}")
                active_orders = [o for o in self.get_orders() if getattr(o, "symbol", "") == symbol and hasattr(o, "is_active") and o.is_active()]
                for o in active_orders:
                    try:
                        self.cancel_order(o)
                    except Exception:
                        pass
                if active_orders:
                    time.sleep(1.0)
                close_order = self.create_order(symbol, quantity=qty, side="sell")
                self.submit_order(close_order)

            if stop_hit:
                side_str = "SHORT (Buy to cover)" if is_short else "LONG (Vendita)"
                entry = self.entry_prices.get(symbol, curr_price)
                if is_short:
                    pnl_dollars = (entry - curr_price) * qty
                    pnl_pct = ((entry - curr_price) / entry) * 100 if entry > 0 else 0.0
                else:
                    pnl_dollars = (curr_price - entry) * qty
                    pnl_pct = ((curr_price - entry) / entry) * 100 if entry > 0 else 0.0

                pnl_icon = "🟢" if pnl_dollars >= 0 else "🔴"
                pnl_sign = "+" if pnl_dollars >= 0 else ""
                liq_value = qty * curr_price
                header_title = "🛡️ <b>BREAK-EVEN / TRAILING STOP PRESO (PROFITTO)</b>" if pnl_dollars >= 0 else "🛑 <b>STOP LOSS COLPITO (DIFESA CAPITALE)</b>"

                try:
                    send_notification(
                        f"{header_title}\n\n"
                        f"• Titolo: <b>{symbol}</b> ({side_str})\n"
                        f"• Azioni liquidate: <b>{qty}</b>\n"
                        f"• Prezzo Carico: <b>${entry:.2f}</b>\n"
                        f"• Prezzo Uscita: <b>${curr_price:.2f}</b>\n"
                        f"• Controvalore Liquidato: <b>${liq_value:,.2f}</b>\n"
                        f"• Risultato Trade: {pnl_icon} <b>{pnl_sign}${pnl_dollars:,.2f} ({pnl_pct:+.2f}%)</b>"
                    )
                except Exception:
                    pass

                peak_fav = self.peak_profits.pop(symbol, 0.0)
                mae_adv = self.max_adverse_excursions.pop(symbol, 0.0)
                try:
                    self.learner.record_trade_result(
                        symbol=symbol,
                        side="short" if is_short else "long",
                        pnl_pct=pnl_pct,
                        pnl_dollars=pnl_dollars,
                        exit_reason="stop_loss" if pnl_dollars < 0 else "trailing_stop",
                        peak_profit_pct=peak_fav,
                        mae_pct=mae_adv
                    )
                except Exception as l_err:
                    logger.warning(f"Errore aggiornamento adaptive learner: {l_err}")

                self.active_stops.pop(symbol, None)
                self.entry_prices.pop(symbol, None)
                self.last_stop_update.pop(symbol, None)
                self.notified_tiers.pop(symbol, None)
                self.notified_tenaglia.pop(symbol, None)
                try:
                    self.learner.clear_peak_price(symbol)
                    self.learner.clear_adverse_price(symbol)
                except Exception:
                    pass

                if pnl_dollars < 0:
                    cooldown_until = datetime.now() + timedelta(minutes=75)
                    self.ticker_cooldowns[symbol] = cooldown_until
                    logger.info(f"[COOLDOWN STOP LOSS] {symbol} messo in pausa per 75 minuti fino alle {cooldown_until.strftime('%H:%M:%S')} (anti-whipsaw)")
                else:
                    cooldown_min = self.parameters.get("profit_reentry_cooldown_min", 15)
                    cooldown_until = datetime.now() + timedelta(minutes=cooldown_min)
                    self.ticker_cooldowns[symbol] = cooldown_until
                    logger.info(f"[COOLDOWN PROFIT/BE] {symbol} chiuso in profitto/BE. Pausa di {cooldown_min} minuti fino alle {cooldown_until.strftime('%H:%M:%S')} (anti-flicker)")

                continue

            # Controllo Hard Exit Pre-Trimestrale: se la trimestrale è oggi (AMC) o domani mattina (BMO), liquidare per azzerare rischio overnight
            hard_exit = self.news_guardian.check_hard_exit_pre_earnings(symbol)
            if hard_exit.get("must_exit", False):
                logger.warning(f"[!] HARD EXIT PRE-EARNINGS per {symbol}: {hard_exit['reason']}")
                # Spunto 3: Cancella preventivamente Emergency Stop per liberare le quote su Alpaca
                active_orders = [o for o in self.get_orders() if getattr(o, "symbol", "") == symbol and hasattr(o, "is_active") and o.is_active()]
                for o in active_orders:
                    try:
                        self.cancel_order(o)
                    except Exception:
                        pass
                if active_orders:
                    time.sleep(1.0)
                close_side = "buy" if is_short else "sell"
                close_order = self.create_order(symbol, quantity=qty, side=close_side)
                self.submit_order(close_order)

                entry = self.entry_prices.get(symbol, curr_price)
                if is_short:
                    pnl_dollars = (entry - curr_price) * qty
                    pnl_pct = ((entry - curr_price) / entry) * 100 if entry > 0 else 0.0
                else:
                    pnl_dollars = (curr_price - entry) * qty
                    pnl_pct = ((curr_price - entry) / entry) * 100 if entry > 0 else 0.0

                pnl_icon = "🟢" if pnl_dollars >= 0 else "🔴"
                pnl_sign = "+" if pnl_dollars >= 0 else ""

                try:
                    send_notification(
                        f"🚨 <b>HARD EXIT PRE-TRIMESTRALE ESEGUITO</b>\n\n"
                        f"• Titolo: <b>{symbol}</b>\n"
                        f"• Azioni liquidate: <b>{qty}</b>\n"
                        f"• Prezzo Carico: <b>${entry:.2f}</b>\n"
                        f"• Prezzo Uscita: <b>${curr_price:.2f}</b>\n"
                        f"• Risultato Trade: {pnl_icon} <b>{pnl_sign}${pnl_dollars:,.2f} ({pnl_pct:+.2f}%)</b>\n"
                        f"• Motivo: {hard_exit['reason']}\n"
                        f"• <i>Liquidato per azzerare il rischio da gap notturno!</i>"
                    )
                except Exception:
                    pass

                peak_fav = self.peak_profits.pop(symbol, 0.0)
                mae_adv = self.max_adverse_excursions.pop(symbol, 0.0)
                try:
                    self.learner.record_trade_result(
                        symbol=symbol,
                        side="short" if is_short else "long",
                        pnl_pct=pnl_pct,
                        pnl_dollars=pnl_dollars,
                        exit_reason="hard_exit",
                        peak_profit_pct=peak_fav,
                        mae_pct=mae_adv
                    )
                except Exception:
                    pass

                self.active_stops.pop(symbol, None)
                self.entry_prices.pop(symbol, None)
                self.last_stop_update.pop(symbol, None)
                self.notified_tiers.pop(symbol, None)
                self.notified_tenaglia.pop(symbol, None)
                try:
                    self.learner.clear_peak_price(symbol)
                    self.learner.clear_adverse_price(symbol)
                except Exception:
                    pass

                if pnl_dollars < 0:
                    cooldown_until = datetime.now() + timedelta(minutes=75)
                    self.ticker_cooldowns[symbol] = cooldown_until
                    logger.info(f"[COOLDOWN EARNINGS EXIT] {symbol} chiuso pre-trimestrale in perdita. Pausa di 75 minuti fino alle {cooldown_until.strftime('%H:%M:%S')} (anti-whipsaw)")
                else:
                    cooldown_min = self.parameters.get("profit_reentry_cooldown_min", 15)
                    cooldown_until = datetime.now() + timedelta(minutes=cooldown_min)
                    self.ticker_cooldowns[symbol] = cooldown_until
                    logger.info(f"[COOLDOWN EARNINGS EXIT] {symbol} chiuso pre-trimestrale in gain/BE. Pausa di {cooldown_min} minuti fino alle {cooldown_until.strftime('%H:%M:%S')}")

                continue

            # Controllo News Guardian durante la detenzione
            news_eval = self.news_guardian.evaluate_symbol_safety(symbol, side="short" if is_short else "long")
            if news_eval.get("emergency_exit_required", False):
                logger.warning(f"[!] VETO D'EMERGENZA NEWS per {symbol} ({'SHORT' if is_short else 'LONG'}): {news_eval['reason']}")
                # Spunto 3: Cancella preventivamente Emergency Stop per liberare le quote su Alpaca
                active_orders = [o for o in self.get_orders() if getattr(o, "symbol", "") == symbol and hasattr(o, "is_active") and o.is_active()]
                for o in active_orders:
                    try:
                        self.cancel_order(o)
                    except Exception:
                        pass
                if active_orders:
                    time.sleep(1.0)
                close_side = "buy" if is_short else "sell"
                close_order = self.create_order(symbol, quantity=qty, side=close_side)
                self.submit_order(close_order)

                entry = self.entry_prices.get(symbol, curr_price)
                if is_short:
                    pnl_dollars = (entry - curr_price) * qty
                    pnl_pct = ((entry - curr_price) / entry) * 100 if entry > 0 else 0.0
                else:
                    pnl_dollars = (curr_price - entry) * qty
                    pnl_pct = ((curr_price - entry) / entry) * 100 if entry > 0 else 0.0

                pnl_icon = "🟢" if pnl_dollars >= 0 else "🔴"
                pnl_sign = "+" if pnl_dollars >= 0 else ""

                try:
                    send_notification(
                        f"⚠️ <b>USCITA D'EMERGENZA NEWS GUARDIAN</b>\n\n"
                        f"• Titolo: <b>{symbol}</b>\n"
                        f"• Azioni liquidate: <b>{qty}</b>\n"
                        f"• Prezzo Carico: <b>${entry:.2f}</b>\n"
                        f"• Prezzo Uscita: <b>${curr_price:.2f}</b>\n"
                        f"• Risultato Trade: {pnl_icon} <b>{pnl_sign}${pnl_dollars:,.2f} ({pnl_pct:+.2f}%)</b>\n"
                        f"• Motivo: {news_eval['reason']}"
                    )
                except Exception:
                    pass
                peak_fav = self.peak_profits.pop(symbol, 0.0)
                mae_adv = self.max_adverse_excursions.pop(symbol, 0.0)
                try:
                    self.learner.record_trade_result(
                        symbol=symbol,
                        side="short" if is_short else "long",
                        pnl_pct=pnl_pct,
                        pnl_dollars=pnl_dollars,
                        exit_reason="news_veto",
                        peak_profit_pct=peak_fav,
                        mae_pct=mae_adv
                    )
                except Exception:
                    pass
                self.active_stops.pop(symbol, None)
                self.entry_prices.pop(symbol, None)
                self.last_stop_update.pop(symbol, None)
                self.notified_tiers.pop(symbol, None)
                self.notified_tenaglia.pop(symbol, None)
                try:
                    self.learner.clear_peak_price(symbol)
                    self.learner.clear_adverse_price(symbol)
                except Exception:
                    pass

                if pnl_dollars < 0:
                    cooldown_until = datetime.now() + timedelta(minutes=75)
                    self.ticker_cooldowns[symbol] = cooldown_until
                    logger.info(f"[COOLDOWN NEWS VETO] {symbol} messo in pausa per 75 minuti fino alle {cooldown_until.strftime('%H:%M:%S')} (anti-whipsaw)")
                else:
                    cooldown_min = self.parameters.get("profit_reentry_cooldown_min", 15)
                    cooldown_until = datetime.now() + timedelta(minutes=cooldown_min)
                    self.ticker_cooldowns[symbol] = cooldown_until
                    logger.info(f"[COOLDOWN NEWS VETO] {symbol} chiuso per veto in gain/BE. Pausa di {cooldown_min} minuti fino alle {cooldown_until.strftime('%H:%M:%S')}")

        # ---------------------------------------------------------------------
        # 2. CONTROLLO LIMITI PORTAFOGLIO
        # ---------------------------------------------------------------------
        if len(positions) >= self.parameters["max_positions"]:
            logger.info("[*] Raggiunto il limite massimo di posizioni contemporanee. Nessun nuovo acquisto.")
            return

        min_required_cash = portfolio_val * self.parameters["cash_buffer"]
        if cash < min_required_cash:
            logger.info(f"[*] Riserva di liquidita' attiva (richiesti ${min_required_cash:,.2f}, disponibili ${cash:,.2f}). Nessun nuovo acquisto.")
            return

        # Controllo finestra pre-chiusura seduta: nessuna nuova operazione dopo le 15:45 EST (21:45 CET)
        no_new_hour, no_new_min = 15, 45
        try:
            nn_parts = self.parameters.get("eod_no_new_positions_est", "15:45").split(":")
            no_new_hour, no_new_min = int(nn_parts[0]), int(nn_parts[1])
        except Exception:
            pass

        try:
            from zoneinfo import ZoneInfo
            now_ny = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            from datetime import timezone
            now_ny = datetime.now(timezone.utc) - timedelta(hours=5)

        is_pre_close_cutoff = (now_ny.weekday() < 5) and (
            (now_ny.hour == no_new_hour and now_ny.minute >= no_new_min) or (now_ny.hour >= 16)
        )
        if is_pre_close_cutoff:
            logger.info(f"[*] Finestra pre-chiusura seduta attiva ({now_ny.strftime('%H:%M:%S')} EST >= {no_new_hour:02d}:{no_new_min:02d}). Nessun nuovo acquisto/short.")
            return

        # Controllo finestra apertura seduta (Buffer di Calma iniziale 9:30 - 9:40 EST / 15:30 - 15:40 CET)
        # Nei primi N minuti dall'apertura di Wall Street le aste sono caotiche e piene di falsi breakout retail.
        open_calm_min = self.parameters.get("market_open_calm_buffer_min", 10)
        is_market_open_buffer = (now_ny.weekday() < 5) and (
            now_ny.hour == 9 and (30 <= now_ny.minute < (30 + open_calm_min))
        )
        if is_market_open_buffer:
            logger.info(f"[*] BUFFER CALMA APERTURA: Finestra di assestamento iniziale attiva ({now_ny.strftime('%H:%M:%S')} EST, 9:30-9:{30+open_calm_min:02d}). Nessun nuovo ingresso per evitare il caos di campana.")
            return

        # ---------------------------------------------------------------------
        # 3. SCANNER CANDIDATI BIDIREZIONALE: TIMESTRAL (LONG & SHORT)
        # ---------------------------------------------------------------------
        now_ts = datetime.now()
        # Cooldown di 10 minuti per ordini recentemente inviati (anti race-condition / triplo acquisto)
        self.pending_order_times = {s: t for s, t in self.pending_order_times.items() if (now_ts - t).total_seconds() < 600}
        # Pulizia ticker in cooldown post-stop loss (anti-whipsaw / revenge trading)
        self.ticker_cooldowns = {s: t for s, t in self.ticker_cooldowns.items() if now_ts < t}

        # Recupera eventuali ordini aperti / pendenti non ancora eseguiti
        tracked_open = set()
        try:
            for o in self.get_orders():
                if hasattr(o, "is_active") and o.is_active():
                    tracked_open.add(o.symbol)
        except Exception:
            pass

        symbols_to_scan = [
            s for s in self.parameters["symbols"] 
            if s not in pos_symbols 
            and s not in tracked_open 
            and s not in self.pending_order_times
            and s not in self.ticker_cooldowns
        ]

        # Spunto 1: Macro Regime Switch QQQ
        macro = self.evaluate_macro_regime()
        regime = macro.get("regime", "NEUTRAL")
        allow_long = macro.get("allow_long", True)
        allow_short = macro.get("allow_short", True)
        logger.info(f"[*] Macro Regime: {regime} (QQQ: ${macro.get('qqq_curr', 0):.2f}, Intraday: {macro.get('qqq_intraday_pct', 0):+.2f}%, Long: {allow_long}, Short: {allow_short})")

        # Spunto 2: Ordinamento Cross-Sectional per Forza Relativa vs QQQ
        symbols_to_scan = self.rank_candidates_by_relative_strength(
            symbols_to_scan,
            is_bearish_regime=(regime == "RISK_OFF_BEARISH")
        )

        for symbol in symbols_to_scan:
            try:
                # A. Recupero candele orarie recenti da Alpaca (Lookback ampliato per catturare cicli orari solidi)
                lookback_days = self.parameters.get("timestral_lookback_days", 28)
                req = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Hour,
                    start=datetime.now() - timedelta(days=lookback_days)
                )
                bars_df = self.alpaca_data.get_stock_bars(req).df
                if bars_df.empty:
                    continue

                # Gestione MultiIndex (symbol, timestamp) restituito da Alpaca
                if symbol in bars_df.index:
                    df_sym = bars_df.loc[symbol]
                else:
                    df_sym = bars_df

                if len(df_sym) < 30:
                    continue

                closes = df_sym["close"].tolist()
                curr_price = float(closes[-1])
                day_open = float(df_sym["open"].iloc[-min(7, len(df_sym))])
                intraday_pct = ((curr_price - day_open) / day_open) * 100 if day_open > 0 else 0.0

                # Spunto 3: Anti-Exhaustion Guard (Filtro Rubber-Band / Rimbalzo Tecnico Eccessivo)
                atr = compute_atr(df_sym, period=14)
                extension_in_atr = abs(curr_price - day_open) / atr if atr > 0 else 0.0
                max_extension_limit = self.parameters.get("anti_exhaustion_atr_limit", 3.2)
                if extension_in_atr > max_extension_limit:
                    logger.info(f"[*] ANTI-EXHAUSTION GUARD: Bloccato ingresso su {symbol} (Estensione da Open: {extension_in_atr:.1f}x ATR > {max_extension_limit:.1f}x, {intraday_pct:+.2f}%). Rischio rimbalzo tecnico / squeeze. Salto trade.")
                    continue

                # B. Filtro 1: Predizione Serie Temporali Bidirezionale con Timestral-209M
                # (Include Path Consistency Check anti-fossa e Skewness Asimmetrico anti fat-tail)
                t_res = self.timestral.predict_quantiles(
                    closes, 
                    prediction_length=6,
                    max_skew_ratio_long=self.parameters.get("max_skew_ratio_long", 2.0),
                    min_skew_ratio_short=self.parameters.get("min_skew_ratio_short", 0.75)
                )

                trade_side = None
                trigger_reason = "TIMESTRAL_AI"
                if t_res["is_bullish"]:
                    trade_side = "long"
                elif t_res["is_bearish"]:
                    trade_side = "short"

                daily_sma20 = self.daily_sma20.get(symbol)
                rs_val = self.rs_scores.get(symbol, 0.0)

                # -----------------------------------------------------------------
                # REGIME-FORCED SHORT & BREAKDOWN MOMENTUM (RISOLUZIONE BOTTLENECK)
                # -----------------------------------------------------------------
                # Se siamo in RISK_OFF_BEARISH (QQQ sprofonda), i LONG sono comunque vietati.
                # Se Timestral vede un rimbalzo ma il titolo scende intraday e rompe supporti,
                # verifichiamo che traiettoria e skew non indichino uno spike anomalo contro lo short.
                if regime == "RISK_OFF_BEARISH" and trade_side != "short":
                    if t_res.get("path_consistent_short", True) and t_res.get("skew_ok_short", True):
                        if intraday_pct <= -0.5 and rs_val <= 0.2:
                            # Se il titolo è sotto la SMA20 Daily OPPURE sta perdendo oltre il -1.5% intraday
                            if (daily_sma20 and curr_price < daily_sma20) or intraday_pct <= -1.5:
                                trade_side = "short"
                                trigger_reason = "REGIME_FORCED_BEARISH_MOMENTUM"
                                logger.info(f"[+] REGIME-FORCED SHORT: {symbol} convertito in SHORT in regime {regime}! (Intraday: {intraday_pct:+.2f}%, RS vs QQQ: {rs_val:+.2f}%, Skew D/U: {t_res.get('skewness_ratio')}).")

                # CASO B: Outlier Breakdown anche in regime Neutrale/Bullish (se un titolo crolla da solo)
                elif trade_side != "short" and intraday_pct <= -2.5 and rs_val <= -2.5:
                    if t_res.get("path_consistent_short", True):
                        trade_side = "short"
                        trigger_reason = "OUTLIER_BREAKDOWN_MOMENTUM"
                        logger.info(f"[+] OUTLIER BREAKDOWN SHORT attivato per {symbol}! (Intraday: {intraday_pct:+.2f}%, RS: {rs_val:+.2f}%).")

                if not trade_side:
                    # Logging diagnostico se scartato dai filtri qualitativi di traiettoria o skewness
                    exp_ret = t_res.get("expected_return_pct", 0.0)
                    if exp_ret > 0.5 and not t_res.get("is_falling_knife", False):
                        if not t_res.get("path_consistent_long", True):
                            logger.info(f"[*] PATH FILTER: Scartato segnale LONG su {symbol} (previsto {exp_ret:+.2f}%, ma dip iniziale q50 del {t_res.get('early_drop_pct')}%)")
                        elif not t_res.get("skew_ok_long", True):
                            logger.info(f"[*] SKEW FILTER: Scartato segnale LONG su {symbol} per coda ribassista eccessiva (Skew D/U: {t_res.get('skewness_ratio')} > {self.parameters.get('max_skew_ratio_long', 2.0)})")
                    elif exp_ret < -0.5 and not t_res.get("is_short_squeeze", False):
                        if not t_res.get("path_consistent_short", True):
                            logger.info(f"[*] PATH FILTER: Scartato segnale SHORT su {symbol} per spike iniziale anomalo (+{t_res.get('early_spike_pct')}%)")
                        elif not t_res.get("skew_ok_short", True):
                            logger.info(f"[*] SKEW FILTER: Scartato segnale SHORT su {symbol} per potenziale ribassista asimmetrico debole (Skew D/U: {t_res.get('skewness_ratio')} < {self.parameters.get('min_skew_ratio_short', 0.75)})")
                    continue

                # Spunto 1 & Miglioramento C: Controllo direzionale Macro Regime QQQ + Permesso Outlier
                if trade_side == "long" and not allow_long:
                    logger.info(f"[*] Macro Regime {regime}: bloccato acquisto LONG per {symbol} (QQQ {macro.get('qqq_intraday_pct', 0):+.2f}%). Salto trade.")
                    continue
                if trade_side == "short" and not allow_short:
                    # Permesso Eccezionale Outlier: se il titolo sta crollando da solo (RS <= -3.0% vs QQQ) con Timestral o crollo intraday
                    if rs_val <= -3.0 and (t_res.get("expected_return_pct", 0) <= -1.2 or intraday_pct <= -2.0):
                        logger.info(f"[+] SHORT OUTLIER AUTORIZZATO per {symbol} in regime {regime}! (Debolezza estrema: RS {rs_val:+.2f}% vs QQQ, Intraday: {intraday_pct:+.2f}%)")
                    else:
                        logger.info(f"[*] Macro Regime {regime}: bloccata vendita SHORT per {symbol} (QQQ {macro.get('qqq_intraday_pct', 0):+.2f}%, RS: {rs_val:+.2f}%). Salto trade.")
                        continue

                # Miglioramento B: Trend Alignment con SMA-20 Daily (Anti-Coltello Cadente)
                if daily_sma20:
                    if trade_side == "long" and curr_price < daily_sma20:
                        dist_sma_pct = ((curr_price - daily_sma20) / daily_sma20) * 100
                        logger.info(f"[*] TREND ALIGNMENT: Bloccato LONG su {symbol} (${curr_price:.2f} < SMA20 Daily ${daily_sma20:.2f}, {dist_sma_pct:+.2f}%). Evitato coltello che cade!")
                        continue
                    if trade_side == "short" and curr_price > daily_sma20:
                        # Se il titolo è sopra la SMA20 Daily, short ammesso SOLO se è un Outlier con RS <= -3.0% o breakdown intraday in Bearish Regime
                        is_bearish_breakdown = (regime == "RISK_OFF_BEARISH" and intraday_pct <= -1.5)
                        if rs_val > -3.0 and not is_bearish_breakdown:
                            dist_sma_pct = ((curr_price - daily_sma20) / daily_sma20) * 100
                            logger.info(f"[*] TREND ALIGNMENT: Bloccato SHORT su {symbol} (${curr_price:.2f} > SMA20 Daily ${daily_sma20:.2f}, {dist_sma_pct:+.2f}%). Trend primario rialzista intatto!")
                            continue

                # Controllo Anti-Concentrazione Cluster di Settore (Max 2 posizioni per cluster)
                cand_cluster = get_symbol_cluster(symbol)
                cluster_count = sum(1 for p in positions if get_symbol_cluster(p.symbol) == cand_cluster)
                if cluster_count >= self.parameters.get("max_cluster_positions", 2):
                    logger.info(f"[*] CLUSTER LIMIT: Bloccato {symbol} ({cand_cluster}) - gia' presenti {cluster_count} posizioni nel settore! Salto trade.")
                    continue

                # Spunto 2: Filtro Anti-Rumore per Cluster CRYPTO_FINTECH (MSTR, COIN, HOOD)
                # Richiede un breakdown/breakout orario marcato (<= -1.5% o RS <= -1.5% per short, >= +1.5% per long)
                # per eliminare falsi segnali dovuti al noise speculativo retail da 5 minuti.
                if cand_cluster == "CRYPTO_FINTECH":
                    if trade_side == "short" and intraday_pct > -1.5 and rs_val > -1.5:
                        logger.info(f"[*] ANTI-RUMORE CRYPTO: Bloccato SHORT su {symbol} (Intraday: {intraday_pct:+.2f}%, RS: {rs_val:+.2f}%). Rumore non confermato da breakdown orario solido.")
                        continue
                    if trade_side == "long" and intraday_pct < 1.5 and rs_val < 1.5:
                        logger.info(f"[*] ANTI-RUMORE CRYPTO: Bloccato LONG su {symbol} (Intraday: {intraday_pct:+.2f}%, RS: {rs_val:+.2f}%). Breakout debole non confermato.")
                        continue

                # C. Filtro 2: Scudo News & Trimestrali con Gemini Flash-Lite
                news_res = self.news_guardian.evaluate_symbol_safety(symbol, side=trade_side)
                if not news_res["safe_to_enter"]:
                    logger.warning(f"[-] VETO NEWS/TRIMESTRALE per {symbol} ({trade_side.upper()}): {news_res['reason']}")
                    continue

                # D. Money Management Matematico con Adaptive Sizing & Respiro (Punti B & C)
                meta_p = self.learner.get_meta_params()
                q_mult = self.learner.get_ticker_multiplier(symbol)

                min_stop_ratio = meta_p.get("min_stop_pct", 1.8) / 100.0
                atr_mult = meta_p.get("atr_multiplier", 1.5)

                atr = compute_atr(df_sym, period=14, default_pct=min_stop_ratio)
                cushion = max(curr_price * min_stop_ratio, atr_mult * atr)

                if trade_side == "long":
                    raw_stop = t_res["dynamic_stop_loss"]
                    # Respiro: lo stop non può essere più vicino di 'cushion' sotto il prezzo
                    dynamic_stop = round(min(raw_stop, curr_price - cushion), 2)
                    risk_distance = curr_price - dynamic_stop
                    risk_pct = self.parameters["risk_pct"]
                    cap_pct = self.parameters["max_cap"]
                else: # Short
                    raw_stop = t_res["dynamic_stop_loss_short"]
                    # Respiro: lo stop non può essere più vicino di 'cushion' sopra il prezzo
                    dynamic_stop = round(max(raw_stop, curr_price + cushion), 2)
                    risk_distance = dynamic_stop - curr_price
                    risk_pct = self.parameters["short_risk_pct"]
                    cap_pct = self.parameters["short_max_cap"]

                # Controllo coerenza dello stop (distanza minima basata su meta_p, massima 6.0%)
                stop_dist_pct = (risk_distance / curr_price) * 100
                if stop_dist_pct < (meta_p.get("min_stop_pct", 1.8) * 0.8):
                    logger.info(f"[*] Stop Loss per {symbol} ({trade_side.upper()}) troppo compresso ({stop_dist_pct:.2f}%). Salto trade.")
                    continue
                if stop_dist_pct > 6.0:
                    logger.info(f"[*] Volatilità eccessiva per {symbol} ({trade_side.upper()}): Stop richiesto {stop_dist_pct:.2f}% > 6.0%. Salto trade.")
                    continue

                # Controllo Correlazione con Posizioni Aperte (Marginal Risk Sizing)
                max_corr = 0.0
                corr_sym = None
                if not self.corr_matrix.empty and symbol in self.corr_matrix.columns:
                    for p in positions:
                        if p.symbol in self.corr_matrix.columns:
                            c_val = float(self.corr_matrix.loc[symbol, p.symbol])
                            if c_val > max_corr:
                                max_corr = c_val
                                corr_sym = p.symbol

                corr_mult = 1.0
                if max_corr >= self.parameters.get("correlation_threshold", 0.65):
                    corr_mult = 0.60  # Taglio prudenziale del 40% se elevata correlazione con posizioni aperte
                    logger.info(f"[*] CORRELATION PENALTY: {symbol} ha alta correlazione (+{max_corr:.2f}) con {corr_sym}. Size ridotta del 40% (moltiplicatore: 0.60x).")

                # Taglio size prudenziale per cluster CRYPTO_FINTECH ad alto beta
                crypto_mult = self.parameters.get("crypto_size_multiplier", 0.70) if cand_cluster == "CRYPTO_FINTECH" else 1.0
                if crypto_mult < 1.0:
                    logger.info(f"[*] CRYPTO VOLATILITY SIZING: {symbol} appartiene a {cand_cluster}. Size ridotta al {crypto_mult*100:.0f}% (moltiplicatore: {crypto_mult:.2f}x).")

                # Formula: Quantità = Rischio Max * Moltiplicatore Bandit Q * Moltiplicatore Correlazione * Moltiplicatore Crypto / Distanza Stop
                max_risk_dollars = portfolio_val * risk_pct * q_mult * corr_mult * crypto_mult
                qty = int(max_risk_dollars / risk_distance)

                # Cap Massimo per singolo titolo
                max_shares_cap = int((portfolio_val * cap_pct) / curr_price)
                qty = min(qty, max_shares_cap)

                # Controllo Portfolio VaR 95% Pre-Trade Guard (Risk Budgeting)
                cand_dollar = qty * curr_price if trade_side == "long" else -qty * curr_price
                test_weights = {}
                for p in positions:
                    p_price = self.get_last_price(p.symbol) or float(getattr(p, "avg_entry_price", 0.0))
                    test_weights[p.symbol] = p.quantity * p_price
                test_weights[symbol] = cand_dollar

                var_pct, var_dollars = self.estimate_portfolio_var(test_weights, portfolio_val)
                max_var_pct = self.parameters.get("max_portfolio_var_pct", 0.025) * 100

                if var_pct > max_var_pct:
                    scale_factor = max_var_pct / var_pct
                    new_qty = int(qty * scale_factor)
                    if new_qty <= 0:
                        logger.info(f"[*] PORTFOLIO VaR BUDGET: Ordine {symbol} bloccato. VaR previsto ({var_pct:.2f}%) eccede il budget massimo del {max_var_pct:.2f}%.")
                        continue
                    logger.info(f"[*] PORTFOLIO VaR RESIZE: Quantita' {symbol} ridotta da {qty} a {new_qty} azioni per contenere VaR ({var_pct:.2f}% -> {max_var_pct:.2f}%).")
                    qty = new_qty
                    var_pct, var_dollars = max_var_pct, (portfolio_val * max_var_pct / 100.0)

                # Verifica disponibilità liquidità
                costo_totale = qty * curr_price
                if costo_totale > (cash - min_required_cash) or qty <= 0:
                    logger.info(f"[*] Liquidita' insufficiente per {trade_side.upper()} {qty} azioni di {symbol}.")
                    continue

                # E. Esecuzione Ordine (Buy per Long, Sell per Short)
                logger.info(f"[+] SEGNALE {trade_side.upper()} VALIDATO per {symbol}! (Bandit Q={q_mult:.2f}x | Corr={corr_mult:.2f}x | VaR={var_pct:.2f}%)")
                logger.info(f"    Prezzo: ${curr_price:.2f} | Stop Dinamico: ${dynamic_stop:.2f} | Quantita': {qty} azioni")
                logger.info(f"    Rendimento atteso: {t_res['expected_return_pct']:+.2f}% | Score News: {news_res['sentiment_score']:+.2f}")

                order_side = "buy" if trade_side == "long" else "sell"
                order = self.create_order(symbol, quantity=qty, side=order_side)
                self.submit_order(order)
                self.pending_order_times[symbol] = datetime.now()
                self.active_stops[symbol] = dynamic_stop
                self.entry_prices[symbol] = curr_price
                self.last_stop_update[symbol] = datetime.now()

                # Notifica istantanea via n8n -> Telegram
                try:
                    icon = "🟢" if trade_side == "long" else "🔴"
                    crypto_str = f" | Crypto: <b>{crypto_mult:.2f}x</b>" if crypto_mult < 1.0 else ""
                    send_notification(
                        f"{icon} <b>NUOVO ORDINE INVIATO ({trade_side.upper()})</b>\n\n"
                        f"• Titolo: <b>{symbol}</b> ({cand_cluster})\n"
                        f"• Modello Trigger: <b>{trigger_reason}</b>\n"
                        f"• Azioni: <b>{qty}</b> (Rating Bandit: <b>{q_mult:.2f}x</b> | Corr: <b>{corr_mult:.2f}x</b>{crypto_str})\n"
                        f"• Prezzo: <b>${curr_price:.2f}</b> (Controvalore: ${costo_totale:,.2f})\n"
                        f"• Stop Dinamico: <b>${dynamic_stop:.2f}</b> (Respiro: {atr_mult:.1f}x ATR)\n"
                        f"• Portfolio VaR 95%: <b>{var_pct:.2f}%</b> (${var_dollars:,.2f})\n"
                        f"• Rendimento atteso Timestral: <b>{t_res['expected_return_pct']:+.2f}%</b>\n"
                        f"• Score News Guardian: {news_res['sentiment_score']:+.2f}"
                    )
                except Exception as n_err:
                    logger.debug(f"Errore notifica: {n_err}")

                # Gestiamo un'operazione per ciclo
                break

            except Exception as e:
                logger.error(f"Errore durante l'analisi di {symbol}: {e}")
