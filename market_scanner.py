from __future__ import annotations
"""
Relative Market Scanner v2 - Piyasa Roentgeni

Her 5 dakikada TUM marketi tarar:
  1. Hacim filtresi (3M-15M$ 24h)
  2. 150 mum derin analiz
  3. Relative Strength (BTC'ye gore guc)
  4. Momentum skoru
  5. Whitelist/Blacklist otomatik guncelleme

Sonuc: Telegram'a "Market Roentgeni" paneli gonderir.
"""

import time
from datetime import datetime
from logger_setup import setup_logger
from binance_client import BinanceClient
from indicators import run_all_indicators, rsi_slope
from sector_mapping import SectorMapper
from scoring import CANSLIMScorer
from short_strategy import analyze_short_setup
from long_strategy import analyze_long_setup
import json
import os

logger = setup_logger("Scanner")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCANNER_FILE = os.path.join(DATA_DIR, "scanner_results.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")


def _weighted_momentum(rs: float, rsi: float, rvol: float,
                        side: str = "LONG") -> float:
    """
    Agirlikli momentum skoru: RS*0.4 + RSI*0.3 + RVOL*0.3 -> 0-100.

    LONG: RS pozitif, RSI 50-70, RVOL yuksek = ideal
    SHORT: RS negatif, RSI 30-50, RVOL yuksek = ideal

    Her bilesen 0-100 arasina normalize edilir, sonra ağırlıklandırılır.
    """
    # RS (-5 ile +5 arasi tipik) -> 0-100
    if side == "LONG":
        rs_norm = max(0, min(100, (rs + 3) * 20))   # RS=-3 -> 0, +3 -> 120, clamped 100
    else:
        rs_norm = max(0, min(100, (-rs + 3) * 20))  # RS=+3 -> 0, -3 -> 100

    # RSI (0-100) -> ideal bandin yakinligi
    if side == "LONG":
        # Ideal 50-70: 60'a yakinlik
        rsi_norm = max(0, min(100, 100 - abs(rsi - 60) * 2.5))
    else:
        # Ideal 30-50: 40'a yakinlik
        rsi_norm = max(0, min(100, 100 - abs(rsi - 40) * 2.5))

    # RVOL (0-3+ arasi) -> 0-100
    rvol_norm = max(0, min(100, rvol * 33))  # 3x -> 99, 1x -> 33

    score = rs_norm * 0.4 + rsi_norm * 0.3 + rvol_norm * 0.3
    return round(score, 1)


class CoinProfile:
    """Tek bir coinin roentgen sonucu."""

    def __init__(self, symbol: str, data: dict):
        self.symbol = symbol
        self.price = data.get("price", 0)
        self.volume_24h = data.get("volume_24h", 0)
        self.price_change_1h = data.get("price_change_1h", 0)
        self.price_change_24h = data.get("price_change_24h", 0)
        self.rsi = data.get("rsi", 50)
        self.adx = data.get("adx", 0)
        self.ut_signal = data.get("ut_signal", 0)
        self.vwap_position = data.get("vwap_position", "notr")
        self.trend_score = data.get("trend_score", 0)
        self.volume_ratio = data.get("volume_ratio", 0)
        self.rvol = data.get("rvol", 0)  # Relative Volume
        self.canslim_score = data.get("canslim_score", 0)
        self.relative_strength = data.get("relative_strength", 0)
        self.category = data.get("category", "NOTR")
        self.wick_ratio = data.get("wick_ratio", 0)
        self.short_score = data.get("short_score", 0)
        self.short_verdict = data.get("short_verdict", "NONE")
        self.short_signals = data.get("short_signals", [])
        self.short_setup = data.get("short_setup", {})
        self.long_score = data.get("long_score", 0)
        self.long_verdict = data.get("long_verdict", "NONE")
        self.long_signals = data.get("long_signals", [])
        self.long_setup = data.get("long_setup", {})
        # BTC korelasyon flag: "GERCEK" (tek basina), "SURU" (birlikte hareket), ""
        self.correlation_tag = data.get("correlation_tag", "")
        # Agirlikli momentum skorlari (RS*0.4 + RSI*0.3 + RVOL*0.3), 0-100
        self.long_momentum = data.get("long_momentum", 50.0)
        self.short_momentum = data.get("short_momentum", 50.0)
        # Sektor (CoinGecko: L1, DeFi, AI, Meme, Gaming vb.)
        self.sector = data.get("sector", "Diger")
        # Anlik funding rate (kontrarian filtre) - pozitif: LONG pahalli, negatif: SHORT pahalli
        self.funding_rate = data.get("funding_rate", 0.0)
        # Onceki taramada STRONG_BUY ise "long", STRONG_SELL ise "short", yoksa None
        self.previously_tracked = data.get("previously_tracked", None)
        # Coklu TF RSI - 5m RSI mevcut "rsi" (3m mumdan), 1m/3m ayri
        self.rsi_5m = data.get("rsi_5m", 50.0)
        self.rsi_3m = data.get("rsi_3m", 50.0)
        self.rsi_1m = data.get("rsi_1m", 50.0)
        # RSI egimi (son 3 bar) - negatif: momentum kaybi
        self.rsi_slope_3m = data.get("rsi_slope_3m", 0.0)
        self.rsi_slope_1m = data.get("rsi_slope_1m", 0.0)
        # Divergence flag: rsi_5m - rsi_1m > 15 ise True (sahte pump)
        self.momentum_divergence = data.get("momentum_divergence", False)


