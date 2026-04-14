from __future__ import annotations
"""
SHORT Strategy v1 - Bagimsiz SHORT pozisyon analiz motoru.

5 BILESEN (0-100 puan):
  25 - Trend Yapisi   (EMA stack, VWAP, DI yon)
  20 - Momentum       (RS vs BTC, 1h fiyat hareketi)
  20 - Distribution   (Kirmizi/yesil hacim orani, RVOL onayi)
  20 - Bounce Filtre  (RSI sweet spot, fitil analizi)
  15 - Trend Gucu     (ADX)

PENALTY:
  -15  RSI < 30 (asiri satim - bounce riski)
  -10  24h dususte > %18 (parabolik dusus, gec)
  -10  Son mum buyuk alt fitil (hammer reddi)

Esik:
  >= 70  -> SHORT ac
  >= 75  -> BULL piyasada SHORT ac (ekstra katilik)
"""

import math
import pandas as pd
from logger_setup import setup_logger

logger = setup_logger("ShortStrategy")


def _safe(value, default=0.0):
    """NaN/Inf temizleme."""
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def analyze_short_setup(df: pd.DataFrame, btc_perf_1h: float = 0.0,
                         price_change_24h: float = 0.0,
                         funding_rate: float = 0.0) -> dict:
    """
    Bir coin icin SHORT skor ve sinyalleri hesapla.

    Args:
        df: Indikatorler hesaplanmis dataframe
        btc_perf_1h: BTC 1h degisim (RS icin)
        price_change_24h: Coin 24h degisim (parabolik dusus penalty icin)
        funding_rate: Anlik funding (decimal). Pozitif (longlar oduyor) -> SHORT BONUS
                      Negatif (shortlar oduyor) -> SHORT CEZA (kalabalik short)

    Returns:
        {
            "score": 0-100,
            "signals": [str],
            "components": {component: points},
            "verdict": "STRONG_SHORT" | "SHORT" | "WEAK" | "NONE",
        }
    """
    if df is None or df.empty or len(df) < 50:
        return {"score": 0, "signals": ["insufficient_data"], "components": {},
                "verdict": "NONE"}

    score = 0
    signals = []
    components = {"trend": 0, "momentum": 0, "distribution": 0,
                  "bounce_filter": 0, "adx": 0, "funding": 0, "penalty": 0}

    # Veri cek
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

    # EMA stack: close < ema20 < ema50 (bearish align)
    if close < ema20 < ema50:
        trend_pts += 12
        signals.append("Bearish EMA stack")
    elif close < ema20:
        trend_pts += 6
        signals.append("Below EMA20")

    # VWAP altinda
    if close < vwap:
        trend_pts += 7
        signals.append("Below VWAP")

    # DI- > DI+ (downtrend yonu)
    if di_minus > di_plus and di_minus > 20:
        trend_pts += 6
        signals.append(f"DI- dominant ({di_minus:.0f}>{di_plus:.0f})")
    elif di_minus > di_plus:
        trend_pts += 3

    components["trend"] = min(trend_pts, 25)
    score += components["trend"]

    # ============================================
    # 2. MOMENTUM (20 puan)
    # ============================================
    mom_pts = 0

    # 1h performans
    if len(df) >= 20:
        c20 = _safe(df["close"].iloc[-20], close)
        perf_1h = ((close - c20) / c20) * 100 if c20 > 0 else 0
    else:
        perf_1h = 0

    # Relative strength vs BTC
    rs = perf_1h - btc_perf_1h

    if rs <= -2.5:
        mom_pts += 12
        signals.append(f"Very weak vs BTC (RS:{rs:.1f})")
    elif rs <= -1.5:
        mom_pts += 8
        signals.append(f"Weak vs BTC (RS:{rs:.1f})")
    elif rs <= -0.5:
        mom_pts += 4

    # 1h dusus hizi
    if perf_1h <= -2.5:
        mom_pts += 8
        signals.append(f"Falling fast (1h:%{perf_1h:.1f})")
    elif perf_1h <= -1.0:
        mom_pts += 4

    components["momentum"] = min(mom_pts, 20)
    score += components["momentum"]

    # ============================================
    # 3. DISTRIBUTION (20 puan)
    # Son 10 mumda kirmizi mum hacmi yesil mum hacminden fazla mi
    # ============================================
    dist_pts = 0
    last_10 = df.tail(10)
    red_mask = last_10["close"] < last_10["open"]
    red_vol = float(last_10.loc[red_mask, "volume"].sum())
    green_vol = float(last_10.loc[~red_mask, "volume"].sum())

    if green_vol > 0:
        dist_ratio = red_vol / green_vol
    else:
        dist_ratio = 99.0 if red_vol > 0 else 0.0

    if dist_ratio >= 2.5:
        dist_pts += 12
        signals.append(f"Heavy distribution ({dist_ratio:.1f}x)")
    elif dist_ratio >= 1.6:
        dist_pts += 8
        signals.append(f"Distribution ({dist_ratio:.1f}x)")
    elif dist_ratio >= 1.2:
        dist_pts += 4

    # RVOL onayi - son mum kirmizi VE hacim yuksek
    current_vol = _safe(df["volume"].iloc[-1])
    avg_vol_20 = _safe(df["volume"].tail(20).mean())
    rvol = current_vol / avg_vol_20 if avg_vol_20 > 0 else 0

    last_open = _safe(df["open"].iloc[-1])
    last_red = close < last_open

    if rvol >= 2.0 and last_red:
        dist_pts += 8
        signals.append(f"Volume spike on red ({rvol:.1f}x)")
    elif rvol >= 1.5 and last_red:
        dist_pts += 4

    components["distribution"] = min(dist_pts, 20)
    score += components["distribution"]

    # ============================================
    # 4. BOUNCE FILTRE (20 puan)
    # RSI sweet spot + fitil analizi
    # ============================================
    bf_pts = 0

    # RSI sweet spot: ne asiri satim, ne asiri alim
    if 40 <= rsi <= 58:
        bf_pts += 10
        signals.append(f"RSI sweet ({rsi:.0f})")
    elif 35 <= rsi <= 62:
        bf_pts += 5

    # Son 3 mum fitil analizi (alt fitil = bounce, ust fitil = saticilar)
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

        # Ust fitiller var = saticilar zirvelerde aktif
        if top_ratio >= 0.8 and top_ratio > bot_ratio * 1.5:
            bf_pts += 10
            signals.append("Top wicks (sellers active)")
        elif top_ratio > bot_ratio:
            bf_pts += 5

    components["bounce_filter"] = min(bf_pts, 20)
    score += components["bounce_filter"]

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
    # FUNDING RATE BONUSU/CEZASI (SHORT perspektifi)
    # Pozitif funding (longlar oduyor) = SHORT icin contrarian firsati
    # Negatif funding (shortlar oduyor) = SHORT icin pahalli giris
    # ============================================
    funding_pts = 0
    if funding_rate >= 0.001:         # >%0.1: kalabalik long, guclu SHORT bonus
        funding_pts = 12
        signals.append(f"Funding cok pozitif (%{funding_rate*100:+.3f}) - SHORT bonusu")
    elif funding_rate >= 0.0005:      # %0.05 ile %0.1: orta bonus
        funding_pts = 7
        signals.append(f"Funding pozitif (%{funding_rate*100:+.3f})")
    elif funding_rate >= 0:           # %0 ile %0.05: hafif bonus
        funding_pts = 3
    elif funding_rate >= -0.0005:     # %0 ile %-0.05: notr
        funding_pts = 0
    elif funding_rate >= -0.001:      # %-0.05 ile %-0.1: hafif ceza
        funding_pts = -5
        signals.append(f"Funding negatif (%{funding_rate*100:+.3f}) - SHORT pahalli")
    else:                              # <-0.1%: agir ceza
        funding_pts = -12
        signals.append(f"Funding cok negatif (%{funding_rate*100:+.3f}) - SHORT riskli")

    components["funding"] = funding_pts
    score += funding_pts

    # ============================================
    # PENALTY
    # ============================================
    penalty = 0

    # RSI oversold = bounce riski
    if rsi < 30:
        penalty -= 15
        signals.append(f"OVERSOLD penalty ({rsi:.0f})")
    elif rsi < 35:
        penalty -= 5

    # 24h zaten cok dustu - parabolik, gec
    if price_change_24h <= -18:
        penalty -= 10
        signals.append(f"Already crashed ({price_change_24h:.0f}%/24h)")
    elif price_change_24h <= -12:
        penalty -= 5

    # Son mumda buyuk alt fitil = hammer reddi
    last_o = _safe(df["open"].iloc[-1])
    last_h = _safe(df["high"].iloc[-1])
    last_l = _safe(df["low"].iloc[-1])
    last_body = abs(close - last_o)
    last_bot_wick = min(close, last_o) - last_l
    if last_body > 0 and last_bot_wick / last_body >= 1.5:
        penalty -= 10
        signals.append("Hammer rejection (last candle)")

    components["penalty"] = penalty
    score += penalty

    # Clamp
    score = max(0, min(100, score))

    # Verdict
    if score >= 75:
        verdict = "STRONG_SHORT"
    elif score >= 65:
        verdict = "SHORT"
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
        "dist_ratio": round(dist_ratio, 2),
    }


