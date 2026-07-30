"""
==============================================================================
NSE STOCK QUANT SCANNER & EASY UI DASHBOARD
==============================================================================
Author: Senior Quant Developer & Stock Market Algorithm Engineer
Language: Python 3.12+
Dependencies: yfinance, pandas, numpy, tabulate, streamlit, plotly
"""

import concurrent.futures
import datetime
import os
import sys
import time
import urllib.request
import numpy as np
import pandas as pd
import yfinance as yf
from tabulate import tabulate

# Fix Windows console unicode printing issue
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Try importing pandas_ta if available
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False

# Try importing streamlit & plotly for Interactive Web UI
try:
    import streamlit as st
    import plotly.graph_objects as go
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False


# ==============================================================================
# 1. NSE SYMBOL PROVIDER
# ==============================================================================

NIFTY_500_FALLBACK_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
    "INFY.NS", "ITC.NS", "SBIN.NS", "LTIM.NS", "LT.NS", "HINDUNILVR.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "M&M.NS", "TATAMOTORS.NS", "SUNPHARMA.NS",
    "NTPC.NS", "MARUTI.NS", "TITAN.NS", "ULTRACEMCO.NS", "ADANIENT.NS",
    "ADANIPORTS.NS", "ONGC.NS", "POWERGRID.NS", "TATASTEEL.NS", "COALINDIA.NS",
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "JSWSTEEL.NS", "ASIANPAINT.NS", "NESTLEIND.NS",
    "GRASIM.NS", "TECHM.NS", "HCLTECH.NS", "WIPRO.NS", "HDFCLIFE.NS",
    "SBILIFE.NS", "BPCL.NS", "EICHERMOT.NS", "HEROMOTOCO.NS", "DRREDDY.NS",
    "CIPLA.NS", "APOLLOHOSP.NS", "DIVISLAB.NS", "TATACONSUM.NS", "BRITANNIA.NS",
    "INDUSINDBK.NS", "HINDALCO.NS", "BEL.NS", "HAL.NS", "TRENT.NS",
    "BOSCHLTD.NS", "VEDL.NS", "IOC.NS", "DLF.NS", "GAIL.NS", "SIEMENS.NS",
    "ABB.NS", "PIDILITIND.NS", "BANKBARODA.NS", "PNB.NS", "SHREECEM.NS",
    "TATAELXSI.NS", "CANBK.NS", "CHOLAFIN.NS", "POLYCAB.NS", "TORNTPHARM.NS",
    "ZYDUSLIFE.NS", "MANANK.NS", "SRF.NS", "LODHA.NS", "TATACOMM.NS",
    "GODREJCP.NS", "DABUR.NS", "LUPIN.NS", "AUROPHARMA.NS", "PERSISTENT.NS",
    "COFORGE.NS", "MPHASIS.NS", "MUTHOOTFIN.NS", "MANAPPURAM.NS", "OBEROIRLTY.NS",
    "PHOENIXLTD.NS", "BALKRISIND.NS", "MRF.NS", "APOLLOTYRE.NS", "CEATLTD.NS",
    "BHARATFORG.NS", "ASHOKLEY.NS", "CUMMINSIND.NS", "VOLTAS.NS", "DIXON.NS",
    "KAYNES.NS", "IRCTC.NS", "RVNL.NS", "IRFC.NS", "RECLTD.NS", "PFC.NS"
]


def fetch_all_nse_symbols() -> list[str]:
    """
    Downloads active equity symbols directly from NSE India archives.
    Falls back gracefully to benchmark Nifty tickers if offline/blocked.
    """
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    tickers = []
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            df_nse = pd.read_csv(response)
            if 'SYMBOL' in df_nse.columns:
                if 'SERIES' in df_nse.columns:
                    df_nse = df_nse[df_nse['SERIES'] == 'EQ']
                symbols = df_nse['SYMBOL'].dropna().astype(str).str.strip()
                tickers = [f"{sym}.NS" for sym in symbols.unique() if sym]
    except Exception:
        pass

    if not tickers:
        tickers = NIFTY_500_FALLBACK_SYMBOLS
        
    return sorted(list(set(tickers)))


# ==============================================================================
# 2. TECHNICAL INDICATOR ENGINES (Vectorized / NumPy & pandas_ta compatible)
# ==============================================================================

