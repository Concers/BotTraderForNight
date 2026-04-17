#!/usr/bin/env python3
from __future__ import annotations
"""
Backtest Penceresi (Replay)
===========================
Belirlenen TR saat penceresinde (ornek: 16 Nisan 16:00 - 17 Nisan 16:00),
her 5 dk'da bir market scanner mantigini TARIHSEL klines uzerinde yeniden
calistirir. Bulunan LONG/SHORT sinyallerini sanal olarak acar; ATR stop,
T1 breakeven, T2 kar kilidi ve trailing kurallarini 1 dk klines ile ileri
sarip uygular. Sonunda islem listesi + toplam PnL basar.

KULLANIM:
  python3 backtest_window.py

Not: Evren (symbols) anlik Binance 24h ticker'indan secilir (10M-200M$).
Gercek zamanda farkli evren varsa, BAKTEST TAM AYNI olmayabilir;
yine de "bu pencerede ne olurdu" sorusuna saglam bir yanit verir.
"""

import argparse
import os
import time
import math
import urllib.request
import urllib.parse
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from binance.um_futures import UMFutures

from indicators import run_all_indicators
from long_strategy import analyze_long_setup, should_open_long
from short_strategy import analyze_short_setup, should_open_short
from logger_setup import setup_logger
import config

logger = setup_logger("BacktestWindow")

TR_TZ = timezone(timedelta(hours=3))

# -------------------- AYARLAR --------------------
# TR saati ile pencere
DEFAULT_START_TR = "2026-04-16 16:00:00"
DEFAULT_END_TR   = "2026-04-17 16:00:00"

# Scanner esikleri (market_scanner_loop ile ayni)
MIN_SCORE = 70
MIN_RVOL = 1.5
MAX_NEW_PER_TICK = 5       # Her 5dk'da acilabilecek max sinyal
MAX_CONCURRENT = 7         # Ayni anda acik max pozisyon
MIN_RR = 2.0               # bot.py'deki RR>=2 kontrolu

# Pozisyon
MARGIN_USDT = 20.0
LEVERAGE = 20
NOTIONAL = MARGIN_USDT * LEVERAGE  # $400

# Stop/ATR (risk_manager ile ayni)
ATR_MULT_LOW = 3.5
ATR_MULT_HIGH = 4.0
MIN_STOP_PCT = 0.025
STRUCT_LOOKBACK = 10
STRUCT_BUFFER = 0.005

# Universe filtresi (market_scanner ile ayni)
MIN_VOL_24H = 10_000_000
MAX_VOL_24H = 200_000_000
TOP_N_UNIVERSE = 100

# Scanner'da funding esiksiz 0 verecegiz (pencere basi anlik alinabilir ama
# 288 tick icin tek tek cekmek pahali; biz LONG/SHORT setup'ina funding=0 veririz)
ASSUME_FUNDING = 0.0


@dataclass
class Trade:
    symbol: str
    side: str               # BUY / SELL
    entry_time: datetime    # UTC
    entry_price: float
    stop_price: float
    quantity: float
    score: float
    rvol: float
    # durum
    breakeven_hit: bool = False
    t2_hit: bool = False
    highest_price: float = 0.0
    # kapanis
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None
    close_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0


# -------------------- TELEGRAM --------------------

def send_telegram(text: str) -> bool:
    """Telegram Bot API'ye sync post. config'deki token + chat_id kullanilir."""
    token = getattr(config, "TELEGRAM_BOT_TOKEN", None)
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", None)
    if not token or not chat_id:
        logger.warning("Telegram token/chat_id yok, mesaj atlandi.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=payload, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        logger.error(f"Telegram gonderilemedi: {e}")
        return False


# -------------------- YARDIMCILAR --------------------

def parse_tr(s: str) -> datetime:
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=TR_TZ)


