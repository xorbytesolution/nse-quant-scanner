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
    import pandas_ta as ta  # type: ignore
    HAS_PANDAS_TA = True
except Exception:
    HAS_PANDAS_TA = False

# Try importing streamlit & plotly for Interactive Web UI
try:
    import streamlit as st
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAS_STREAMLIT = True
except ImportError:
    HAS_STREAMLIT = False

# Try importing local SQLite database engine from market_data_priority
try:
    from market_data_priority import get_ohlcv_dataframe, normalize_symbol
    HAS_LOCAL_DB_ENGINE = True
except Exception:
    HAS_LOCAL_DB_ENGINE = False

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
            
        if not df.empty:
            clean_df = df.copy()
            for col in list(clean_df.columns):
                if str(col).startswith('_'):
                    clean_df.drop(columns=[col], inplace=True, errors='ignore')
            results_json = json.dumps(clean_df.to_dict(orient="records"))
        else:
            results_json = "[]"
            
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
            df = pd.DataFrame()
            if results_json:
                try:
                    data_records = json.loads(results_json)
                    if isinstance(data_records, list):
                        df = pd.DataFrame(data_records)
                    elif isinstance(data_records, str):
                        data_nested = json.loads(data_records)
                        df = pd.DataFrame(data_nested)
                except Exception:
                    try:
                        df = pd.read_json(results_json)
                    except Exception:
                        df = pd.DataFrame()
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
            try:
                data_rec = json.loads(results_json)
                df_scan = pd.DataFrame(data_rec) if isinstance(data_rec, list) else pd.read_json(results_json)
            except Exception:
                continue

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

NIFTY_100_BENCHMARK_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
    "INFY.NS", "ITC.NS", "SBIN.NS", "LT.NS", "HINDUNILVR.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "M&M.NS", "SUNPHARMA.NS",
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
    "ZYDUSLIFE.NS", "MANKIND.NS", "SRF.NS", "LODHA.NS", "TATACOMM.NS",
    "GODREJCP.NS", "DABUR.NS", "LUPIN.NS", "AUROPHARMA.NS", "PERSISTENT.NS",
    "COFORGE.NS", "MPHASIS.NS", "MUTHOOTFIN.NS", "MANAPPURAM.NS", "OBEROIRLTY.NS",
    "PHOENIXLTD.NS", "BALKRISIND.NS", "MRF.NS", "APOLLOTYRE.NS", "CEATLTD.NS",
    "BHARATFORG.NS", "ASHOKLEY.NS", "CUMMINSIND.NS", "VOLTAS.NS", "DIXON.NS",
    "KAYNES.NS", "IRCTC.NS", "RVNL.NS", "IRFC.NS", "RECLTD.NS", "PFC.NS",
    "BHEL.NS", "NMDC.NS", "MAXHEALTH.NS", "NHPC.NS"
]

