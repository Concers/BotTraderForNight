#!/usr/bin/env python3
from __future__ import annotations
"""
Backtest: 8 Nisan 2026 ETH islemini test et.
Giris: 2026-04-08 01:03 @ 2,204.60 USDT (BUY)
Cikis: 2026-04-08 14:42 @ 2,239.69 USDT
PNL:  +18,635.92 USDT (531.049 ETH)
"""

import os
import pandas as pd
from datetime import datetime, timezone
from binance.um_futures import UMFutures
from indicators import run_all_indicators
from scoring import CANSLIMScorer
from risk_manager import RiskManager
from logger_setup import setup_logger

logger = setup_logger("Backtest")

TRADE = {
    "symbol": "ETHUSDT",
    "side": "BUY",
    "entry_time": "2026-04-08 01:03:00",
    "entry_price": 2204.60,
    "close_time": "2026-04-08 14:42:00",
    "close_price": 2239.69,
    "quantity": 531.049,
    "pnl": 18635.92,
}


def ts_to_ms(dt_str: str) -> int:
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def get_klines(client, symbol, interval, start_ms, end_ms):
    """Tarihsel mum verisi cek."""
    klines = client.klines(
        symbol=symbol, interval=interval,
        startTime=start_ms, endTime=end_ms, limit=500
    )
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def run_backtest():
    print("=" * 60)
    print("  BACKTEST: ETH 8 Nisan 2026 Islemi")
    print("  (Gercek Binance verisi kullaniliyor)")
    print("=" * 60)
    print(f"  Giris : {TRADE['entry_time']} @ ${TRADE['entry_price']}")
    print(f"  Cikis : {TRADE['close_time']} @ ${TRADE['close_price']}")
    print(f"  PNL   : +${TRADE['pnl']:,.2f}")
    print("=" * 60)

    # Gercek Binance API (sadece veri okuma - islem yok)
    client = UMFutures()  # Public endpoint, key gerekmez
    scorer = CANSLIMScorer()
    risk = RiskManager()

    entry_ms = ts_to_ms(TRADE["entry_time"])
    # Giris zamanindan once 200 mum icin (150 mum analiz + 50 buffer)
    start_3m = entry_ms - (200 * 3 * 60 * 1000)
    start_5m = entry_ms - (200 * 5 * 60 * 1000)

    # =============================================
    # 3 DAKIKALIK MUMLAR (gercek veri)
    # =============================================
    print("\n📊 3 DAKIKALIK ANALIZ (giris oncesi 150 mum)")
    print("-" * 40)

    try:
        df_3m = get_klines(client, TRADE["symbol"], "3m", start_3m, entry_ms)
        df_3m = run_all_indicators(df_3m)

        print(f"  Mum sayisi : {len(df_3m)}")
        print(f"  Baslangic  : {df_3m['open_time'].iloc[0]}")
        print(f"  Bitis      : {df_3m['open_time'].iloc[-1]}")
        print(f"  Son fiyat  : ${df_3m['close'].iloc[-1]:.2f}")
        print(f"  UT Bot     : {df_3m['ut_signal'].iloc[-1]}")
        print(f"  RSI        : {df_3m['rsi'].iloc[-1]:.1f}")
        print(f"  VWAP       : ${df_3m['vwap'].iloc[-1]:.2f}")
        print(f"  ADX        : {df_3m['adx'].iloc[-1]:.1f}")
        print(f"  Hacim Spike: {df_3m['volume_spike'].iloc[-1]}")
        print(f"  Vol Ratio  : {df_3m['volume_ratio'].iloc[-1]:.2f}x")

        # 100 mum karar matrisi
        window = df_3m.tail(150)
        n_w = len(window)
        above_ts = (window["close"] > window["trailing_stop"]).sum()
        above_vwap = (window["close"] > window["vwap"]).sum()
        avg_rsi = window["rsi"].dropna().mean()

        # Higher Lows kontrolu
        lows = window["low"].values
        chunk = n_w // 5
        dips = [lows[i*chunk:(i+1)*chunk].min() for i in range(5) if len(lows[i*chunk:(i+1)*chunk]) > 0]
        hl_count = sum(1 for i in range(1, len(dips)) if dips[i] > dips[i-1])

        # Coklu zaman dilimi trendi
        first_half_avg = window.head(n_w // 2)["close"].mean()
        second_half_avg = window.tail(n_w // 2)["close"].mean()
        trend_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100

        print(f"\n  --- 150 Mum Karar Matrisi ---")
        print(f"  Trailing Stop ustunde: {above_ts}/{n_w}")
        print(f"  VWAP ustunde         : {above_vwap}/{n_w}")
        print(f"  Ortalama RSI         : {avg_rsi:.1f}")
        print(f"  Yukselen Dipler      : {hl_count}/4")
        print(f"  Trend (1.yari->2.yari): %{trend_pct:.2f}")

        # BTC verisi
        btc_3m = get_klines(client, "BTCUSDT", "3m", start_3m, entry_ms)
        btc_3m = run_all_indicators(btc_3m)

        score_3m = scorer.calculate_score(df_3m, TRADE["symbol"], btc_3m)
        print(f"\n  CANSLIM Skor: {score_3m['score']}/100")
        print(f"  Karar      : {score_3m['decision']}")
        print(f"  Bilesenler : {score_3m['components']}")

    except Exception as e:
        print(f"  HATA: {e}")
        score_3m = None
        df_3m = pd.DataFrame()

    # =============================================
    # 5 DAKIKALIK MUMLAR (gercek veri)
    # =============================================
    print("\n📊 5 DAKIKALIK ANALIZ (giris oncesi 150 mum)")
    print("-" * 40)

    try:
        df_5m = get_klines(client, TRADE["symbol"], "5m", start_5m, entry_ms)
        df_5m = run_all_indicators(df_5m)

        print(f"  Mum sayisi : {len(df_5m)}")
        print(f"  Son fiyat  : ${df_5m['close'].iloc[-1]:.2f}")
        print(f"  UT Bot     : {df_5m['ut_signal'].iloc[-1]}")
        print(f"  RSI        : {df_5m['rsi'].iloc[-1]:.1f}")
        print(f"  VWAP       : ${df_5m['vwap'].iloc[-1]:.2f}")
        print(f"  Hacim Spike: {df_5m['volume_spike'].iloc[-1]}")

        btc_5m = get_klines(client, "BTCUSDT", "5m", start_5m, entry_ms)
        btc_5m = run_all_indicators(btc_5m)

        score_5m = scorer.calculate_score(df_5m, TRADE["symbol"], btc_5m)
        print(f"\n  CANSLIM Skor: {score_5m['score']}/100")
        print(f"  Karar      : {score_5m['decision']}")
        print(f"  Bilesenler : {score_5m['components']}")

    except Exception as e:
        print(f"  HATA: {e}")
        score_5m = None
        df_5m = pd.DataFrame()

    # =============================================
    # RISK ANALIZI
    # =============================================
    print("\n🛡️ RISK ANALIZI")
    print("-" * 40)

    if not df_3m.empty:
        stop_info = risk.get_adaptive_stop_loss(df_3m, TRADE["entry_price"], TRADE["side"])
        print(f"  Stop-Loss    : ${stop_info['stop_price']:.2f}")
        print(f"  Volatilite   : %{stop_info['volatility_pct']}")
        print(f"  ATR Carpani  : {stop_info['multiplier']}x")
        print(f"  Zaman Limiti : {stop_info['time_limit_min']}dk")

        sim_balance = 50000
        quantity = risk.calculate_position_size(sim_balance, TRADE["entry_price"], stop_info["stop_price"], 100)
        sim_pnl = quantity * (TRADE["close_price"] - TRADE["entry_price"])

        print(f"\n  --- Simulasyon (${sim_balance:,.0f} bakiye) ---")
        print(f"  Pozisyon   : {quantity:.3f} ETH")
        print(f"  Tahmini PNL: ${sim_pnl:,.2f}")

    # =============================================
    # SONUC
    # =============================================
    print("\n" + "=" * 60)
    print("  SONUC")
    print("=" * 60)

    decisions = []
    if score_3m:
        decisions.append(("3dk", score_3m))
    if score_5m:
        decisions.append(("5dk", score_5m))

    any_approved = any(s["decision"] != "REJECTED" for _, s in decisions)

    if any_approved:
        print("  ✅ BOT BU FIRSATI YAKALARDI!")
        for tf, s in decisions:
            status = "✅" if s["decision"] != "REJECTED" else "⚠️"
            print(f"     {status} {tf}: Skor {s['score']} -> {s['decision']}")
        print(f"\n  Gercek PNL: +${TRADE['pnl']:,.2f}")
    else:
        print("  ❌ Bot bu firsati kacirirdi.")
        for tf, s in decisions:
            print(f"     ❌ {tf}: Skor {s['score']} -> {s['decision']}")
        print("\n  Dusuk skorlu bilesenler:")
        for tf, s in decisions:
            weak = {k: v for k, v in s["components"].items() if v < 50}
            if weak:
                print(f"     {tf}: {weak}")

    print("=" * 60)


if __name__ == "__main__":
    run_backtest()
