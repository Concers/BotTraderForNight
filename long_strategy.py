from __future__ import annotations
"""
LONG Strategy v1 - Bagimsiz LONG pozisyon analiz motoru.
short_strategy.py'nin simetrigi.

5 BILESEN (0-100 puan):
  25 - Trend Yapisi   (EMA stack yukari, VWAP ustu, DI+ dominant)
  20 - Momentum       (RS vs BTC pozitif, 1h yukseliş)
  20 - Accumulation   (Yesil/kirmizi hacim orani, RVOL onayi)
  20 - Pullback Filtre(RSI sweet spot, fitil analizi)
  15 - Trend Gucu     (ADX)

PENALTY:
  -15  RSI > 75 (asiri alim - yorgun)
  -10  24h yukselişte > %25 (parabolik, gec)
  -10  Son mum buyuk ust fitil (saticilar dirence basladi)

Esik (mood-aware):
  BULL piyasa : >= 58  (tum market yukseliyor, LONG'a ruzgar)
  NOTR piyasa : >= 65
  BEAR piyasa : >= 72  (akintiya karsi, sert)
"""

import math
import pandas as pd
from logger_setup import setup_logger

logger = setup_logger("LongStrategy")


def _safe(value, default=0.0):
    """NaN/Inf temizleme."""
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def analyze_long_setup(df: pd.DataFrame, btc_perf_1h: float = 0.0,
                        price_change_24h: float = 0.0,
                        funding_rate: float = 0.0) -> dict:
    """
    Bir coin icin LONG skor ve sinyalleri hesapla.

    funding_rate: Anlik funding rate (decimal, orn 0.001 = %0.1)
      Negatif (shortlar oduyor) -> LONG icin BONUS (kalabalik short, contrarian firsat)
      Pozitif (longlar oduyor)  -> LONG icin CEZA (long pahalli)

    Returns:
        {
            "score": 0-100,
            "signals": [str],
            "components": {component: points},
            "verdict": "STRONG_LONG" | "LONG" | "WEAK" | "NONE",
        }
    """
    if df is None or df.empty or len(df) < 50:
        return {"score": 0, "signals": ["insufficient_data"], "components": {},
                "verdict": "NONE"}

    score = 0
    signals = []
    components = {"trend": 0, "momentum": 0, "accumulation": 0,
                  "pullback_filter": 0, "adx": 0, "funding": 0, "penalty": 0}

    close = _safe(df["close"].iloc[-1])
    if close <= 0:
        return {"score": 0, "signals": ["invalid_price"], "components": {},
                "verdict": "NONE"}

    ema20 = _safe(df["ema_20"].iloc[-1], close)
    ema50 = _safe(df["ema_50"].iloc[-1], close)
    vwap = _safe(df["vwap"].iloc[-1], close)
    rsi = _safe(df["rsi"].iloc[-1], 50)
    adx = _safe(df["adx"].iloc[-1], 0)
    di_plus = _safe(df["di_plus"].iloc[-1], 0) if "di_plus" in df.columns else 0
    di_minus = _safe(df["di_minus"].iloc[-1], 0) if "di_minus" in df.columns else 0

    # ============================================
    # 1. TREND YAPISI (25 puan)
    # ============================================
    trend_pts = 0

    # EMA stack: close > ema20 > ema50 (bullish align)
    if close > ema20 > ema50:
        trend_pts += 12
        signals.append("Bullish EMA stack")
    elif close > ema20:
        trend_pts += 6
        signals.append("Above EMA20")

    # VWAP ustunde
    if close > vwap:
        trend_pts += 7
        signals.append("Above VWAP")

    # DI+ > DI- (uptrend yonu)
    if di_plus > di_minus and di_plus > 20:
        trend_pts += 6
        signals.append(f"DI+ dominant ({di_plus:.0f}>{di_minus:.0f})")
    elif di_plus > di_minus:
        trend_pts += 3

    components["trend"] = min(trend_pts, 25)
    score += components["trend"]

    # ============================================
    # 2. MOMENTUM (20 puan)
    # ============================================
    mom_pts = 0

    if len(df) >= 20:
        c20 = _safe(df["close"].iloc[-20], close)
        perf_1h = ((close - c20) / c20) * 100 if c20 > 0 else 0
    else:
        perf_1h = 0

    rs = perf_1h - btc_perf_1h

    if rs >= 2.5:
        mom_pts += 12
        signals.append(f"Very strong vs BTC (RS:+{rs:.1f})")
    elif rs >= 1.5:
        mom_pts += 8
        signals.append(f"Strong vs BTC (RS:+{rs:.1f})")
    elif rs >= 0.5:
        mom_pts += 4

    if perf_1h >= 2.5:
        mom_pts += 8
        signals.append(f"Rising fast (1h:+%{perf_1h:.1f})")
    elif perf_1h >= 1.0:
        mom_pts += 4

    components["momentum"] = min(mom_pts, 20)
    score += components["momentum"]

    # ============================================
    # 3. ACCUMULATION (20 puan)
    # Son 10 mumda yesil hacim > kirmizi hacim
    # ============================================
    acc_pts = 0
    last_10 = df.tail(10)
    green_mask = last_10["close"] >= last_10["open"]
    green_vol = float(last_10.loc[green_mask, "volume"].sum())
    red_vol = float(last_10.loc[~green_mask, "volume"].sum())

    if red_vol > 0:
        acc_ratio = green_vol / red_vol
    else:
        acc_ratio = 99.0 if green_vol > 0 else 0.0

    if acc_ratio >= 2.5:
        acc_pts += 12
        signals.append(f"Heavy accumulation ({acc_ratio:.1f}x)")
    elif acc_ratio >= 1.6:
        acc_pts += 8
        signals.append(f"Accumulation ({acc_ratio:.1f}x)")
    elif acc_ratio >= 1.2:
        acc_pts += 4

    # RVOL onayi - son mum yesil VE hacim yuksek
    current_vol = _safe(df["volume"].iloc[-1])
    avg_vol_20 = _safe(df["volume"].tail(20).mean())
    rvol = current_vol / avg_vol_20 if avg_vol_20 > 0 else 0

    last_open = _safe(df["open"].iloc[-1])
    last_green = close > last_open

    if rvol >= 2.0 and last_green:
        acc_pts += 8
        signals.append(f"Volume spike on green ({rvol:.1f}x)")
    elif rvol >= 1.5 and last_green:
        acc_pts += 4

    components["accumulation"] = min(acc_pts, 20)
    score += components["accumulation"]

    # ============================================
    # 4. PULLBACK FILTRE (20 puan)
    # ============================================
    pf_pts = 0

    # RSI sweet spot
    if 42 <= rsi <= 65:
        pf_pts += 10
        signals.append(f"RSI sweet ({rsi:.0f})")
    elif 38 <= rsi <= 70:
        pf_pts += 5

    # Son 3 mum fitil analizi (alt fitil = alicilar destekledi, ust fitil = saticilar)
    last_3 = df.tail(3)
    total_top_wick = 0.0
    total_bot_wick = 0.0
    total_body = 0.0
    for _, row in last_3.iterrows():
        o = _safe(row["open"])
        c = _safe(row["close"])
        h = _safe(row["high"])
        l = _safe(row["low"])
        body = abs(c - o)
        top = h - max(c, o)
        bot = min(c, o) - l
        total_body += body
        total_top_wick += top
        total_bot_wick += bot

    if total_body > 0:
        top_ratio = total_top_wick / total_body
        bot_ratio = total_bot_wick / total_body

        # Alt fitiller var = alicilar dipleri savunuyor
        if bot_ratio >= 0.8 and bot_ratio > top_ratio * 1.5:
            pf_pts += 10
            signals.append("Bottom wicks (buyers active)")
        elif bot_ratio > top_ratio:
            pf_pts += 5

    components["pullback_filter"] = min(pf_pts, 20)
    score += components["pullback_filter"]

    # ============================================
    # 5. ADX TREND GUCU (15 puan)
    # ============================================
    adx_pts = 0
    if adx >= 30:
        adx_pts = 15
        signals.append(f"Strong trend (ADX:{adx:.0f})")
    elif adx >= 25:
        adx_pts = 10
        signals.append(f"Trend ok (ADX:{adx:.0f})")
    elif adx >= 20:
        adx_pts = 5

    components["adx"] = adx_pts
    score += adx_pts

    # ============================================
    # FUNDING RATE BONUSU/CEZASI (LONG perspektifi)
    # Negatif funding (shortlar oduyor) = LONG icin contrarian firsati
    # Pozitif funding (longlar oduyor) = LONG icin pahalli giris
    # ============================================
    funding_pts = 0
    if funding_rate <= -0.001:        # <%-0.1: kalabalik short, guclu LONG bonus
        funding_pts = 12
        signals.append(f"Funding cok negatif (%{funding_rate*100:+.3f}) - LONG bonusu")
    elif funding_rate <= -0.0005:     # %-0.05 ile %-0.1: orta bonus
        funding_pts = 7
        signals.append(f"Funding negatif (%{funding_rate*100:+.3f})")
    elif funding_rate <= 0:           # %0 ile %-0.05: hafif bonus
        funding_pts = 3
    elif funding_rate <= 0.0005:      # %0 ile %0.05: notr
        funding_pts = 0
    elif funding_rate <= 0.001:       # %0.05 ile %0.1: hafif ceza
        funding_pts = -5
        signals.append(f"Funding pozitif (%{funding_rate*100:+.3f}) - LONG pahalli")
    else:                              # >%0.1: agir ceza
        funding_pts = -12
        signals.append(f"Funding cok pozitif (%{funding_rate*100:+.3f}) - LONG riskli")

    components["funding"] = funding_pts
    score += funding_pts

    # ============================================
    # PENALTY
    # ============================================
    penalty = 0

    # RSI overbought = late entry
    if rsi > 75:
        penalty -= 15
        signals.append(f"OVERBOUGHT penalty ({rsi:.0f})")
    elif rsi > 70:
        penalty -= 5

    # 24h zaten cok yukseldi - parabolik, gec
    if price_change_24h >= 25:
        penalty -= 10
        signals.append(f"Already pumped ({price_change_24h:.0f}%/24h)")
    elif price_change_24h >= 15:
        penalty -= 5

    # Son mumda buyuk ust fitil = direnc reddi
    last_o = _safe(df["open"].iloc[-1])
    last_h = _safe(df["high"].iloc[-1])
    last_l = _safe(df["low"].iloc[-1])
    last_body = abs(close - last_o)
    last_top_wick = last_h - max(close, last_o)
    if last_body > 0 and last_top_wick / last_body >= 1.5:
        penalty -= 10
        signals.append("Top wick rejection (last candle)")

    components["penalty"] = penalty
    score += penalty

    # Clamp
    score = max(0, min(100, score))

    # Verdict
    if score >= 75:
        verdict = "STRONG_LONG"
    elif score >= 65:
        verdict = "LONG"
    elif score >= 50:
        verdict = "WEAK"
    else:
        verdict = "NONE"

    return {
        "score": int(round(score)),
        "signals": signals,
        "components": components,
        "verdict": verdict,
        "rs": round(rs, 2),
        "perf_1h": round(perf_1h, 2),
        "rvol": round(rvol, 2),
        "acc_ratio": round(acc_ratio, 2),
    }


