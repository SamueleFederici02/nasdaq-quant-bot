# AlphaStream: Autonomous Quantitative Trading Engine

AlphaStream is an autonomous, production-grade algorithmic trading system tailored for high-liquidity US equities (Nasdaq-100 constituents). Built on top of **LumiBot** and powered by **Mistral's Timestral Foundation Model**, the system pairs multi-quantile probabilistic forecasting with multi-layered statistical risk management and dynamic trade lifecycle control.

---

## Architecture Overview

![System Architecture Slide](assets/slide_architecture_overview.png)

---

## Live Performance Showcase: +$1,100.33 Session (Friday, Nov 20, 2026)

During the Friday, Nov 20, 2026 trading session, AlphaStream generated **+$1,100.33 net profit (+1.12% single-day return)** on a $98,159.46 starting account, closing **100% flat in cash** ($99,259.79 NAV) at the 15:50 EST sweep with zero overnight exposure:

![Live Performance Friday Nov 20](assets/live_session_performance.png)

### Session Trade Breakdown & Capital Allocation

| Symbol | Sector Cluster | Max Capital Exposure | Net Realized PnL | Return on Capital | Key Driver / Exit Mechanism |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **NVDA** | Semiconductors | $9,817.92 | **+$587.68** | **+2.51%** | Accelerated Tenaglia (>2R lock @ +3.8R peak) |
| **META** | Big Tech | $8,608.65 | **+$260.51** | **+1.26%** | Dynamic 5-Min Ratchet Trailing Stop |
| **QQQ** | Benchmark ETF | $5,585.14 | **+$119.77** | **+2.24%** | Trend Continuation Momentum |
| **SMH** | Chipmakers ETF | $9,073.62 | **+$102.65** | **+0.84%** | Sector Momentum Rider |
| **ASML** | Semiconductors | $7,889.52 | **+$72.80** | **+0.80%** | Structural Support Bounce ($q_{05}$) |
| **HOOD** | Crypto / Fintech | $7,576.06 | **+$58.42** | **+0.70%** | Volatility-sized Breakout Sizing |
| **TSM** | Semiconductors | $8,197.60 | **+$45.22** | **+0.44%** | Quantile Median Target Reached |
| **JPM / MSTR / ORCL** | Diversified / Mixed | ~$8,500 each | **-$30.78** (comb.) | -0.06% | Controlled Break-Even Stop Loss |
| **DDOG / COIN** | Cloud / Crypto | ~$6,200 each | **-$115.94** (comb.) | -0.74% | Strict Dynamic Tail Stop ($q_{05}$) |
| **TOTAL SESSION** | **100% Cash at 15:50 EST** | **Peak: ~$38k Concurrent** | **+$1,100.33** | **+1.12% NAV** | **Zero Overnight Gap Risk Sweep** |

#### Session Takeaways
- **Disciplined Exposure**: Individual position exposure never exceeded the 10% risk cap ($10,000 max capital per single ticker).
- **Asymmetric Risk/Reward**: Total gains from winners (**+$1,247.05**) outweighed total controlled losses (**-$146.72**) by more than $8.5:1$.
- **Zero Overnight Shock**: All positions were systematically swept at 15:50 EST, freeing all capital to cash ahead of the weekend.

---

## Key Highlights

- **Foundation AI Forecasting**: Leverages `Timestral-209M` for 21-quantile probabilistic time-series trajectory estimation on hourly bars.
- **Path Consistency Filter**: Validates early forecast trajectories ($t+1, t+2, t+3$) to prevent adverse dip/spike stop-outs before recovery.
- **Asymmetric Skewness Guard**: Calculates Downside-to-Upside quantile tail dispersion ($D/U$) to eliminate fat-tail risk entries.
- **Dynamic Structural Stops**: Adaptive volatility cushions ($ATR$ + quantile boundary $q_{05}/q_{95}$) with active server-side emergency stops.
- **Tenaglia Accelerated Profit Lock**: Ratchet mechanics lock up to 85% of peak gains once a trade exceeds $+2R$ favorable excursion.
- **Flat-Overnight EOD Sweep**: Automatically liquidates speculative positions at 15:50 EST (21:50 CET), eliminating overnight gap exposure.
- **Multi-Factor Risk Engine**: Sector cluster caps, cross-asset correlation penalties, contextual bandit parameter tuning, and Finnhub/Gemini earnings shields.

---

## Timestral-209M Probabilistic Forecasting

Rather than relying on noisy single-point predictions, AlphaStream evaluates the full probability distribution of future price steps across 21 quantiles:

