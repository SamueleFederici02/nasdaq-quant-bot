"""
Modulo Daily Reporter & Post-Mortem Quantitativo.
Analizza a fine seduta (o su richiesta):
1. I trade eseguiti e il PnL del conto (risolto calcolo daily vs last_equity).
2. Cruscotto di Rischio Istituzionale: Portfolio VaR 95% (1-Day), Expected Shortfall (CVaR 95%),
   esposizione aggregata per Cluster Settoriali (Anti-Concentrazione).
3. Efficienza di Esecuzione & Asimmetria: MFE (Massima Escursione Favorevole),
   MAE (Massima Escursione Avversa) e Capture Ratio dall'Adaptive Learner.
4. Genera con Gemini Flash-Lite il Diario di Bordo analitico e lo notifica su Telegram via n8n.
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from google import genai

from config import (
    ALPACA_CONFIG,
    GEMINI_API_KEY,
    WATCHLIST_CONFIG,
    DEFAULT_SYMBOLS,
    SECTOR_CLUSTERS,
    get_symbol_cluster
)


def generate_daily_report(save_dir: str = None) -> str:
    print("[*] Avvio generazione Report Giornaliero Post-Mortem...")

    # 1. Connessione ad Alpaca
    trade_client = TradingClient(ALPACA_CONFIG["API_KEY"], ALPACA_CONFIG["API_SECRET"], paper=True)
    data_client = StockHistoricalDataClient(ALPACA_CONFIG["API_KEY"], ALPACA_CONFIG["API_SECRET"])

    # Account status
    acct = trade_client.get_account()
    equity = float(acct.equity)
    cash = float(acct.cash)
    starting_capital = 100000.00

    # Calcolo corretto del PnL giornaliero rispetto all'equity di chiusura precedente
    last_equity = float(acct.last_equity) if acct.last_equity else equity
    daily_pnl = equity - last_equity
    daily_pnl_pct = (daily_pnl / last_equity) * 100 if last_equity > 0 else 0.0
    total_pnl = equity - starting_capital
    total_pnl_pct = (total_pnl / starting_capital) * 100

    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    # 2. Recupero ordini eseguiti oggi
    orders = trade_client.get_orders(GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        after=today_start,
        limit=50
    ))

    executed_trades = []
    for o in orders:
        if o.status.value == "filled":
            executed_trades.append({
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty),
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else 0.0,
                "filled_at": str(o.filled_at)
            })

    # 3. Analisi Posizioni Aperte, Rischio di Portafoglio (VaR / CVaR) e Cluster Settoriali
    print("[*] Calcolo metriche di rischio di portafoglio (VaR 95%, CVaR 95% & Cluster)...")
    positions = trade_client.get_all_positions()
    open_positions = []
    cluster_exposure = {}
    weights_dict = {}

    for p in positions:
        sym = p.symbol
        qty = float(p.qty)
        mkt_val = float(p.market_value)
        avg_entry = float(p.avg_entry_price)
        unrealized_pl = float(p.unrealized_pl)
        unrealized_plpc = float(p.unrealized_plpc) * 100
        cluster = get_symbol_cluster(sym)

        cluster_exposure[cluster] = cluster_exposure.get(cluster, 0.0) + abs(mkt_val)
        weights_dict[sym] = mkt_val  # Signed dollar value

        open_positions.append({
            "symbol": sym,
            "side": "short" if qty < 0 else "long",
            "qty": abs(qty),
            "market_value": round(mkt_val, 2),
            "avg_entry": round(avg_entry, 2),
            "unrealized_pnl": round(unrealized_pl, 2),
            "unrealized_pnl_pct": round(unrealized_plpc, 2),
            "cluster": cluster
        })

    var_95_dollars = 0.0
    var_95_pct = 0.0
    cvar_95_dollars = 0.0
    cvar_95_pct = 0.0

    if open_positions:
        try:
            pos_syms = list(weights_dict.keys())
            req_risk = StockBarsRequest(
                symbol_or_symbols=pos_syms,
                timeframe=TimeFrame.Day,
                start=now_utc - timedelta(days=45)
            )
            df_bars = data_client.get_stock_bars(req_risk).df
            if not df_bars.empty:
                pivot_closes = df_bars.reset_index().pivot(index='timestamp', columns='symbol', values='close')
                returns_df = pivot_closes.pct_change().dropna()
                valid_syms = [s for s in pos_syms if s in returns_df.columns]
                if valid_syms:
                    cov_mat = returns_df[valid_syms].cov().values
                    w_arr = np.array([weights_dict[s] for s in valid_syms], dtype=float)
                    port_var_sq = float(np.dot(w_arr, np.dot(cov_mat, w_arr)))
                    if port_var_sq > 0:
                        port_std = np.sqrt(port_var_sq)
                        var_95_dollars = round(1.645 * port_std, 2)
                        var_95_pct = round((var_95_dollars / equity) * 100, 2) if equity > 0 else 0.0
                        cvar_95_dollars = round(2.063 * port_std, 2)  # Gaussian Expected Shortfall
                        cvar_95_pct = round((cvar_95_dollars / equity) * 100, 2) if equity > 0 else 0.0
        except Exception as e_var:
            print(f"[-] Errore calcolo metriche VaR/CVaR: {e_var}")

    # 4. Recupero Metriche Adaptive Learner (MFE, MAE, Capture Ratio, Sortino)
    print("[*] Analisi metriche Adaptive Learner (MFE / MAE / Capture Ratio)...")
    adaptive_state_path = BASE_DIR / "data" / "adaptive_state.json"
    meta_params = {}
    top_q_scores = {}
    recent_trades_metrics = []
    avg_mfe = 0.0
    avg_mae = 0.0
    avg_capture = 0.0

    if adaptive_state_path.exists():
        try:
            with open(adaptive_state_path, "r", encoding="utf-8") as f:
                adaptive_data = json.load(f)
            meta_params = adaptive_data.get("meta_params", {})
            top_q_scores = adaptive_data.get("ticker_q_scores", {})
            trade_hist = adaptive_data.get("trade_history", [])
            if trade_hist:
                recent_trades_metrics = trade_hist[-15:]
                mfes = [t.get("peak_profit_pct", 0.0) for t in recent_trades_metrics if "peak_profit_pct" in t]
                maes = [t.get("max_adverse_pct", 0.0) for t in recent_trades_metrics if "max_adverse_pct" in t]
                captures = [t.get("capture_ratio", 0.0) for t in recent_trades_metrics if "capture_ratio" in t]
                if mfes:
                    avg_mfe = round(sum(mfes) / len(mfes), 2)
                if maes:
                    avg_mae = round(sum(maes) / len(maes), 2)
                if captures:
                    avg_capture = round((sum(captures) / len(captures)) * 100, 1)
        except Exception as e_adap:
            print(f"[-] Errore lettura adaptive_state.json: {e_adap}")

    # 5. Analisi Watchlist (Top Gainers, Top Losers, Grandi Movimenti)
    print("[*] Recupero performance giornaliera dei 31 titoli della Watchlist...")
    market_movers = []
    sample_symbols = DEFAULT_SYMBOLS[:20]  # Top 20 per velocità di calcolo
    try:
        req = StockBarsRequest(
            symbol_or_symbols=sample_symbols,
            timeframe=TimeFrame.Hour,
            start=now_utc - timedelta(days=2)
        )
        bars_dict = data_client.get_stock_bars(req)
        for sym, b_list in bars_dict.data.items():
            if len(b_list) >= 2:
                open_price = float(b_list[0].open)
                last_price = float(b_list[-1].close)
                change_pct = ((last_price - open_price) / open_price) * 100
                market_movers.append({
                    "symbol": sym,
                    "open": round(open_price, 2),
                    "close": round(last_price, 2),
                    "change_pct": round(change_pct, 2)
                })
    except Exception as e:
        print(f"[-] Errore recupero movers: {e}")

    market_movers.sort(key=lambda x: x["change_pct"], reverse=True)
    top_gainers = market_movers[:3] if market_movers else []
    top_losers = market_movers[-3:] if market_movers else []
    big_movers_not_traded = [
        m for m in market_movers
        if abs(m["change_pct"]) >= 2.5 and m["symbol"] not in [t["symbol"] for t in executed_trades]
    ]

    # 6. Payload e Prompt a Gemini Flash-Lite per l'Analisi Qualitativa & Critica
    print("[*] Elaborazione analisi critica con Gemini Flash-Lite...")
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    risk_dashboard = {
        "portfolio_var_95_1d_dollars": var_95_dollars,
        "portfolio_var_95_1d_pct": var_95_pct,
        "portfolio_cvar_95_1d_dollars": cvar_95_dollars,
        "portfolio_cvar_95_1d_pct": cvar_95_pct,
        "posizioni_aperte": open_positions,
        "esposizione_cluster": {k: round(v, 2) for k, v in cluster_exposure.items()},
        "mfe_medio_pct": avg_mfe,
        "mae_medio_pct": avg_mae,
        "capture_ratio_medio_pct": avg_capture,
        "parametri_adattivi": meta_params,
        "q_scores_principali": top_q_scores,
        "ultimi_trade_eseguiti": recent_trades_metrics[-5:]
    }

    data_payload = {
        "data_oggi": str(datetime.now().strftime("%Y-%m-%d")),
        "capitale_iniziale": starting_capital,
        "capitale_attuale": equity,
        "liquidita_cassa": cash,
        "pnl_giornaliero_dollari": round(daily_pnl, 2),
        "pnl_giornaliero_pct": round(daily_pnl_pct, 2),
        "pnl_cumulativo_dollari": round(total_pnl, 2),
        "pnl_cumulativo_pct": round(total_pnl_pct, 2),
        "cruscotto_rischio_ed_efficienza": risk_dashboard,
        "trade_eseguiti": executed_trades,
        "top_gainers_mercato": top_gainers,
        "top_losers_mercato": top_losers,
        "grandi_movimenti_non_tradati": big_movers_not_traded
    }

    prompt = f"""Sei il Lead Quantitative Risk Manager di un fondo di trading quantitativo autonomo su Nasdaq-100.
