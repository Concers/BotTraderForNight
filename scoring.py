import pandas as pd
import numpy as np
from logger_setup import setup_logger, setup_rejected_logger
import config

logger = setup_logger("Scoring")
rejected_logger = setup_rejected_logger()


class CANSLIMScorer:
    """
    Hibrit CANSLIM + V2 Analiz skorlama motoru.

    C - Current Momentum (RSI + Fiyat ivmesi)     : %15
    A - ATR / Volatility                           : %10
    V - Volume (Spike + istikrar)                  : %15
    S - Supply/Demand (Mum yapi analizi)           : %10
    L - Leadership / Relative Strength             : %15
    M - Market Context (BTC trendi)                : %5
    T - Trend Istikrari (100 mum UT Bot + VWAP)    : %30  ← YENI
    """

    WEIGHTS = {
        "C": 0.15,  # Momentum
        "A": 0.10,  # Volatilite
        "V": 0.10,  # Hacim (dusuruldu - sessiz birikme icin)
        "S": 0.05,  # Arz/Talep
        "L": 0.10,  # Relative Strength
        "M": 0.05,  # Market (dusuk - coin bagimsiz olabilir)
        "T": 0.45,  # Trend istikrari - EN ONEMLI (VWAP+UT+EMA+Birikme)
    }

    def score_current_momentum(self, df: pd.DataFrame) -> float:
        """C - RSI ve fiyat ivmesi skoru (0-100)."""
        rsi = df["rsi"].iloc[-1]
        if pd.isna(rsi):
            return 0

        # RSI 35-65 arasi genis aralik (yavas yukselisi de yakala)
        if 40 <= rsi <= 60:
            rsi_score = 100
        elif 35 <= rsi < 40 or 60 < rsi <= 70:
            rsi_score = 75
        elif 30 <= rsi < 35 or 70 < rsi <= 75:
            rsi_score = 50
        else:
            rsi_score = 20

        # Son 5 mum fiyat ivmesi
        closes = df["close"].tail(5).values
        momentum = ((closes[-1] - closes[0]) / closes[0]) * 100
        mom_score = min(100, max(0, 50 + momentum * 20))

        return (rsi_score * 0.6 + mom_score * 0.4)

    def score_volatility(self, df: pd.DataFrame) -> float:
        """A - ATR / Volatilite skoru (0-100). Dusuk vol = sakin trend, yuksek vol = firsat."""
        atr = df["atr"].iloc[-1]
        close = df["close"].iloc[-1]
        if pd.isna(atr):
            return 0

        vol_pct = (atr / close) * 100

        # Hem dusuk hem yuksek volatilite kabul, sadece asiri uclar dusuk skor
        if 0.3 <= vol_pct <= 3.0:
            return 80  # Normal aralik - iyi
        elif 0.1 <= vol_pct < 0.3:
            return 60  # Cok sakin ama islem yapilabilir
        elif 3.0 < vol_pct <= 5.0:
            return 50  # Hircinlasma
        else:
            return 30  # Asiri uclar

    def score_volume(self, df: pd.DataFrame) -> float:
        """V - Hacim skoru (spike + istikrar)."""
        ratio = df["volume_ratio"].iloc[-1]
        if pd.isna(ratio):
            return 30

        # Spike varsa bonus, yoksa istikrarli hacim de yeterli
        if df["volume_spike"].iloc[-1]:
            if ratio >= 3.0:
                return 100
            elif ratio >= 2.0:
                return 90
            elif ratio >= 1.5:
                return 80
            else:
                return 70

        # Spike yok ama hacim varsa (ort. yakininda)
        if ratio >= 0.8:
            return 60  # Normal hacim - kabul edilebilir
        elif ratio >= 0.5:
            return 40  # Dusuk hacim
        else:
            return 20  # Cok dusuk

    def score_supply_demand(self, df: pd.DataFrame) -> float:
        """S - Son 5 mumun genel yapisi (tek mum yerine)."""
        last5 = df.tail(5)
        green_count = (last5["close"] > last5["open"]).sum()
        total_body = 0
        total_range = 0

        for _, row in last5.iterrows():
            body = abs(row["close"] - row["open"])
            candle_range = row["high"] - row["low"]
            total_body += body
            total_range += candle_range

        if total_range == 0:
            return 50

        body_ratio = total_body / total_range

        # 5 mumun cogu yesil + guclu govdeler = alici hakimiyeti
        if green_count >= 4 and body_ratio > 0.5:
            return 95
        elif green_count >= 3 and body_ratio > 0.4:
            return 75
        elif green_count >= 3:
            return 60
        elif green_count >= 2:
            return 45
        else:
            return 25

    def score_leadership(self, df: pd.DataFrame, btc_df: pd.DataFrame = None) -> float:
        """L - Relative Strength (coin vs BTC)."""
        # 3dk mumlardan ~20 tane = 1 saat, 5dk mumlardan ~12 = 1 saat
        lookback = min(20, len(df) - 1)
        closes = df["close"].tail(lookback).values
        if len(closes) < 5:
            return 50

        coin_perf = ((closes[-1] - closes[0]) / closes[0]) * 100

        if btc_df is not None and len(btc_df) >= lookback:
            btc_closes = btc_df["close"].tail(lookback).values
            btc_perf = ((btc_closes[-1] - btc_closes[0]) / btc_closes[0]) * 100
            rs = coin_perf - btc_perf
        else:
            rs = coin_perf

        # RS skorlama - daha genis aralik
        if rs > 3.0:
            return 100
        elif rs > 1.5:
            return 85
        elif rs > 0.5:
            return 70
        elif rs > 0:
            return 55
        elif rs > -0.5:
            return 45
        elif rs > -1.5:
            return 30
        else:
            return 15

    def score_market_context(self, btc_df: pd.DataFrame = None) -> float:
        """M - BTC genel trend. Agirlik dusuk (%5) - coin guclu olabilir."""
        if btc_df is None or len(btc_df) < 20:
            return 50

        ema20 = btc_df["close"].ewm(span=20).mean().iloc[-1]
        current = btc_df["close"].iloc[-1]

        diff_pct = ((current - ema20) / ema20) * 100
        if diff_pct > 1.0:
            return 90
        elif diff_pct > 0:
            return 70
        elif diff_pct > -1.0:
            return 45  # Hafif dusus - notr
        else:
            return 25

    def score_trend_stability(self, df: pd.DataFrame) -> float:
        """
        T - 150 Mum Trend Istikrari (test.md V2 analizinden).
        3dk mumda 150 mum = ~7.5 saat, 5dk mumda = ~12.5 saat derinlik.
        UT Bot + VWAP + EMA egimi + Sessiz Birikme + Coklu zaman dilimi.
        """
        window = df.tail(150) if len(df) >= 150 else df
        n = len(window)
        score = 0

        # KURAL 1: UT Bot trend istikrari (150 mumda)
        if "trailing_stop" in window.columns:
            above_ts = (window["close"] > window["trailing_stop"]).sum()
            ratio = above_ts / n
            if ratio > 0.70:
                score += 20  # Cok guclu trend
            elif ratio > 0.55:
                score += 15
            elif ratio > 0.40:
                score += 10
            else:
                score += 3

        # KURAL 2: VWAP ustunde kalma (150 mum)
        vwap_ratio = 0
        if "vwap" in window.columns:
            above_vwap = (window["close"] > window["vwap"]).sum()
            vwap_ratio = above_vwap / n
            if vwap_ratio > 0.75:
                score += 20  # Baskici alim
            elif vwap_ratio > 0.60:
                score += 15
            elif vwap_ratio > 0.45:
                score += 8
            else:
                score += 3

        # KURAL 3: EMA20 egimi (uzun vadeli)
        if "ema_20" in window.columns:
            ema_vals = window["ema_20"].dropna()
            if len(ema_vals) >= 20:
                slope = (ema_vals.iloc[-1] - ema_vals.iloc[-20]) / ema_vals.iloc[-20] * 100
                if slope > 1.0:
                    score += 12  # Dik yukselis
                elif slope > 0.3:
                    score += 10
                elif slope > 0:
                    score += 8   # Hafif yukselis
                elif slope > -0.5:
                    score += 4
                else:
                    score += 1

        # KURAL 4: RSI ortalama (saglikli momentum)
        if "rsi" in window.columns:
            avg_rsi = window["rsi"].dropna().mean()
            if 45 <= avg_rsi <= 60:
                score += 8
            elif 40 <= avg_rsi <= 65:
                score += 6
            elif 35 <= avg_rsi <= 70:
                score += 4
            else:
                score += 1

        # KURAL 5: SESSIZ BIRIKME (Accumulation)
        if vwap_ratio > 0.60:
            price_range_pct = ((window["high"].max() - window["low"].min())
                               / window["close"].mean()) * 100
            vol_ratio = window["volume"].tail(15).mean() / window["volume"].mean()
            is_quiet = vol_ratio < 1.5

            if is_quiet and price_range_pct < 5:
                score += 20
                logger.debug(
                    f"Sessiz Birikme! VWAP={vwap_ratio:.0%}, "
                    f"Range=%{price_range_pct:.1f}, Vol={vol_ratio:.2f}x"
                )
            elif is_quiet and price_range_pct < 8:
                score += 12
            elif price_range_pct < 5:
                score += 8  # Dar aralik ama hacim var

        # KURAL 6: COKLU ZAMAN DILIMI TRENDI
        # Son 50 mum vs ilk 50 mum karsilastirmasi (yukselis trendi dogrulamasi)
        if n >= 100:
            first_half = window.head(n // 2)
            second_half = window.tail(n // 2)
            first_avg = first_half["close"].mean()
            second_avg = second_half["close"].mean()
            trend_pct = ((second_avg - first_avg) / first_avg) * 100

            if trend_pct > 1.5:
                score += 15  # Net yukselis trendi
            elif trend_pct > 0.5:
                score += 10
            elif trend_pct > 0:
                score += 6
            else:
                score += 2

        # KURAL 7: Higher Lows (Yukselen dipler) - trend sagligi
        if n >= 30:
            lows = window["low"].values
            chunk_size = n // 5
            dip_seviyeleri = []
            for i in range(5):
                chunk = lows[i * chunk_size:(i + 1) * chunk_size]
                if len(chunk) > 0:
                    dip_seviyeleri.append(chunk.min())

            if len(dip_seviyeleri) >= 3:
                higher_lows = sum(
                    1 for i in range(1, len(dip_seviyeleri))
                    if dip_seviyeleri[i] > dip_seviyeleri[i - 1]
                )
                if higher_lows >= 3:
                    score += 5  # Guclu yukselen dipler

        return min(100, score)

    def calculate_score(self, df: pd.DataFrame, symbol: str,
                        btc_df: pd.DataFrame = None) -> dict:
        """Toplam skoru hesapla."""
        components = {
            "C": self.score_current_momentum(df),
            "A": self.score_volatility(df),
            "V": self.score_volume(df),
            "S": self.score_supply_demand(df),
            "L": self.score_leadership(df, btc_df),
            "M": self.score_market_context(btc_df),
            "T": self.score_trend_stability(df),
        }

        total_score = sum(
            components[key] * self.WEIGHTS[key] for key in components
        )

        # Karar
        if total_score >= 90:
            decision = "HIGH_CONFIDENCE"
            allocation = 100
        elif total_score >= config.MIN_CONFIDENCE_SCORE:
            decision = "MODERATE_CONFIDENCE"
            allocation = 50
        else:
            decision = "REJECTED"
            allocation = 0
            reasons = []
            for key, val in components.items():
                if val < 50:
                    reasons.append(f"{key}={val:.0f}")
            rejected_logger.info(
                f"{symbol} | Skor: {total_score:.1f} | RED: {', '.join(reasons)}"
            )

        result = {
            "score": round(total_score, 1),
            "components": {k: round(v, 1) for k, v in components.items()},
            "decision": decision,
            "allocation_pct": allocation,
        }

        logger.info(
            f"{symbol} | Skor: {result['score']} | Karar: {decision} | "
            f"C={components['C']:.0f} A={components['A']:.0f} V={components['V']:.0f} "
            f"S={components['S']:.0f} L={components['L']:.0f} M={components['M']:.0f} "
            f"T={components['T']:.0f}"
        )

        return result