def to_utc_ms(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def klines_df(client: UMFutures, symbol: str, interval: str,
              start_ms: int, end_ms: int, max_per_call: int = 1500) -> pd.DataFrame:
    """Verilen pencereye ait tum mumlari 1500'luk parcalar halinde cek."""
    frames = []
    cursor = start_ms
    while cursor < end_ms:
        try:
            batch = client.klines(symbol=symbol, interval=interval,
                                   startTime=cursor, endTime=end_ms,
                                   limit=max_per_call)
        except Exception as e:
            logger.warning(f"klines({symbol},{interval}) hata: {e}")
            break
        if not batch:
            break
        frames.append(batch)
        last_open = batch[-1][0]
        next_cursor = last_open + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < max_per_call:
            break
        time.sleep(0.05)

    if not frames:
        return pd.DataFrame()

    rows = [r for f in frames for r in f]
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df = df.drop_duplicates(subset="open_time").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def pick_universe(client: UMFutures) -> list[str]:
    """Likit USDT perp evrenini anlik ticker'dan sec (10M-200M$ 24h hacim)."""
    tickers = client.ticker_24hr_price_change()
    cands = []
    for t in tickers:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        try:
            vol = float(t.get("quoteVolume", 0))
            price = float(t.get("lastPrice", 0))
        except Exception:
            continue
        if price <= 0:
            continue
        if MIN_VOL_24H <= vol <= MAX_VOL_24H:
            cands.append((sym, vol))
    cands.sort(key=lambda x: x[1], reverse=True)
    syms = [c[0] for c in cands[:TOP_N_UNIVERSE]]
    if "BTCUSDT" not in syms:
        syms.append("BTCUSDT")
    return syms


def adaptive_stop(df_3m: pd.DataFrame, entry: float, side: str) -> float:
    """risk_manager.get_adaptive_stop_loss'un sade kopyasi."""
    atr = float(df_3m["atr"].iloc[-1])
    vol_pct = (atr / entry) * 100
    mult = ATR_MULT_HIGH if vol_pct > 1.0 else ATR_MULT_LOW
    dist = atr * mult
    min_dist = entry * MIN_STOP_PCT
    if dist < min_dist:
        dist = min_dist

    if side == "BUY":
        recent_low = float(df_3m["low"].tail(STRUCT_LOOKBACK).min())
        struct_stop = recent_low * (1 - STRUCT_BUFFER)
        atr_stop = entry - dist
        return min(atr_stop, struct_stop)
    else:
        recent_high = float(df_3m["high"].tail(STRUCT_LOOKBACK).max())
        struct_stop = recent_high * (1 + STRUCT_BUFFER)
        atr_stop = entry + dist
        return max(atr_stop, struct_stop)


def profit_pct(trade: Trade, price: float) -> float:
    if trade.side == "BUY":
        return ((price - trade.entry_price) / trade.entry_price) * 100 * LEVERAGE
    return ((trade.entry_price - price) / trade.entry_price) * 100 * LEVERAGE


def update_stop_for_layers(trade: Trade, price: float):
    """T1 breakeven, T2 kar kilidi, kademeli trailing uygula."""
    p = profit_pct(trade, price)

    # en iyi fiyati guncelle
    if trade.side == "BUY":
        if price > trade.highest_price:
            trade.highest_price = price
    else:
        if price < trade.highest_price or trade.highest_price == 0:
            trade.highest_price = price

    # T1 (%2 lev kar -> entry + %0.3)
    if not trade.breakeven_hit and p >= 2.0:
        trade.breakeven_hit = True
        if trade.side == "BUY":
            ns = trade.entry_price * 1.003
            if ns > trade.stop_price:
                trade.stop_price = ns
        else:
            ns = trade.entry_price * 0.997
            if ns < trade.stop_price:
                trade.stop_price = ns

    # T2 (%4 lev kar -> entry + %1.5)
    if not trade.t2_hit and p >= 4.0:
        trade.t2_hit = True
        if trade.side == "BUY":
            ns = trade.entry_price * 1.015
            if ns > trade.stop_price:
                trade.stop_price = ns
        else:
            ns = trade.entry_price * 0.985
            if ns < trade.stop_price:
                trade.stop_price = ns

    # Kademeli trailing (T2 sonrasi)
    if trade.t2_hit:
        if p >= 20:
            tr = 0.5
        elif p >= 12:
            tr = 0.8
        elif p >= 7:
            tr = 1.2
        else:
            tr = 1.5
        if trade.side == "BUY":
            cand = trade.highest_price * (1 - tr / 100)
            if cand > trade.stop_price:
                trade.stop_price = cand
        else:
            cand = trade.highest_price * (1 + tr / 100)
            if cand < trade.stop_price:
                trade.stop_price = cand


def simulate_forward(trade: Trade, df_1m_full: pd.DataFrame,
                      end_utc: datetime) -> None:
    """1 dk mumlarla ileri sar; stop hit / pencere bitisinde kapat."""
    # Girisi iceren mumdan SONRAKI mumla basla (aynı 3 dk mumun içi açılış oldu)
    mask = df_1m_full["open_time"] > trade.entry_time
    forward = df_1m_full.loc[mask]
    if forward.empty:
        trade.close_time = trade.entry_time
        trade.close_price = trade.entry_price
        trade.close_reason = "VERI_YOK"
        return

    for _, bar in forward.iterrows():
        if bar["open_time"] > end_utc:
            # Pencere bitti - son close ile mark-to-market
            trade.close_time = end_utc
            trade.close_price = float(bar["open"])
            trade.close_reason = "PENCERE_BITTI"
            trade.pnl_pct = profit_pct(trade, trade.close_price)
            trade.pnl = (trade.pnl_pct / 100) * MARGIN_USDT
            return

        high = float(bar["high"])
        low = float(bar["low"])

        # Once en kotu (LONG icin low, SHORT icin high) -> stop kontrolu
        # GAP KORUMA: -%5 lev kar < -5 (yani entry karsiti %0.25 dalgalanma)
        worst_price = low if trade.side == "BUY" else high
        if profit_pct(trade, worst_price) <= -5.0:
            trade.close_time = bar["open_time"]
            trade.close_price = worst_price
            trade.close_reason = "GAP_KORUMA"
            trade.pnl_pct = profit_pct(trade, worst_price)
            trade.pnl = (trade.pnl_pct / 100) * MARGIN_USDT
            return

        # Stop tetiklendi mi? (low/high stop seviyesine degdiyse)
        if trade.side == "BUY" and low <= trade.stop_price:
            trade.close_time = bar["open_time"]
            trade.close_price = trade.stop_price
            trade.close_reason = (
                "T2_TRAIL" if trade.t2_hit
                else "T1_BREAKEVEN" if trade.breakeven_hit
                else "STOP_LOSS"
            )
            trade.pnl_pct = profit_pct(trade, trade.close_price)
            trade.pnl = (trade.pnl_pct / 100) * MARGIN_USDT
            return
        if trade.side == "SELL" and high >= trade.stop_price:
            trade.close_time = bar["open_time"]
            trade.close_price = trade.stop_price
            trade.close_reason = (
                "T2_TRAIL" if trade.t2_hit
                else "T1_BREAKEVEN" if trade.breakeven_hit
                else "STOP_LOSS"
            )
            trade.pnl_pct = profit_pct(trade, trade.close_price)
            trade.pnl = (trade.pnl_pct / 100) * MARGIN_USDT
            return

        # Stop tetiklenmedi -> mum high/low uzerinden katmanlari guncelle
        # (LONG: once low sonra high, SHORT: tersi)
        if trade.side == "BUY":
            update_stop_for_layers(trade, low)
            # ara kontrol: low stop'u kaldirdi mi? degil, low zaten kontrol edildi yukarda
            update_stop_for_layers(trade, high)
        else:
            update_stop_for_layers(trade, high)
            update_stop_for_layers(trade, low)

    # Forward bitti ama pencere henuz gecmedi
    last_close = float(forward.iloc[-1]["close"])
    trade.close_time = forward.iloc[-1]["open_time"].to_pydatetime()
    trade.close_price = last_close
    trade.close_reason = "VERI_SONU"
    trade.pnl_pct = profit_pct(trade, last_close)
    trade.pnl = (trade.pnl_pct / 100) * MARGIN_USDT


# -------------------- ANA AKIS --------------------

def run(start_tr: str, end_tr: str):
    start_dt = parse_tr(start_tr)
    end_dt = parse_tr(end_tr)
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc = end_dt.astimezone(timezone.utc)

    print("=" * 70)
    print(f"  BACKTEST PENCERESI: {start_tr} - {end_tr} (TR)")
    print(f"  UTC: {start_utc} - {end_utc}")
    print("=" * 70)

    client = UMFutures()

    print("\n[1/4] Evren belirleniyor (likit USDT perp, 10M-200M$ 24h)...")
    universe = pick_universe(client)
    print(f"   -> {len(universe)} sembol")

    # Veri aralligi: tick basina 200 adet 3 dk mum lazim => pencereden 12 saat geri
    # Ayrica forward simulasyon icin pencere sonuna kadar 1 dk mum
    data_start_utc = start_utc - timedelta(hours=12)
    # Forward simulasyonu icin biraz buffer (pencere sonunu da kapsamak icin)
    data_end_utc = end_utc + timedelta(minutes=5)

    print("\n[2/4] Tarihsel klines indiriliyor (3m + 1m). Bu birkac dakika surebilir...")
    klines_3m: dict[str, pd.DataFrame] = {}
    klines_1m: dict[str, pd.DataFrame] = {}

    for i, sym in enumerate(universe, 1):
        d3 = klines_df(client, sym, "3m",
                        to_utc_ms(data_start_utc), to_utc_ms(data_end_utc))
        d1 = klines_df(client, sym, "1m",
                        to_utc_ms(start_utc), to_utc_ms(data_end_utc))
        if not d3.empty and len(d3) >= 60:
            klines_3m[sym] = d3
        if not d1.empty:
            klines_1m[sym] = d1
        if i % 10 == 0:
            print(f"   ... {i}/{len(universe)}")
        time.sleep(0.05)

    print(f"   -> 3m OK: {len(klines_3m)} | 1m OK: {len(klines_1m)}")

    # Tickler: her 5 dk, scanner market_scanner_loop gibi
    ticks: list[datetime] = []
    t = start_utc
    # 5 dk mum kapanisina hizala (05, 10, 15, ...)
    t_epoch = int(t.timestamp())
    t_epoch = ((t_epoch + 299) // 300) * 300
    t = datetime.fromtimestamp(t_epoch, tz=timezone.utc)
    while t <= end_utc:
        ticks.append(t)
        t = t + timedelta(minutes=5)

    print(f"\n[3/4] {len(ticks)} tick taraniyor...")

    active: dict[str, Trade] = {}      # sembol -> acik trade
    closed: list[Trade] = []
    signal_count = 0

    for ti, tick_utc in enumerate(ticks, 1):
        # BTC mood / perf 1h (tick aninda)
        btc_df_full = klines_3m.get("BTCUSDT")
        if btc_df_full is None:
            continue
        btc_slice = btc_df_full[btc_df_full["open_time"] <= tick_utc]
        if len(btc_slice) < 60:
            continue
        btc_df = run_all_indicators(btc_slice.tail(200).copy().reset_index(drop=True))
        btc_closes = btc_df["close"].values
        btc_perf_1h = ((btc_closes[-1] - btc_closes[-20]) / btc_closes[-20]) * 100 \
            if len(btc_closes) >= 20 else 0.0
        if btc_perf_1h > 0.5:
            mood = "BULL"
        elif btc_perf_1h < -0.5:
            mood = "BEAR"
        else:
            mood = "NOTR"

        # Acik pozisyonlari guncelle - pencere sonunda toplu kapatilacak
        # (stop_forward her trade icin ayri calisiyor; burada hicbir sey yapmiyoruz)

        if len(active) >= MAX_CONCURRENT:
            continue

        # Adaylari topla
        candidates = []
        for sym, full in klines_3m.items():
            if sym == "BTCUSDT":
                continue
            if sym in active:
                continue
            sl = full[full["open_time"] <= tick_utc]
            if len(sl) < 60:
                continue
            d = run_all_indicators(sl.tail(200).copy().reset_index(drop=True))
            if d.empty or len(d) < 50:
                continue

            close = float(d["close"].iloc[-1])
            if close <= 0:
                continue

            # RVOL
            cur_vol = float(d["volume"].iloc[-1])
            avg_vol = float(d["volume"].tail(20).mean())
            rvol = cur_vol / avg_vol if avg_vol > 0 else 0
            if math.isnan(rvol) or math.isinf(rvol):
                rvol = 0
            if rvol < MIN_RVOL:
                continue

            # 24h degisim (yaklasik): 480 adet 3 dk mum = 24h
            closes = d["close"].values
            if len(closes) >= 480:
                pc24 = ((closes[-1] - closes[-480]) / closes[-480]) * 100
            else:
                pc24 = ((closes[-1] - closes[0]) / closes[0]) * 100

            long_s = analyze_long_setup(d, btc_perf_1h=btc_perf_1h,
                                         price_change_24h=pc24,
                                         funding_rate=ASSUME_FUNDING)
            short_s = analyze_short_setup(d, btc_perf_1h=btc_perf_1h,
                                           price_change_24h=pc24,
                                           funding_rate=ASSUME_FUNDING)

            if long_s["score"] >= MIN_SCORE and should_open_long(long_s, mood):
                candidates.append(("BUY", sym, long_s["score"], rvol, d))
            if short_s["score"] >= MIN_SCORE and should_open_short(short_s, mood):
                candidates.append(("SELL", sym, short_s["score"], rvol, d))

        # Sentiment gate (basit versiyon: sadece asiri BULL/BEAR'da ters yon engelli)
        # market_scanner_loop'taki aggregate sentiment skoru basitlestirildi - mood kullaniyoruz
        # (tam replika icin BTC 4h ve funding gerekir; kabul edilebilir sadelestirme)

        # Siralama: skora gore desc, sonra rvol desc
        candidates.sort(key=lambda x: (-x[2], -x[3]))
        opened_this_tick = 0
        for side, sym, score, rvol, d in candidates:
            if opened_this_tick >= MAX_NEW_PER_TICK:
                break
            if len(active) >= MAX_CONCURRENT:
                break

            entry_price = float(d["close"].iloc[-1])

            # RR >= 2.0 kontrolu
            stop = adaptive_stop(d, entry_price, side)
            stop_dist = abs(entry_price - stop)
            if side == "BUY":
                reward = float(d["high"].tail(20).max()) - entry_price
            else:
                reward = entry_price - float(d["low"].tail(20).min())
            rr = (reward / stop_dist) if stop_dist > 0 else 0
            if rr < MIN_RR:
                continue

            qty = NOTIONAL / entry_price
            trade = Trade(
                symbol=sym, side=side,
                entry_time=tick_utc,
                entry_price=entry_price,
                stop_price=stop,
                quantity=qty,
                score=score,
                rvol=rvol,
            )
            active[sym] = trade
            opened_this_tick += 1
            signal_count += 1
            print(f"   tick {ti}/{len(ticks)} {tick_utc.astimezone(TR_TZ).strftime('%H:%M')} "
                  f"-> {side} {sym} @ {entry_price:.6f} "
                  f"skor:{score:.0f} rvol:{rvol:.2f} stop:{stop:.6f} rr:{rr:.1f}")

        # Pencere sonunda da yeni sinyal acabiliriz; active trade'leri forward simuluyoruz pencere bitince.

    print(f"\n   Toplam uretilen sinyal: {signal_count}")

    print("\n[4/4] Ileri simulasyon (1 dk klines) + PnL hesabi...")
    for sym, trade in active.items():
        d1 = klines_1m.get(sym)
        if d1 is None:
            trade.close_time = trade.entry_time
            trade.close_price = trade.entry_price
            trade.close_reason = "1m_VERI_YOK"
            continue
        # trailing highest baslat
        trade.highest_price = trade.entry_price
        simulate_forward(trade, d1, end_utc)
        closed.append(trade)

    # -------------------- RAPOR --------------------
    closed.sort(key=lambda t: t.entry_time)
    total_pnl = sum(t.pnl for t in closed)
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0

    print("\n" + "=" * 70)
    print(f"  SONUCLAR ({len(closed)} islem)")
    print("=" * 70)
    print(f"  Toplam PnL: ${total_pnl:+.2f} (marjin ${MARGIN_USDT} x{LEVERAGE})")
    print(f"  Win rate: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    if wins:
        avg_w = sum(t.pnl for t in wins) / len(wins)
        print(f"  Ortalama kazanc: ${avg_w:+.2f}")
    if losses:
        avg_l = sum(t.pnl for t in losses) / len(losses)
        print(f"  Ortalama kayip : ${avg_l:+.2f}")
    print("=" * 70)

    print(f"\n{'Giris (TR)':<19} {'Symbol':<14} {'Yon':<5} {'Skor':<5} "
          f"{'RVOL':<5} {'Giris':<11} {'Cikis':<11} {'%':<7} {'$':<8} Sebep")
    print("-" * 120)
    for t in closed:
        et = t.entry_time.astimezone(TR_TZ).strftime("%m-%d %H:%M")
        ct = t.close_time.astimezone(TR_TZ).strftime("%m-%d %H:%M") if t.close_time else "-"
        print(f"{et:<19} {t.symbol:<14} {t.side:<5} "
              f"{t.score:<5.0f} {t.rvol:<5.2f} "
              f"{t.entry_price:<11.6f} "
              f"{(t.close_price or 0):<11.6f} "
              f"{t.pnl_pct:<+7.2f} {t.pnl:<+8.2f} {t.close_reason}")

    # CSV yaz
    out = pd.DataFrame([{
        "entry_time_tr": t.entry_time.astimezone(TR_TZ).isoformat(),
        "close_time_tr": t.close_time.astimezone(TR_TZ).isoformat() if t.close_time else None,
        "symbol": t.symbol, "side": t.side, "score": t.score, "rvol": t.rvol,
        "entry": t.entry_price, "stop": t.stop_price,
        "close": t.close_price, "reason": t.close_reason,
        "pnl_pct_lev": t.pnl_pct, "pnl_usd": t.pnl,
    } for t in closed])
    out_path = f"backtest_window_{start_utc.strftime('%Y%m%d_%H%M')}.csv"
    out.to_csv(out_path, index=False)
    print(f"\nCSV yazildi: {out_path}")

    # -------------------- TELEGRAM SAATLIK RAPOR --------------------
    print("\n[5/5] Saatlik rapor Telegram'a gonderiliyor...")
    send_telegram(
        f"🧪 <b>BACKTEST BASLADI</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {start_tr} - {end_tr} (TR)\n"
        f"📦 Sinyal: {len(closed)} | Marjin: ${MARGIN_USDT}x{LEVERAGE}\n"
        f"💰 Toplam PnL: <b>${total_pnl:+.2f}</b>\n"
        f"✅ Win rate: {win_rate:.1f}% ({len(wins)}W/{len(losses)}L)"
    )

    hour = start_utc.replace(minute=0, second=0, microsecond=0)
    while hour < end_utc:
        h_end = hour + timedelta(hours=1)
        # Bu saatte acilan trade'ler
        in_hour = [t for t in closed if hour <= t.entry_time < h_end]

        hour_tr = hour.astimezone(TR_TZ).strftime("%d.%m %H:%M")
        h_end_tr = h_end.astimezone(TR_TZ).strftime("%H:%M")

        if not in_hour:
            send_telegram(
                f"⏰ <b>{hour_tr} - {h_end_tr} (TR)</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Sinyal yok."
            )
            hour = h_end
            time.sleep(1.5)  # rate limit
            continue

        h_pnl = sum(t.pnl for t in in_hour)
        h_wins = [t for t in in_hour if t.pnl > 0]
        h_losses = [t for t in in_hour if t.pnl <= 0]
        h_wr = (len(h_wins) / len(in_hour) * 100) if in_hour else 0

        lines = [
            f"⏰ <b>{hour_tr} - {h_end_tr} (TR)</b>",
            f"━━━━━━━━━━━━━━━━━━",
            f"📦 Acilan: {len(in_hour)} | ✅ {len(h_wins)}W ❌ {len(h_losses)}L",
            f"💰 PnL: <b>${h_pnl:+.2f}</b> | WR: {h_wr:.0f}%",
            f"━━━━━━━━━━━━━━━━━━",
        ]
        for t in in_hour:
            et = t.entry_time.astimezone(TR_TZ).strftime("%H:%M")
            ct = t.close_time.astimezone(TR_TZ).strftime("%H:%M") if t.close_time else "-"
            emoji = "🟢" if t.side == "BUY" else "🔴"
            res = "✅" if t.pnl > 0 else "❌"
            lines.append(
                f"{emoji} <b>{t.symbol}</b> {t.side} skor:{t.score:.0f} "
                f"rvol:{t.rvol:.1f}\n"
                f"   {et}→{ct} {t.entry_price:.6f}→{(t.close_price or 0):.6f}\n"
                f"   {res} <b>${t.pnl:+.2f}</b> (%{t.pnl_pct:+.2f}) [{t.close_reason}]"
            )
        # Telegram mesaj limiti 4096; guvenli parcalayalim
        msg = "\n".join(lines)
        if len(msg) > 3800:
            # Her isleme bir mesaj
            send_telegram("\n".join(lines[:5]))
            time.sleep(1)
            chunk = []
            cur_len = 0
            for line in lines[5:]:
                if cur_len + len(line) > 3800:
                    send_telegram("\n".join(chunk))
                    time.sleep(1)
                    chunk = []
                    cur_len = 0
                chunk.append(line)
                cur_len += len(line) + 1
            if chunk:
                send_telegram("\n".join(chunk))
        else:
            send_telegram(msg)

        time.sleep(1.5)
        hour = h_end

    # Final ozet
    send_telegram(
        f"🏁 <b>BACKTEST BITTI</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 Toplam sinyal: {len(closed)}\n"
        f"✅ {len(wins)}W ❌ {len(losses)}L | WR: {win_rate:.1f}%\n"
        f"💰 Toplam PnL: <b>${total_pnl:+.2f}</b>\n"
        f"📄 CSV: {out_path}"
    )
    print("Telegram raporu tamam.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START_TR,
                    help=f"TR baslangic (default: {DEFAULT_START_TR})")
    ap.add_argument("--end", default=DEFAULT_END_TR,
                    help=f"TR bitis (default: {DEFAULT_END_TR})")
    args = ap.parse_args()
    run(args.start, args.end)


if __name__ == "__main__":
    main()