TOP_300_LIQUID_SYMBOLS = list(dict.fromkeys(NIFTY_100_BENCHMARK_SYMBOLS + [
    "ADANIGREEN.NS", "ADANIPOWER.NS", "ATGL.NS", "AWL.NS", "AMBUJACEM.NS",
    "ASTRAL.NS", "AUBANK.NS", "BAJAJHLDNG.NS", "BANKINDIA.NS", "BERGEPAINT.NS",
    "BHARATFORG.NS", "BOSCHLTD.NS", "CGPOWER.NS", "COLPAL.NS", "CONCOR.NS",
    "COROMANDEL.NS", "CYIENT.NS", "DELHIVERY.NS", "EMAMILTD.NS", "FACT.NS",
    "FEDERALBNK.NS", "FORTIS.NS", "GLAXO.NS", "GLAND.NS", "GODREJPROP.NS",
    "GUJGASLTD.NS", "HAVELSS.NS", "HDFCAMC.NS", "HINDPETRO.NS", "HONAUT.NS",
    "IDFCFIRSTB.NS", "INDIANB.NS", "INDHOTEL.NS", "INDUSTOWER.NS", "IOB.NS",
    "IPCALAB.NS", "JISLJALEQS.NS", "JINDALSTEL.NS", "JIOFIN.NS", "JSWENERGY.NS",
    "JUBLFOOD.NS", "KEI.NS", "KPRMILL.NS", "LALPATHLAB.NS", "LICHSGFIN.NS",
    "LICI.NS", "LUPIN.NS", "MAHABANK.NS", "MFSL.NS", "MOTHERSON.NS",
    "MPHASIS.NS", "MRF.NS", "NAVINFLUOR.NS", "OIL.NS", "OFSS.NS",
    "PAGEIND.NS", "PATANJALI.NS", "PAYTM.NS", "PETRONET.NS", "PIIND.NS",
    "POLYCAB.NS", "POONAWALLA.NS", "PRESTIGE.NS", "SAIL.NS", "SBICARD.NS",
    "SCHAEFFLER.NS", "SOLARINDS.NS", "SONACOMS.NS", "SUNDARMFIN.NS", "SUNTV.NS",
    "SUPREMEIND.NS", "SUZLON.NS", "SYNGENE.NS", "TATACHEM.NS", "TATAPOWER.NS",
    "TATAINVEST.NS", "TATATECH.NS", "TORNTPOWER.NS", "UCOBANK.NS", "UNOMINDA.NS",
    "UPL.NS", "VBL.NS", "YESBANK.NS", "ZOMATO.NS", "ZYDUSLIFE.NS",
    "ABBOTINDIA.NS", "ACC.NS", "ALKEM.NS", "ALOKINDS.NS", "ARE&M.NS",
    "ASTERDM.NS", "AARTIIND.NS", "ANGELONE.NS", "APARINDS.NS", "APTUS.NS",
    "ASHOKLEY.NS", "AUBANK.NS", "BALKRISIND.NS", "BANDHANBNK.NS", "BATAINDIA.NS",
    "BBL.NS", "BDL.NS", "BIOCON.NS", "BIRLACORPN.NS", "BLUESTARCO.NS",
    "CAMS.NS", "CDSL.NS", "CENTRALBK.NS", "CENTURYTEX.NS", "CESC.NS",
    "CHAMBLFERT.NS", "CHALET.NS", "COCHINSHIP.NS", "CREDITACC.NS", "CROMPTON.NS",
    "CUMMINSIND.NS", "DATAPATTNS.NS", "DEEPAKNTR.NS", "DEVYANI.NS", "EIDPARRY.NS",
    "EQUITASBNK.NS", "EXIDEIND.NS", "FSL.NS", "GLENMARK.NS", "GMMPFAUDLR.NS",
    "GODREJIND.NS", "GRANULES.NS", "GRAPHITE.NS", "GREATEAST.NS", "GSPL.NS",
    "HEG.NS", "HFCL.NS", "HINDCOPPER.NS", "HUDCO.NS", "IDBI.NS",
    "IEX.NS", "IIFL.NS", "IRB.NS", "ISEC.NS", "JBCHEPHARM.NS",
    "JINDALSAW.NS", "JKCEMENT.NS", "JWL.NS", "KALYANKJIL.NS", "KARURVYSYA.NS",
    "KEC.NS", "KFINTECH.NS", "KIMS.NS", "KPRMILL.NS", "KRBL.NS",
    "LTF.NS", "LATENTVIEW.NS", "LINDEINDIA.NS", "MANAPPURAM.NS", "MAPMYINDIA.NS",
    "MAZDOCK.NS", "METROPOLIS.NS", "MCX.NS", "MEDANTA.NS", "NATIONALUM.NS",
    "NAVINFLUOR.NS", "NBCC.NS", "NCC.NS", "NLCINDIA.NS", "NUVAMA.NS",
    "OBEROIRLTY.NS", "ORISSAMINE.NS", "PNBHOUSING.NS", "PRAJIND.NS", "RADICO.NS",
    "RAILTEL.NS", "RAMCOCEM.NS", "RITES.NS", "RBLBANK.NS", "RRKABEL.NS",
    "SJVN.NS", "SKFINDIA.NS", "SONATSOFTW.NS", "STARHEALTH.NS", "SUMICHEM.NS",
    "SUNDRMFAST.NS", "SUNTECK.NS", "SUPRAJIT.NS", "SYRMA.NS", "TATAELXSI.NS",
    "TEJASNET.NS", "THERMAX.NS", "TIMKEN.NS", "TITAGARH.NS", "TRIDENT.NS",
    "TRIVENI.NS", "UGRID.NS", "UNIONBANK.NS", "UTIAMC.NS", "VAIBHAVGBL.NS",
    "VGUARD.NS", "VINATIORGA.NS", "VIPIND.NS", "VOLTAS.NS", "WELCORP.NS",
    "WELSPUNLIV.NS", "WHIRLPOOL.NS", "ZENSARTECH.NS"
]))

# Set of NSE Futures & Options (F&O) stock symbols to exclude when Cash-only universe is selected
NSE_FNO_SYMBOLS = {
    "AARTIIND", "ABB", "ABBOTINDIA", "ABFRL", "ACC", "ADANIENT", "ADANIPORTS", 
    "ALKEM", "AMBUJACEM", "APOLLOHOSP", "APOLLOTYRE", "ASHOKLEY", "ASIANPAINT", 
    "ASTRAL", "ATUL", "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", 
    "BAJFINANCE", "BALKRISIND", "BALRAMCHIN", "BANDHANBNK", "BANKBARODA", "BATAINDIA", 
    "BEL", "BHARATFORG", "BHARTIARTL", "BHEL", "BIOCON", "BOSCHLTD", "BPCL", 
    "BRITANNIA", "BSOFT", "CANBK", "CANFINHOME", "CHAMBLFERT", "CHOLAFIN", "CIPLA", 
    "COALINDIA", "COFORGE", "COLPAL", "CONCOR", "COROMANDEL", "CROMPTON", "CUMMINSIND", 
    "DABUR", "DALBHARAT", "DEEPAKNTR", "DELTATECH", "DIVISLAB", "DIXON", "DLF", 
    "DRREDDY", "EICHERMOT", "ESCORTS", "EXIDEIND", "GAIL", "GLENMARK", "GMRINFRA", 
    "GNFC", "GODREJCP", "GODREJPROP", "GRANULES", "GRASIM", "GUJGASLTD", "HAL", 
    "HAVELLS", "HCLTECH", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", 
    "HINDCOPPER", "HINDPETRO", "HINDUNILVR", "ICICIBANK", "ICICIGI", "ICICIPRULI", 
    "IDEA", "IDFC", "IDFCFIRSTB", "IEX", "INDHOTEL", "INDIACEM", "INDIAMART", 
    "INDUSINDBK", "INDUSTOWER", "INFY", "IOC", "IPCALAB", "IRCTC", "ITC", 
    "JINDALSTEL", "JSWSTEEL", "JUBLFOOD", "KOTAKBANK", "LALPATHLAB", "LT", "LTF", 
    "LTIM", "LTTS", "LUPIN", "M&M", "M&MFIN", "MANAPPURAM", "MARUTI", "MCDOWELL-N", 
    "MCX", "METROPOLIS", "MFSL", "MGL", "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN", 
    "NATIONALUM", "NAVINFLUOR", "NESTLEIND", "NMDC", "NTPC", "OBEROIRLTY", "OFSS", 
    "ONGC", "PAGEIND", "PERSISTENT", "PETRONET", "PFC", "PIDILITIND", "PIIND", 
    "PNB", "POLYCAB", "POWERGRID", "PVRINOX", "RAMCOCEM", "RBLBANK", "RECLTD", 
    "RELIANCE", "SAIL", "SBICARD", "SBILIFE", "SBIN", "SHREECEM", "SHRIRAMFIN", 
    "SIEMENS", "SRF", "SUNPHARMA", "SUNTV", "SYNGENE", "TATACHEM", "TATACOMM", 
    "TATACONSUM", "TATAMOTORS", "TATAPOWER", "TATASTEEL", "TCS", "TECHM", "TITAN", 
    "TORNTPHARM", "TORNTPOWER", "TRENT", "TVSMOTOR", "UBL", "ULTRACEMCO", "UPL", 
    "VEDL", "VOLTAS", "WIPRO", "ZEEL", "ZYDUSLIFE", "JIOFIN", "RVNL", "IRFC", "REC",
    "BOSCHLTD", "KAYNES", "MAXHEALTH", "NHPC", "CGPOWER", "PATANJALI", "ZOMATO"
}


