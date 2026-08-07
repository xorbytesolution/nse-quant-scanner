#!/usr/bin/env python3
"""
Priority-based Indian cash-market data loader.

Priority order:
1. Official NSE/BSE cash-market bhavcopy files.
2. Twelve Data, only when TWELVEDATA_API_KEY is configured.
3. yfinance as final fallback.

The script stores OHLCV data in SQLite, prevents duplicates, logs the source
used for each row, and replaces temporary fallback rows when official exchange
data becomes available later.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import sqlite3
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OFFICIAL_PRIORITY = 1
TWELVEDATA_PRIORITY = 2
YFINANCE_PRIORITY = 3

NSE_UDIFF_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
NSE_OLD_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
    "{yyyy}/{mmm}/cm{dd}{mmm}{yyyy}bhav.csv.zip"
)
BSE_API_URL = "https://api.bseindia.com/BseIndiaAPI/api/GetBhavcopydata/w"
BSE_DOWNLOAD_BASE = "https://www.bseindia.com/download/BhavCopy/Equity/"
BSE_UDIFF_FILE = "BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"
BSE_OLD_FILE = "EQ{ddmmyy}_CSV.ZIP"


class DataSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    exchange: str = "NSE"
    isin: Optional[str] = None
    yfinance_ticker: Optional[str] = None


@dataclass
class MarketRecord:
    trade_date: str
    exchange: str
    symbol: str
    isin: Optional[str]
    instrument_type: Optional[str]
    security_series: Optional[str]
    name: Optional[str]
    open: float
    high: float
    low: float
    close: float
    last: Optional[float]
    prev_close: Optional[float]
    volume: int
    turnover: Optional[float]
    trades: Optional[int]
    source: str
    source_priority: int
    is_temporary: bool
    raw_url: Optional[str]

    @property
    def symbol_key(self) -> str:
        if self.isin:
            return f"ISIN:{self.isin.upper()}"
        return f"SYM:{normalize_symbol(self.symbol)}"


def normalize_symbol(symbol: str) -> str:
    symbol = (symbol or "").strip().upper()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[1]
    for suffix in (".NS", ".BO"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    return symbol.replace(" ", "")


def to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "" or text.upper() in {"NA", "NAN", "NULL", "-"}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def to_int(value: object) -> Optional[int]:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def parse_date(value: object, default: dt.date) -> str:
    text = str(value or "").strip()
    if not text:
        return default.isoformat()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return default.isoformat()


def parse_cli_date(value: Optional[str]) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.datetime.now(IST).date()


def get_ohlcv_dataframe(
    symbol: str,
    start_date: Optional[str | dt.date] = None,
    end_date: Optional[str | dt.date] = None,
    db_path: str = "market_data.sqlite"
) -> pd.DataFrame:
    """
    Fetches OHLCV price series for a symbol directly from SQLite database.
    Returns pandas DataFrame with DatetimeIndex and columns: Open, High, Low, Close, Volume.
    """
    clean_sym = normalize_symbol(symbol)
    if not os.path.exists(db_path):
        return pd.DataFrame()
    
    try:
        conn = sqlite3.connect(db_path)
        query = """
            SELECT trade_date, open AS Open, high AS High, low AS Low, close AS Close, volume AS Volume
            FROM market_ohlcv
            WHERE (symbol = ? OR symbol_key = ? OR symbol_key = ?)
        """
        params = [clean_sym, f"SYM:{clean_sym}", f"ISIN:{clean_sym}"]
        
        if start_date:
            s_str = start_date.isoformat() if isinstance(start_date, dt.date) else str(start_date)
            query += " AND trade_date >= ?"
            params.append(s_str)
            
        if end_date:
            e_str = end_date.isoformat() if isinstance(end_date, dt.date) else str(end_date)
            query += " AND trade_date <= ?"
            params.append(e_str)
            
        query += " ORDER BY trade_date ASC"
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        if df.empty:
            return pd.DataFrame()
            
        df['Date'] = pd.to_datetime(df['trade_date'])
        df.set_index('Date', inplace=True)
        df.drop(columns=['trade_date'], inplace=True, errors='ignore')
        
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)
        return df
    except Exception:
        return pd.DataFrame()



def request_headers(site: str) -> Dict[str, str]:
    if site == "nse":
        return {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.nseindia.com/all-reports",
        }
    if site == "bse_api":
        return {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.bseindia.com/markets/MarketInfo/BhavCopy",
            "Origin": "https://www.bseindia.com",
        }
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/zip,*/*",
        "Referer": "https://www.bseindia.com/markets/MarketInfo/BhavCopy",
    }


def http_get(url: str, headers: Dict[str, str], timeout: int = 30, **kwargs) -> requests.Response:
    response = requests.get(url, headers=headers, timeout=timeout, **kwargs)
    if response.status_code != 200:
        raise DataSourceError(f"HTTP {response.status_code} for {url}")
    return response


def csv_rows_from_bytes(content: bytes, filename: str = "") -> List[Dict[str, str]]:
    if content.startswith(b"PK"):
        rows: List[Dict[str, str]] = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise DataSourceError("ZIP did not contain a CSV file")
            for name in names:
                with archive.open(name) as handle:
                    text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                    rows.extend(list(csv.DictReader(text)))
        return rows

    text = content.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def validate_record(record: MarketRecord) -> Tuple[bool, str]:
    prices = [record.open, record.high, record.low, record.close]
    if any(price is None or not math.isfinite(price) for price in prices):
        return False, "missing or non-finite OHLC"
    if any(price < 0 for price in prices):
        return False, "negative OHLC"
    if record.high < max(record.open, record.low, record.close):
        return False, "high is below an OHLC value"
    if record.low > min(record.open, record.high, record.close):
        return False, "low is above an OHLC value"
    if record.volume is None or record.volume < 0:
        return False, "negative or missing volume"
    if not record.symbol:
        return False, "missing symbol"
    return True, "ok"


def make_udiff_record(
    row: Dict[str, str],
    trade_date: dt.date,
    source: str,
    priority: int,
    temporary: bool,
    raw_url: str,
    forced_exchange: Optional[str] = None,
) -> Optional[MarketRecord]:
    open_price = to_float(row.get("OpnPric"))
    high = to_float(row.get("HghPric"))
    low = to_float(row.get("LwPric"))
    close = to_float(row.get("ClsPric"))
    volume = to_int(row.get("TtlTradgVol"))
    if None in (open_price, high, low, close, volume):
        return None
    exchange = forced_exchange or (row.get("Src") or "").strip().upper()
    symbol = normalize_symbol(row.get("TckrSymb", ""))
    return MarketRecord(
        trade_date=parse_date(row.get("TradDt"), trade_date),
        exchange=exchange,
        symbol=symbol,
        isin=(row.get("ISIN") or "").strip().upper() or None,
        instrument_type=(row.get("FinInstrmTp") or "").strip() or None,
        security_series=(row.get("SctySrs") or "").strip() or None,
        name=(row.get("FinInstrmNm") or "").strip() or None,
        open=open_price,
        high=high,
        low=low,
        close=close,
        last=to_float(row.get("LastPric")),
        prev_close=to_float(row.get("PrvsClsgPric")),
        volume=volume,
        turnover=to_float(row.get("TtlTrfVal")),
        trades=to_int(row.get("TtlNbOfTxsExctd")),
        source=source,
        source_priority=priority,
        is_temporary=temporary,
        raw_url=raw_url,
    )


def make_old_nse_record(row: Dict[str, str], trade_date: dt.date, raw_url: str) -> Optional[MarketRecord]:
    open_price = to_float(row.get("OPEN"))
    high = to_float(row.get("HIGH"))
    low = to_float(row.get("LOW"))
    close = to_float(row.get("CLOSE"))
    volume = to_int(row.get("TOTTRDQTY"))
    if None in (open_price, high, low, close, volume):
        return None
    return MarketRecord(
        trade_date=parse_date(row.get("TIMESTAMP"), trade_date),
        exchange="NSE",
        symbol=normalize_symbol(row.get("SYMBOL", "")),
        isin=(row.get("ISIN") or "").strip().upper() or None,
        instrument_type="STK",
        security_series=(row.get("SERIES") or "").strip() or None,
        name=None,
        open=open_price,
        high=high,
        low=low,
        close=close,
        last=to_float(row.get("LAST")),
        prev_close=to_float(row.get("PREVCLOSE")),
        volume=volume,
        turnover=to_float(row.get("TOTTRDVAL")),
        trades=to_int(row.get("TOTALTRADES")),
        source="official_nse_bhavcopy_old",
        source_priority=OFFICIAL_PRIORITY,
        is_temporary=False,
        raw_url=raw_url,
    )


def make_old_bse_record(row: Dict[str, str], trade_date: dt.date, raw_url: str) -> Optional[MarketRecord]:
    open_price = to_float(row.get("OPEN"))
    high = to_float(row.get("HIGH"))
    low = to_float(row.get("LOW"))
    close = to_float(row.get("CLOSE"))
    volume = to_int(row.get("NO_OF_SHRS") or row.get("NO_OF_SHARES"))
    if None in (open_price, high, low, close, volume):
        return None
    symbol = row.get("SC_CODE") or row.get("SC_NAME") or ""
    return MarketRecord(
        trade_date=trade_date.isoformat(),
        exchange="BSE",
        symbol=normalize_symbol(symbol),
        isin=None,
        instrument_type="STK",
        security_series=(row.get("SC_GROUP") or "").strip() or None,
        name=(row.get("SC_NAME") or "").strip() or None,
        open=open_price,
        high=high,
        low=low,
        close=close,
        last=None,
        prev_close=to_float(row.get("PREVCLOSE")),
        volume=volume,
        turnover=to_float(row.get("NET_TURNOV")),
        trades=None,
        source="official_bse_bhavcopy_old",
        source_priority=OFFICIAL_PRIORITY,
        is_temporary=False,
        raw_url=raw_url,
    )


def fetch_nse_official(trade_date: dt.date) -> Tuple[List[MarketRecord], str]:
    yyyymmdd = trade_date.strftime("%Y%m%d")
    url = NSE_UDIFF_URL.format(yyyymmdd=yyyymmdd)
    try:
        response = http_get(url, request_headers("nse"))
        rows = csv_rows_from_bytes(response.content, url)
        records = [
            record
            for row in rows
            if (record := make_udiff_record(row, trade_date, "official_nse_bhavcopy", OFFICIAL_PRIORITY, False, url, "NSE"))
        ]
        if records:
            return records, url
    except Exception as exc:
        modern_error = str(exc)
    else:
        modern_error = "no rows"

    mmm = trade_date.strftime("%b").upper()
    old_url = NSE_OLD_URL.format(
        yyyy=trade_date.strftime("%Y"),
        mmm=mmm,
        dd=trade_date.strftime("%d"),
    )
    try:
        response = http_get(old_url, request_headers("nse"))
        rows = csv_rows_from_bytes(response.content, old_url)
        records = [record for row in rows if (record := make_old_nse_record(row, trade_date, old_url))]
        if records:
            return records, old_url
    except Exception as exc:
        raise DataSourceError(f"NSE official unavailable: {modern_error}; old format: {exc}") from exc

    raise DataSourceError(f"NSE official unavailable: {modern_error}; old format had no rows")


def bse_filename_from_api(trade_date: dt.date) -> Optional[str]:
    params = {"flag": "1", "segment": "equity", "date": trade_date.strftime("%Y%m%d")}
    response = http_get(BSE_API_URL, request_headers("bse_api"), params=params)
    payload = response.json()
    for item in payload.get("dsedcudiff") or []:
        if str(item.get("Filetype", "")).upper() == "EQ" and item.get("filename"):
            return str(item["filename"])
    return None


def fetch_bse_official(trade_date: dt.date) -> Tuple[List[MarketRecord], str]:
    try:
        filename = bse_filename_from_api(trade_date) or BSE_UDIFF_FILE.format(yyyymmdd=trade_date.strftime("%Y%m%d"))
    except Exception:
        filename = BSE_UDIFF_FILE.format(yyyymmdd=trade_date.strftime("%Y%m%d"))

    url = BSE_DOWNLOAD_BASE + filename
    try:
        response = http_get(url, request_headers("bse_file"))
        rows = csv_rows_from_bytes(response.content, filename)
        records = [
            record
            for row in rows
            if (record := make_udiff_record(row, trade_date, "official_bse_bhavcopy", OFFICIAL_PRIORITY, False, url, "BSE"))
        ]
        if records:
            return records, url
    except Exception as exc:
        modern_error = str(exc)
    else:
        modern_error = "no rows"

    old_filename = BSE_OLD_FILE.format(ddmmyy=trade_date.strftime("%d%m%y"))
    old_url = BSE_DOWNLOAD_BASE + old_filename
    try:
        response = http_get(old_url, request_headers("bse_file"))
        rows = csv_rows_from_bytes(response.content, old_filename)
        records = [record for row in rows if (record := make_old_bse_record(row, trade_date, old_url))]
        if records:
            return records, old_url
    except Exception as exc:
        raise DataSourceError(f"BSE official unavailable: {modern_error}; old format: {exc}") from exc

    raise DataSourceError(f"BSE official unavailable: {modern_error}; old format had no rows")


def parse_symbol_specs(symbols: Sequence[str], symbols_file: Optional[Path]) -> List[SymbolSpec]:
    specs: List[SymbolSpec] = []

    def add_spec(raw_symbol: str, exchange: Optional[str] = None, isin: Optional[str] = None, yf: Optional[str] = None) -> None:
        raw_symbol = (raw_symbol or "").strip()
        if not raw_symbol or raw_symbol.startswith("#"):
            return
        inferred_exchange = (exchange or "NSE").strip().upper()
        symbol = raw_symbol
        if ":" in raw_symbol and not exchange:
            prefix, symbol = raw_symbol.split(":", 1)
            inferred_exchange = prefix.strip().upper()
        if raw_symbol.upper().endswith(".BO") and not exchange:
            inferred_exchange = "BSE"
        if raw_symbol.upper().endswith(".NS") and not exchange:
            inferred_exchange = "NSE"
        specs.append(
            SymbolSpec(
                symbol=normalize_symbol(symbol),
                exchange=inferred_exchange,
                isin=(isin or "").strip().upper() or None,
                yfinance_ticker=(yf or "").strip() or None,
            )
        )

    for chunk in symbols:
        for raw in chunk.split(","):
            add_spec(raw)

    if symbols_file and symbols_file.exists():
        with symbols_file.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            if "," in sample and "symbol" in sample.lower():
                reader = csv.DictReader(handle)
                for row in reader:
                    add_spec(
                        row.get("symbol", ""),
                        row.get("exchange"),
                        row.get("isin"),
                        row.get("yfinance") or row.get("yfinance_ticker"),
                    )
            else:
                for line in handle:
                    add_spec(line.strip())

    unique: Dict[Tuple[str, str], SymbolSpec] = {}
    for spec in specs:
        unique[(spec.exchange, spec.symbol)] = spec
    return list(unique.values())


def make_fallback_record(
    spec: SymbolSpec,
    trade_date: dt.date,
    values: Dict[str, object],
    source: str,
    priority: int,
    raw_url: str,
) -> Optional[MarketRecord]:
    open_price = to_float(values.get("open"))
    high = to_float(values.get("high"))
    low = to_float(values.get("low"))
    close = to_float(values.get("close"))
    volume = to_int(values.get("volume") or 0)
    if None in (open_price, high, low, close, volume):
        return None
    return MarketRecord(
        trade_date=trade_date.isoformat(),
        exchange=spec.exchange,
        symbol=normalize_symbol(spec.symbol),
        isin=spec.isin,
        instrument_type="STK",
        security_series=None,
        name=None,
        open=open_price,
        high=high,
        low=low,
        close=close,
        last=close,
        prev_close=None,
        volume=volume,
        turnover=None,
        trades=None,
        source=source,
        source_priority=priority,
        is_temporary=True,
        raw_url=raw_url,
    )


def fetch_twelvedata_records(trade_date: dt.date, specs: Sequence[SymbolSpec], api_key: str) -> List[MarketRecord]:
    records: List[MarketRecord] = []
    url = "https://api.twelvedata.com/time_series"
    for spec in specs:
        params = {
            "symbol": spec.symbol,
            "exchange": spec.exchange,
            "interval": "1day",
            "outputsize": 5,
            "start_date": trade_date.isoformat(),
            "end_date": trade_date.isoformat(),
            "timezone": "Asia/Kolkata",
            "apikey": api_key,
        }
        try:
            response = http_get(url, {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, params=params)
            payload = response.json()
            if payload.get("status") == "error":
                continue
            values = payload.get("values") or []
            matching = None
            for item in values:
                if str(item.get("datetime", ""))[:10] == trade_date.isoformat():
                    matching = item
                    break
            if matching is None and values:
                matching = values[0]
            if matching:
                record = make_fallback_record(
                    spec,
                    trade_date,
                    matching,
                    "twelvedata_api",
                    TWELVEDATA_PRIORITY,
                    response.url.replace(api_key, "***"),
                )
                if record:
                    records.append(record)
        except Exception:
            continue
    return records


def yfinance_ticker(spec: SymbolSpec) -> str:
    if spec.yfinance_ticker:
        return spec.yfinance_ticker
    if spec.exchange == "BSE":
        return f"{spec.symbol}.BO"
    return f"{spec.symbol}.NS"


def spec_key(spec: SymbolSpec) -> Tuple[str, str]:
    key = f"ISIN:{spec.isin.upper()}" if spec.isin else f"SYM:{normalize_symbol(spec.symbol)}"
    return spec.exchange, key


def fetch_yfinance_records(trade_date: dt.date, specs: Sequence[SymbolSpec]) -> List[MarketRecord]:
    try:
        import yfinance as yf  # pyright: ignore[reportMissingImports]
    except ImportError:
        return []

    records: List[MarketRecord] = []
    start = trade_date.isoformat()
    end = (trade_date + dt.timedelta(days=1)).isoformat()
    for spec in specs:
        ticker = yfinance_ticker(spec)
        try:
            frame = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False, threads=False)
            if frame is None or frame.empty:
                continue
            row = frame.iloc[0]
            values = {
                "open": row_value(row, "Open"),
                "high": row_value(row, "High"),
                "low": row_value(row, "Low"),
                "close": row_value(row, "Close"),
                "volume": row_value(row, "Volume") or 0,
            }
            record = make_fallback_record(
                spec,
                trade_date,
                values,
                "yfinance",
                YFINANCE_PRIORITY,
                ticker,
            )
            if record:
                records.append(record)
        except Exception:
            continue
    return records


def row_value(row: object, name: str) -> object:
    try:
        value = row[name]
        if hasattr(value, "iloc"):
            return value.iloc[0]
        return value
    except Exception:
        pass
    try:
        matches = [key for key in row.index if isinstance(key, tuple) and key[0] == name]
        if matches:
            return row[matches[0]]
    except Exception:
        pass
    return None


class MarketDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS market_ohlcv (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                symbol_key TEXT NOT NULL,
                isin TEXT,
                instrument_type TEXT,
                security_series TEXT,
                name TEXT,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                last REAL,
                prev_close REAL,
                volume INTEGER NOT NULL,
                turnover REAL,
                trades INTEGER,
                source TEXT NOT NULL,
                source_priority INTEGER NOT NULL,
                is_temporary INTEGER NOT NULL,
                raw_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (trade_date, exchange, symbol_key)
            );

            CREATE TABLE IF NOT EXISTS source_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                trade_date TEXT,
                exchange TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                rows INTEGER DEFAULT 0,
                message TEXT,
                url TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS technical_indicators (
                trade_date TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol_key TEXT NOT NULL,
                close REAL NOT NULL,
                sma20 REAL,
                sma50 REAL,
                updated_at TEXT NOT NULL,
                UNIQUE (trade_date, exchange, symbol_key)
            );

            CREATE TABLE IF NOT EXISTS scanner_results (
                trade_date TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                close REAL NOT NULL,
                sma20 REAL,
                sma50 REAL,
                above_sma20 INTEGER,
                above_sma50 INTEGER,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (trade_date, exchange, symbol_key)
            );

            CREATE TABLE IF NOT EXISTS backtest_results (
                exchange TEXT NOT NULL,
                symbol_key TEXT NOT NULL,
                strategy TEXT NOT NULL,
                through_date TEXT NOT NULL,
                observations INTEGER NOT NULL,
                total_return REAL,
                win_rate REAL,
                updated_at TEXT NOT NULL,
                UNIQUE (exchange, symbol_key, strategy)
            );
            """
        )
        self.conn.commit()

    def audit(
        self,
        run_id: str,
        source: str,
        status: str,
        trade_date: Optional[str] = None,
        exchange: Optional[str] = None,
        rows: int = 0,
        message: Optional[str] = None,
        url: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO source_audit
                (run_id, trade_date, exchange, source, status, rows, message, url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, trade_date, exchange, source, status, rows, message, url, now_utc()),
        )
        self.conn.commit()

    def upsert_records(self, records: Iterable[MarketRecord]) -> Dict[str, int]:
        stats = {"inserted": 0, "replaced": 0, "kept": 0, "invalid": 0}
        touched: set[Tuple[str, str]] = set()
        for record in records:
            ok, reason = validate_record(record)
            if not ok:
                stats["invalid"] += 1
                continue
            existing = self.conn.execute(
                """
                SELECT id, source_priority
                FROM market_ohlcv
                WHERE trade_date = ? AND exchange = ? AND symbol_key = ?
                """,
                (record.trade_date, record.exchange, record.symbol_key),
            ).fetchone()
            if existing and int(existing["source_priority"]) < record.source_priority:
                stats["kept"] += 1
                continue
            if existing:
                self.conn.execute(
                    """
                    UPDATE market_ohlcv
                    SET symbol = ?, isin = ?, instrument_type = ?, security_series = ?, name = ?,
                        open = ?, high = ?, low = ?, close = ?, last = ?, prev_close = ?,
                        volume = ?, turnover = ?, trades = ?, source = ?, source_priority = ?,
                        is_temporary = ?, raw_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    record_update_values(record) + (now_utc(), existing["id"]),
                )
                stats["replaced"] += 1
            else:
                self.conn.execute(
                    """
                    INSERT INTO market_ohlcv
                        (trade_date, exchange, symbol, symbol_key, isin, instrument_type,
                         security_series, name, open, high, low, close, last, prev_close,
                         volume, turnover, trades, source, source_priority, is_temporary,
                         raw_url, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record_values(record) + (now_utc(), now_utc()),
                )
                stats["inserted"] += 1
            touched.add((record.exchange, record.symbol_key))
        self.conn.commit()
        self.refresh_downstream(touched)
        return stats

    def temporary_dates(self, since_date: dt.date) -> List[dt.date]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM market_ohlcv
            WHERE is_temporary = 1 AND trade_date >= ?
            ORDER BY trade_date
            """,
            (since_date.isoformat(),),
        ).fetchall()
        return [dt.date.fromisoformat(row["trade_date"]) for row in rows]

    def refresh_downstream(self, touched: Iterable[Tuple[str, str]]) -> None:
        touched = set(touched)
        if not touched:
            return
        for exchange, symbol_key in touched:
            rows = self.conn.execute(
                """
                SELECT trade_date, exchange, symbol_key, symbol, close, source
                FROM market_ohlcv
                WHERE exchange = ? AND symbol_key = ?
                ORDER BY trade_date
                """,
                (exchange, symbol_key),
            ).fetchall()
            closes: List[float] = []
            returns: List[float] = []
            previous_close: Optional[float] = None
            for row in rows:
                close = float(row["close"])
                closes.append(close)
                sma20 = average(closes[-20:]) if len(closes) >= 20 else None
                sma50 = average(closes[-50:]) if len(closes) >= 50 else None
                self.conn.execute(
                    """
                    INSERT INTO technical_indicators
                        (trade_date, exchange, symbol_key, close, sma20, sma50, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, exchange, symbol_key) DO UPDATE SET
                        close = excluded.close,
                        sma20 = excluded.sma20,
                        sma50 = excluded.sma50,
                        updated_at = excluded.updated_at
                    """,
                    (row["trade_date"], exchange, symbol_key, close, sma20, sma50, now_utc()),
                )
                self.conn.execute(
                    """
                    INSERT INTO scanner_results
                        (trade_date, exchange, symbol_key, symbol, close, sma20, sma50,
                         above_sma20, above_sma50, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_date, exchange, symbol_key) DO UPDATE SET
                        symbol = excluded.symbol,
                        close = excluded.close,
                        sma20 = excluded.sma20,
                        sma50 = excluded.sma50,
                        above_sma20 = excluded.above_sma20,
                        above_sma50 = excluded.above_sma50,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["trade_date"],
                        exchange,
                        symbol_key,
                        row["symbol"],
                        close,
                        sma20,
                        sma50,
                        int(sma20 is not None and close > sma20),
                        int(sma50 is not None and close > sma50),
                        row["source"],
                        now_utc(),
                    ),
                )
                if previous_close:
                    returns.append((close - previous_close) / previous_close)
                previous_close = close

            observations = len(returns)
            total_return = None
            win_rate = None
            if returns:
                compounded = 1.0
                wins = 0
                for daily_return in returns:
                    compounded *= 1.0 + daily_return
                    if daily_return > 0:
                        wins += 1
                total_return = compounded - 1.0
                win_rate = wins / len(returns)
            through_date = rows[-1]["trade_date"] if rows else ""
            self.conn.execute(
                """
                INSERT INTO backtest_results
                    (exchange, symbol_key, strategy, through_date, observations,
                     total_return, win_rate, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, symbol_key, strategy) DO UPDATE SET
                    through_date = excluded.through_date,
                    observations = excluded.observations,
                    total_return = excluded.total_return,
                    win_rate = excluded.win_rate,
                    updated_at = excluded.updated_at
                """,
                (
                    exchange,
                    symbol_key,
                    "buy_and_hold_close_to_close",
                    through_date,
                    observations,
                    total_return,
                    win_rate,
                    now_utc(),
                ),
            )
        self.conn.commit()