Analizza i dati operativi della giornata di oggi generati dal nostro algoritmo:

DATI OPERATIVI DI OGGI:
{json.dumps(data_payload, indent=2)}

Scrivi un Diario di Bordo Post-Mortem in perfetto italiano Markdown, strutturato con queste sezioni esatte:
# 🦅 Diario di Bordo & Post-Mortem Quantitativo - {datetime.now().strftime("%d/%m/%Y")}

### 1. 📊 Sintesi Performance & Stato Conto
(Evidenzia capitale totale NAV, liquidità in cassa, PnL realizzato odierno e cumulativo con percentuali).

### 2. 🛡️ Cruscotto di Rischio Istituzionale & Efficienza Esecuzione (MFE / MAE)
- **Rischio di Portafoglio:** Commenta il Value at Risk 1-Day 95% (${var_95_dollars:,.2f} / {var_95_pct:.2f}%) e l'Expected Shortfall CVaR 95% (${cvar_95_dollars:,.2f} / {cvar_95_pct:.2f}%). Se siamo al 100% in liquidità, sottolinea il rischio zero overnight e la piena disponibilità di cassa per cogliere le migliori occasioni. Commenta la suddivisione per cluster settoriali (Semiconductors, Big Tech, Crypto, Consumer) e il rispetto dei limiti (max 2 posizioni per cluster, cap 2.5% VaR).
- **Efficienza di Cattura del Trend (MFE vs MAE):** Commenta il Capture Ratio medio ({avg_capture}%), l'escursione favorevole media (+{avg_mfe}%) vs l'escursione avversa media ({avg_mae}%). Evidenzia l'efficacia del Sortino Reward e del cricchetto Profit-Lock progressivo che impedisce ai trade vincenti di trasformarsi in perdite.

