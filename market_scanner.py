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
from indicators import run_all_indicators
from scoring import CANSLIMScorer
import json
import os

logger = setup_logger("Scanner")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCANNER_FILE = os.path.join(DATA_DIR, "scanner_results.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")


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


class MarketScanner:
    """Piyasa tarayici - Relative Market v2."""

    def __init__(self, binance: BinanceClient):
        self.binance = binance
        self.scorer = CANSLIMScorer()
        self.results: list[CoinProfile] = []
        self.btc_perf_1h = 0.0
        self.btc_perf_24h = 0.0
        self.market_mood = "NOTR"  # BULL, BEAR, NOTR
        self.scan_time = ""

    async def scan(self) -> dict:
        """Tam market taramasi yap."""
        start = time.time()
        self.scan_time = datetime.now().strftime("%H:%M")
        self.results = []

        logger.info("Market Scanner v2 baslatildi...")

        # 1. BTC referans verisi
        btc_df = self.binance.get_klines("BTCUSDT", interval="3m", limit=200)
        if not btc_df.empty:
            btc_df = run_all_indicators(btc_df)
            btc_closes = btc_df["close"].values
            self.btc_perf_1h = ((btc_closes[-1] - btc_closes[-20]) / btc_closes[-20]) * 100
            self.btc_perf_24h = ((btc_closes[-1] - btc_closes[0]) / btc_closes[0]) * 100

            if self.btc_perf_1h > 0.5:
                self.market_mood = "BULL"
            elif self.btc_perf_1h < -0.5:
                self.market_mood = "BEAR"
            else:
                self.market_mood = "NOTR"

        # 2. Tum ticker'lari al
        try:
            tickers = self.binance.public_client.ticker_24hr_price_change()
        except Exception as e:
            logger.error(f"Ticker alinamadi: {e}")
            return {}

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

        # 6. Kaydet
        self._save_results()

        duration = time.time() - start
        logger.info(
            f"Scanner tamamlandi: {len(self.results)} coin analiz edildi | "
            f"{duration:.0f}sn | Mood: {self.market_mood}"
        )

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

    def generate_telegram_report(self) -> str:
        """Telegram icin Market Roentgeni raporu."""
        s = self.get_summary()

        mood_emoji = {"BULL": "🟢", "BEAR": "🔴", "NOTR": "⚪"}.get(s["market_mood"], "⚪")

        lines = [
            f"🔬 <b>MARKET ROENTGENI</b> ({s['scan_time']})",
            f"━━━━━━━━━━━━━━━━━━",
            f"{mood_emoji} Piyasa: <b>{s['market_mood']}</b>",
            f"₿ BTC: %{s['btc_1h']:+.2f} (1s) | %{s['btc_24h']:+.2f} (24s)",
            f"📊 Taranan: {s['total']} coin",
            f"━━━━━━━━━━━━━━━━━━",
        ]

        # STRONG BUY
        if s["strong_buy"]:
            lines.append(f"\n🟢🟢 <b>STRONG BUY ({len(s['strong_buy'])})</b>")
            for c in sorted(s["strong_buy"], key=lambda x: x.canslim_score, reverse=True)[:10]:
                coin = c.symbol.replace("USDT", "")
                rvol_tag = f"🔥{c.rvol:.1f}x" if c.rvol >= 2.0 else f"{c.rvol:.1f}x"
                lines.append(
                    f"  {coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{rvol_tag} %{c.price_change_1h:+.1f}"
                )

        # BUY
        if s["buy"]:
            lines.append(f"\n🟢 <b>BUY ({len(s['buy'])})</b>")
            for c in sorted(s["buy"], key=lambda x: x.canslim_score, reverse=True)[:10]:
                coin = c.symbol.replace("USDT", "")
                rvol_tag = f"🔥{c.rvol:.1f}x" if c.rvol >= 2.0 else f"{c.rvol:.1f}x"
                lines.append(
                    f"  {coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{rvol_tag}"
                )

        # STRONG SELL (olu coinleri gosterme)
        real_strong_sell = [c for c in s["strong_sell"] if c.rsi < 99 and c.rvol > 0]
        if real_strong_sell:
            lines.append(f"\n🔴🔴 <b>STRONG SELL ({len(real_strong_sell)})</b>")
            for c in sorted(real_strong_sell, key=lambda x: x.canslim_score)[:10]:
                coin = c.symbol.replace("USDT", "")
                lines.append(
                    f"  {coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{c.rvol:.1f}x"
                )

        # SELL (olu coinleri gosterme)
        real_sell = [c for c in s["sell"] if c.rsi < 99 and c.rvol > 0]
        if real_sell:
            lines.append(f"\n🔴 <b>SELL ({len(real_sell)})</b>")
            for c in sorted(real_sell, key=lambda x: x.canslim_score)[:10]:
                coin = c.symbol.replace("USDT", "")
                lines.append(
                    f"  {coin:8s} S:{c.canslim_score:.0f} RS:{c.relative_strength:+.1f} "
                    f"RSI:{c.rsi:.0f} RVOL:{c.rvol:.1f}x"
                )

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
