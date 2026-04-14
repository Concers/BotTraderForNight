from __future__ import annotations
"""
CoinGecko API ile coin -> sektor eslemesi.
Rate limit: 30 req/dk (ucretsiz tier). Cache'li.
"""

import os
import json
import time
import requests
from logger_setup import setup_logger

logger = setup_logger("SectorMap")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SECTOR_FILE = os.path.join(DATA_DIR, "sector_map.json")
CACHE_TTL = 7 * 24 * 3600  # 7 gun

# CoinGecko kategori -> kisa isim eslemesi
CATEGORY_ALIASES = {
    "smart-contract-platform": "L1",
    "layer-1": "L1",
    "layer-2": "L2",
    "ethereum-ecosystem": "ETH-Eco",
    "solana-ecosystem": "SOL-Eco",
    "binance-smart-chain": "BSC",
    "meme-token": "Meme",
    "meme": "Meme",
    "gaming": "Gaming",
    "metaverse": "Metaverse",
    "decentralized-finance-defi": "DeFi",
    "defi": "DeFi",
    "artificial-intelligence": "AI",
    "ai-big-data": "AI",
    "storage": "Storage",
    "privacy-coins": "Privacy",
    "oracle": "Oracle",
    "exchange-based-tokens": "CEX-Token",
    "stablecoins": "Stable",
    "real-world-assets-rwa": "RWA",
    "liquid-staking": "LSD",
    "nft": "NFT",
    "dao": "DAO",
}


class SectorMapper:
    """Coin symbol -> sektor adi esleyici (CoinGecko + cache)."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.map: dict[str, str] = {}   # "BTCUSDT" -> "L1"
        self.last_updated = 0
        self._load()

    def _load(self):
        if not os.path.exists(SECTOR_FILE):
            return
        try:
            with open(SECTOR_FILE, "r") as f:
                data = json.load(f)
            self.map = data.get("map", {})
            self.last_updated = data.get("updated", 0)
        except Exception as e:
            logger.error(f"Sektor cache yuklenemedi: {e}")

    def _save(self):
        try:
            with open(SECTOR_FILE, "w") as f:
                json.dump({"map": self.map, "updated": self.last_updated}, f)
        except Exception as e:
            logger.error(f"Sektor cache kaydedilemedi: {e}")

    def get(self, symbol: str) -> str:
        """Sembol icin sektor dondur. Yoksa 'Diger'."""
        return self.map.get(symbol.upper(), "Diger")

    def refresh_if_stale(self):
        """Cache eskiyse (7 gun+) CoinGecko'dan yenile."""
        if time.time() - self.last_updated < CACHE_TTL:
            return
        self.refresh()

    def refresh(self):
        """CoinGecko'dan top 500 coin cekip kategorilere esle."""
        logger.info("CoinGecko sektor verisi cekiliyor...")
        try:
            # Top 500 coin (2 sayfa, sayfa basi 250)
            new_map: dict[str, str] = {}
            for page in (1, 2):
                url = "https://api.coingecko.com/api/v3/coins/markets"
                params = {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                }
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"CoinGecko {page}: {resp.status_code}")
                    continue
                for coin in resp.json():
                    sym = coin.get("symbol", "").upper()
                    if not sym:
                        continue
                    # USDT pairing
                    usdt_sym = f"{sym}USDT"
                    # Kategori yok bu endpoint'te; detay gerekir. Hizli cozum:
                    # Bu coin'in id'si ile /coins/{id} yerine "categories" query yok
                    # -> Alternatif: /coins/categories + coin listesi (ayri)
                    new_map[usdt_sym] = "Diger"  # placeholder

                time.sleep(1.5)  # rate limit

            # Kategoriler endpoint: hangi coin hangi kategoride
            # /coins/categories/list verir kategori listesi
            # Her kategori icin /coins/markets?category=... yapabiliriz ama 30+ call olur
            # Hizli yol: onceden bilinen buyuk kategorileri cek
            quick_categories = [
                "artificial-intelligence", "meme-token", "gaming",
                "decentralized-finance-defi", "layer-1", "layer-2",
                "stablecoins", "real-world-assets-rwa", "liquid-staking",
                "metaverse", "nft", "oracle", "privacy-coins",
                "exchange-based-tokens",
            ]
            for cat_id in quick_categories:
                try:
                    params = {
                        "vs_currency": "usd",
                        "category": cat_id,
                        "per_page": 100,
                        "page": 1,
                    }
                    resp = requests.get(url, params=params, timeout=15)
                    if resp.status_code == 200:
                        alias = CATEGORY_ALIASES.get(cat_id, cat_id[:10])
                        for coin in resp.json():
                            sym = coin.get("symbol", "").upper()
                            if sym:
                                new_map[f"{sym}USDT"] = alias
                    time.sleep(2.5)  # 30 req/dk limit - rahat olsun
                except Exception as e:
                    logger.debug(f"Kategori {cat_id} hatasi: {e}")

            if new_map:
                self.map = new_map
                self.last_updated = int(time.time())
                self._save()
                logger.info(f"Sektor cache guncellendi: {len(new_map)} coin")
        except Exception as e:
            logger.error(f"Sektor refresh hatasi: {e}")
