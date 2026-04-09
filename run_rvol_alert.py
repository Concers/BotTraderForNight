#!/usr/bin/env python3
"""
RVOL Alert - Saf hacim patlamasi tespiti.
24h hacim $20M-$100M arasi coinlerde RVOL >= 2.0 olanlari Telegram'a gonderir.
Paralel tarama ile hizli calisir.
"""
import asyncio
import math
import time
from binance_client import BinanceClient
from telegram_bot import TelegramNotifier
from logger_setup import setup_logger

logger = setup_logger("RVOLAlert")

RVOL_MIN = 1.0
SCAN_INTERVAL = 180
COOLDOWN_SEC = 900
sent_cache = {}


async def process_symbol(symbol, vol_24h, bc, now):
    """Tek bir coin icin RVOL hesapla."""
    try:
        klines = await asyncio.to_thread(
            bc.public_client.klines, symbol=symbol, interval="5m", limit=11
        )

        if not klines or len(klines) < 11:
            return None

        volumes = [float(k[5]) for k in klines]
        current_vol = volumes[-1]
        avg_vol = sum(volumes[:-1]) / 10

        if avg_vol <= 0:
            return None

        rvol = current_vol / avg_vol

        if rvol < RVOL_MIN or math.isnan(rvol) or math.isinf(rvol):
            return None

        price = float(klines[-1][4])
        price_open = float(klines[-1][1])
        change_pct = ((price - price_open) / price_open) * 100 if price_open > 0 else 0

        direction = "YUKARI" if price > price_open else "ASAGI"
        emoji = "🟢🔥" if price > price_open else "🔴🔥"

        return {
            "symbol": symbol,
            "rvol": round(rvol, 2),
            "price": price,
            "change_pct": round(change_pct, 2),
            "direction": direction,
            "emoji": emoji,
            "current_vol": current_vol,
            "avg_vol": avg_vol,
            "vol_24h": vol_24h,
        }
    except Exception:
        return None


async def scan(bc, notifier):
    """Paralel RVOL taramasi."""
    now = time.time()

    # Cooldown temizle
    expired = [s for s, t in sent_cache.items() if now - t > COOLDOWN_SEC]
    for s in expired:
        del sent_cache[s]

    try:
        tickers = await asyncio.to_thread(bc.public_client.ticker_24hr_price_change)
    except Exception as e:
        logger.error(f"Ticker alinamadi: {e}")
        return

    # 24h hacim $20M-$100M filtresi + cooldown
    tasks = []
    for t in tickers:
        sym = t["symbol"]
        vol_24h = float(t.get("quoteVolume", 0))

        if sym.endswith("USDT") and 20_000_000 <= vol_24h <= 100_000_000:
            if sym not in sent_cache:
                tasks.append(process_symbol(sym, vol_24h, bc, now))

    if not tasks:
        logger.info("Taranacak coin yok.")
        return

    logger.info(f"Tarama: {len(tasks)} coin isleniyor...")

    # 10'arli batch'ler halinde gonder (connection pool tasmasin)
    results = []
    batch_size = 10
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*batch)
        results.extend(batch_results)
        await asyncio.sleep(0.5)

    alerts = [r for r in results if r is not None]
    alerts.sort(key=lambda x: x["rvol"], reverse=True)

    for a in alerts:
        sent_cache[a["symbol"]] = now
        coin = a["symbol"].replace("USDT", "")
        vol_24h_m = a["vol_24h"] / 1_000_000
        msg = (
            f"{a['emoji']} <b>RVOL: {coin}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔥 RVOL: <b>{a['rvol']}x</b>\n"
            f"📊 Yon: {a['direction']}\n"
            f"💰 Fiyat: {a['price']}\n"
            f"📈 Mum: %{a['change_pct']:+.2f}\n"
            f"📦 Hacim: {a['current_vol']:,.0f} (ort: {a['avg_vol']:,.0f})\n"
            f"💵 24h Hacim: ${vol_24h_m:.1f}M"
        )
        await notifier.send_message(msg)
        logger.info(f"ALERT: {coin} RVOL:{a['rvol']}x {a['direction']}")

    if alerts:
        logger.info(f"{len(alerts)} alert gonderildi.")
    else:
        logger.info("RVOL >= 2.0 olan coin yok.")


async def main():
    bc = BinanceClient()
    notifier = TelegramNotifier()

    await notifier.send_message(
        "🔥 <b>RVOL Alert baslatildi</b>\n"
        f"24h Hacim: $20M-$100M | RVOL >= {RVOL_MIN}x\n"
        f"5dk mumlar | Paralel tarama | {SCAN_INTERVAL}sn aralik"
    )

    while True:
        try:
            start = time.time()
            await scan(bc, notifier)
            logger.info(f"Tarama suresi: {time.time() - start:.1f}sn")
        except Exception as e:
            logger.error(f"Hata: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
