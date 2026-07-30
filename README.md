# NSE Quant Momentum Scanner Pro

> **Live Algorithmic Stock Scanner for NSE India — Powered by 17 Quant Rules**

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://share.streamlit.io)

---

## What It Does

This tool scans **2,300+ NSE listed stocks** using real-time OHLCV data and filters only the **strongest breakout & uptrend stocks** based on 17 quantitative technical conditions.

## 17 Scanner Conditions

| # | Condition | Logic |
|---|-----------|-------|
| 1-7 | **7-Day Range Expansion** | Today's High-Low > Range of past 7 consecutive days |
| 8 | **Bullish Day Candle** | Close > Open |
| 9 | **Day Gain** | Today Close > Yesterday Close |
| 10 | **Weekly Bullish** | Weekly Close > Weekly Open |
| 11 | **Monthly Bullish** | Monthly Close > Monthly Open |
| 12 | **Volume Filter** | Yesterday Volume > 10,000 |
| 13 | **SMA Alignment** | SMA(20) > SMA(40) |
| 14 | **SMA Alignment** | SMA(40) > SMA(60) |
| 15 | **Gap Down Open** | Today Open < Yesterday Close |
| 16 | **Ichimoku Cloud Breakout** | Close > Senkou Span B (9,26,52) |
| 17 | **Parabolic SAR Bullish** | PSAR(0.02,0.02,0.2) < Close |

## Strategy Presets

- **Super Bullish Breakout (Recommended)** — Best for finding today's top movers
- **Trend Following Breakout** — Stocks in steady uptrend
- **Ultra High Momentum (All 17 Rules)** — Strictest filter

## How to Run

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Run Web Dashboard (Recommended)
```bash
streamlit run nse_scanner.py
```

### Run CLI Terminal Mode
```bash
python nse_scanner.py
```

## Output

- **Live web dashboard** with interactive Plotly candlestick charts
- **CSV export** — `scanner_results.csv` sorted by Volume descending

## Tech Stack

- **Python 3.12+**
- **yfinance** — Live OHLCV data
- **pandas / numpy** — Vectorized indicator computation
- **Streamlit** — Web dashboard
- **Plotly** — Interactive charts
- Wilder's **Parabolic SAR** (pure NumPy implementation)
- **Ichimoku Senkou Span B** (vectorized rolling)