def setup_to_score_dict(setup: dict) -> dict:
    """LONG setup'i CANSLIM uyumlu score dict'e cevir."""
    comps = setup.get("components", {})
    return {
        "score": setup.get("score", 0),
        "decision": setup.get("verdict", "LONG"),
        "components": {
            "C": min(100, int(comps.get("momentum", 0) * 5)),
            "A": min(100, int(comps.get("adx", 0) * 6.66)),
            "V": min(100, int(comps.get("accumulation", 0) * 5)),
            "S": min(100, int(comps.get("pullback_filter", 0) * 5)),
            "L": 50,
            "M": 50,
            "T": min(100, int(comps.get("trend", 0) * 4)),
        },
        "allocation_pct": 10,
        "long_signals": setup.get("signals", []),
    }


def should_open_long(setup: dict, market_mood: str = "NOTR") -> bool:
    """
    LONG acilmali mi karari (mood-aware esik).
      BULL piyasa : >= 58  (tum market yukseliyor, LONG'a ruzgar)
      NOTR piyasa : >= 65
      BEAR piyasa : >= 72  (akintiya karsi, sert esik)
    """
    score = setup.get("score", 0)
    if market_mood == "BEAR":
        return score >= 72
    if market_mood == "BULL":
        return score >= 58
    return score >= 65