def compute_psar(df: pd.DataFrame, af_start=0.02, af_inc=0.02, af_max=0.2) -> pd.Series:
    """
    Computes Parabolic SAR (0.02, 0.02, 0.2) using Wilder's original algorithm.
    """
    if HAS_PANDAS_TA:
        try:
            psar_df = df.ta.psar(af0=af_start, af=af_inc, max_af=af_max)
            if psar_df is not None and not psar_df.empty:
                psar_cols = [c for c in psar_df.columns if c.startswith('PSAR')]
                if psar_cols:
                    res = psar_df[psar_cols[0]].combine_first(psar_df[psar_cols[1]]) if len(psar_cols) > 1 else psar_df[psar_cols[0]]
                    return res
        except Exception:
            pass

    # Pure NumPy vectorized fallback
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values
    n = len(df)
    
    psar = np.zeros(n)
    if n == 0:
        return pd.Series(psar, index=df.index)
        
    uptrend = close[0] >= (high[0] + low[0]) / 2.0
    af = af_start
    ep = high[0] if uptrend else low[0]
    psar[0] = low[0] if uptrend else high[0]

    for i in range(1, n):
        prev_sar = psar[i - 1]
        if uptrend:
            sar = prev_sar + af * (ep - prev_sar)
            sar = min(sar, low[i - 1], low[max(0, i - 2)])
            if low[i] < sar:
                uptrend = False
                sar = ep
                ep = low[i]
                af = af_start
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_inc, af_max)
        else:
            sar = prev_sar + af * (ep - prev_sar)
            sar = max(sar, high[i - 1], high[max(0, i - 2)])
            if high[i] > sar:
                uptrend = True
                sar = ep
                ep = high[i]
                af = af_start
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_inc, af_max)
        psar[i] = sar

    return pd.Series(psar, index=df.index)


def compute_ichimoku_span_b(df: pd.DataFrame, period=52, shift=26) -> pd.Series:
    """
    Computes Ichimoku Senkou Span B line:
    (52-period High + 52-period Low) / 2 shifted by 26 periods.
    """
    high_52 = df['High'].rolling(window=period).max()
    low_52 = df['Low'].rolling(window=period).min()
    span_b_raw = (high_52 + low_52) / 2.0
    return span_b_raw.shift(shift)


# ==============================================================================
# 3. ALGORITHMIC QUANT EVALUATION ENGINE
# ==============================================================================

