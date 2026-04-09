#!/usr/bin/env python3
"""
RVOL Alert - Surekli hacim takibi.
24h hacim $20M-$100M arasi coinlerde RVOL >= 2.0 olanlari bulur.
Her 5dk mum kapanisinda tekrar tarar - RVOL gecen coinleri HER MUM takip eder.
"""
import asyncio
import math
import time
from binance_client import BinanceClient
from telegram_bot import TelegramNotifier
from logger_setup import setup_logger

logger = setup_logger("RVOLAlert")

RVOL_MIN = 2.0
TRACKING = {}  # {symbol: {"first_seen": ts, "last_rvol": x, "count": n, "peak_rvol": x}}


async def process_symbol(symbol, vol_24h, bc):
    """
    Tek coin RVOL hesapla - 5dk mumlar, son 100 mum (~8.3 saat).

    RVOL = Son mumun hacmi / Onceki 99 mumun ortalama hacmi
    RVOL >= 2.0 = Hacim ortalamadan 2 kat fazla -> dikkat cekici hareket
    """
    try:
        klines = await asyncio.to_thread(
            bc.public_client.klines, symbol=symbol, interval="5m", limit=100
        )
        if not klines or len(klines) < 50:
            return None

        volumes = [float(k[5]) for k in klines]
        current_vol = volumes[-1]
        avg_vol = sum(volumes[:-1]) / (len(volumes) - 1)  # Son mum haric ortalama

        if avg_vol <= 0:
            return None

        rvol = current_vol / avg_vol
        if math.isnan(rvol) or math.isinf(rvol):
            return None

        if rvol < RVOL_MIN:
            return None

        price = float(klines[-1][4])
        price_open = float(klines[-1][1])
        change_pct = ((price - price_open) / price_open) * 100 if price_open > 0 else 0

        # Son 100 mumdaki fiyat trendi
        first_close = float(klines[0][4])
        trend_pct = ((price - first_close) / first_close) * 100

        return {
            "symbol": symbol,
            "rvol": round(rvol, 2),
            "price": price,
            "change_pct": round(change_pct, 2),
            "trend_pct": round(trend_pct, 2),
            "direction": "YUKARI" if price > price_open else "ASAGI",
            "emoji": "🟢🔥" if price > price_open else "🔴🔥",
            "current_vol": current_vol,
            "avg_vol": avg_vol,
            "vol_24h": vol_24h,
        }
    except Exception:
        return None