def setup_to_score_dict(setup: dict) -> dict:
    """
    short_strategy ciktisini CANSLIM uyumlu score dict'e cevir.
    Telegram rapor + journal icin gerekli.
    """
    comps = setup.get("components", {})
    return {
        "score": setup.get("score", 0),
        "decision": setup.get("verdict", "SHORT"),
        "components": {
            # CANSLIM bileseni -> SHORT karsiligi (0-100 olcekli)
            "C": min(100, int(comps.get("momentum", 0) * 5)),       # Momentum
            "A": min(100, int(comps.get("adx", 0) * 6.66)),         # Volatilite/Trend gucu
            "V": min(100, int(comps.get("distribution", 0) * 5)),   # Hacim
            "S": min(100, int(comps.get("bounce_filter", 0) * 5)),  # Bounce filtre
            "L": 50,
            "M": 50,
            "T": min(100, int(comps.get("trend", 0) * 4)),          # Trend yapisi
        },
        "allocation_pct": 10,
        "short_signals": setup.get("signals", []),
    }


def should_open_short(setup: dict, market_mood: str = "NOTR") -> bool:
    """
    SHORT acilmali mi karari (mood-aware esik).
      BEAR piyasa : >= 58  (tum market dusuyor, SHORT'a ruzgar)
      NOTR piyasa : >= 65
      BULL piyasa : >= 72  (akintiya karsi, sert esik)
    """
    score = setup.get("score", 0)
    if market_mood == "BULL":
        return score >= 72
    if market_mood == "BEAR":
        return score >= 58
    return score >= 65