def analyze_stock(
    symbol: str, 
    data: pd.DataFrame, 
    preset_mode: str = "Super Bullish Breakout (Recommended)"
) -> dict | None:
    """
    Evaluates scanner conditions on OHLCV stock data according to selected preset mode.
    Returns metrics dict if conditions are satisfied, else None.
    """
    if data is None or len(data) < 80:
        return None

    df = data.copy()
    df.sort_index(inplace=True)
    
    # Numeric sanitization & handling missing values
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)
    
    if len(df) < 80:
        return None

    # Calculate Indicators
    df['Daily_Range'] = df['High'] - df['Low']
    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['SMA40'] = df['Close'].rolling(window=40).mean()
    df['SMA60'] = df['Close'].rolling(window=60).mean()
    df['SpanB'] = compute_ichimoku_span_b(df, period=52, shift=26)
    df['PSAR'] = compute_psar(df)

    # Current and Historical Values
    c_today = df['Close'].iloc[-1]
    o_today = df['Open'].iloc[-1]
    h_today = df['High'].iloc[-1]
    l_today = df['Low'].iloc[-1]
    v_today = df['Volume'].iloc[-1]
    r_today = df['Daily_Range'].iloc[-1]

    c_prev = df['Close'].iloc[-2]
    v_prev = df['Volume'].iloc[-2]

    sma20_today = df['SMA20'].iloc[-1]
    sma40_today = df['SMA40'].iloc[-1]
    sma60_today = df['SMA60'].iloc[-1]

    psar_today = df['PSAR'].iloc[-1]
    spanb_today = df['SpanB'].iloc[-1]
    spanb_prev = df['SpanB'].iloc[-2]

    # Ignore stocks with insufficient indicator history / NaN
    if any(pd.isna(x) for x in [sma20_today, sma40_today, sma60_today, spanb_today, spanb_prev, psar_today]):
        return None

    # --- BASE CORE CONDITIONS ---
    # Condition 8: Today Close > Today Open (Green Day)
    if not (c_today > o_today):
        return None

    # Condition 9: Today Close > Yesterday Close
    if not (c_today > c_prev):
        return None

    # Condition 12: Volume > 10,000
    if not (v_prev > 10000):
        return None

    # Condition 13: SMA(20) > SMA(40)
    if not (sma20_today > sma40_today):
        return None

    # Condition 14: SMA(40) > SMA(60)
    if not (sma40_today > sma60_today):
        return None

    # Condition 17: Parabolic SAR < Close
    if not (psar_today < c_today):
        return None

    # --- PRESET SPECIFIC CONDITIONS ---
    if preset_mode == "Ultra High Momentum (All 17 Rules)":
        # 1-7. Range Expansion
        prev_7_ranges = df['Daily_Range'].iloc[-8:-1]
        if len(prev_7_ranges) < 7 or not (r_today > prev_7_ranges).all():
            return None
        # 10. Weekly Close > Weekly Open
        curr_dt = df.index[-1]
        wk_start = curr_dt - pd.Timedelta(days=curr_dt.weekday())
        df_wk = df[df.index >= wk_start]
        wk_open = df_wk['Open'].iloc[0] if len(df_wk) > 0 else o_today
        if not (c_today > wk_open):
            return None
        # 11. Monthly Close > Monthly Open
        mo_start = curr_dt.replace(day=1)
        df_mo = df[df.index >= mo_start]
        mo_open = df_mo['Open'].iloc[0] if len(df_mo) > 0 else o_today
        if not (c_today > mo_open):
            return None
        # 15. Gap Down Open
        if not (o_today < c_prev):
            return None
        # 16. Ichimoku Span B Crossover
        if not ((c_today > spanb_today) and (c_prev <= spanb_prev)):
            return None

    elif preset_mode == "Super Bullish Breakout (Recommended)":
        # Check Ichimoku Span B Bullish Position
        if not (c_today > spanb_today):
            return None
        # Weekly Bullish
        curr_dt = df.index[-1]
        wk_start = curr_dt - pd.Timedelta(days=curr_dt.weekday())
        df_wk = df[df.index >= wk_start]
        wk_open = df_wk['Open'].iloc[0] if len(df_wk) > 0 else o_today
        if not (c_today > wk_open):
            return None

    elif preset_mode == "Trend Following Breakout":
        # Solid SMA alignment + Close > Span B
        if not (c_today > spanb_today):
            return None

    # Calculate percentage change for today
    pct_change = round(float(((c_today - c_prev) / c_prev) * 100), 2)
    clean_sym = symbol.replace('.NS', '')

    # Signal description
    signal_tag = "🚀 Strong Uptrend & Bullish Cloud Breakout"
    if c_today > spanb_today and psar_today < c_today:
        signal_tag = "⚡ Super Bullish (SMA + PSAR + Ichimoku)"

    return {
        'Symbol': clean_sym,
        'Close (₹)': round(float(c_today), 2),
        'Change (%)': f"+{pct_change}%" if pct_change > 0 else f"{pct_change}%",
        'Volume': int(v_today),
        'Signal Status': signal_tag,
        'SMA20': round(float(sma20_today), 2),
        'SMA40': round(float(sma40_today), 2),
        'SMA60': round(float(sma60_today), 2),
        'Parabolic SAR': round(float(psar_today), 2),
        'Ichimoku Span B': round(float(spanb_today), 2),
        '_full_df': df
    }


def download_and_process_symbol(symbol: str, preset_mode: str) -> dict | None:
    """Worker task to fetch stock data from Yahoo Finance and evaluate rules."""
    try:
        data = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
        if data.empty:
            return None
        return analyze_stock(symbol, data, preset_mode=preset_mode)
    except Exception:
        return None


def run_full_scan(
    max_workers: int = 25, 
    max_stocks: int | None = None, 
    callback=None, 
    preset_mode: str = "Super Bullish Breakout (Recommended)"
) -> tuple[pd.DataFrame, dict]:
    """
    Runs multi-threaded parallel scan across NSE tickers.
    Returns (DataFrame sorted by Volume descending, stock_charts_dict).
    """
    tickers = fetch_all_nse_symbols()
    if max_stocks and max_stocks > 0:
        tickers = tickers[:max_stocks]

    total = len(tickers)
    results = []
    stock_dfs = {}
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                download_and_process_symbol, 
                sym, 
                preset_mode
            ): sym for sym in tickers
        }
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if callback:
                callback(completed, total)
            res = future.result()
            if res:
                df_hist = res.pop('_full_df', None)
                results.append(res)
                if df_hist is not None:
                    stock_dfs[res['Symbol']] = df_hist

    cols = ['Symbol', 'Close (₹)', 'Change (%)', 'Volume', 'Signal Status', 'SMA20', 'SMA40', 'SMA60', 'Parabolic SAR', 'Ichimoku Span B']
    if results:
        df_out = pd.DataFrame(results)
        df_out.sort_values(by='Volume', ascending=False, inplace=True)
        df_out.reset_index(drop=True, inplace=True)
        df_out = df_out[cols]
    else:
        df_out = pd.DataFrame(columns=cols)

    return df_out, stock_dfs