### 3. 🟢 Cosa abbiamo fatto BENE (Decisioni Vincenti & Difesa del Capitale)
(Spiega come sono state gestite le posizioni, es. monetizzazione profitti, protezione con trailing stop dinamico, scudo trimestrali o salvaguardia della liquidità).

### 4. 🔴 Cosa abbiamo fatto MENO BENE / Margini di Miglioramento
(Analisi obiettiva: timing delle entrate, sensibilità stop, slippage o opportunità di uscita anticipate).

### 5. ⚡ Occasioni Mancate o Sprecate della Giornata
(Analizza i titoli della watchlist con forti escursioni rialziste o ribassiste non tradati e spiega perché il bot non è intervenuto o se le regole macro/trend l'hanno correttamente filtrato).

### 6. 🎯 Focus Operativo per la Prossima Seduta
(1-2 indicazioni quantitative e operative per l'apertura successiva dei mercati).

Sii estremamente professionale, quantitativo, chiaro e trasparente."""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=prompt
        )
        report_md = response.text.strip()
    except Exception as api_err:
        print(f"[!] Errore chiamata Gemini per Report: {api_err}. Generazione report statistico di fallback...")
        pos_lines = "\n".join([f"- {p['side'].upper()} {int(p['qty'])}x {p['symbol']} (Valore: ${p['market_value']:,.2f}, PnL: ${p['unrealized_pnl']:+,.2f})" for p in open_positions]) if open_positions else "- Nessuna (100% Cash)"
        cluster_lines = "\n".join([f"- {k}: ${v:,.2f}" for k, v in cluster_exposure.items()]) if cluster_exposure else "- 100% Liquidità"
        orders_lines = "\n".join([f"- {t['side'].upper()} {int(t['qty'])}x {t['symbol']} @ ${t['filled_avg_price']:.2f}" for t in executed_trades]) if executed_trades else "- Nessun ordine oggi"

        report_md = f"""# 🦅 Diario di Bordo & Post-Mortem Quantitativo (Fallback) - {datetime.now().strftime('%d/%m/%Y')}

### 1. 📊 Sintesi Performance & Stato Conto
* **Capitale Iniziale:** ${starting_capital:,.2f}
* **Capitale Attuale (NAV):** ${equity:,.2f}
* **Liquidità in Cassa (Cash):** ${cash:,.2f}
* **PnL Giornaliero:** ${daily_pnl:+,.2f} ({daily_pnl_pct:+.2f}%)
* **PnL Cumulativo:** ${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)
* **Ordini Eseguiti Oggi:** {len(executed_trades)}
* **Posizioni Attive:** {len(open_positions)}

### 2. 🛡️ Cruscotto di Rischio Istituzionale & Efficienza Esecuzione
* **Portfolio 1-Day 95% VaR:** ${var_95_dollars:,.2f} ({var_95_pct:.2f}% del NAV)
* **Portfolio 1-Day 95% CVaR (Expected Shortfall):** ${cvar_95_dollars:,.2f} ({cvar_95_pct:.2f}% del NAV)
* **Esposizione per Cluster Settoriali:**
{cluster_lines}
* **Efficienza Esecuzione (Adaptive Learner):**
  - MFE Medio (Picco Favorevole): +{avg_mfe:.2f}%
  - MAE Medio (Escursione Avversa): {avg_mae:.2f}%
  - Capture Ratio Medio: {avg_capture:.1f}%

### 3. 📋 Dettaglio Posizioni ed Esecuzioni
**Posizioni Aperte:**
{pos_lines}

**Ordini Eseguiti Oggi:**
{orders_lines}

*Nota: Report generato automaticamente in modalità fallback statistico causa indisponibilità temporanea API IA ({api_err}).*
"""

    # 7. Salvataggio su disco con Policy di Retention a 30 giorni
    today_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(save_dir) if save_dir else (BASE_DIR / "data" / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_file = out_dir / f"daily_report_{today_str}.md"

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[+] Report generato con successo e salvato in: {report_file}")

    # Pulizia automatica: elimina report > 30 giorni
    try:
        cutoff = datetime.now() - timedelta(days=30)
        for old_file in out_dir.glob("daily_report_*.md"):
            try:
                date_part = old_file.stem.replace("daily_report_", "")
                file_dt = datetime.strptime(date_part, "%Y-%m-%d")
                if file_dt < cutoff:
                    old_file.unlink()
                    print(f"[*] Pulizia retention: rimosso vecchio report >30gg ({old_file.name})")
            except Exception:
                pass
    except Exception as clean_err:
        print(f"[-] Errore pulizia vecchi report: {clean_err}")

    # 8. Notifica Telegram via n8n
    try:
        from notifier import send_notification
        pnl_icon = "🟢" if daily_pnl >= 0 else "🔴"
        pnl_sign = "+" if daily_pnl >= 0 else ""

        trades_lines = ""
        if executed_trades:
            for t in executed_trades:
                trades_lines += f"  • {t['side'].upper()} {int(t['qty'])}x <b>{t['symbol']}</b> @ ${t['filled_avg_price']:.2f}\n"
        else:
            trades_lines = "  • Nessun nuovo trade aperto/chiuso oggi\n"

        risk_str = f"• VaR 95%: <b>${var_95_dollars:,.2f} ({var_95_pct:.2f}%)</b> | CVaR: <b>${cvar_95_dollars:,.2f}</b>" if open_positions else "• Rischio Overnight: <b>$0.00 (100% Cash)</b>"
        capture_str = f"• Efficienza Cattura: <b>{avg_capture:.0f}%</b> (MFE: +{avg_mfe:.1f}%, MAE: {avg_mae:.1f}%)" if avg_capture > 0 else "• Efficienza Esecuzione: <i>N/D (nessun trade recente)</i>"

        tg_msg = f"""🦅 <b>DIARIO DI BORDO POST-MORTEM - {datetime.now().strftime('%d/%m/%Y')}</b>

📊 <b>Sintesi Giornata:</b>
• NAV Portafoglio: <b>${equity:,.2f}</b>
• Liquidità Residua: <b>${cash:,.2f}</b>
• PnL Odierno: {pnl_icon} <b>{pnl_sign}${daily_pnl:,.2f} ({daily_pnl_pct:+.2f}%)</b>
• PnL Cumulativo: <b>{'+' if total_pnl >= 0 else ''}${total_pnl:,.2f} ({total_pnl_pct:+.2f}%)</b>

🛡️ <b>Rischio & Efficienza:</b>
{risk_str}
{capture_str}

📋 <b>Operazioni Eseguite Oggi:</b>
{trades_lines}
📄 <i>Il report dettagliato con analisi approfondita è archiviato sul server (conservato per 30 giorni).</i>"""
        send_notification(tg_msg)
    except Exception as ex:
        print(f"[-] Errore invio notifica report Telegram: {ex}")

    return report_md


if __name__ == "__main__":
    report = generate_daily_report()
    print("\n" + "=" * 50 + "\n")
    print(report)
