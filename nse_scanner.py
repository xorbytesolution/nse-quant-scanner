"""
==============================================================================
NSE STOCK QUANT SCANNER PRO & ADVANCED HISTORY PORTAL
==============================================================================
Author: Senior Quant Developer & Stock Market Algorithm Engineer
Language: Python 3.12+
Dependencies: yfinance, pandas, numpy, tabulate, streamlit, plotly, sqlite3
"""

import concurrent.futures
import datetime
import json
import os
import sqlite3
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

HISTORY_DB = "scan_history.db"

# ==============================================================================
# 0. SCAN HISTORY DATABASE ENGINE (SQLite)
# ==============================================================================

def init_history_db():
    """Initializes SQLite table for storing scan history if not exists."""
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS scan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                preset_mode TEXT,
                universe TEXT,
                total_matches INTEGER,
                scan_duration REAL,
                results_json TEXT
            )
        ''')
        conn.commit()
        conn.close()
    except Exception:
        pass


def save_to_history(preset_mode: str, universe: str, total_matches: int, duration: float, df: pd.DataFrame, target_date_str: str | None = None):
    """Saves a completed scan run into the SQLite history database."""
    try:
        init_history_db()
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if target_date_str:
            now_str += f" [Target Date: {target_date_str}]"
        results_json = df.to_json(orient="records") if not df.empty else "[]"
        c.execute('''
            INSERT INTO scan_logs (timestamp, preset_mode, universe, total_matches, scan_duration, results_json)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (now_str, preset_mode, universe, total_matches, duration, results_json))
        conn.commit()
        conn.close()
    except Exception:
        pass


def load_history_summary(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    strategy_filter: str = "All"
) -> pd.DataFrame:
    """Loads and filters list of past scans from the history database."""
    try:
        init_history_db()
        conn = sqlite3.connect(HISTORY_DB)
        
        query = "SELECT id AS 'Scan ID', timestamp AS 'Scan Time', preset_mode AS 'Strategy Preset', universe AS 'Stock Universe', total_matches AS 'Matches Found', scan_duration AS 'Duration (s)' FROM scan_logs WHERE 1=1"
        params = []

        if strategy_filter != "All":
            query += " AND preset_mode = ?"
            params.append(strategy_filter)

        query += " ORDER BY id DESC"

        df_hist = pd.read_sql_query(query, conn, params=params)
        conn.close()

        if not df_hist.empty and (start_date or end_date):
            df_hist['date_obj'] = pd.to_datetime(df_hist['Scan Time'].str.split().str[0]).dt.date
            if start_date:
                df_hist = df_hist[df_hist['date_obj'] >= start_date]
            if end_date:
                df_hist = df_hist[df_hist['date_obj'] <= end_date]
            df_hist.drop(columns=['date_obj'], inplace=True)

        return df_hist
    except Exception:
        return pd.DataFrame()


def load_history_detail(scan_id: int) -> dict | None:
    """Retrieves full stock result table for a specific past scan ID."""
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        c.execute("SELECT timestamp, preset_mode, universe, results_json FROM scan_logs WHERE id = ?", (scan_id,))
        row = c.fetchone()
        conn.close()
        if row:
            timestamp, preset_mode, universe, results_json = row
            df = pd.read_json(results_json) if results_json else pd.DataFrame()
            return {
                'timestamp': timestamp,
                'preset_mode': preset_mode,
                'universe': universe,
                'df': df
            }
    except Exception:
        pass
    return None