class MarketScanner:
    """Piyasa tarayici - Relative Market v2."""

    def __init__(self, binance: BinanceClient):
        self.binance = binance
        self.scorer = CANSLIMScorer()
        self.results: list[CoinProfile] = []
        self.btc_perf_1h = 0.0
        self.btc_perf_24h = 0.0
        self.btc_rvol = 1.0  # BTC'nin son 3dk mumunun 20 mum avg'sine orani
        self.btc_rsi = 50.0
        self.market_mood = "NOTR"  # BULL, BEAR, NOTR
        self.scan_time = ""
        # Onceki taramadaki STRONG_BUY/STRONG_SELL coinleri hafizala (oncelik icin)
        self._prev_strong_buy: set[str] = set()
        self._prev_strong_sell: set[str] = set()
        # Funding rates cache (tum semboller tek cagride)
        self._funding_rates: dict[str, float] = {}
        # Sektor haritasi (CoinGecko, 7 gun cache)
        self.sector_map = SectorMapper()

    async def scan(self) -> dict:
        """Tam market taramasi yap."""
        start = time.time()
        self.scan_time = datetime.now().strftime("%H:%M")
        self.results = []

        logger.info("Market Scanner v2 baslatildi...")

        # Sektor cache (7 gun+ eskiyse yenilenir, normalde hizli no-op)
        try:
            self.sector_map.refresh_if_stale()
        except Exception as e:
            logger.debug(f"Sector refresh atlandi: {e}")

        # 1. BTC referans verisi (perf + RVOL + RSI)
        btc_df = self.binance.get_klines("BTCUSDT", interval="3m", limit=200)
        if not btc_df.empty:
            btc_df = run_all_indicators(btc_df)
            btc_closes = btc_df["close"].values
            self.btc_perf_1h = ((btc_closes[-1] - btc_closes[-20]) / btc_closes[-20]) * 100
            self.btc_perf_24h = ((btc_closes[-1] - btc_closes[0]) / btc_closes[0]) * 100

            # BTC RVOL (son mum hacmi / 20 mum avg)
            btc_vol_cur = float(btc_df["volume"].iloc[-1])
            btc_vol_avg = float(btc_df["volume"].tail(20).mean())
            self.btc_rvol = (btc_vol_cur / btc_vol_avg) if btc_vol_avg > 0 else 1.0

            # BTC RSI (korelasyon filtresinde kullanilabilir)
            self.btc_rsi = float(btc_df["rsi"].iloc[-1]) if "rsi" in btc_df.columns else 50.0

            if self.btc_perf_1h > 0.5:
                self.market_mood = "BULL"
            elif self.btc_perf_1h < -0.5:
                self.market_mood = "BEAR"
            else:
                self.market_mood = "NOTR"

            logger.info(
                f"BTC: Perf:%{self.btc_perf_1h:+.2f}/1h RVOL:x{self.btc_rvol:.2f} "
                f"RSI:{self.btc_rsi:.0f} Mood:{self.market_mood}"
            )

        # 2. Tum ticker'lari al
        try:
            tickers = self.binance.public_client.ticker_24hr_price_change()
        except Exception as e:
            logger.error(f"Ticker alinamadi: {e}")
            return {}

        # 2b. Tum funding rate'leri bir kerede cek
        self._funding_rates = self.binance.get_all_funding_rates()
        if self._funding_rates:
            logger.info(f"Funding rate: {len(self._funding_rates)} sembol yuklendi")

        # 3. Hacim filtresi: 3M-15M$ 24h hacim (45M-75M mcap hedef)
        candidates = []
        for t in tickers:
            sym = t["symbol"]
            if not sym.endswith("USDT"):
                continue
            vol = float(t.get("quoteVolume", 0))
            price = float(t.get("lastPrice", 0))
            change_24h = float(t.get("priceChangePercent", 0))

            if price > 0 and 500_000 <= vol <= 50_000_000:
                candidates.append({
                    "symbol": sym,
                    "price": price,
                    "volume_24h": vol,
                    "price_change_24h": change_24h,
                })

        logger.info(f"Hacim filtresi: {len(tickers)} -> {len(candidates)} aday")

        # 4. Derin analiz (top 100 hacimli)
        candidates.sort(key=lambda x: x["volume_24h"], reverse=True)
        analyze_list = candidates[:100]

        for coin in analyze_list:
            try:
                profile = await self._analyze_coin(coin, btc_df)
                if profile:
                    self.results.append(profile)
            except Exception as e:
                logger.debug(f"{coin['symbol']} analiz hatasi: {e}")

            await self._sleep(0.3)

        # 5. Sonuclari kategorize et
        self._categorize_results()

        # 5a2. BTC Korelasyon Analizi
        self._analyze_correlation()

        # 5b. Bu turun STRONG'larini bir sonraki tarama icin hafizala
        new_strong_buy: set[str] = set()
        new_strong_sell: set[str] = set()
        for r in self.results:
            if r.category == "STRONG_BUY":
                new_strong_buy.add(r.symbol)
            elif r.category == "STRONG_SELL":
                new_strong_sell.add(r.symbol)

        # 6. Kaydet
        self._save_results()

        duration = time.time() - start
        logger.info(
            f"Scanner tamamlandi: {len(self.results)} coin analiz edildi | "
            f"{duration:.0f}sn | Mood: {self.market_mood}"
        )

        # Bir sonraki turda "previously_tracked" icin sakla
        self._prev_strong_buy = new_strong_buy
        self._prev_strong_sell = new_strong_sell

        return self.get_summary()

    async def _analyze_coin(self, coin: dict, btc_df) -> CoinProfile | None:
        """Tek coin derin analiz + RVOL."""
        symbol = coin["symbol"]
        import math

        df = self.binance.get_klines(symbol, interval="3m", limit=200)
        if df.empty or len(df) < 50:
            return None

        # Olu coin filtresi: son 5 mumda hic hacim yoksa atla
        if df["volume"].tail(5).sum() == 0:
            return None

        df = run_all_indicators(df)

        close = df["close"].iloc[-1]
        vwap = df["vwap"].iloc[-1] if "vwap" in df.columns else close
        raw_rsi = df["rsi"].iloc[-1] if "rsi" in df.columns else 50
        raw_adx = df["adx"].iloc[-1] if "adx" in df.columns else 0
        ut = df["ut_signal"].iloc[-1] if "ut_signal" in df.columns else 0

        # NaN kontrolu (RSI 100 bug'inin cozumu)
        rsi = 50 if (isinstance(raw_rsi, float) and (math.isnan(raw_rsi) or math.isinf(raw_rsi))) else raw_rsi
        adx = 0 if (isinstance(raw_adx, float) and (math.isnan(raw_adx) or math.isinf(raw_adx))) else raw_adx

        # --- RVOL HESAPLA ---
        # Son mumun hacmi / son 20 mumun ortalama hacmi
        current_vol = df["volume"].iloc[-1]
        avg_vol_20 = df["volume"].tail(20).mean()
        rvol = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0

        # NaN kontrolu
        if math.isnan(rvol) or math.isinf(rvol):
            rvol = 0

        # Volume ratio (3 mumun ort / 20 mumun ort)
        raw_vol_ratio = df["volume_ratio"].iloc[-1] if "volume_ratio" in df.columns else 0
        vol_ratio = 0 if (isinstance(raw_vol_ratio, float) and math.isnan(raw_vol_ratio)) else raw_vol_ratio

        # 1 saatlik performans (20 x 3dk mum)
        closes = df["close"].tail(20).values
        perf_1h = ((closes[-1] - closes[0]) / closes[0]) * 100 if len(closes) >= 2 else 0

        # Relative Strength vs BTC
        rs = perf_1h - self.btc_perf_1h

        # Igne orani (risk gostergesi)
        last20 = df.tail(20)
        total_wick = 0
        total_body = 0
        for _, row in last20.iterrows():
            body = abs(row["close"] - row["open"])
            wick = (row["high"] - row["low"]) - body
            total_wick += wick
            total_body += body
        wick_ratio = (total_wick / (total_body + 0.0001)) * 100

        # CANSLIM skor
        score = self.scorer.calculate_score(
            df, symbol, btc_df if btc_df is not None and not btc_df.empty else None
        )

        trend_score = score["components"].get("T", 0)

        # Funding rate'i al (cache'den) - skor bilesenine etki eder
        coin_funding = self._funding_rates.get(symbol, 0.0)

        # SHORT setup analizi (bagimsiz motor)
        short_setup = analyze_short_setup(
            df,
            btc_perf_1h=self.btc_perf_1h,
            price_change_24h=coin["price_change_24h"],
            funding_rate=coin_funding,
        )

        # LONG setup analizi (bagimsiz motor)
        long_setup = analyze_long_setup(
            df,
            btc_perf_1h=self.btc_perf_1h,
            price_change_24h=coin["price_change_24h"],
            funding_rate=coin_funding,
        )

        data = {
            "price": close,
            "volume_24h": coin["volume_24h"],
            "price_change_1h": round(perf_1h, 2),
            "price_change_24h": coin["price_change_24h"],
            "rsi": round(float(rsi), 1),
            "adx": round(float(adx), 1),
            "ut_signal": int(ut),
            "vwap_position": "ustunde" if close > vwap else "altinda",
            "trend_score": trend_score,
            "volume_ratio": round(float(vol_ratio), 2),
            "rvol": rvol,
            "canslim_score": score["score"],
            "relative_strength": round(rs, 2),
            "wick_ratio": round(wick_ratio, 1),
            "short_score": short_setup["score"],
            "short_verdict": short_setup["verdict"],
            "short_signals": short_setup["signals"],
            "short_setup": short_setup,
            "long_score": long_setup["score"],
            "long_verdict": long_setup["verdict"],
            "long_signals": long_setup["signals"],
            "long_setup": long_setup,
            "funding_rate": self._funding_rates.get(symbol, 0.0),
            # 3m RSI egimi (son 3 bar) - negatif: momentum zayifliyor
            "rsi_slope_3m": round(rsi_slope(df, 3), 2),
            # BTC Korelasyon flag (sonradan doldurulacak)
            "correlation_tag": "",
            # Agirlikli momentum skorlari (RS*0.4 + RSI*0.3 + RVOL*0.3)
            "long_momentum": _weighted_momentum(rs, rsi, rvol, side="LONG"),
            "short_momentum": _weighted_momentum(rs, rsi, rvol, side="SHORT"),
            # Sektor (CoinGecko)
            "sector": self.sector_map.get(symbol),
            "previously_tracked": (
                "long" if symbol in self._prev_strong_buy
                else "short" if symbol in self._prev_strong_sell
                else None
            ),
        }

        return CoinProfile(symbol, data)

    def _categorize_results(self):
        """Coinleri kategorize et (RVOL dahil)."""
        for r in self.results:
            bull = 0
            bear = 0

            # RS pozitif = BTC'den guclu
            if r.relative_strength > 2: bull += 2
            elif r.relative_strength > 0.5: bull += 1
            elif r.relative_strength < -2: bear += 2
            elif r.relative_strength < -0.5: bear += 1

            # CANSLIM skor
            if r.canslim_score >= 75: bull += 2
            elif r.canslim_score >= 65: bull += 1
            elif r.canslim_score < 50: bear += 1

            # UT Bot
            if r.ut_signal == 1: bull += 1
            elif r.ut_signal == -1: bear += 1

            # VWAP
            if r.vwap_position == "ustunde": bull += 1
            else: bear += 1

            # RVOL - hacim patlamasi
            if r.rvol >= 2.0: bull += 1   # Guclu hacim = bir sey oluyor
            elif r.rvol < 0.5: bear += 1  # Hacim olmus = ilgi yok

            # RSI
            if r.rsi > 70: bear += 1
            elif r.rsi < 30: bull += 1

            # Igne orani yuksek = riskli
            if r.wick_ratio > 200: bear += 1

            # FUNDING RATE - kontrarian sinyal
            # >+0.05%: long'lar asiri, short risk dusuk (bear egilim)
            # <-0.05%: short'lar asiri, long risk dusuk (bull egilim)
            if r.funding_rate > 0.0005: bear += 1
            elif r.funding_rate < -0.0005: bull += 1

            # Kategori belirle
            if bull >= 5:
                r.category = "STRONG_BUY"
            elif bull >= 3 and bull > bear:
                r.category = "BUY"
            elif bear >= 5:
                r.category = "STRONG_SELL"
            elif bear >= 3 and bear > bull:
                r.category = "SELL"
            else:
                r.category = "NOTR"

    def _analyze_correlation(self):
        """
        BTC Korelasyon Analizi:
          * BTC notr (|btc_perf_1h| < 0.5):
              - Coin RVOL >= 2 -> "GERCEK" (tek basina inci, kaliteli fırsat)
          * Piyasa suru halinde (>60% coin ayni yonde + BTC RVOL yuksek):
              - Tum high-RVOL coinlere "SURU" flag (pump baskisi, riskli)
        """
        if not self.results:
            return

        btc_neutral = abs(self.btc_perf_1h) < 0.5

        # Pozitif/negatif perf oranı
        positive = sum(1 for r in self.results if r.price_change_1h > 0.5)
        negative = sum(1 for r in self.results if r.price_change_1h < -0.5)
        total = len(self.results)
        herd_long = (positive / total > 0.6) and self.btc_rvol > 1.5
        herd_short = (negative / total > 0.6) and self.btc_rvol > 1.5

        for r in self.results:
            if btc_neutral and r.rvol >= 2.0:
                r.correlation_tag = "GERCEK"
            elif herd_long and r.rvol >= 2.0 and r.price_change_1h > 0.5:
                r.correlation_tag = "SURU"
            elif herd_short and r.rvol >= 2.0 and r.price_change_1h < -0.5:
                r.correlation_tag = "SURU"

    def _save_results(self):
        """Sonuclari JSON'a kaydet."""
        os.makedirs(DATA_DIR, exist_ok=True)
        data = {
            "scan_time": self.scan_time,
            "market_mood": self.market_mood,
            "btc_1h": round(self.btc_perf_1h, 2),
            "btc_24h": round(self.btc_perf_24h, 2),
            "total_scanned": len(self.results),
            "coins": {}
        }
        for r in self.results:
            data["coins"][r.symbol] = {
                "price": r.price,
                "category": r.category,
                "canslim": r.canslim_score,
                "rs": r.relative_strength,
                "rsi": r.rsi,
                "adx": r.adx,
                "rvol": r.rvol,
                "volume_ratio": r.volume_ratio,
                "trend": r.trend_score,
                "wick_ratio": r.wick_ratio,
                "change_1h": r.price_change_1h,
                "change_24h": r.price_change_24h,
            }

        with open(SCANNER_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def get_summary(self) -> dict:
        """Ozet istatistikler."""
        cats = {"STRONG_BUY": [], "BUY": [], "NOTR": [], "SELL": [], "STRONG_SELL": []}
        for r in self.results:
            cats[r.category].append(r)

        return {
            "scan_time": self.scan_time,
            "market_mood": self.market_mood,
            "btc_1h": self.btc_perf_1h,
            "btc_24h": self.btc_perf_24h,
            "total": len(self.results),
            "strong_buy": cats["STRONG_BUY"],
            "buy": cats["BUY"],
            "notr": cats["NOTR"],
            "sell": cats["SELL"],
            "strong_sell": cats["STRONG_SELL"],
        }

    def generate_funding_report(self) -> str:
        """
        Funding Rate Analizi - SADECE LONG/SHORT adaylarindaki coinler.

        En yuksek pozitif (longlar shortlara odem yapiyor -> SHORT bias)
        En negatif (shortlar longlara oduyor -> LONG bias)
        BTC funding bilgisi
        """
        # Aday coinler: long_score >= 60 VEYA short_score >= 60
        candidate_symbols = set()
        for r in self.results:
            if r.long_score >= 60 or r.short_score >= 60:
                candidate_symbols.add(r.symbol)

        # Aday coinlerin funding rate'i (CoinProfile'da var)
        candidates_with_funding = [
            r for r in self.results if r.symbol in candidate_symbols
        ]

        if not candidates_with_funding:
            return (
                f"📊 <b>FUNDING RATE TARAMASI</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Aday coin yok (LONG/SHORT score ≥ 60)"
            )

        # Pozitif (yuksekten dusuge) - SHORT bias
        positives = sorted(
            [c for c in candidates_with_funding if c.funding_rate > 0],
            key=lambda x: -x.funding_rate
        )[:10]

        # Negatif (en negatiften az negatife) - LONG bias
        negatives = sorted(
            [c for c in candidates_with_funding if c.funding_rate < 0],
            key=lambda x: x.funding_rate
        )[:10]

        # BTC funding
        btc_funding = self._funding_rates.get("BTCUSDT", 0.0)
        btc_funding_pct = btc_funding * 100
        btc_emoji = ("🔴" if btc_funding > 0.005
                     else "🟢" if btc_funding < -0.005 else "🟠")
        btc_label = ("Hafif yuksuli" if btc_funding > 0.005
                     else "Hafif dususte" if btc_funding < -0.005
                     else "Notr/Karisik")

        lines = [
            f"📊 <b>FUNDING RATE TARAMASI</b>",
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M')} · UTC+3</i>",
            f"<i>Sadece market adaylari ({len(candidates_with_funding)} coin)</i>",
            f"━━━━━━━━━━━━━━━━━━",
        ]

        # 🔴 EN YUKSEK POZITIF (SHORT firsati)
        if positives:
            lines.append(
                f"\n🔴 <b>EN YUKSEK POZITIF</b>\n"
                f"<i>(Longlar oduyor · SHORT'a uygun)</i>"
            )
            lines.append("<code> #  Coin           Funding</code>")
            lines.append("<code>───────────────────────────</code>")
            for i, c in enumerate(positives, 1):
                coin = c.symbol.replace("USDT", "")
                f_pct = c.funding_rate * 100
                lines.append(
                    f"<code>{i:2d}  {coin:12s}  %{f_pct:+.4f}</code>"
                )

        # 🟢 EN NEGATIF (LONG firsati)
        if negatives:
            lines.append(
                f"\n🟢 <b>EN NEGATIF</b>\n"
                f"<i>(Shortlar oduyor · LONG'a uygun)</i>"
            )
            lines.append("<code> #  Coin           Funding</code>")
            lines.append("<code>───────────────────────────</code>")
            for i, c in enumerate(negatives, 1):
                coin = c.symbol.replace("USDT", "")
                f_pct = c.funding_rate * 100
                lines.append(
                    f"<code>{i:2d}  {coin:12s}  %{f_pct:+.4f}</code>"
                )

        # CIKARIM
        lines.append(f"\n💡 <b>YORUMLAR</b>")
        if positives:
            top_pos = positives[0]
            lines.append(
                f"• En kalabalik LONG: <b>{top_pos.symbol.replace('USDT','')}</b> "
                f"(%{top_pos.funding_rate*100:+.4f}) → SHORT firsati olabilir"
            )
        if negatives:
            top_neg = negatives[0]
            lines.append(
                f"• En kalabalik SHORT: <b>{top_neg.symbol.replace('USDT','')}</b> "
                f"(%{top_neg.funding_rate*100:+.4f}) → LONG firsati olabilir"
            )

        # BTC bilgisi
        lines.append(
            f"• {btc_emoji} BTC: {btc_label}\n"
            f"  Funding: %{btc_funding_pct:+.4f}"
        )

        # Trend ozeti
        lines.append(
            f"• ₿ BTC trend: 1s %{self.btc_perf_1h:+.2f} · "
            f"24s %{self.btc_perf_24h:+.2f}"
        )

        lines.append(f"\n━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"📈 Toplam taranan: {len(self.results)} coin | "
            f"Aday: {len(candidates_with_funding)} | "
            f"Funding cache: {len(self._funding_rates)}"
        )

        return "\n".join(lines)

    def generate_telegram_report(self) -> str:
        """Telegram icin Market Rontgeni raporu."""
        s = self.get_summary()

        mood_emoji = {"BULL": "🟢", "BEAR": "🔴", "NOTR": "⚪"}.get(s["market_mood"], "⚪")

        # Korelasyon ozeti
        gercek_count = sum(1 for r in self.results if r.correlation_tag == "GERCEK")
        suru_count = sum(1 for r in self.results if r.correlation_tag == "SURU")

        lines = [
            f"🔬 <b>MARKET RONTGENI</b> ({s['scan_time']})",
            f"━━━━━━━━━━━━━━━━━━",
            f"{mood_emoji} Piyasa: <b>{s['market_mood']}</b>",
            f"₿ BTC: %{s['btc_1h']:+.2f} (1s) | %{s['btc_24h']:+.2f} (24s) "
            f"| RVOL:x{self.btc_rvol:.1f}",
            f"📊 Taranan: {s['total']} coin | ⭐ {gercek_count} gercek | 🐑 {suru_count} suru",
            f"━━━━━━━━━━━━━━━━━━",
        ]

        # STRONG BUY
        if s["strong_buy"]:
            lines.append(f"\n🟢🟢 <b>STRONG BUY ({len(s['strong_buy'])})</b>")
            for c in sorted(s["strong_buy"], key=lambda x: x.canslim_score, reverse=True)[:10]:
                coin = c.symbol.replace("USDT", "")
                rvol_tag = f"🔥{c.rvol:.1f}x" if c.rvol >= 2.0 else f"{c.rvol:.1f}x"
                tracked_tag = "🔄" if c.previously_tracked == "long" else ""
                f_pct = c.funding_rate * 100
                lines.append(
                    f"  {tracked_tag}{coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{rvol_tag} F:%{f_pct:+.3f} %{c.price_change_1h:+.1f}"
                )

        # BUY
        if s["buy"]:
            lines.append(f"\n🟢 <b>BUY ({len(s['buy'])})</b>")
            for c in sorted(s["buy"], key=lambda x: x.canslim_score, reverse=True)[:10]:
                coin = c.symbol.replace("USDT", "")
                rvol_tag = f"🔥{c.rvol:.1f}x" if c.rvol >= 2.0 else f"{c.rvol:.1f}x"
                tracked_tag = "🔄" if c.previously_tracked == "long" else ""
                f_pct = c.funding_rate * 100
                lines.append(
                    f"  {tracked_tag}{coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{rvol_tag} F:%{f_pct:+.3f}"
                )

        # STRONG SELL (olu coinleri gosterme)
        real_strong_sell = [c for c in s["strong_sell"] if c.rsi < 99 and c.rvol > 0]
        if real_strong_sell:
            lines.append(f"\n🔴🔴 <b>STRONG SELL ({len(real_strong_sell)})</b>")
            for c in sorted(real_strong_sell, key=lambda x: x.canslim_score)[:10]:
                coin = c.symbol.replace("USDT", "")
                tracked_tag = "🔄" if c.previously_tracked == "short" else ""
                f_pct = c.funding_rate * 100
                lines.append(
                    f"  {tracked_tag}{coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{c.rvol:.1f}x F:%{f_pct:+.3f}"
                )

        # SELL (olu coinleri gosterme)
        real_sell = [c for c in s["sell"] if c.rsi < 99 and c.rvol > 0]
        if real_sell:
            lines.append(f"\n🔴 <b>SELL ({len(real_sell)})</b>")
            for c in sorted(real_sell, key=lambda x: x.canslim_score)[:10]:
                coin = c.symbol.replace("USDT", "")
                tracked_tag = "🔄" if c.previously_tracked == "short" else ""
                f_pct = c.funding_rate * 100
                lines.append(
                    f"  {tracked_tag}{coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{c.rvol:.1f}x F:%{f_pct:+.3f}"
                )

        # LONG/SHORT CANDIDATES (yeni motorlar) - kategoriden bagimsiz
        all_coins = (s["strong_sell"] + s["sell"] + s["notr"]
                     + s["buy"] + s["strong_buy"])

        top_longs = sorted(
            [c for c in all_coins if c.long_score >= 60 and c.rsi < 99],
            key=lambda x: x.long_score, reverse=True
        )[:8]
        if top_longs:
            lines.append(f"\n⬆️ <b>LONG ADAYLARI ({len(top_longs)})</b>")
            for c in top_longs:
                coin = c.symbol.replace("USDT", "")
                tracked_tag = "🔄" if c.previously_tracked == "long" else ""
                decay_tag = "⚠️" if c.rsi_slope_3m < -3 else ""
                # Korelasyon: GERCEK=⭐ SURU=🐑
                corr_tag = "⭐" if c.correlation_tag == "GERCEK" else (
                    "🐑" if c.correlation_tag == "SURU" else ""
                )
                f_pct = c.funding_rate * 100
                lines.append(
                    f"  {tracked_tag}{decay_tag}{corr_tag}{coin:8s} "
                    f"LongS:{c.long_score} M:{c.long_momentum:.0f} "
                    f"RSI:{c.rsi:.0f}({c.rsi_slope_3m:+.1f}) "
                    f"RS:{c.relative_strength:+.1f} F:%{f_pct:+.3f}"
                )

        top_shorts = sorted(
            [c for c in all_coins if c.short_score >= 60 and c.rsi < 99],
            key=lambda x: x.short_score, reverse=True
        )[:8]
        if top_shorts:
            lines.append(f"\n⬇️ <b>SHORT ADAYLARI ({len(top_shorts)})</b>")
            for c in top_shorts:
                coin = c.symbol.replace("USDT", "")
                tracked_tag = "🔄" if c.previously_tracked == "short" else ""
                decay_tag = "⚠️" if c.rsi_slope_3m > 3 else ""
                corr_tag = "⭐" if c.correlation_tag == "GERCEK" else (
                    "🐑" if c.correlation_tag == "SURU" else ""
                )
                f_pct = c.funding_rate * 100
                lines.append(
                    f"  {tracked_tag}{decay_tag}{corr_tag}{coin:8s} "
                    f"ShortS:{c.short_score} M:{c.short_momentum:.0f} "
                    f"RSI:{c.rsi:.0f}({c.rsi_slope_3m:+.1f}) "
                    f"RS:{c.relative_strength:+.1f} F:%{f_pct:+.3f}"
                )

        # Sektor Ozeti - hangi sektor bull/bear
        sector_perf: dict[str, list[float]] = {}
        for r in self.results:
            sector_perf.setdefault(r.sector, []).append(r.price_change_1h)

        top_sectors = []
        for sec, perfs in sector_perf.items():
            if sec == "Diger" or len(perfs) < 2:
                continue
            avg_perf = sum(perfs) / len(perfs)
            top_sectors.append((sec, avg_perf, len(perfs)))
        top_sectors.sort(key=lambda x: x[1], reverse=True)

        if top_sectors:
            lines.append(f"\n🏭 <b>SEKTOR HAREKETI (1s)</b>")
            # En iyi 3 + en kotu 3
            for sec, perf, n in top_sectors[:3]:
                emoji = "🟢" if perf > 0 else "🔴"
                lines.append(f"  {emoji} {sec:10s} %{perf:+.2f} ({n} coin)")
            if len(top_sectors) > 6:
                lines.append("  ...")
                for sec, perf, n in top_sectors[-3:]:
                    emoji = "🟢" if perf > 0 else "🔴"
                    lines.append(f"  {emoji} {sec:10s} %{perf:+.2f} ({n} coin)")

        # Ozet
        lines.append(f"\n━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"🟢🟢 {len(s['strong_buy'])} | 🟢 {len(s['buy'])} | "
            f"⚪ {len(s['notr'])} | 🔴 {len(real_sell)} | 🔴🔴 {len(real_strong_sell)}"
        )

        return "\n".join(lines)

    def update_watchlists(self) -> dict:
        """
        Scanner sonuclarina gore whitelist/blacklist guncelle.
        STRONG_BUY x3+ = whitelist, STRONG_SELL x3+ = blacklist
        """
        os.makedirs(DATA_DIR, exist_ok=True)

        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, "r") as f:
                watchlist = json.load(f)
        else:
            watchlist = {"whitelist": {}, "blacklist": {}, "history": {}}

        # Eski format uyumlulugu: eksik anahtarlari tamamla
        watchlist.setdefault("whitelist", {})
        watchlist.setdefault("blacklist", {})
        watchlist.setdefault("history", {})

        now = datetime.now().isoformat()

        for r in self.results:
            sym = r.symbol
            if sym not in watchlist["history"]:
                watchlist["history"][sym] = {
                    "strong_buy_count": 0, "buy_count": 0,
                    "sell_count": 0, "strong_sell_count": 0,
                    "last_seen": now, "last_category": r.category,
                }

            h = watchlist["history"][sym]
            h["last_seen"] = now
            h["last_category"] = r.category

            if r.category == "STRONG_BUY":
                h["strong_buy_count"] += 1
            elif r.category == "BUY":
                h["buy_count"] += 1
            elif r.category == "SELL":
                h["sell_count"] += 1
            elif r.category == "STRONG_SELL":
                h["strong_sell_count"] += 1

            # Whitelist: 3+ kez STRONG_BUY
            if h["strong_buy_count"] >= 3:
                watchlist["whitelist"][sym] = {
                    "reason": f"STRONG_BUY x{h['strong_buy_count']} | Skor:{r.canslim_score}",
                    "added": now,
                    "score": r.canslim_score,
                    "rs": r.relative_strength,
                }
                watchlist["blacklist"].pop(sym, None)

            # Blacklist: 3+ kez STRONG_SELL
            if h["strong_sell_count"] >= 3:
                watchlist["blacklist"][sym] = {
                    "reason": f"STRONG_SELL x{h['strong_sell_count']} | Skor:{r.canslim_score}",
                    "added": now,
                }
                watchlist["whitelist"].pop(sym, None)

        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f, indent=2, default=str)

        logger.info(
            f"Watchlist: {len(watchlist['whitelist'])} whitelist | "
            f"{len(watchlist['blacklist'])} blacklist"
        )
        return watchlist

    def get_watchlist_report(self) -> str:
        """Telegram icin watchlist raporu."""
        if not os.path.exists(WATCHLIST_FILE):
            return "📋 Watchlist henuz olusturulmadi."

        with open(WATCHLIST_FILE, "r") as f:
            wl = json.load(f)

        lines = [
            "📋 <b>WATCHLIST</b>",
            "━━━━━━━━━━━━━━━━━━",
        ]

        if wl.get("whitelist"):
            lines.append(f"\n✅ <b>WHITELIST ({len(wl['whitelist'])})</b>")
            for sym, info in sorted(wl["whitelist"].items(),
                                     key=lambda x: x[1].get("score", 0), reverse=True):
                coin = sym.replace("USDT", "")
                lines.append(f"  {coin:8s} | {info['reason']}")
        else:
            lines.append("\n✅ Whitelist: Henuz veri yok (3+ STRONG_BUY gerekli)")

        if wl.get("blacklist"):
            lines.append(f"\n❌ <b>BLACKLIST ({len(wl['blacklist'])})</b>")
            for sym, info in wl["blacklist"].items():
                coin = sym.replace("USDT", "")
                lines.append(f"  {coin:8s} | {info['reason']}")
        else:
            lines.append("\n❌ Blacklist: Henuz veri yok (3+ STRONG_SELL gerekli)")

        return "\n".join(lines)

    async def _sleep(self, seconds: float):
        import asyncio
        await asyncio.sleep(seconds)