async def scan(bc, notifier):
    """Her mum kapanisinda tum coinleri tara."""
    now = time.time()

    try:
        tickers = await asyncio.to_thread(bc.public_client.ticker_24hr_price_change)
    except Exception as e:
        logger.error(f"Ticker alinamadi: {e}")
        return

    # 24h hacim $20M-$100M
    tasks = []
    sym_vol = {}
    for t in tickers:
        sym = t["symbol"]
        vol_24h = float(t.get("quoteVolume", 0))
        if sym.endswith("USDT") and 20_000_000 <= vol_24h <= 100_000_000:
            tasks.append(process_symbol(sym, vol_24h, bc))
            sym_vol[sym] = vol_24h

    if not tasks:
        return

    logger.info(f"Tarama: {len(tasks)} coin...")

    # 10'arli batch
    results = []
    for i in range(0, len(tasks), 10):
        batch = tasks[i:i + 10]
        batch_results = await asyncio.gather(*batch)
        results.extend(batch_results)
        await asyncio.sleep(0.5)

    # RVOL sonuclari
    new_alerts = []      # Ilk kez RVOL gecen
    update_alerts = []   # Zaten takipte, guncelleme
    lost_tracking = []   # RVOL dustu, takipten cikti

    active_symbols = set()

    for r in results:
        if r is None:
            continue

        sym = r["symbol"]

        if r["rvol"] >= RVOL_MIN:
            active_symbols.add(sym)

            if sym not in TRACKING:
                # YENi - ilk kez RVOL gecti
                TRACKING[sym] = {
                    "first_seen": now,
                    "last_rvol": r["rvol"],
                    "peak_rvol": r["rvol"],
                    "count": 1,
                    "first_price": r["price"],
                }
                new_alerts.append(r)
            else:
                # GUNCELLEME - zaten takipte
                t = TRACKING[sym]
                t["count"] += 1
                t["last_rvol"] = r["rvol"]
                if r["rvol"] > t["peak_rvol"]:
                    t["peak_rvol"] = r["rvol"]

                # Fiyat degisimi (ilk goruldugunden beri)
                price_change = ((r["price"] - t["first_price"]) / t["first_price"]) * 100
                r["price_since_first"] = round(price_change, 2)
                r["mum_count"] = t["count"]
                r["peak_rvol"] = t["peak_rvol"]
                update_alerts.append(r)

    # Takipten dusenler (RVOL < 2.0 olan eski takiptekiler)
    for sym in list(TRACKING.keys()):
        if sym not in active_symbols:
            t = TRACKING.pop(sym)
            lost_tracking.append({
                "symbol": sym,
                "count": t["count"],
                "peak_rvol": t["peak_rvol"],
            })

    # --- TELEGRAM MESAJLARI ---

    # 1. Yeni alertler
    for a in sorted(new_alerts, key=lambda x: x["rvol"], reverse=True):
        coin = a["symbol"].replace("USDT", "")
        vol_m = a["vol_24h"] / 1_000_000
        trend = a.get("trend_pct", 0)
        trend_emoji = "📈" if trend > 0 else "📉"
        msg = (
            f"{a['emoji']} <b>RVOL YENi: {coin}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔥 RVOL: <b>{a['rvol']}x</b>\n"
            f"📊 Yon: {a['direction']}\n"
            f"💰 Fiyat: {a['price']}\n"
            f"📈 Mum: %{a['change_pct']:+.2f}\n"
            f"{trend_emoji} 100 mum trend: %{trend:+.2f}\n"
            f"📦 Hacim: {a['current_vol']:,.0f} (ort: {a['avg_vol']:,.0f})\n"
            f"💵 24h: ${vol_m:.1f}M"
        )
        await notifier.send_message(msg)

    # 2. Takipteki guncelleme (hepsi tek mesajda)
    if update_alerts:
        lines = [f"📡 <b>RVOL TAKIP</b> ({len(update_alerts)} coin)"]
        lines.append("━━━━━━━━━━━━━━━━━━")
        for a in sorted(update_alerts, key=lambda x: x["rvol"], reverse=True):
            coin = a["symbol"].replace("USDT", "")
            since = a.get("price_since_first", 0)
            mums = a.get("mum_count", 0)
            peak = a.get("peak_rvol", a["rvol"])
            arrow = "↑" if a["direction"] == "YUKARI" else "↓"
            lines.append(
                f"{arrow} {coin:8s} RVOL:{a['rvol']}x "
                f"(zirve:{peak}x) {mums}mum %{since:+.1f}"
            )
        await notifier.send_message("\n".join(lines))

    # 3. Takipten dusenler
    if lost_tracking:
        lines = [f"⚪ <b>RVOL DUSTU</b> ({len(lost_tracking)} coin)"]
        for l in lost_tracking:
            coin = l["symbol"].replace("USDT", "")
            lines.append(f"  {coin}: {l['count']} mum takip, zirve {l['peak_rvol']}x")
        await notifier.send_message("\n".join(lines))

    if new_alerts:
        logger.info(f"Yeni: {len(new_alerts)} | Takip: {len(update_alerts)} | Dusen: {len(lost_tracking)}")
    elif update_alerts:
        logger.info(f"Takip: {len(update_alerts)} | Dusen: {len(lost_tracking)}")
    else:
        logger.info(f"RVOL >= {RVOL_MIN}x yok. Takip: {len(TRACKING)}")


async def main():
    bc = BinanceClient()
    notifier = TelegramNotifier()

    await notifier.send_message(
        "🔥 <b>RVOL Surekli Takip baslatildi</b>\n"
        f"24h Hacim: $20M-$100M\n"
        f"RVOL >= {RVOL_MIN}x | 5dk mumlar\n"
        f"Her mum kapanisinda tekrar taranir\n"
        f"RVOL gecen coinler SUREKLI takip edilir"
    )

    # 5dk mum kapanisina senkronize ol
    while True:
        try:
            # Simdiki zamandan sonraki 5dk mum kapanisini hesapla
            now = time.time()
            next_candle = (int(now / 300) + 1) * 300  # Sonraki 5dk siniri
            wait = next_candle - now + 5  # +5sn buffer (mum kapansin)

            logger.info(f"Sonraki mum kapanisina {wait:.0f}sn bekleniyor...")
            await asyncio.sleep(wait)

            start = time.time()
            await scan(bc, notifier)
            logger.info(f"Tarama: {time.time() - start:.1f}sn")

        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