def search_symbol_in_history(symbol_query: str) -> pd.DataFrame:
    """Searches across all historical scan logs for a specific stock symbol."""
    try:
        init_history_db()
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        c.execute("SELECT id, timestamp, preset_mode, universe, results_json FROM scan_logs ORDER BY id DESC")
        rows = c.fetchall()
        conn.close()

        found_records = []
        clean_query = symbol_query.strip().upper()

        for scan_id, timestamp, preset_mode, universe, results_json in rows:
            if not results_json or results_json == "[]":
                continue
            df_scan = pd.read_json(results_json)
            if 'Symbol' in df_scan.columns:
                matched = df_scan[df_scan['Symbol'].astype(str).str.upper().str.contains(clean_query)]
                for _, row in matched.iterrows():
                    rec = dict(row)
                    rec['Scan ID'] = scan_id
                    rec['Scan Time'] = timestamp
                    rec['Strategy'] = preset_mode
                    found_records.append(rec)

        if found_records:
            df_matched = pd.DataFrame(found_records)
            first_cols = ['Scan ID', 'Scan Time', 'Symbol', 'Close (₹)', 'Change (%)', 'Volume', 'Signal Status', 'Strategy']
            other_cols = [c for c in df_matched.columns if c not in first_cols]
            return df_matched[first_cols + other_cols]
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def clear_history_db():
    """Clears all saved scan history."""
    try:
        conn = sqlite3.connect(HISTORY_DB)
        c = conn.cursor()
        c.execute("DELETE FROM scan_logs")
        conn.commit()
        conn.close()
    except Exception:
        pass


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
    preset_mode: str = "Super Bullish Breakout (Recommended)",
    target_date: datetime.date | None = None
) -> dict | None:
    """
    Evaluates scanner conditions on OHLCV stock data up to target_date according to selected preset mode.
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

    # Historical Target Date Slicing
    if target_date is not None:
        target_dt = pd.to_datetime(target_date)
        df = df[df.index <= target_dt]
    
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


def download_and_process_symbol(symbol: str, preset_mode: str, target_date: datetime.date | None = None) -> dict | None:
    """Worker task to fetch stock data from Yahoo Finance and evaluate rules."""
    try:
        data = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
        if data.empty:
            return None
        return analyze_stock(symbol, data, preset_mode=preset_mode, target_date=target_date)
    except Exception:
        return None


def run_full_scan(
    max_workers: int = 25, 
    max_stocks: int | None = None, 
    callback=None, 
    preset_mode: str = "Super Bullish Breakout (Recommended)",
    target_date: datetime.date | None = None
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
                preset_mode,
                target_date
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
# 4. STREAMLIT EASY & POWERFUL UI DASHBOARD WITH CLAYMORPHISM 3D DESIGN
# ==============================================================================

def launch_streamlit_dashboard():
    """Launches high-end interactive quant web application in modern Claymorphism 3D Design System."""
    st.set_page_config(
        page_title="NSE Quant Stock Scanner Pro", 
        page_icon="🎨", 
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Masterpiece Claymorphism (Inflated Soft 3D + Double Inset Highlights) CSS
    st.markdown("""
        <style>
            /* ==========================================
               CLAYMORPHISM 3D DESIGN SYSTEM
               ========================================== */
            
            /* Main Soft Pastel Background */
            .stApp {
                background-color: #eef2ff !important;
                color: #1e1b4b !important;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            }
            .stApp, .stApp p, .stApp span, .stApp div, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
                color: #1e1b4b !important;
            }

            /* Soft Clay Sidebar */
            .stSidebar {
                background-color: #f5f7ff !important;
                border-right: 2px solid #e0e7ff !important;
            }
            .stSidebar div, .stSidebar span, .stSidebar label, .stSidebar p {
                color: #1e1b4b !important;
            }

            /* Inflated Clay Title Banner */
            h1 {
                background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
                color: #ffffff !important;
                font-weight: 900 !important;
                letter-spacing: -0.5px;
                display: inline-block !important;
                padding: 14px 28px !important;
                border-radius: 24px !important;
                box-shadow: inset -6px -6px 12px rgba(0,0,0,0.2), inset 6px 6px 12px rgba(255,255,255,0.4), 8px 14px 24px rgba(79, 70, 229, 0.25) !important;
                margin-bottom: 24px !important;
            }
            h1 * {
                color: #ffffff !important;
            }
            h2, h3, h4, h5, h6 {
                color: #1e1b4b !important;
                font-weight: 800 !important;
            }

            /* Clay Info Box */
            .info-box {
                background-color: #ffffff !important;
                border-radius: 24px !important;
                box-shadow: inset -8px -8px 12px rgba(99, 102, 241, 0.05), inset 8px 8px 12px #ffffff, 8px 16px 24px rgba(99, 102, 241, 0.1) !important;
                padding: 22px !important;
                color: #1e1b4b !important;
                margin-bottom: 24px !important;
                border: 2px solid #e0e7ff !important;
            }
            .info-box b, .info-box span, .info-box div, .info-box p {
                color: #1e1b4b !important;
                font-weight: 700 !important;
            }

            /* Inflated 3D Clay Metric Cards */
            .metric-card {
                background: #ffffff !important;
                border-radius: 24px !important;
                padding: 22px !important;
                text-align: center !important;
                border: 2px solid #e0e7ff !important;
                box-shadow: inset -8px -8px 12px rgba(99, 102, 241, 0.05), inset 8px 8px 12px #ffffff, 8px 16px 28px rgba(99, 102, 241, 0.12) !important;
                transition: all 0.2s ease !important;
            }
            .metric-card:hover {
                transform: translateY(-4px) scale(1.01) !important;
                box-shadow: inset -8px -8px 12px rgba(99, 102, 241, 0.08), inset 8px 8px 12px #ffffff, 12px 20px 36px rgba(99, 102, 241, 0.18) !important;
            }
            .metric-value {
                font-size: 32px !important;
                font-weight: 900 !important;
                color: #4f46e5 !important;
            }
            .metric-label {
                font-size: 12px !important;
                font-weight: 800 !important;
                color: #6366f1 !important;
                text-transform: uppercase !important;
                letter-spacing: 0.8px !important;
                margin-top: 6px !important;
                background-color: #e0e7ff !important;
                display: inline-block !important;
                padding: 3px 10px !important;
                border-radius: 12px !important;
            }

            /* Inflated 3D Clay Action Buttons */
            .stButton>button {
                background: linear-gradient(135deg, #6366f1 0%, #4338ca 100%) !important;
                color: #ffffff !important;
                font-weight: 800 !important;
                border: none !important;
                border-radius: 20px !important;
                padding: 14px 30px !important;
                font-size: 16px !important;
                box-shadow: inset -6px -6px 10px rgba(0,0,0,0.25), inset 6px 6px 10px rgba(255,255,255,0.35), 6px 12px 20px rgba(99, 102, 241, 0.3) !important;
                transition: all 0.15s ease !important;
            }
            .stButton>button * {
                color: #ffffff !important;
                font-weight: 800 !important;
            }
            .stButton>button:hover {
                background: linear-gradient(135deg, #4f46e5 0%, #3730a3 100%) !important;
                transform: translateY(-2px) !important;
                box-shadow: inset -6px -6px 10px rgba(0,0,0,0.25), inset 6px 6px 10px rgba(255,255,255,0.4), 8px 16px 26px rgba(99, 102, 241, 0.4) !important;
            }

            /* Clay Rounded Tabs */
            button[data-baseweb="tab"] {
                background: #ffffff !important;
                border-radius: 18px !important;
                border: 2px solid #e0e7ff !important;
                box-shadow: inset -4px -4px 8px rgba(99, 102, 241, 0.04), inset 4px 4px 8px #ffffff, 4px 8px 16px rgba(99, 102, 241, 0.08) !important;
                margin-right: 14px !important;
                padding: 10px 24px !important;
            }
            button[data-baseweb="tab"] div p {
                color: #4338ca !important;
                font-weight: 800 !important;
                font-size: 15px !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] {
                background: linear-gradient(135deg, #e0e7ff, #c7d2fe) !important;
                box-shadow: inset 4px 4px 8px rgba(99, 102, 241, 0.15), inset -4px -4px 8px #ffffff !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] div p {
                color: #3730a3 !important;
                font-weight: 900 !important;
            }

            /* Clay Inset Form Controls & Data Table */
            .stDataFrame {
                border-radius: 20px !important;
                background-color: #ffffff !important;
                border: 2px solid #e0e7ff !important;
                box-shadow: inset -6px -6px 10px rgba(99, 102, 241, 0.05), inset 6px 6px 10px #ffffff, 6px 12px 20px rgba(99, 102, 241, 0.08) !important;
                padding: 12px !important;
            }
            [data-testid="stDataFrame"] div, [data-testid="stDataFrame"] span {
                color: #1e1b4b !important;
                font-weight: 700 !important;
            }
            div[data-baseweb="select"] span, div[data-baseweb="select"] div, input {
                color: #1e1b4b !important;
                background-color: #ffffff !important;
                border-radius: 16px !important;
                border: 2px solid #c7d2fe !important;
                box-shadow: inset -4px -4px 8px rgba(99, 102, 241, 0.04), inset 4px 4px 8px #ffffff !important;
                font-weight: 700 !important;
            }
            label[data-testid="stWidgetLabel"] p {
                color: #1e1b4b !important;
                font-weight: 800 !important;
                font-size: 14px !important;
            }
        </style>
    """, unsafe_allow_html=True)

    st.title("🎨 NSE Stock Quant Scanner Pro")
    st.caption("Claymorphism 3D Institutional Dashboard for Breakout Detection & Market Analysis")

    # STREAMLIT DIALOG MODAL FOR SCAN CONFIGURATION & TARGET DATE PICKER
    if hasattr(st, "dialog"):
        @st.dialog("🎯 Configure & Run Market Scan Filter")
        def open_scan_filter_dialog():
            st.markdown("Select Target Trading Date, Strategy Preset, and Stock Group before starting:")
            
            modal_target_date = st.date_input(
                "📅 Target Trading Date (Default: Today)",
                value=datetime.date.today(),
                help="Select Today for live scan, or pick any past date to run backtest scans as of that specific trading day!"
            )
            
            modal_preset = st.radio(
                "🎯 Scanner Strategy Preset:",
                [
                    "Super Bullish Breakout (Recommended)",
                    "Trend Following Breakout",
                    "Ultra High Momentum (All 17 Rules)"
                ]
            )

            modal_universe = st.selectbox("⚙️ Stock Group Universe", ["Top 300 Liquid Stocks", "All NSE Equities", "Nifty Benchmark 100"])
            modal_workers = st.slider("⚡ Parallel Speed", 10, 40, 25)

            if st.button("▶️ Launch Market Scan Now", type="primary", use_container_width=True):
                st.session_state['run_scan_triggered'] = True
                st.session_state['selected_target_date'] = modal_target_date
                st.session_state['selected_preset'] = modal_preset
                st.session_state['selected_universe'] = modal_universe
                st.session_state['selected_workers'] = modal_workers
                st.rerun()

    # Main Tabs: Live Scanner & Scan History
    tab_live, tab_history = st.tabs(["🚀 Live Market Scanner", "📜 Scan History & Past Reports"])

    # --------------------------------------------------------------------------
    # TAB 1: LIVE MARKET SCANNER
    # --------------------------------------------------------------------------
    with tab_live:
        st.markdown("""
            <div class="info-box">
                <b>💡 Live Stock Scanner Overview</b><br>
                • <b>Target Date Selection</b>: Click <i>'🚀 Start Live Stock Scan'</i> to choose Today or pick any historical trade date!<br>
                • <b>Moving Average Trend (SMA 20 > 40 > 60)</b>: Confirms short, medium & long term uptrend.<br>
                • <b>Ichimoku & Parabolic SAR</b>: Verifies indicator buy signals.<br>
                • <b>Auto History Save</b>: Every scan is automatically logged in the History database for viewing anytime!
            </div>
        """, unsafe_allow_html=True)

        st.sidebar.header("🎯 Step 1: Mode Select")
        preset = st.sidebar.radio(
            "Scanner Strategy Preset:",
            [
                "Super Bullish Breakout (Recommended)",
                "Trend Following Breakout",
                "Ultra High Momentum (All 17 Rules)"
            ],
            key="sidebar_preset"
        )

        st.sidebar.header("⚙️ Step 2: Stock Universe")
        universe = st.sidebar.selectbox("Stocks Group", ["Top 300 Liquid Stocks", "All NSE Equities", "Nifty Benchmark 100"], key="sidebar_universe")

        st.sidebar.header("📅 Step 3: Target Scan Date")
        target_date_input = st.sidebar.date_input(
            "Target Date (Live or Past)",
            value=datetime.date.today(),
            help="Select Today for live scan, or pick any past date for backtesting scans!"
        )

        workers = st.sidebar.slider("Parallel Download Speed", 10, 40, 25, key="sidebar_workers")

        # Main Button
        if st.button("🚀 Start Market Scan (Open Filter Modal)", type="primary", use_container_width=True):
            if hasattr(st, "dialog"):
                open_scan_filter_dialog()
            else:
                st.session_state['run_scan_triggered'] = True
                st.session_state['selected_target_date'] = target_date_input
                st.session_state['selected_preset'] = preset
                st.session_state['selected_universe'] = universe
                st.session_state['selected_workers'] = workers
                st.rerun()

        # CHECK IF SCAN TRIGGERED FROM MODAL OR BUTTON
        if st.session_state.get('run_scan_triggered', False):
            st.session_state['run_scan_triggered'] = False
            
            run_target_date = st.session_state.get('selected_target_date', target_date_input)
            run_preset = st.session_state.get('selected_preset', preset)
            run_universe = st.session_state.get('selected_universe', universe)
            run_workers = st.session_state.get('selected_workers', workers)

            limit = None if run_universe == "All NSE Equities" else (100 if run_universe == "Nifty Benchmark 100" else 300)

            start_time = time.time()
            progress_bar = st.progress(0.0)
            status_text = st.empty()

            target_str = run_target_date.strftime("%Y-%m-%d")
            status_text.text(f"⚡ Launching scan for Target Date: {target_str}...")

            def ui_callback(done, total):
                ratio = done / total
                progress_bar.progress(ratio)
                status_text.text(f"⚡ Scanning {done}/{total} NSE stocks for Trade Date: {target_str} ({(ratio * 100):.1f}%)...")

            df_res, stock_dfs = run_full_scan(
                max_workers=run_workers, 
                max_stocks=limit, 
                callback=ui_callback, 
                preset_mode=run_preset,
                target_date=run_target_date
            )
            
            elapsed = round(time.time() - start_time, 2)
            status_text.success(f"✅ Scan Complete for Target Date ({target_str}) in {elapsed} Seconds!")
            
            # Save to SQLite History Database with Target Date
            save_to_history(
                preset_mode=run_preset,
                universe=run_universe,
                total_matches=len(df_res),
                duration=elapsed,
                df=df_res,
                target_date_str=target_str
            )

            st.session_state['df_res'] = df_res
            st.session_state['stock_dfs'] = stock_dfs
            st.session_state['elapsed'] = elapsed
            st.session_state['last_target_date'] = target_str

            df_res.to_csv("scanner_results.csv", index=False)

        if 'df_res' in st.session_state:
            df_res = st.session_state['df_res']
            stock_dfs = st.session_state['stock_dfs']
            elapsed = st.session_state['elapsed']
            last_target_date = st.session_state.get('last_target_date', datetime.date.today().strftime("%Y-%m-%d"))

            st.markdown("<br>", unsafe_allow_html=True)
            c1, c2, c3, c4 = st.columns(4)
            
            c1.markdown(f"""
                <div class="metric-card">
                    <div class="metric-value">{len(df_res)}</div>
                    <div class="metric-label">Matches ({last_target_date})</div>
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
                    <div class="metric-value">Saved ✅</div>
                    <div class="metric-label">Saved to History DB</div>
                </div>
            """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            if not df_res.empty:
                st.subheader(f"📊 Matching Stocks List for Date: {last_target_date} ({len(df_res)} Stocks Found - Sorted by Highest Volume)")
                st.dataframe(df_res, hide_index=True, use_container_width=True)

                st.download_button(
                    label="📥 Download Results CSV File (scanner_results.csv)",
                    data=df_res.to_csv(index=False).encode('utf-8'),
                    file_name=f"scanner_results_{last_target_date}.csv",
                    mime="text/csv"
                )

                # Candlestick Chart Viewer (Clean Light Mode)
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
                        name='Candles',
                        increasing_line_color='#16a34a',
                        decreasing_line_color='#dc2626'
                    ))
                    fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA20'], mode='lines', name='SMA 20 (Blue)', line=dict(color='#2563eb', width=2)))
                    fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA40'], mode='lines', name='SMA 40 (Amber)', line=dict(color='#d97706', width=2)))
                    fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA60'], mode='lines', name='SMA 60 (Cyan)', line=dict(color='#0284c7', width=2)))
                    fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SpanB'], mode='lines', name='Ichimoku Span B', line=dict(color='#dc2626', width=2)))
                    fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['PSAR'], mode='markers', name='Parabolic SAR', marker=dict(size=5, color='#9333ea')))

                    fig.update_layout(
                        title=f"{selected_symbol} Daily Price Chart & Moving Averages (as of {last_target_date})",
                        template="plotly_white",
                        xaxis_rangeslider_visible=False,
                        height=500,
                        margin=dict(l=20, r=20, t=40, b=20)
                    )
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning(f"⚠️ No stocks matched for Trade Date {last_target_date} in this strict preset mode. Try switching Strategy to 'Super Bullish Breakout (Recommended)'!")

    # --------------------------------------------------------------------------
    # TAB 2: SCAN HISTORY & PAST REPORTS (ENHANCED WITH DATE & SYMBOL FILTERS)
    # --------------------------------------------------------------------------
    with tab_history:
        st.subheader("📜 Scan History Database & Date-wise Filter")
        st.caption("Filter past scans by date, strategy preset, or search for any stock symbol across historical logs.")

        # --- HISTORY FILTER CONTROLS BAR ---
        st.markdown("##### 🔍 History Filter Controls")
        f_col1, f_col2, f_col3, f_col4 = st.columns([2, 2, 2, 2])

        with f_col1:
            date_range_choice = st.selectbox(
                "📅 Quick Date Filter",
                ["All Time", "Today", "Past 7 Days", "Past 30 Days", "Custom Date Range"]
            )

        start_d, end_d = None, None
        today_date = datetime.date.today()

        if date_range_choice == "Today":
            start_d, end_d = today_date, today_date
        elif date_range_choice == "Past 7 Days":
            start_d = today_date - datetime.timedelta(days=7)
            end_d = today_date
        elif date_range_choice == "Past 30 Days":
            start_d = today_date - datetime.timedelta(days=30)
            end_d = today_date
        elif date_range_choice == "Custom Date Range":
            with f_col2:
                start_d = st.date_input("Start Date", value=today_date - datetime.timedelta(days=7))
            with f_col3:
                end_d = st.date_input("End Date", value=today_date)

        with f_col4:
            strat_filter = st.selectbox(
                "🎯 Strategy Filter",
                ["All", "Super Bullish Breakout (Recommended)", "Trend Following Breakout", "Ultra High Momentum (All 17 Rules)"]
            )

        # Stock Search Bar across all history
        st.markdown("##### 🔎 Search Stock Symbol in Past Scans")
        symbol_search_query = st.text_input("Enter Stock Symbol to Search History (e.g. RELIANCE, DIXON, TCS):", placeholder="Type stock name...").strip()

        st.markdown("---")

        # --- IF USER IS SEARCHING A STOCK SYMBOL ---
        if symbol_search_query:
            st.subheader(f"🔎 Symbol History Results for: `{symbol_search_query.upper()}`")
            df_symbol_matches = search_symbol_in_history(symbol_search_query)

            if not df_symbol_matches.empty:
                st.success(f"Found `{len(df_symbol_matches)}` past scan record(s) where `{symbol_search_query.upper()}` matched!")
                st.dataframe(df_symbol_matches, hide_index=True, use_container_width=True)

                st.download_button(
                    label=f"📥 Download {symbol_search_query.upper()} History CSV",
                    data=df_symbol_matches.to_csv(index=False).encode('utf-8'),
                    file_name=f"{symbol_search_query.upper()}_history_scans.csv",
                    mime="text/csv"
                )
            else:
                st.warning(f"No past scan records found containing stock symbol `{symbol_search_query.upper()}`.")

        # --- GENERAL DATE-WISE HISTORY LOGS ---
        df_summary = load_history_summary(start_date=start_d, end_date=end_d, strategy_filter=strat_filter)

        if not df_summary.empty:
            # Summary Metrics for Filtered History
            h1, h2, h3, h4 = st.columns(4)
            h1.markdown(f"""
                <div class="metric-card">
                    <div class="metric-value">{len(df_summary)}</div>
                    <div class="metric-label">Filtered Scans Logged</div>
                </div>
            """, unsafe_allow_html=True)

            total_matches_sum = int(df_summary['Matches Found'].sum()) if 'Matches Found' in df_summary.columns else 0
            h2.markdown(f"""
                <div class="metric-card">
                    <div class="metric-value">{total_matches_sum}</div>
                    <div class="metric-label">Total Matches Found</div>
                </div>
            """, unsafe_allow_html=True)

            avg_dur = round(float(df_summary['Duration (s)'].mean()), 1) if 'Duration (s)' in df_summary.columns else 0
            h3.markdown(f"""
                <div class="metric-card">
                    <div class="metric-value">{avg_dur}s</div>
                    <div class="metric-label">Avg Scan Speed</div>
                </div>
            """, unsafe_allow_html=True)

            h4.markdown(f"""
                <div class="metric-card">
                    <div class="metric-value">{df_summary.iloc[0]['Scan Time'].split()[0]}</div>
                    <div class="metric-label">Latest Scan Date</div>
                </div>
            """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            col_left, col_right = st.columns([3, 1])
            with col_left:
                st.subheader("📋 Past Scan Sessions Table")
                st.dataframe(df_summary, hide_index=True, use_container_width=True)
            with col_right:
                st.markdown("<br><br>", unsafe_allow_html=True)
                if st.button("🗑️ Clear All History Logs", use_container_width=True):
                    clear_history_db()
                    st.rerun()

            st.markdown("---")
            st.subheader("🔍 Inspect Specific Past Scan Details")
            scan_options = {
                f"Scan #{row['Scan ID']} | {row['Scan Time']} | {row['Strategy Preset']} ({row['Matches Found']} matches)": row['Scan ID'] 
                for _, row in df_summary.iterrows()
            }
            selected_scan_label = st.selectbox("Select Past Scan to View Full Stock List:", list(scan_options.keys()))
            selected_scan_id = scan_options[selected_scan_label]

            detail = load_history_detail(selected_scan_id)
            if detail and detail['df'] is not None and not detail['df'].empty:
                st.markdown(f"**Scan Date/Time**: `{detail['timestamp']}` | **Strategy**: `{detail['preset_mode']}` | **Universe**: `{detail['universe']}`")
                st.dataframe(detail['df'], hide_index=True, use_container_width=True)

                st.download_button(
                    label=f"📥 Download Scan #{selected_scan_id} CSV",
                    data=detail['df'].to_csv(index=False).encode('utf-8'),
                    file_name=f"scan_history_{selected_scan_id}.csv",
                    mime="text/csv"
                )
            else:
                st.info("No matching stocks recorded in this specific scan session.")
        else:
            st.info("ℹ️ No scan history logs found matching your selected date and strategy filters. Run a live scan in Tab 1 or adjust your filters above!")


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

    start_t = time.time()
    df_results, _ = run_full_scan(
        max_workers=25, 
        max_stocks=None, 
        callback=progress, 
        preset_mode="Super Bullish Breakout (Recommended)"
    )
    elapsed_t = round(time.time() - start_t, 2)
    print("\n\nScan Completed Successfully!\n")

    # Save to SQLite History Database
    save_to_history(
        preset_mode="Super Bullish Breakout (Recommended)",
        universe="All NSE Equities",
        total_matches=len(df_results),
        duration=elapsed_t,
        df=df_results
    )

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