![Timestral Quantile Fan Chart](assets/timestral_forecast_example.png)

- **Statistical Boundaries**: $q_{05}$ serves as the floor stop for Long entries, while $q_{95}$ bounds Short positions.
- **Trajectory Validation**: Before opening any position, the engine verifies that the immediate steps do not violate risk buffers before reaching target median $q_{50}$.

---

## Dynamic Profit Protection & Lifecycle

Positions are dynamically managed throughout the trading session to lock in gains and prevent round-trips:

![Accelerated Tenaglia Slide](assets/slide_tenaglia_lifecycle.png)

- **Market Open Buffer**: 10-minute quiet period (9:30–9:40 EST) to avoid opening-bell whipsaws.
- **Accelerated Tenaglia**: When favorable excursion exceeds $+2R$, trailing stops instantly lock **85% of maximum peak profits**.
- **15:50 EST EOD Sweep**: Closes 100% of open positions flat before the closing bell to ensure zero overnight holding risk.

---

## Trade Decision Sequence & Logic Flow

AlphaStream operates in an orderly, highly deterministic sequence during every 30-second cycle:

```mermaid
sequenceDiagram
    autonumber
    participant Scanner as Market Scanner
    participant AI as Timestral Engine
    participant Risk as Risk & Portfolio Filter
    participant Broker as Alpaca Execution
    participant Trailing as Ratchet / Tenaglia

    Scanner->>AI: Fetch 28-day hourly history
    AI->>AI: Compute q05, q50, q95 quantiles
    AI->>AI: Check Path Consistency (Anti-Dip) & Asymmetric Skew (D/U)
    AI-->>Risk: Send trade direction & dynamic stops
    Risk->>Risk: Check QQQ Macro Regime & 20-day SMA alignment
    Risk->>Risk: Check Sector Cluster & Cross-Asset Correlation
    Risk->>Risk: Apply Contextual Bandit sizing & Volatility cushion
    Risk->>Broker: Submit limit/market entry + catastrophic server stop
    Broker-->>Trailing: Register active position
    loop Every 5 Minutes
        Trailing->>Trailing: Evaluate peak profit (> 2R activates Accelerated Tenaglia)
        Trailing->>Broker: Tighten dynamic stop (ratchet forward only)
    end
    Trailing->>Broker: 15:50 EST EOD Sweep -> Close all positions flat
```

```mermaid
flowchart TD
    A[Alpaca Market Data API] --> B[28-Day Hourly Context Window]
    B --> C[Timestral-209M Inference]
    C --> D[Path Consistency & Asymmetric Skew Verification]
    D --> E[Macro Regime & 20-Day Daily SMA Filter]
    E --> F[Sector Cluster & Cross-Correlation Sizing]
    F --> G[Order Submission via Alpaca API]
    G --> H[5-Min Ratchet & Accelerated Tenaglia Lock]
    H --> I[15:50 EST Flat-Overnight Cash Sweep]
```

---

## Core Modules

| Module | Primary Responsibility |
| :--- | :--- |
| `timestral_engine.py` | Runs CPU-optimized quantile forecasting with `Timestral-209M`. Extracts median expectations, structural stops, and tail risk asymmetries. |
| `strategy.py` | Orchestrates order flow, macro regime detection, sector limits, correlation-based size adjustments, and dynamic profit locking. |
| `config.py` | Centralized repository of risk constants, sector clusters, watchlist constituents, and operational schedules. |
| `news_guardian.py` | Pre-trade news vetting and earnings blackout enforcement using Finnhub calendars and Google Gemini Flash. |
| `adaptive_learner.py` | Contextual multi-armed bandit tuning parameters such as stop distances, break-even thresholds, and ticker weights based on real trade feedback. |
| `notifier.py` | Sends structured HTML telemetry and trade alerts to Telegram via webhook integration. |

---

## Getting Started

### Prerequisites

- Python 3.10+
- Alpaca Markets Account (Paper Trading credentials)
- Google Gemini API Key (optional for news sentiment veto)
- Finnhub API Key (for earnings calendar checks)

### Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/SamueleFederici02/nasdaq-quant-bot.git
   cd nasdaq-quant-bot
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   ```
   Fill in your API keys in `.env`.

3. **Install dependencies:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. **Run the bot:**
   ```bash
   python main.py
   ```

---

## Docker Deployment (Headless Server / OMV)

The system includes a production-ready `Dockerfile` and `docker-compose.yml` configured with memory constraints (`mem_limit: 1500m`) for running safely on low-resource Debian/OpenMediaVault servers.

```bash
docker compose up -d --build
```
