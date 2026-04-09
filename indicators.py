import pandas as pd
import numpy as np
import ta as ta_lib
from logger_setup import setup_logger

logger = setup_logger("Indicators")


def calculate_ut_bot_alerts(df: pd.DataFrame, sensitivity: float = 1.0, atr_period: int = 14) -> pd.DataFrame:
    """
    UT Bot Alerts - Pine Script'teki ATR Trailing Stop mantigi.
    Sinyal: 1 = BUY, -1 = SELL, 0 = notr
    """
    close = df["close"].values
    atr_indicator = ta_lib.volatility.AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=atr_period
    )
    atr = atr_indicator.average_true_range()
    n_loss = sensitivity * atr

    trailing_stop = np.zeros(len(close))
    signal = np.zeros(len(close))

    for i in range(1, len(close)):
        if np.isnan(n_loss.iloc[i]):
            continue

        if close[i] > trailing_stop[i - 1]:
            trailing_stop[i] = max(trailing_stop[i - 1], close[i] - n_loss.iloc[i])
        else:
            trailing_stop[i] = min(trailing_stop[i - 1], close[i] + n_loss.iloc[i])

        if close[i] > trailing_stop[i] and close[i - 1] <= trailing_stop[i - 1]:
            signal[i] = 1  # BUY
        elif close[i] < trailing_stop[i] and close[i - 1] >= trailing_stop[i - 1]:
            signal[i] = -1  # SELL

    df["trailing_stop"] = trailing_stop
    df["ut_signal"] = signal
    return df


def calculate_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """VWAP - Hacim Agirlikli Ortalama Fiyat."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
    cumulative_vol = df["volume"].cumsum()
    df["vwap"] = cumulative_tp_vol / cumulative_vol
    return df


def calculate_rsi(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """RSI hesapla."""
    rsi_indicator = ta_lib.momentum.RSIIndicator(close=df["close"], window=length)
    df["rsi"] = rsi_indicator.rsi()
    return df


def calculate_volume_spike(df: pd.DataFrame, lookback: int = 20, multiplier: float = 1.5) -> pd.DataFrame:
    """
    Hacim patlamasi tespiti.
    Son 3 mumun ortalama hacmi, onceki 20 mumun ortalamasinin multiplier katindan buyukse = True.
    """
    avg_vol_20 = df["volume"].rolling(window=lookback).mean()
    recent_vol_3 = df["volume"].rolling(window=3).mean()
    df["volume_spike"] = recent_vol_3 > (avg_vol_20 * multiplier)
    df["volume_ratio"] = recent_vol_3 / avg_vol_20
    return df


def calculate_adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """ADX - Trend gucu olcumu."""
    adx_indicator = ta_lib.trend.ADXIndicator(
        high=df["high"], low=df["low"], close=df["close"], window=length
    )
    df["adx"] = adx_indicator.adx()
    df["di_plus"] = adx_indicator.adx_pos()
    df["di_minus"] = adx_indicator.adx_neg()
    return df


def calculate_ema(df: pd.DataFrame, lengths: list = None) -> pd.DataFrame:
    """EMA hesapla (varsayilan 20 ve 50)."""
    if lengths is None:
        lengths = [20, 50]
    for length in lengths:
        ema_indicator = ta_lib.trend.EMAIndicator(close=df["close"], window=length)
        df[f"ema_{length}"] = ema_indicator.ema_indicator()
    return df


def calculate_atr(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """ATR hesapla."""
    atr_indicator = ta_lib.volatility.AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=length
    )
    df["atr"] = atr_indicator.average_true_range()
    return df


def calculate_trend_slope(df: pd.DataFrame, length: int = 10) -> pd.DataFrame:
    """EMA20'nin egim acisini hesapla - trend dikligi."""
    if "ema_20" not in df.columns:
        df = calculate_ema(df, [20])
    ema = df["ema_20"]
    df["trend_slope"] = ema.diff(length) / length
    df["trend_slope_pct"] = (df["trend_slope"] / df["close"]) * 100
    return df


def run_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Tum indikatorleri sirayla hesapla."""
    logger.info("Indikatorler hesaplaniyor...")
    df = calculate_atr(df)
    df = calculate_ut_bot_alerts(df)
    df = calculate_vwap(df)
    df = calculate_rsi(df)
    df = calculate_volume_spike(df)
    df = calculate_adx(df)
    df = calculate_ema(df)
    df = calculate_trend_slope(df)
    logger.info("Tum indikatorler hesaplandi.")
    return df