def record_values(record: MarketRecord) -> Tuple[object, ...]:
    return (
        record.trade_date,
        record.exchange,
        record.symbol,
        record.symbol_key,
        record.isin,
        record.instrument_type,
        record.security_series,
        record.name,
        record.open,
        record.high,
        record.low,
        record.close,
        record.last,
        record.prev_close,
        record.volume,
        record.turnover,
        record.trades,
        record.source,
        record.source_priority,
        int(record.is_temporary),
        record.raw_url,
    )


def record_update_values(record: MarketRecord) -> Tuple[object, ...]:
    return (
        record.symbol,
        record.isin,
        record.instrument_type,
        record.security_series,
        record.name,
        record.open,
        record.high,
        record.low,
        record.close,
        record.last,
        record.prev_close,
        record.volume,
        record.turnover,
        record.trades,
        record.source,
        record.source_priority,
        int(record.is_temporary),
        record.raw_url,
    )


def average(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run_for_date(
    db: MarketDatabase,
    run_id: str,
    trade_date: dt.date,
    specs: Sequence[SymbolSpec],
    skip_nse: bool,
    skip_bse: bool,
) -> Dict[str, int]:
    summary = {
        "official_rows": 0,
        "fallback_rows": 0,
        "inserted": 0,
        "replaced": 0,
        "kept": 0,
        "invalid": 0,
    }
    fallback_exchanges: List[str] = []

    if skip_nse:
        fallback_exchanges.append("NSE")
        db.audit(
            run_id,
            "official_nse_bhavcopy",
            "skipped",
            trade_date.isoformat(),
            "NSE",
            message="Skipped by --skip-nse; fallback will be attempted for matching NSE symbols",
        )
    else:
        try:
            records, url = fetch_nse_official(trade_date)
            stats = db.upsert_records(records)
            merge_stats(summary, stats)
            summary["official_rows"] += len(records)
            db.audit(run_id, "official_nse_bhavcopy", "success", trade_date.isoformat(), "NSE", len(records), url=url)
        except Exception as exc:
            fallback_exchanges.append("NSE")
            db.audit(run_id, "official_nse_bhavcopy", "failed", trade_date.isoformat(), "NSE", message=str(exc))

    if skip_bse:
        fallback_exchanges.append("BSE")
        db.audit(
            run_id,
            "official_bse_bhavcopy",
            "skipped",
            trade_date.isoformat(),
            "BSE",
            message="Skipped by --skip-bse; fallback will be attempted for matching BSE symbols",
        )
    else:
        try:
            records, url = fetch_bse_official(trade_date)
            stats = db.upsert_records(records)
            merge_stats(summary, stats)
            summary["official_rows"] += len(records)
            db.audit(run_id, "official_bse_bhavcopy", "success", trade_date.isoformat(), "BSE", len(records), url=url)
        except Exception as exc:
            fallback_exchanges.append("BSE")
            db.audit(run_id, "official_bse_bhavcopy", "failed", trade_date.isoformat(), "BSE", message=str(exc))

    if not fallback_exchanges:
        return summary

    fallback_specs = [spec for spec in specs if spec.exchange in fallback_exchanges]
    if not fallback_specs:
        db.audit(
            run_id,
            "fallback",
            "skipped",
            trade_date.isoformat(),
            rows=0,
            message=(
                "Official data unavailable for "
                + ",".join(fallback_exchanges)
                + " and no matching --symbols or --symbols-file universe was provided"
            ),
        )
        return summary

    api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()
    fallback_records: List[MarketRecord] = []
    if api_key:
        fallback_records = fetch_twelvedata_records(trade_date, fallback_specs, api_key)
        db.audit(
            run_id,
            "twelvedata_api",
            "success" if fallback_records else "failed",
            trade_date.isoformat(),
            rows=len(fallback_records),
            message=None if fallback_records else "No Twelve Data rows returned",
        )
    else:
        db.audit(
            run_id,
            "twelvedata_api",
            "skipped",
            trade_date.isoformat(),
            message="TWELVEDATA_API_KEY is not configured",
        )

    returned_keys = {(record.exchange, record.symbol_key) for record in fallback_records}
    remaining_specs = [spec for spec in fallback_specs if spec_key(spec) not in returned_keys]

    if remaining_specs:
        yfinance_records = fetch_yfinance_records(trade_date, remaining_specs)
        db.audit(
            run_id,
            "yfinance",
            "success" if yfinance_records else "failed",
            trade_date.isoformat(),
            rows=len(yfinance_records),
            message=None if yfinance_records else "No yfinance rows returned or yfinance is not installed",
        )
        fallback_records.extend(yfinance_records)

    if fallback_records:
        stats = db.upsert_records(fallback_records)
        merge_stats(summary, stats)
        summary["fallback_rows"] += len(fallback_records)
    return summary


def merge_stats(target: Dict[str, int], update: Dict[str, int]) -> None:
    for key in ("inserted", "replaced", "kept", "invalid"):
        target[key] += update.get(key, 0)


def recover_temporary_rows(
    db: MarketDatabase,
    run_id: str,
    recover_days: int,
    specs: Sequence[SymbolSpec],
    skip_nse: bool,
    skip_bse: bool,
) -> Dict[str, int]:
    if recover_days <= 0:
        return {"dates": 0, "inserted": 0, "replaced": 0, "kept": 0, "invalid": 0}
    since = dt.datetime.now(IST).date() - dt.timedelta(days=recover_days)
    dates = db.temporary_dates(since)
    summary = {"dates": len(dates), "inserted": 0, "replaced": 0, "kept": 0, "invalid": 0}
    for trade_date in dates:
        result = run_for_date(db, run_id, trade_date, specs, skip_nse, skip_bse)
        merge_stats(summary, result)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load Indian cash-market OHLCV data with official NSE/BSE priority and automatic fallbacks."
    )
    parser.add_argument("--date", help="Trade date in YYYY-MM-DD format. Defaults to today in Asia/Kolkata.")
    parser.add_argument("--db", default="market_data.sqlite", help="SQLite database path.")
    parser.add_argument(
        "--symbols",
        action="append",
        default=[],
        help="Fallback universe, comma-separated. Examples: SBIN,TCS or NSE:SBIN,BSE:500325.",
    )
    parser.add_argument(
        "--symbols-file",
        type=Path,
        help="Optional text or CSV file. CSV columns: symbol, exchange, isin, yfinance_ticker.",
    )
    parser.add_argument("--skip-nse", action="store_true", help="Do not fetch official NSE bhavcopy.")
    parser.add_argument("--skip-bse", action="store_true", help="Do not fetch official BSE bhavcopy.")
    parser.add_argument(
        "--recover-days",
        type=int,
        default=7,
        help="After the run, retry official data for temporary fallback rows from the last N days.",
    )
    parser.add_argument(
        "--recover-only",
        action="store_true",
        help="Only run recovery for existing temporary rows. Does not fetch --date first.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_id = str(uuid.uuid4())
    trade_date = parse_cli_date(args.date)
    specs = parse_symbol_specs(args.symbols, args.symbols_file)
    db = MarketDatabase(Path(args.db))
    try:
        summary = {
            "official_rows": 0,
            "fallback_rows": 0,
            "inserted": 0,
            "replaced": 0,
            "kept": 0,
            "invalid": 0,
        }
        if not args.recover_only:
            summary = run_for_date(db, run_id, trade_date, specs, args.skip_nse, args.skip_bse)
        recovery = recover_temporary_rows(db, run_id, args.recover_days, specs, args.skip_nse, args.skip_bse)
    finally:
        db.close()

    print(json.dumps({"run_id": run_id, "date": trade_date.isoformat(), "load": summary, "recovery": recovery}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
