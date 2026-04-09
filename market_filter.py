from __future__ import annotations
from logger_setup import setup_logger
import config

logger = setup_logger("MarketFilter")


class MarketCapFilter:
    """Binance API uzerinden market cap filtresi ve coin taramasi."""

    def __init__(self, binance_client):
        self.binance = binance_client
        self._cache: dict = {}

    def get_coins_in_range(self) -> list[str]:
        """
        Binance Futures'taki coinlerden market cap 45M-75M$ araligindakileri bul.
        Binance'te dogrudan market cap yok, bu yuzden:
          market_cap ≈ circulating_supply * price
        Binance ticker'dan 24h volume ve fiyat bilgisiyle filtreliyoruz.
        """
        try:
            # Tum futures sembollerin 24h ticker verisini al
            tickers = self.binance.public_client.ticker_24hr_price_change()

            # Sadece aktif USDT perpetual sembolleri
            futures_symbols = set(self.binance.get_all_futures_symbols())

            candidates = []
            for ticker in tickers:
                symbol = ticker["symbol"]
                if symbol not in futures_symbols:
                    continue

                # BTC, ETH gibi buyuk coinleri atla (market cap cok yuksek)
                # Cok dusuk hacimli coinleri de atla
                volume_usdt = float(ticker["quoteVolume"])  # 24h USDT hacim
                last_price = float(ticker["lastPrice"])
                price_change_pct = float(ticker["priceChangePercent"])

                if last_price == 0 or volume_usdt == 0:
                    continue

                # Hacim bazli market cap tahmini:
                # Dusuk-orta cap coinler genelde 24h hacim / market cap orani %5-%50
                # 45M cap, %10 oran -> ~4.5M daily volume
                # 75M cap, %10 oran -> ~7.5M daily volume
                # Genis aralik kullanarak filtrele, sonra hassas kontrol yap
                estimated_mcap_low = volume_usdt * 2    # Cok aktif coin
                estimated_mcap_high = volume_usdt * 150  # Az aktif/dusuk hacimli coin

                # Aralikla kesisim var mi?
                if estimated_mcap_high < config.MIN_MARKET_CAP:
                    continue  # Kesinlikle cok kucuk
                if estimated_mcap_low > config.MAX_MARKET_CAP * 10:
                    continue  # Kesinlikle cok buyuk

                # Aday listesine ekle
                candidates.append({
                    "symbol": symbol,
                    "price": last_price,
                    "volume_24h": volume_usdt,
                    "price_change": price_change_pct,
                })

            # Circulating supply ile gercek market cap hesabi
            # Binance mark price endpoint'inden open interest ile yaklasik hesap
            filtered = self._refine_with_open_interest(candidates)

            logger.info(
                f"Tarama: {len(tickers)} ticker -> {len(candidates)} aday -> "
                f"{len(filtered)} coin (${config.MIN_MARKET_CAP/1e6:.0f}M-${config.MAX_MARKET_CAP/1e6:.0f}M)"
            )
            return filtered

        except Exception as e:
            logger.error(f"Coin listesi alinamadi: {e}")
            return []

    def _refine_with_open_interest(self, candidates: list[dict]) -> list[str]:
        """
        Open Interest ve hacim ile market cap tahmini yaparak filtrele.
        Kucuk-orta cap coinleri hedefle.
        """
        refined = []
        try:
            # Open interest verisi al
            oi_data = {}
            try:
                oi_list = self.binance.client.open_interest("")
            except Exception:
                oi_list = []

            for item in oi_list:
                if isinstance(item, dict):
                    oi_data[item.get("symbol", "")] = float(item.get("openInterest", 0))
        except Exception:
            oi_data = {}

        for coin in candidates:
            symbol = coin["symbol"]
            volume = coin["volume_24h"]
            price = coin["price"]

            # Open interest varsa kullan
            oi = oi_data.get(symbol, 0) * price if symbol in oi_data else 0

            # Market cap tahmini:
            # Kucuk cap coinler icin hacim/mcap orani genelde %10-%30
            # OI/mcap orani genelde %1-%5
            if oi > 0:
                est_mcap = oi * 30
            else:
                # Dusuk hacimli coinler icin genis tahmin
                # ASTR gibi coinler: $650K hacim ama $50M mcap olabilir
                est_mcap = volume * 50

            in_range = config.MIN_MARKET_CAP <= est_mcap <= config.MAX_MARKET_CAP

            # Cok genis tolerans - sinyal kalitesi zaten CANSLIM'de filtreleniyor
            loose_range = (
                config.MIN_MARKET_CAP * 0.3 <= est_mcap <= config.MAX_MARKET_CAP * 3
            )

            if loose_range:
                refined.append(symbol)
                self._cache[symbol] = {
                    "est_mcap": est_mcap,
                    "volume": volume,
                    "price": price,
                    "tight_match": in_range,
                }

                if in_range:
                    logger.debug(
                        f"{symbol} | Tahmini MCap: ${est_mcap/1e6:.1f}M | "
                        f"Hacim: ${volume/1e6:.1f}M | UYGUN"
                    )

        return refined

    def is_in_range(self, symbol: str) -> bool:
        """Cache'teki bilgiyle kontrol."""
        if symbol in self._cache:
            return self._cache[symbol].get("tight_match", True)
        return True

    def get_scan_summary(self) -> str:
        """Tarama ozet raporu icin text olustur."""
        tight = [s for s, v in self._cache.items() if v.get("tight_match")]
        return (
            f"Toplam aday: {len(self._cache)} | "
            f"Hedef aralik: {len(tight)} coin"
        )
