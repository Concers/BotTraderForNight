#!/usr/bin/env python3
"""
Sadece Market Scanner - 5dk aralikla tarama + Telegram rapor.
Islem acmaz, sadece piyasayi tarar ve raporlar.
"""
import asyncio
from binance_client import BinanceClient
from telegram_bot import TelegramNotifier
from market_scanner import MarketScanner
from logger_setup import setup_logger

logger = setup_logger("RunScanner")


async def main():
    bc = BinanceClient()
    notifier = TelegramNotifier()
    scanner = MarketScanner(bc)

    await notifier.send_message("🔬 <b>Market Scanner baslatildi</b>\nHer 5 dakikada tarama yapilacak.")

    while True:
        try:
            logger.info("Tarama basliyor...")
            await scanner.scan()

            # Telegram rapor
            report = scanner.generate_telegram_report()
            await notifier.send_message(report)

            # Watchlist guncelle + gonder
            scanner.update_watchlists()
            await notifier.send_message(scanner.get_watchlist_report())

            logger.info("Tarama tamamlandi. 5dk bekleniyor...")
        except Exception as e:
            logger.error(f"Tarama hatasi: {e}")

        await asyncio.sleep(300)  # 5 dakika


if __name__ == "__main__":
    asyncio.run(main())