def fetch_all_nse_symbols(universe: str = "Non-F&O Cash Equities (No Futures)") -> list[str]:
    """
    Returns symbol list according to user selected universe filter:
    - Non-F&O Cash Equities (No Futures): All active NSE cash equities excluding F&O / Future stocks
    - Top 300 Liquid Stocks: Top 300 market cap & liquid equities
    - Nifty Benchmark 100: Top 100 bluechip index constituents
    - All NSE Equities: Full download of 2400+ active equities from NSE archives
    """
    clean_universe = (universe or "").strip()
    
    if clean_universe == "Nifty Benchmark 100":
        tickers = sorted(list(set(NIFTY_100_BENCHMARK_SYMBOLS)))
    elif "Top 300" in clean_universe:
        tickers = sorted(list(set(TOP_300_LIQUID_SYMBOLS)))
    else:
        # Download full active list for NSE Equities
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
            tickers = TOP_300_LIQUID_SYMBOLS

    # Filter out F&O / Future stocks if "No Futures" or "Non-F&O" is specified (or by default)
    if "No Futures" in clean_universe or "Non-F&O" in clean_universe:
        tickers = [t for t in tickers if t.replace('.NS', '').strip() not in NSE_FNO_SYMBOLS]

    return sorted(list(set(tickers)))


# ==============================================================================
# 2. TECHNICAL INDICATOR ENGINES (100% TradingView Math Identical)
# ==============================================================================

def compute_psar(df: pd.DataFrame, af_start=0.02, af_inc=0.02, af_max=0.2) -> pd.Series:
    """
    Computes Parabolic SAR (0.02, 0.02, 0.2) using Wilder's original exact algorithm.
    Matches TradingView indicator dots precisely.
    """
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


