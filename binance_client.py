from typing import Optional
import pandas as pd
from binance.um_futures import UMFutures
from logger_setup import setup_logger
import config

logger = setup_logger("BinanceClient")


class BinanceClient:
    """Binance Futures API wrapper."""

    def __init__(self):
        # Islem icin (testnet veya canli)
        if config.BINANCE_TESTNET:
            self.client = UMFutures(
                key=config.BINANCE_API_KEY,
                secret=config.BINANCE_API_SECRET,
                base_url="https://testnet.binancefuture.com",
            )
            logger.info("Binance TESTNET modunda baslatildi.")
        else:
            self.client = UMFutures(
                key=config.BINANCE_API_KEY,
                secret=config.BINANCE_API_SECRET,
            )
            logger.info("Binance CANLI modda baslatildi!")

        # Veri okuma icin her zaman gercek Binance (public, key gerekmez)
        self.public_client = UMFutures()

        # Sembol hassasiyet cache'i
        self._precision_cache = {}
        self._load_precision_cache()

        # Testnet'te gecerli semboller + testnet limitleri
        self._testnet_symbols = set()
        if config.BINANCE_TESTNET:
            self._load_testnet_symbols()
            self._load_testnet_limits()

    def get_funding_rate(self, symbol: str) -> float:
        """
        Binance Futures premiumIndex endpoint'inden anlik funding rate.
        Pozitif: long'lar short'lara odeme yapiyor (LONG pahalli).
        Negatif: short'lar long'lara odeme yapiyor (SHORT pahalli).
        """
        try:
            data = self.public_client.mark_price(symbol=symbol)
            return float(data.get("lastFundingRate", 0.0))
        except Exception as e:
            logger.debug(f"Funding rate alinamadi ({symbol}): {e}")
            return 0.0

    def get_all_funding_rates(self) -> dict[str, float]:
        """Tum semboller icin funding rate (tek cagride)."""
        try:
            data = self.public_client.mark_price()
            return {
                item["symbol"]: float(item.get("lastFundingRate", 0.0))
                for item in data
            }
        except Exception as e:
            logger.error(f"Toplu funding rate alinamadi: {e}")
            return {}

    def get_klines(self, symbol: str, interval: str = "1m",
                   limit: int = 200) -> pd.DataFrame:
        """Mum verilerini al (her zaman gercek Binance verileri)."""
        try:
            klines = self.public_client.klines(
                symbol=symbol, interval=interval, limit=limit
            )
            df = pd.DataFrame(klines, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_base",
                "taker_buy_quote", "ignore",
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            return df
        except Exception as e:
            logger.error(f"Kline verisi alinamadi ({symbol}): {e}")
            return pd.DataFrame()

    def get_account_balance(self) -> float:
        """USDT bakiyesini al."""
        try:
            account = self.client.account()
            for asset in account["assets"]:
                if asset["asset"] == "USDT":
                    balance = float(asset["availableBalance"])
                    logger.info(f"Kullanilabilir bakiye: {balance} USDT")
                    return balance
            return 0.0
        except Exception as e:
            logger.error(f"Bakiye alinamadi: {e}")
            return 0.0

    def get_open_positions(self) -> list[dict]:
        """Binance'teki acik pozisyonlari al."""
        try:
            account = self.client.account()
            positions = []
            for pos in account.get("positions", []):
                amt = float(pos["positionAmt"])
                if amt == 0:
                    continue
                entry = float(pos["entryPrice"])
                mark = float(pos.get("markPrice", 0))
                unrealized = float(pos.get("unrealizedProfit", 0))
                positions.append({
                    "symbol": pos["symbol"],
                    "side": "BUY" if amt > 0 else "SELL",
                    "quantity": abs(amt),
                    "entry_price": entry,
                    "mark_price": mark,
                    "unrealized_pnl": unrealized,
                    "leverage": int(pos.get("leverage", 20)),
                })
                logger.info(
                    f"Acik pozisyon: {pos['symbol']} | "
                    f"{'LONG' if amt > 0 else 'SHORT'} x{abs(amt)} @ {entry} | "
                    f"PnL: ${unrealized:.2f}"
                )
            return positions
        except Exception as e:
            logger.error(f"Acik pozisyonlar alinamadi: {e}")
            return []

    def set_leverage(self, symbol: str, leverage: int = None):
        """Kaldiraci ayarla."""
        lev = leverage or config.DEFAULT_LEVERAGE
        try:
            self.client.change_leverage(symbol=symbol, leverage=lev)
            logger.info(f"{symbol} kaldirac: {lev}x")
        except Exception as e:
            logger.error(f"Kaldirac ayarlanamadi ({symbol}): {e}")

    def _load_precision_cache(self):
        """Baslangicta tum sembollerin hassasiyet + max qty yukle."""
        try:
            info = self.public_client.exchange_info()
            for s in info["symbols"]:
                sym = s["symbol"]
                qty_prec = 0
                price_prec = 4
                max_qty = 999999999
                min_qty = 0
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = f["stepSize"]
                        if "." in step:
                            qty_prec = len(step.rstrip("0").split(".")[1])
                        else:
                            qty_prec = 0
                        max_qty = float(f.get("maxQty", 999999999))
                        min_qty = float(f.get("minQty", 0))
                    elif f["filterType"] == "PRICE_FILTER":
                        tick = f["tickSize"]
                        if "." in tick:
                            price_prec = len(tick.rstrip("0").split(".")[1])
                        else:
                            price_prec = 0
                self._precision_cache[sym] = {
                    "qty": qty_prec,
                    "price": price_prec,
                    "max_qty": max_qty,
                    "min_qty": min_qty,
                }
            logger.info(f"Hassasiyet cache: {len(self._precision_cache)} sembol yuklendi.")
        except Exception as e:
            logger.error(f"Hassasiyet cache yuklenemedi: {e}")

    def _load_testnet_symbols(self):
        """Testnet'teki gecerli sembolleri yukle."""
        try:
            info = self.client.exchange_info()
            for s in info["symbols"]:
                if s["status"] == "TRADING":
                    self._testnet_symbols.add(s["symbol"])
            logger.info(f"Testnet: {len(self._testnet_symbols)} gecerli sembol")
        except Exception as e:
            logger.error(f"Testnet sembol listesi alinamadi: {e}")

    def _load_testnet_limits(self):
        """Testnet'in kendi max qty limitlerini yukle (farkli olabilir)."""
        try:
            info = self.client.exchange_info()
            for s in info["symbols"]:
                sym = s["symbol"]
                if sym in self._precision_cache:
                    for f in s["filters"]:
                        if f["filterType"] == "LOT_SIZE":
                            testnet_max = float(f.get("maxQty", 999999999))
                            current_max = self._precision_cache[sym]["max_qty"]
                            # Testnet limiti daha dusukse onu kullan
                            self._precision_cache[sym]["max_qty"] = min(current_max, testnet_max)
                        if f["filterType"] == "MARKET_LOT_SIZE":
                            market_max = float(f.get("maxQty", 999999999))
                            if market_max > 0:
                                current = self._precision_cache[sym]["max_qty"]
                                self._precision_cache[sym]["max_qty"] = min(current, market_max)
            logger.info("Testnet limitleri yuklendi.")
        except Exception as e:
            logger.error(f"Testnet limit yuklenemedi: {e}")

    def is_tradeable(self, symbol: str) -> bool:
        """Bu sembolde islem acilabilir mi?"""
        if config.BINANCE_TESTNET and self._testnet_symbols:
            return symbol in self._testnet_symbols
        return True

    def get_quantity_precision(self, symbol: str) -> int:
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]["qty"]
        return 0

    def get_price_precision(self, symbol: str) -> int:
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]["price"]
        return 4

    def place_market_order(self, symbol: str, side: str,
                           quantity: float) -> Optional[dict]:
        """Market emri gonder (hassasiyet + max/min qty kontrolu)."""
        try:
            qty_precision = self.get_quantity_precision(symbol)
            adjusted_qty = round(quantity, qty_precision)
            if adjusted_qty <= 0:
                adjusted_qty = 10 ** (-qty_precision)

            # Max/min quantity kontrolu
            if symbol in self._precision_cache:
                max_qty = self._precision_cache[symbol]["max_qty"]
                min_qty = self._precision_cache[symbol]["min_qty"]
                if adjusted_qty > max_qty:
                    logger.warning(f"{symbol} miktar {adjusted_qty} > max {max_qty}, kisitlaniyor")
                    adjusted_qty = round(max_qty, qty_precision)
                if adjusted_qty < min_qty:
                    logger.warning(f"{symbol} miktar {adjusted_qty} < min {min_qty}, islem yapilamaz")
                    return None

            order = self.client.new_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=adjusted_qty,
            )
            logger.info(
                f"MARKET emir: {symbol} {side} x{adjusted_qty} | "
                f"OrderID: {order.get('orderId')}"
            )
            return order
        except Exception as e:
            logger.error(f"Market emir HATASI ({symbol} {side}): {e}")
            return None

    def place_stop_market(self, symbol: str, side: str,
                          stop_price: float) -> Optional[dict]:
        """Stop-Market emri gonder. Basarisiz olursa bot kendi takip eder."""
        try:
            price_precision = self.get_price_precision(symbol)
            adjusted_price = round(stop_price, price_precision)

            order = self.client.new_order(
                symbol=symbol,
                side=side,
                type="STOP_MARKET",
                stopPrice=adjusted_price,
                closePosition=True,
            )
            logger.info(
                f"STOP emir: {symbol} {side} @ {adjusted_price} | "
                f"OrderID: {order.get('orderId')}"
            )
            return order
        except Exception as e:
            # Testnet veya bazi coinlerde STOP_MARKET desteklenmeyebilir
            # Bot kendi monitor sistemiyle stop takibi yapiyor
            logger.warning(
                f"Stop emir gonderilemedi ({symbol}), bot takip edecek: {e}"
            )
            return None

    def close_position(self, symbol: str, side: str,
                       quantity: float) -> Optional[dict]:
        """Pozisyonu kapat (ters yonde market emri)."""
        close_side = "SELL" if side == "BUY" else "BUY"
        return self.place_market_order(symbol, close_side, quantity)

    def cancel_all_orders(self, symbol: str):
        """Bir semboldeki tum acik emirleri iptal et."""
        try:
            self.client.cancel_open_orders(symbol=symbol)
            logger.info(f"Tum emirler iptal edildi: {symbol}")
        except Exception as e:
            logger.error(f"Emir iptal HATASI ({symbol}): {e}")

    def get_current_price(self, symbol: str) -> float:
        """Anlik fiyati al (islem yapilan client'tan - testnet/canli)."""
        try:
            ticker = self.client.ticker_price(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logger.error(f"Fiyat alinamadi ({symbol}): {e}")
            return 0.0

    def get_all_futures_symbols(self) -> list:
        """Binance Futures'taki tum aktif USDT sembollerini al (gercek veri)."""
        try:
            info = self.public_client.exchange_info()
            symbols = [
                s["symbol"] for s in info["symbols"]
                if s["contractType"] == "PERPETUAL"
                and s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
            ]
            logger.info(f"Binance Futures: {len(symbols)} aktif USDT sembol")
            return symbols
        except Exception as e:
            logger.error(f"Sembol listesi alinamadi: {e}")
            return []

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Sembol bilgilerini al (hassasiyet vs.)."""
        try:
            info = self.client.exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    return s
            return None
        except Exception as e:
            logger.error(f"Sembol bilgisi alinamadi ({symbol}): {e}")
            return None