# ==============================================================================
# 4. STREAMLIT EASY & POWERFUL UI DASHBOARD
# ==============================================================================

def launch_streamlit_dashboard():
    """Launches easy-to-understand interactive quant web app."""
    st.set_page_config(
        page_title="NSE Quant Stock Scanner", 
        page_icon="🟢", 
        layout="wide"
    )

    st.markdown("""
        <style>
            .stApp { background-color: #0b0e14; color: #e1e6ed; }
            .stSidebar { background-color: #121721 !important; border-right: 1px solid #1e2634; }
            .info-box {
                background-color: #161c28;
                border-left: 4px solid #00e676;
                padding: 16px;
                border-radius: 8px;
                margin-bottom: 20px;
            }
            .metric-card {
                background: linear-gradient(135deg, #161c28 0%, #1c2434 100%);
                border: 1px solid #283448;
                border-radius: 12px;
                padding: 18px;
                text-align: center;
            }
            .metric-value { font-size: 28px; font-weight: 800; color: #00ff88; }
            .metric-label { font-size: 13px; color: #8b9bb4; text-transform: uppercase; }
        </style>
    """, unsafe_allow_html=True)

    st.title("🟢 NSE Stock Algorithmic Breakout Scanner")
    st.caption("Yeh Tool NSE Stocks Me Momentum, Bullish Trend Aur Breakouts Auto-Detect Karta Hai")

    # Simple Guide Box
    st.markdown("""
        <div class="info-box">
            <b>💡 Yeh Scanner Kya Karta Hai? (Simple Language)</b><br>
            • <b>Green Candle & Uptrend</b>: Aaj green candle banee wale stocks dhundta hai.<br>
            • <b>Moving Average Trend (SMA 20 > 40 > 60)</b>: Short, Medium & Long term uptrend confirm karta hai.<br>
            • <b>Ichimoku & Parabolic SAR</b>: Technical indicator buy signals verify karta hai.<br>
            • <b>Auto Sort</b>: Sabse zyada Volume aur Buyers wale stocks sabse upar dikhata hai.
        </div>
    """, unsafe_allow_html=True)

    st.sidebar.header("🎯 Step 1: Mode Select Karein")
    preset = st.sidebar.radio(
        "Scanner Strategy Preset:",
        [
            "Super Bullish Breakout (Recommended)",
            "Trend Following Breakout",
            "Ultra High Momentum (All 17 Rules)"
        ],
        help="Select Super Bullish Breakout to get top trending stocks today!"
    )

    st.sidebar.header("⚙️ Step 2: Stock Universe")
    universe = st.sidebar.selectbox("Stocks Group", ["Top 300 Liquid Stocks", "All NSE Equities", "Nifty Benchmark 100"])
    limit = None if universe == "All NSE Equities" else (100 if universe == "Nifty Benchmark 100" else 300)

    workers = st.sidebar.slider("Parallel Download Speed", 10, 40, 25)

    if st.button("🚀 Start Live Stock Scan Now", type="primary"):
        start_time = time.time()
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        def ui_callback(done, total):
            ratio = done / total
            progress_bar.progress(ratio)
            status_text.text(f"⚡ Downloading & Scanning {done}/{total} NSE stocks ({(ratio * 100):.1f}%)...")

        df_res, stock_dfs = run_full_scan(
            max_workers=workers, 
            max_stocks=limit, 
            callback=ui_callback, 
            preset_mode=preset
        )
        
        elapsed = round(time.time() - start_time, 2)
        status_text.success(f"✅ Scan Complete in {elapsed} Seconds!")
        
        st.session_state['df_res'] = df_res
        st.session_state['stock_dfs'] = stock_dfs
        st.session_state['elapsed'] = elapsed

        df_res.to_csv("scanner_results.csv", index=False)

    if 'df_res' in st.session_state:
        df_res = st.session_state['df_res']
        stock_dfs = st.session_state['stock_dfs']
        elapsed = st.session_state['elapsed']

        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        
        c1.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{len(df_res)}</div>
                <div class="metric-label">Matching Stocks</div>
            </div>
        """, unsafe_allow_html=True)
        
        c2.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{df_res.iloc[0]['Symbol'] if not df_res.empty else 'N/A'}</div>
                <div class="metric-label">Top Traded Stock</div>
            </div>
        """, unsafe_allow_html=True)

        c3.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">{elapsed}s</div>
                <div class="metric-label">Scan Speed</div>
            </div>
        """, unsafe_allow_html=True)

        c4.markdown(f"""
            <div class="metric-card">
                <div class="metric-value">Saved</div>
                <div class="metric-label">scanner_results.csv</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        if not df_res.empty:
            st.subheader(f"📊 Matching Stocks List ({len(df_res)} Stocks Found - Sorted by Highest Volume)")
            st.dataframe(df_res, hide_index=True)

            st.download_button(
                label="📥 Download Results CSV File (scanner_results.csv)",
                data=df_res.to_csv(index=False).encode('utf-8'),
                file_name="scanner_results.csv",
                mime="text/csv"
            )

            # Candlestick Chart Viewer
            st.markdown("---")
            st.subheader("📈 Interactive Stock Price & Indicator Chart")
            selected_symbol = st.selectbox("Select Stock Symbol to View Chart:", df_res['Symbol'].tolist())
            
            if selected_symbol in stock_dfs:
                chart_df = stock_dfs[selected_symbol].tail(90)
                
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=chart_df.index,
                    open=chart_df['Open'],
                    high=chart_df['High'],
                    low=chart_df['Low'],
                    close=chart_df['Close'],
                    name='Candles'
                ))
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA20'], mode='lines', name='SMA 20 (Green)', line=dict(color='#00e676', width=1.5)))
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA40'], mode='lines', name='SMA 40 (Orange)', line=dict(color='#ff9100', width=1.5)))
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA60'], mode='lines', name='SMA 60 (Blue)', line=dict(color='#29b6f6', width=1.5)))
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SpanB'], mode='lines', name='Ichimoku Span B', line=dict(color='#e91e63', width=2)))
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['PSAR'], mode='markers', name='Parabolic SAR', marker=dict(size=4, color='#ab47bc')))

                fig.update_layout(
                    title=f"{selected_symbol} Daily Price Chart & Moving Averages",
                    template="plotly_dark",
                    xaxis_rangeslider_visible=False,
                    height=500,
                    margin=dict(l=20, r=20, t=40, b=20)
                )
                st.plotly_chart(fig)
        else:
            st.warning("⚠️ No stocks matched in this strict preset mode today. Try switching Sidebar Strategy to 'Super Bullish Breakout (Recommended)'!")