def compute_ichimoku_cloud(df: pd.DataFrame, tenkan_period=9, kijun_period=26, senkou_b_period=52, shift=26) -> dict[str, pd.Series]:
    """
    Computes complete Ichimoku Kinko Hyo Cloud lines:
    - Tenkan-sen (Conversion Line): (9-period High + 9-period Low) / 2
    - Kijun-sen (Base Line): (26-period High + 26-period Low) / 2
    - Senkou Span A (Leading Span A): (Tenkan + Kijun) / 2 shifted 26 periods ahead
    - Senkou Span B (Leading Span B): (52-period High + 52-period Low) / 2 shifted 26 periods ahead
    - Cloud Top: max(Span A, Span B)
    - Cloud Bottom: min(Span A, Span B)
    """
    high_9 = df['High'].rolling(window=tenkan_period).max()
    low_9 = df['Low'].rolling(window=tenkan_period).min()
    tenkan = (high_9 + low_9) / 2.0

    high_26 = df['High'].rolling(window=kijun_period).max()
    low_26 = df['Low'].rolling(window=kijun_period).min()
    kijun = (high_26 + low_26) / 2.0

    span_a_raw = (tenkan + kijun) / 2.0
    span_a = span_a_raw.shift(shift)

    high_52 = df['High'].rolling(window=senkou_b_period).max()
    low_52 = df['Low'].rolling(window=senkou_b_period).min()
    span_b_raw = (high_52 + low_52) / 2.0
    span_b = span_b_raw.shift(shift)

    cloud_top = np.maximum(span_a.fillna(0), span_b.fillna(0))
    cloud_bottom = np.minimum(span_a.fillna(0), span_b.fillna(0))

    return {
        'Tenkan': tenkan,
        'Kijun': kijun,
        'SpanA': span_a,
        'SpanB': span_b,
        'CloudTop': pd.Series(cloud_top, index=df.index),
        'CloudBottom': pd.Series(cloud_bottom, index=df.index)
    }


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Computes Wilder's Relative Strength Index (RSI 14).
    """
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


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
    
    # Strip timezone from index if present to prevent tz-naive vs tz-aware comparison TypeError
    if getattr(df.index, 'tz', None) is not None:
        df.index = df.index.tz_localize(None)

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

    # Ichimoku Cloud Components
    ichi = compute_ichimoku_cloud(df)
    df['Tenkan'] = ichi['Tenkan']
    df['Kijun'] = ichi['Kijun']
    df['SpanA'] = ichi['SpanA']
    df['SpanB'] = ichi['SpanB']
    df['CloudTop'] = ichi['CloudTop']
    df['CloudBottom'] = ichi['CloudBottom']

    df['PSAR'] = compute_psar(df)
    df['RSI'] = compute_rsi(df, period=14)

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
    spana_today = df['SpanA'].iloc[-1]
    spanb_today = df['SpanB'].iloc[-1]
    cloudtop_today = df['CloudTop'].iloc[-1]
    rsi_today = df['RSI'].iloc[-1]

    # Ignore stocks with insufficient indicator history / NaN
    if any(pd.isna(x) for x in [sma20_today, sma40_today, sma60_today, cloudtop_today, psar_today]):
        return None

    # --- BASE CORE CONDITIONS ---
    # Condition 8: Today Close > Today Open (Green Day)
    if not (c_today > o_today):
        return None

    # Condition 9: Today Close > Yesterday Close
    if not (c_today > c_prev):
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
        # 16. True Ichimoku Cloud Breakout (Close > max(Span A, Span B))
        if not (c_today > cloudtop_today):
            return None

    elif preset_mode == "Super Bullish Breakout (Recommended)":
        # True Ichimoku Cloud Breakout (Close > max(Span A, Span B))
        if not (c_today > cloudtop_today):
            return None
        # Weekly Bullish
        curr_dt = df.index[-1]
        wk_start = curr_dt - pd.Timedelta(days=curr_dt.weekday())
        df_wk = df[df.index >= wk_start]
        wk_open = df_wk['Open'].iloc[0] if len(df_wk) > 0 else o_today
        if not (c_today > wk_open):
            return None

    elif preset_mode == "Trend Following Breakout":
        # Solid SMA alignment + Close > CloudTop
        if not (c_today > cloudtop_today):
            return None

    # Calculate percentage change for today
    pct_change = round(float(((c_today - c_prev) / c_prev) * 100), 2)
    clean_sym = symbol.replace('.NS', '')

    # Signal description & Target levels
    stop_loss = round(float(min(psar_today, sma20_today)), 2)
    target1 = round(float(c_today * 1.04), 2)
    target2 = round(float(c_today * 1.08), 2)

    signal_tag = "🚀 Strong Uptrend & Bullish Cloud Breakout"
    if c_today > cloudtop_today and psar_today < c_today:
        signal_tag = "⚡ Super Bullish (SMA + PSAR + Ichimoku Cloud)"

    return {
        'Symbol': clean_sym,
        'Close (₹)': round(float(c_today), 2),
        'Change (%)': f"+{pct_change}%" if pct_change > 0 else f"{pct_change}%",
        'Volume': int(v_today),
        'Signal Status': signal_tag,
        'Stop Loss (₹)': stop_loss,
        'Target 1 (₹)': target1,
        'Target 2 (₹)': target2,
        'RSI (14)': round(float(rsi_today), 1),
        'SMA20': round(float(sma20_today), 2),
        'SMA40': round(float(sma40_today), 2),
        'SMA60': round(float(sma60_today), 2),
        'Parabolic SAR': round(float(psar_today), 2),
        'Ichimoku Cloud Top': round(float(cloudtop_today), 2),
        '_full_df': df,
        '_pct_change_num': pct_change
    }


def fetch_symbol_ohlcv(symbol: str, target_date: datetime.date | None = None) -> tuple[pd.DataFrame | None, str]:
    """
    Hybrid data retriever:
    1. Checks local market_data.sqlite database first (Official Bhavcopy).
    2. Falls back to yfinance with retry mechanism.
    Returns (DataFrame, source_str).
    """
    clean_sym = symbol.replace('.NS', '')
    
    # 1. Try local SQLite database
    if HAS_LOCAL_DB_ENGINE:
        try:
            df_sqlite = get_ohlcv_dataframe(clean_sym, end_date=target_date)
            if df_sqlite is not None and len(df_sqlite) >= 80:
                return df_sqlite, "🟢 Official NSE Bhavcopy (SQLite)"
        except Exception:
            pass

    # 2. yfinance fallback
    for attempt in range(3):
        try:
            data = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
            if data is not None and not data.empty:
                return data, "⚡ Real-Time NSE Feed (yfinance)"
            time.sleep(0.2 * (attempt + 1))
        except Exception:
            time.sleep(0.3 * (attempt + 1))
            
    return None, "Unknown"


def download_and_process_symbol(symbol: str, preset_mode: str, target_date: datetime.date | None = None) -> dict | None:
    """Worker task to fetch stock data from hybrid sources and evaluate quant rules."""
    data, source = fetch_symbol_ohlcv(symbol, target_date=target_date)
    if data is not None and not data.empty:
        res = analyze_stock(symbol, data, preset_mode=preset_mode, target_date=target_date)
        if res:
            res['Data Source'] = source
            return res
    return None


def run_full_scan(
    max_workers: int = 25, 
    max_stocks: int | None = None, 
    callback=None, 
    preset_mode: str = "Super Bullish Breakout (Recommended)",
    target_date: datetime.date | None = None,
    universe: str = "Non-F&O Cash Equities (No Futures)"
) -> tuple[pd.DataFrame, dict]:
    """
    Runs multi-threaded parallel scan across NSE tickers.
    Returns (DataFrame sorted by % Change descending, stock_charts_dict).
    """
    tickers = fetch_all_nse_symbols(universe=universe)
    if max_stocks and max_stocks > 0 and universe == "All NSE Equities":
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

    cols = ['Symbol', 'Close (₹)', 'Change (%)', 'Volume', 'Signal Status', 'Stop Loss (₹)', 'Target 1 (₹)', 'Target 2 (₹)', 'RSI (14)', 'SMA20', 'SMA40', 'SMA60', 'Parabolic SAR', 'Ichimoku Cloud Top', 'Data Source']
    if results:
        df_out = pd.DataFrame(results)
        if '_pct_change_num' in df_out.columns:
            df_out.sort_values(by='_pct_change_num', ascending=False, inplace=True)
            df_out.drop(columns=['_pct_change_num'], inplace=True, errors='ignore')
        else:
            df_out.sort_values(by='Symbol', ascending=True, inplace=True)
        df_out.reset_index(drop=True, inplace=True)
        # Ensure all columns exist
        available_cols = [c for c in cols if c in df_out.columns]
        df_out = df_out[available_cols]
    else:
        df_out = pd.DataFrame(columns=cols)

    return df_out, stock_dfs


# ==============================================================================
# 4. STREAMLIT EASY & POWERFUL UI DASHBOARD WITH HIGHLY ORGANIZED LAYOUT
# ==============================================================================

def launch_streamlit_dashboard():
    """Launches high-end interactive quant web application with clean, well-organized institutional layout."""
    st.set_page_config(
        page_title="NSE Quant Stock Scanner Pro", 
        page_icon="📈", 
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Clean Professional Financial Dashboard CSS
    st.markdown("""
        <style>
            /* Base Canvas & Typography */
            .stApp {
                background-color: #121824 !important;
                color: #f1f5f9 !important;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            }
            .stApp, .stApp p, .stApp span, .stApp div, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
                color: #f1f5f9 !important;
            }
            .stSidebar {
                background-color: #182232 !important;
                border-right: 1px solid #283548 !important;
            }
            .stSidebar div, .stSidebar span, .stSidebar label, .stSidebar p {
                color: #f1f5f9 !important;
            }

            /* Main Hero Header Card */
            .hero-header {
                background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                border: 1px solid #334155;
                border-top: 3px solid #3b82f6;
                border-radius: 14px;
                padding: 20px 28px;
                margin-bottom: 24px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.4);
            }
            .hero-title {
                font-size: 26px;
                font-weight: 800;
                color: #ffffff !important;
                margin: 0 0 6px 0;
            }
            .hero-subtitle {
                font-size: 14px;
                color: #94a3b8 !important;
                margin: 0;
            }

            /* Container Cards */
            .section-card {
                background-color: #182232;
                border: 1px solid #283548;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 24px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
            }

            /* Info Box */
            .info-box {
                background: linear-gradient(135deg, #1e293b 0%, #172438 100%) !important;
                border: 1px solid #334155 !important;
                border-left: 5px solid #3b82f6 !important;
                border-radius: 12px !important;
                padding: 18px 22px !important;
                color: #f1f5f9 !important;
                margin-bottom: 24px !important;
            }
            .info-box b, .info-box span, .info-box div, .info-box p {
                color: #f1f5f9 !important;
            }

            /* Metric Cards */
            .metric-card {
                background: linear-gradient(135deg, #1c2638 0%, #151d2c 100%) !important;
                border: 1px solid #2d3b52 !important;
                border-top: 2px solid #3b82f6 !important;
                border-radius: 12px !important;
                padding: 18px !important;
                text-align: center !important;
                box-shadow: 0 6px 16px rgba(0, 0, 0, 0.3) !important;
            }
            .metric-value {
                font-size: 28px !important;
                font-weight: 800 !important;
                color: #38bdf8 !important;
            }
            .metric-label {
                font-size: 12px !important;
                font-weight: 700 !important;
                color: #94a3b8 !important;
                text-transform: uppercase !important;
                letter-spacing: 0.8px !important;
                margin-top: 4px !important;
            }

            /* Buttons */
            .stButton>button {
                background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
                color: #ffffff !important;
                font-weight: 700 !important;
                border: none !important;
                border-radius: 8px !important;
                padding: 12px 26px !important;
                font-size: 15px !important;
                box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3) !important;
            }
            .stButton>button * {
                color: #ffffff !important;
                font-weight: 700 !important;
            }
            .stButton>button:hover {
                background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%) !important;
                box-shadow: 0 6px 18px rgba(37, 99, 235, 0.45) !important;
            }

            /* Tabs Styling */
            button[data-baseweb="tab"] {
                background: #182232 !important;
                border-radius: 10px !important;
                border: 1px solid #283548 !important;
                margin-right: 12px !important;
                padding: 10px 22px !important;
            }
            button[data-baseweb="tab"] div p {
                color: #94a3b8 !important;
                font-weight: 700 !important;
                font-size: 15px !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] {
                background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
                border-color: #3b82f6 !important;
            }
            button[data-baseweb="tab"][aria-selected="true"] div p {
                color: #ffffff !important;
                font-weight: 800 !important;
            }

            /* Data Tables & Inputs */
            .stDataFrame {
                border-radius: 10px !important;
                background-color: #182232 !important;
                border: 1px solid #283548 !important;
            }
            [data-testid="stDataFrame"] div, [data-testid="stDataFrame"] span {
                color: #f1f5f9 !important;
            }
            
            /* Clean Select Box Styling */
            div[data-baseweb="select"] > div {
                color: #f1f5f9 !important;
                background-color: #1c2638 !important;
                border-radius: 8px !important;
                border: 1px solid #2d3b52 !important;
                box-shadow: none !important;
            }
            div[data-baseweb="select"] div {
                border: none !important;
                box-shadow: none !important;
            }
            div[data-baseweb="select"] input {
                border: none !important;
                background: transparent !important;
                box-shadow: none !important;
                outline: none !important;
            }
            div[data-baseweb="select"] span {
                color: #f1f5f9 !important;
                font-weight: 600 !important;
            }

            /* Inputs */
            div[data-baseweb="input"] > div {
                color: #f1f5f9 !important;
                background-color: #1c2638 !important;
                border-radius: 8px !important;
                border: 1px solid #2d3b52 !important;
            }

            label[data-testid="stWidgetLabel"] p {
                color: #cbd5e1 !important;
                font-weight: 700 !important;
                font-size: 14px !important;
            }
        </style>
    """, unsafe_allow_html=True)

    # HERO TITLE BANNER
    st.markdown("""
        <div class="hero-header">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h1 class="hero-title">📈 NSE Stock Algorithmic Quant Scanner Pro</h1>
                    <p class="hero-subtitle">Real-time 17-Rule Momentum & Technical Breakout Engine for NSE Listed Equities</p>
                </div>
                <div>
                    <span style="background: #1e3a8a; color: #60a5fa; padding: 6px 16px; border-radius: 20px; font-weight: 700; font-size: 13px; border: 1px solid #3b82f6;">INSTITUTIONAL QUANT EDITION</span>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Calculate Market Status (IST Asia/Kolkata)
    ist_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    is_market_open = (
        ist_now.weekday() < 5 and 
        datetime.time(9, 15) <= ist_now.time() <= datetime.time(15, 30)
    )
    market_badge = "🟢 MARKET OPEN (IST 09:15-15:30)" if is_market_open else "🔴 MARKET CLOSED"

    # Clean Sidebar Dashboard Panel
    st.sidebar.markdown("### 📊 Engine Status")
    engine_status = '🟢 Active' if HAS_LOCAL_DB_ENGINE else '⚪ Fallback Mode'
    st.sidebar.info(
        f"**Market Feed Status**:\n{market_badge}\n\n"
        f"**Local SQLite Engine**: {engine_status}\n\n"
        f"**Scan History DB**: Active"
    )
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 💡 Scanner Rules")
    st.sidebar.markdown("""
    • **Moving Average Trend**: SMA 20 > 40 > 60
    • **True Cloud Breakout**: Close > max(Span A, Span B)
    • **Trend Confirmation**: Parabolic SAR < Close
    • **Target Date**: Live or Historical Backtest
    """)

    # Main Tabs: Live Scanner & Scan History
    tab_live, tab_history = st.tabs(["🚀 Live Market Scanner", "📜 Scan History & Past Reports"])

    # --------------------------------------------------------------------------
    # TAB 1: LIVE MARKET SCANNER
    # --------------------------------------------------------------------------
    with tab_live:
        st.markdown("""
            <div class="info-box">
                <b>💡 Live Stock Scanner & Data Engine Overview</b><br>
                • <b>Hybrid Data Engine</b>: Fast priority querying from local <code>market_data.sqlite</code> (Official NSE Bhavcopy) with fallback to live yfinance feeds.<br>
                • <b>TradingView Indicator Accuracy</b>: Complete Ichimoku Cloud (Span A & Span B) + Wilder's Parabolic SAR + RSI 14.<br>
                • <b>Auto History Save</b>: All scan results are automatically recorded in SQLite history logs!
            </div>
        """, unsafe_allow_html=True)

        # Sleek Controls Panel
        with st.container():
            st.markdown("#### 🎯 Market Scan Configuration & Parameters")
            ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([3, 3, 2, 2])
            
            with ctrl_col1:
                preset = st.selectbox(
                    "🎯 Strategy Preset",
                    [
                        "Super Bullish Breakout (Recommended)",
                        "Trend Following Breakout",
                        "Ultra High Momentum (All 17 Rules)"
                    ],
                    key="main_preset"
                )
            
            with ctrl_col2:
                universe = st.selectbox(
                    "⚙️ Stock Group Universe",
                    ["Non-F&O Cash Equities (No Futures)", "Top 300 Liquid Stocks", "Nifty Benchmark 100", "All NSE Equities"],
                    key="main_universe"
                )
                
            with ctrl_col3:
                target_date_input = st.date_input(
                    "📅 Target Trade Date",
                    value=datetime.date.today(),
                    help="Select Today for live scan, or pick any past date for backtesting scans!",
                    key="main_target_date"
                )
                
            with ctrl_col4:
                workers = st.slider("⚡ Parallel Speed", 10, 40, 25, key="main_workers")

        st.markdown("<br>", unsafe_allow_html=True)

        # Single Direct Scan Launch Button
        if st.button("🚀 Launch Live Stock Scan Now", type="primary", use_container_width=True):
            st.session_state['run_scan_triggered'] = True
            st.session_state['selected_target_date'] = target_date_input
            st.session_state['selected_preset'] = preset
            st.session_state['selected_universe'] = universe
            st.session_state['selected_workers'] = workers
            st.rerun()

        # CHECK IF SCAN TRIGGERED
        if st.session_state.get('run_scan_triggered', False):
            st.session_state['run_scan_triggered'] = False
            
            run_target_date = st.session_state.get('selected_target_date', target_date_input)
            run_preset = st.session_state.get('selected_preset', preset)
            run_universe = st.session_state.get('selected_universe', universe)
            run_workers = st.session_state.get('selected_workers', workers)

            limit = None if "All NSE" in run_universe else (100 if "100" in run_universe else 300)

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
                target_date=run_target_date,
                universe=run_universe
            )
            
            elapsed = round(time.time() - start_time, 2)
            status_text.success(f"✅ Scan Complete for Target Date ({target_str}) in {elapsed} Seconds!")
            
            # Save to SQLite History Database
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
                    <div class="metric-label">Top Gainer Stock</div>
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
                    <div class="metric-value">SAVED ✅</div>
                    <div class="metric-label">History DB</div>
                </div>
            """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            if not df_res.empty:
                top_pick = df_res.iloc[0]
                
                # 🏆 TOP QUANT PICK HIGHLIGHT BANNER
                st.markdown(f"""
                    <div style="background: linear-gradient(135deg, #064e3b 0%, #022c22 100%); border: 2px solid #10b981; border-radius: 12px; padding: 18px 24px; margin: 15px 0 24px 0; box-shadow: 0 8px 20px rgba(16, 185, 129, 0.25);">
                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                            <div>
                                <span style="background: #10b981; color: #022c22; padding: 4px 12px; border-radius: 12px; font-weight: 800; font-size: 12px; letter-spacing: 0.5px;">🏆 #1 TOP QUANT BREAKOUT PICK</span>
                                <h2 style="margin: 8px 0 2px 0; color: #34d399 !important; font-size: 26px; font-weight: 900;">{top_pick['Symbol']} <span style="font-size: 18px; color: #a7f3d0;">(₹{top_pick['Close (₹)']})</span></h2>
                                <p style="margin: 0; color: #6ee7b7 !important; font-weight: 600; font-size: 14px;">Day Gain: <b>{top_pick['Change (%)']}</b> &nbsp;|&nbsp; Volume: <b>{top_pick['Volume']:,} shares</b> &nbsp;|&nbsp; Stop Loss: <b>₹{top_pick.get('Stop Loss (₹)', 'N/A')}</b> &nbsp;|&nbsp; Target 1: <b>₹{top_pick.get('Target 1 (₹)', 'N/A')}</b></p>
                            </div>
                            <div style="text-align: right;">
                                <span style="font-size: 36px;">🌟</span>
                            </div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                st.subheader(f"📊 Matching Stocks List for Date: {last_target_date} ({len(df_res)} Stocks Found - Row #1 Highlighted in Green)")
                
                # Function to highlight top stock row
                def highlight_top_stock_row(df):
                    style_df = pd.DataFrame('', index=df.index, columns=df.columns)
                    if not df.empty:
                        style_df.iloc[0] = 'background-color: rgba(16, 185, 129, 0.22); color: #34d399; font-weight: bold; border-left: 5px solid #10b981;'
                    return style_df

                st.dataframe(df_res.style.apply(highlight_top_stock_row, axis=None), hide_index=True, use_container_width=True)

                st.download_button(
                    label="📥 Download Results CSV File (scanner_results.csv)",
                    data=df_res.to_csv(index=False).encode('utf-8'),
                    file_name=f"scanner_results_{last_target_date}.csv",
                    mime="text/csv"
                )

                # TradingView 3-Subplot Plotly Chart Viewer
                st.markdown("---")
                st.subheader("📈 Institutional TradingView Interactive Stock Chart & Indicators")
                selected_symbol = st.selectbox("Select Stock Symbol to View Chart:", df_res['Symbol'].tolist())
                
                if selected_symbol in stock_dfs:
                    chart_df = stock_dfs[selected_symbol].tail(100)
                    
                    fig = make_subplots(
                        rows=3, cols=1,
                        shared_xaxes=True,
                        vertical_spacing=0.04,
                        row_heights=[0.55, 0.22, 0.23],
                        subplot_titles=(
                            f"{selected_symbol} - Daily Price, SMAs, PSAR & Ichimoku Kumo Cloud (as of {last_target_date})",
                            "Volume & 20-SMA Volume Line",
                            "RSI (14) Momentum Oscillator"
                        )
                    )

                    # 1. Candlestick
                    fig.add_trace(go.Candlestick(
                        x=chart_df.index,
                        open=chart_df['Open'],
                        high=chart_df['High'],
                        low=chart_df['Low'],
                        close=chart_df['Close'],
                        name='Candles',
                        increasing_line_color='#00e676',
                        decreasing_line_color='#ff3d00'
                    ), row=1, col=1)

                    # SMAs
                    if 'SMA20' in chart_df.columns:
                        fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA20'], mode='lines', name='SMA 20', line=dict(color='#38bdf8', width=2)), row=1, col=1)
                    if 'SMA40' in chart_df.columns:
                        fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA40'], mode='lines', name='SMA 40', line=dict(color='#fbbf24', width=2)), row=1, col=1)
                    if 'SMA60' in chart_df.columns:
                        fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['SMA60'], mode='lines', name='SMA 60', line=dict(color='#22d3ee', width=2)), row=1, col=1)

                    # PSAR
                    if 'PSAR' in chart_df.columns:
                        fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df['PSAR'], mode='markers', name='Parabolic SAR', marker=dict(size=5, color='#a855f7')), row=1, col=1)

                    # Ichimoku Cloud Lines (Senkou Span A & Senkou Span B)
                    if 'SpanA' in chart_df.columns and 'SpanB' in chart_df.columns:
                        fig.add_trace(go.Scatter(
                            x=chart_df.index, y=chart_df['SpanA'],
                            mode='lines', name='Ichimoku Span A (Green)',
                            line=dict(color='#4ade80', width=2)
                        ), row=1, col=1)
                        fig.add_trace(go.Scatter(
                            x=chart_df.index, y=chart_df['SpanB'],
                            mode='lines', name='Ichimoku Span B (Red)',
                            line=dict(color='#f87171', width=2)
                        ), row=1, col=1)

                    # 2. Volume Subplot
                    vol_colors = ['#00e676' if c >= o else '#ff3d00' for c, o in zip(chart_df['Close'], chart_df['Open'])]
                    fig.add_trace(go.Bar(
                        x=chart_df.index, y=chart_df['Volume'],
                        name='Volume',
                        marker_color=vol_colors,
                        opacity=0.75
                    ), row=2, col=1)
                    
                    vol_ma20 = chart_df['Volume'].rolling(20).mean()
                    fig.add_trace(go.Scatter(
                        x=chart_df.index, y=vol_ma20,
                        mode='lines', name='Vol MA 20',
                        line=dict(color='#facc15', width=1.5)
                    ), row=2, col=1)

                    # 3. RSI Subplot
                    if 'RSI' in chart_df.columns:
                        fig.add_trace(go.Scatter(
                            x=chart_df.index, y=chart_df['RSI'],
                            mode='lines', name='RSI (14)',
                            line=dict(color='#38bdf8', width=2)
                        ), row=3, col=1)
                        fig.add_hline(y=70, line_dash="dash", line_color="#f87171", row=3, col=1)
                        fig.add_hline(y=30, line_dash="dash", line_color="#4ade80", row=3, col=1)

                    fig.update_layout(
                        template="plotly_dark",
                        xaxis_rangeslider_visible=False,
                        height=750,
                        margin=dict(l=20, r=20, t=40, b=20),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning(f"⚠️ No stocks matched for Trade Date {last_target_date} in this strict preset mode. Try switching Strategy to 'Super Bullish Breakout (Recommended)'!")

    # --------------------------------------------------------------------------
    # TAB 2: SCAN HISTORY & PAST REPORTS
    # --------------------------------------------------------------------------
    with tab_history:
        st.markdown("### 📜 Scan History Database & Analytics Portal")
        st.caption("Browse past market scan logs, filter by date ranges, or search for any stock across historical scans.")

        with st.container():
            st.markdown("#### 🔍 Filter Controls & Symbol Search")
            
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

            symbol_search_query = st.text_input("🔎 Search Stock Symbol Across History (e.g. RELIANCE, DIXON, TCS):", placeholder="Type stock symbol...").strip()

        st.markdown("<br>", unsafe_allow_html=True)

        if symbol_search_query:
            st.markdown(f"#### 🔎 History Results for Symbol: `{symbol_search_query.upper()}`")
            df_symbol_matches = search_symbol_in_history(symbol_search_query)

            if not df_symbol_matches.empty:
                st.success(f"Found `{len(df_symbol_matches)}` past scan session(s) where `{symbol_search_query.upper()}` matched!")
                st.dataframe(df_symbol_matches, hide_index=True, use_container_width=True)

                st.download_button(
                    label=f"📥 Download {symbol_search_query.upper()} Search CSV",
                    data=df_symbol_matches.to_csv(index=False).encode('utf-8'),
                    file_name=f"{symbol_search_query.upper()}_history_scans.csv",
                    mime="text/csv"
                )
            else:
                st.warning(f"No past scan records found containing stock symbol `{symbol_search_query.upper()}`.")
            st.markdown("---")

        df_summary = load_history_summary(start_date=start_d, end_date=end_d, strategy_filter=strat_filter)

        if not df_summary.empty:
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

            latest_date_str = str(df_summary.iloc[0]['Scan Time']).split()[0] if not df_summary.empty else 'N/A'
            h4.markdown(f"""
                <div class="metric-card">
                    <div class="metric-value">{latest_date_str}</div>
                    <div class="metric-label">Latest Scan Date</div>
                </div>
            """, unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            st.markdown("#### 📋 Past Scan Sessions Table")
            st.dataframe(df_summary, hide_index=True, use_container_width=True)

            st.markdown("---")

            st.markdown("#### 🔍 Inspect Stock Results for Selected Past Scan")
            scan_options = {
                f"Scan #{row['Scan ID']} | {row['Scan Time']} | Preset: {row['Strategy Preset']} ({row['Matches Found']} matches)": row['Scan ID'] 
                for _, row in df_summary.iterrows()
            }
            selected_scan_label = st.selectbox("Select Past Scan Session to View:", list(scan_options.keys()))
            selected_scan_id = scan_options[selected_scan_label]

            detail = load_history_detail(selected_scan_id)
            if detail and detail['df'] is not None and not detail['df'].empty:
                st.info(f"📅 **Scan Timestamp**: `{detail['timestamp']}` &nbsp;|&nbsp; 🎯 **Strategy**: `{detail['preset_mode']}` &nbsp;|&nbsp; ⚙️ **Universe**: `{detail['universe']}`")
                st.dataframe(detail['df'], hide_index=True, use_container_width=True)

                st.download_button(
                    label=f"📥 Download Scan #{selected_scan_id} CSV Results",
                    data=detail['df'].to_csv(index=False).encode('utf-8'),
                    file_name=f"scan_history_{selected_scan_id}.csv",
                    mime="text/csv"
                )
            else:
                st.info("ℹ️ No matching stocks were recorded in this specific scan session.")

            st.markdown("<br>", unsafe_allow_html=True)
            with st.expander("⚠️ Danger Zone: Clear History Database"):
                st.write("Clicking below will delete all stored scan history logs from SQLite database.")
                if st.button("🗑️ Clear All History Database Logs"):
                    clear_history_db()
                    st.rerun()

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