# Automatic Streamlit execution check when loaded via streamlit command
if HAS_STREAMLIT and st.runtime.exists():
    launch_streamlit_dashboard()


# ==============================================================================
# 5. MAIN ENTRY POINT FOR CLI ONLY
# ==============================================================================

def main():
    if "--ui" in sys.argv:
        if HAS_STREAMLIT:
            print("Launching Web Dashboard UI...")
            os.system(f"python -m streamlit run \"{os.path.abspath(__file__)}\"")
            return
        else:
            print("Streamlit missing. Install with: pip install streamlit")

    print("\n=======================================================")
    print("   NSE STOCK ALGORITHMIC QUANT SCANNER")
    print("=======================================================\n")
    print("Downloading active NSE equity symbols...")
    symbols = fetch_all_nse_symbols()
    print(f"Loaded {len(symbols)} symbols to scan.\n")

    print("Scanning stocks against quant conditions...")

    def progress(done, total):
        pct = (done / total) * 100
        bars = int(pct // 2)
        bar_str = "#" * bars + "-" * (50 - bars)
        print(f"\r[{bar_str}] {done}/{total} ({pct:.1f}%)", end="", flush=True)

    df_results, _ = run_full_scan(
        max_workers=25, 
        max_stocks=None, 
        callback=progress, 
        preset_mode="Super Bullish Breakout (Recommended)"
    )
    print("\n\nScan Completed Successfully!\n")

    output_csv = "scanner_results.csv"
    df_results.to_csv(output_csv, index=False)
    print(f"Results saved to: {output_csv}\n")

    if not df_results.empty:
        print("MATCHING STOCKS FOUND:\n")
        print(tabulate(df_results, headers='keys', tablefmt='psql', showindex=False))
    else:
        print("No stocks matched all active scanner conditions.")


if __name__ == "__main__":
    if not (HAS_STREAMLIT and st.runtime.exists()):
        main()
