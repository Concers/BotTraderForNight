#!/usr/bin/env python3
from __future__ import annotations
"""
BotTraderForNight - Ana Bot Dongusu
====================================
1) CMC'den 45M-75M market cap coinleri ceker
2) Binance Futures'ta 3dk ve 5dk mumlari analiz eder
3) CANSLIM skorlar, uygun olanlarda islem acar
4) Telegram'a detayli rapor gonderir
5) Pozisyon takibi (breakeven, time-stop)
"""

import asyncio
import time
from datetime import datetime
from logger_setup import setup_logger
from binance_client import BinanceClient
from telegram_bot import TelegramNotifier, TelegramSignalReceiver
from indicators import run_all_indicators
from scoring import CANSLIMScorer
from risk_manager import RiskManager
from market_filter import MarketCapFilter
from trade_journal import TradeJournal, CoinLists
from report_generator import generate_pdf_report
from wallet import Wallet
from market_scanner import MarketScanner
import config

logger = setup_logger("Bot")


class TradingBot:
    """Ana trading bot."""

    def __init__(self):
        logger.info("=" * 50)
        logger.info("BotTraderForNight baslatiliyor...")
        logger.info("=" * 50)

        self.binance = BinanceClient()
        self.notifier = TelegramNotifier()
        self.scorer = CANSLIMScorer()
        self.risk = RiskManager()
        self.market_filter = MarketCapFilter(self.binance)
        self.journal = TradeJournal()
        self.coin_lists = CoinLists()
        self.wallet = Wallet()
        self.scanner = MarketScanner(self.binance)
        self._monitoring = True
        self._futures_symbols = set()

    async def recover_open_positions(self):
        """
        Bot acildiginda Binance'teki acik pozisyonlari kontrol et.
        Varsa risk manager'a kaydet ve Telegram'a rapor gonder.
        """
        logger.info("Acik pozisyonlar kontrol ediliyor...")
        positions = self.binance.get_open_positions()

        if not positions:
            logger.info("Acik pozisyon yok.")
            await self.notifier.send_message("🔄 Bot yeniden basladi. Acik pozisyon yok.")
            return

        msg_lines = ["🔄 <b>BOT YENIDEN BASLADI</b>\n━━━━━━━━━━━━━━━━━━"]

        for pos in positions:
            symbol = pos["symbol"]
            side = pos["side"]
            entry = pos["entry_price"]
            mark = pos["mark_price"]
            pnl = pos["unrealized_pnl"]
            qty = pos["quantity"]

            # Adaptive stop hesapla
            df = self.binance.get_klines(symbol, interval="3m", limit=200)
            if not df.empty:
                df = run_all_indicators(df)
                stop_info = self.risk.get_adaptive_stop_loss(df, entry, side)
                stop_price = stop_info["stop_price"]
                time_limit = stop_info["time_limit_min"]
            else:
                # Veri yoksa basit stop (%2)
                stop_price = entry * 0.98 if side == "BUY" else entry * 1.02
                time_limit = 30

            # Risk manager'a kaydet
            trade = self.risk.register_trade(
                symbol=symbol, side=side, entry_price=entry,
                stop_price=stop_price, quantity=qty,
                time_limit_min=time_limit,
            )
            # Time-stop'u sifirla (ne zaman acildigini bilmiyoruz, 30dk ver)
            trade.entry_time = time.time()

            # Kar durumuna gore TP flag'lerini ayarla
            profit_pct = trade.current_profit_pct(mark)
            if profit_pct >= 1.5:
                trade.tp1_hit = True
                trade.breakeven_activated = True
                trade.stop_price = entry  # Breakeven
            if profit_pct >= 3.0:
                trade.tp2_hit = True

            emoji = "🟢" if pnl >= 0 else "🔴"
            msg_lines.append(
                f"\n{emoji} <b>{symbol} {'LONG' if side == 'BUY' else 'SHORT'}</b>\n"
                f"   Giris: {entry} | Simdi: {mark}\n"
                f"   PnL: <b>${pnl:+.2f}</b> (%{profit_pct:+.2f})\n"
                f"   Miktar: {qty} | Stop: {stop_price:.6f}"
            )

        msg_lines.append(f"\n━━━━━━━━━━━━━━━━━━\n📂 Toplam: {len(positions)} acik pozisyon")
        await self.notifier.send_message("\n".join(msg_lines))
        logger.info(f"{len(positions)} acik pozisyon yuklendi.")

    async def process_signal(self, side: str, symbol: str):
        """
        Ana sinyal isleme pipeline'i.
        Sequential Processing: Receipt -> Analysis -> Synthesis -> Execution -> Monitoring
        """
        logger.info(f"{'='*40}")
        logger.info(f"SINYAL ISLENIYOR: {side} {symbol}")
        logger.info(f"{'='*40}")

        # --- 1. HIZLI FILTRELER ---

        # Vadeli islemde var mi?
        if self._futures_symbols and symbol not in self._futures_symbols:
            logger.warning(f"{symbol} vadeli islemlerde yok. Atlanacak.")
            return

        # Testnet'te gecerli mi?
        if not self.binance.is_tradeable(symbol):
            logger.warning(f"{symbol} testnet'te yok. Atlanacak.")
            return

        # Blacklist kontrolu
        if self.coin_lists.is_blacklisted(symbol):
            logger.info(f"{symbol} blacklist'te. Atlanacak.")
            return

        # Max acik islem kontrolu
        if not self.risk.can_open_trade():
            return

        # Kasa kontrolu ($50 marjin var mi?)
        if not self.wallet.can_open_trade():
            logger.warning(f"Kasa yetersiz: ${self.wallet.available_balance:.2f}")
            return

        # Ayni coinde acik islem var mi?
        if symbol in self.risk.active_trades:
            logger.warning(f"{symbol} icin zaten acik islem var. Atlanacak.")
            return

        # Market cap filtresi kaldirildi - tum coinler taranacak

        # --- 2. VERI AL ve INDIKATOR HESAPLA ---
        df = self.binance.get_klines(symbol, interval="3m", limit=200)
        if df.empty:
            logger.error(f"{symbol} icin veri alinamadi.")
            return

        df = run_all_indicators(df)

        # BTC verisi
        btc_df = self.binance.get_klines("BTCUSDT", interval="3m", limit=200)
        if not btc_df.empty:
            btc_df = run_all_indicators(btc_df)

        current_price = df["close"].iloc[-1]

        # --- 3. CANSLIM SKORLAMA ---
        score = self.scorer.calculate_score(
            df, symbol, btc_df if not btc_df.empty else None
        )

        if score["decision"] == "REJECTED":
            self.journal.record_rejected(symbol, score, "Skor dusuk")
            return

        # --- 4. RISK HESAPLAMA ---
        stop_info = self.risk.get_adaptive_stop_loss(df, current_price, side)

        # Bakiye: Binance'ten al, yoksa sabit bakiye kullan
        balance = self.binance.get_account_balance()
        if balance <= 0:
            balance = 1000.0  # Varsayilan bakiye ($1000)
            logger.info(f"Binance bakiye alinamadi, sabit bakiye: ${balance}")

        # Pozisyon: bakiyenin %10'u x 20 kaldirac = $100 x 20 = $2000 pozisyon
        quantity = self.risk.calculate_position_size(
            balance, current_price, stop_info["stop_price"],
            score["allocation_pct"]
        )

        if quantity <= 0:
            logger.error("Pozisyon buyuklugu 0.")
            return

        # --- 5. TELEGRAM RAPOR (her zaman gonder) ---
        await self.notifier.send_signal_report(
            symbol, side, score, stop_info, quantity
        )

        # --- 6. BINANCE EMIR (API key varsa) ---
        if config.BINANCE_API_KEY and config.BINANCE_API_KEY != "your_binance_api_key":
            self.binance.set_leverage(symbol, config.DEFAULT_LEVERAGE)

            order = self.binance.place_market_order(symbol, side, quantity)
            if not order:
                await self.notifier.send_message(
                    f"⚠️ {symbol} {side} sinyal gonderildi ama emir acilamadi."
                )
                return

            stop_side = "SELL" if side == "BUY" else "BUY"
            self.binance.place_stop_market(symbol, stop_side, stop_info["stop_price"])

            self.risk.register_trade(
                symbol=symbol, side=side,
                entry_price=current_price,
                stop_price=stop_info["stop_price"],
                quantity=quantity,
                time_limit_min=stop_info["time_limit_min"],
            )
            self.journal.record_trade_open(symbol, side, current_price,
                                           stop_info["stop_price"], quantity, score)
            self.wallet.open_trade(symbol, side, current_price)
            logger.info(f"ISLEM ACILDI: {symbol} {side} @ {current_price} | Kasa: ${self.wallet.total_balance:.2f}")
        else:
            self.journal.record_trade_open(symbol, side, current_price,
                                           stop_info["stop_price"], quantity, score)
            self.wallet.open_trade(symbol, side, current_price)
            logger.info(f"SINYAL (emir yok): {symbol} {side} @ {current_price} | Kasa: ${self.wallet.total_balance:.2f}")

    async def _safe_monitor(self):
        """Monitor'u hata yutarak calistir - ASLA durmasin."""
        while self._monitoring:
            try:
                await self.monitor_positions()
            except Exception as e:
                logger.error(f"Monitor hatasi (yeniden basliyor): {e}")
                await asyncio.sleep(5)

    async def periodic_report(self):
        """Her 20 dakikada Telegram'a durum raporu gonder."""
        await asyncio.sleep(60)  # Ilk rapor 1dk sonra
        while self._monitoring:
            try:
                positions = self.binance.get_open_positions()
                balance = self.binance.get_account_balance()
                if balance <= 0:
                    balance = 350.0

                s = self.journal.get_summary()
                total_pnl = sum(p["unrealized_pnl"] for p in positions)
                open_count = len(positions)

                # Acik pozisyon detaylari
                pos_lines = []
                for p in sorted(positions, key=lambda x: x["unrealized_pnl"], reverse=True):
                    side_txt = "L" if p["side"] == "BUY" else "S"
                    emoji = "🟢" if p["unrealized_pnl"] >= 0 else "🔴"
                    entry = p["entry_price"]
                    mark = p["mark_price"]
                    if p["side"] == "BUY":
                        pct = ((mark - entry) / entry) * 100 if entry > 0 else 0
                    else:
                        pct = ((entry - mark) / entry) * 100 if entry > 0 else 0
                    coin = p["symbol"].replace("USDT", "")
                    pos_lines.append(
                        f"{emoji} {coin} {side_txt} | ${p['unrealized_pnl']:+.2f} (%{pct:+.1f})"
                    )

                pos_text = "\n".join(pos_lines) if pos_lines else "Acik pozisyon yok"

                # Kapanan islem sayisi ve PnL
                closed_pnl = s["total_pnl"]
                combined_pnl = closed_pnl + total_pnl

                # Kasa bilgisi
                w = self.wallet
                kasa_pnl_pct = ((w.total_balance - 350) / 350) * 100

                await self.notifier.send_message(
                    f"📊 <b>20DK RAPOR</b> ({datetime.now().strftime('%H:%M')})\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💼 KASA: <b>${w.total_balance:.2f}</b> "
                    f"(${w.data['total_pnl']:+.2f} | %{kasa_pnl_pct:+.1f})\n"
                    f"💵 Kullanilabilir: ${w.available_balance:.2f}\n"
                    f"📂 Acik: {open_count} | Kapanan: {s['closed']}\n"
                    f"🎯 Win Rate: %{s['win_rate']:.0f} "
                    f"({w.data['wins']}W/{w.data['losses']}L)\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{pos_text}"
                )
            except Exception as e:
                logger.error(f"Periyodik rapor hatasi: {e}")

            await asyncio.sleep(1200)  # 20 dakika

    async def auto_shutdown(self, hours: float = 3.0):
        """Belirli sure sonra botu kapat ve final rapor olustur."""
        total_seconds = int(hours * 3600)
        logger.info(f"Bot {hours} saat sonra kapanacak ({total_seconds}sn)")

        await self.notifier.send_message(
            f"⏱️ Bot {hours:.0f} saat boyunca calisacak.\n"
            f"Kapanma: {datetime.now().strftime('%H:%M')} + {hours:.0f}saat"
        )

        await asyncio.sleep(total_seconds)

        logger.info("SURE DOLDU - Bot kapaniyor...")

        # Tum acik pozisyonlari kapat
        positions = self.binance.get_open_positions()
        for p in positions:
            try:
                close_side = "SELL" if p["side"] == "BUY" else "BUY"
                self.binance.cancel_all_orders(p["symbol"])
                self.binance.close_position(p["symbol"], p["side"], p["quantity"])
                self.journal.record_trade_close(
                    p["symbol"],
                    self.binance.get_current_price(p["symbol"]),
                    "BOT KAPANIYOR - 3 saat doldu"
                )
            except Exception as e:
                logger.error(f"Kapanma hatasi ({p['symbol']}): {e}")

        # PDF rapor olustur ve gonder
        try:
            pdf_path = generate_pdf_report()
            s = self.journal.get_summary()

            await self.notifier.send_message(
                f"🏁 <b>3 SAAT TEST TAMAMLANDI</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Toplam islem: {s['total_trades']}\n"
                f"Kazanan: {s['wins']} | Kaybeden: {s['losses']}\n"
                f"Win Rate: %{s['win_rate']:.0f}\n"
                f"Toplam PnL: <b>${s['total_pnl']:+.2f}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"PDF rapor gonderiliyor..."
            )

            await self.notifier.send_document(pdf_path, "Detayli 3 Saatlik Rapor")
        except Exception as e:
            logger.error(f"Final rapor hatasi: {e}")

        # Botu durdur
        self._monitoring = False
        logger.info("Bot kapatildi.")

    async def market_scanner_loop(self):
        """Her 10 dakikada Market Roentgeni taramasi."""
        await asyncio.sleep(30)  # Ilk tarama 30sn sonra
        while self._monitoring:
            try:
                logger.info("Market Scanner basliyor...")
                await self.scanner.scan()

                # Telegram'a rapor gonder
                report = self.scanner.generate_telegram_report()
                await self.notifier.send_message(report)

                # Watchlist guncelle
                watchlist = self.scanner.update_watchlists()
                await self.notifier.send_message(self.scanner.get_watchlist_report())

            except Exception as e:
                logger.error(f"Scanner hatasi: {e}")

            await asyncio.sleep(600)  # 10 dakika

    async def _safe_scanner(self):
        """Scanner'i hata yutarak calistir."""
        while self._monitoring:
            try:
                await self.market_scanner_loop()
            except Exception as e:
                logger.error(f"Scanner crash (yeniden basliyor): {e}")
                await asyncio.sleep(30)

    async def _safe_scan(self):
        """Scan'i hata yutarak calistir - ASLA durmasin."""
        while self._monitoring:
            try:
                await self.auto_scan()
            except Exception as e:
                logger.error(f"Scan hatasi (yeniden basliyor): {e}")
                await asyncio.sleep(10)

    async def auto_scan(self):
        """
        Otomatik tarama dongusu:
        1. CMC'den 45M-75M coinleri cek
        2. Binance Futures'ta olanlari filtrele
        3. Her coin icin 3dk ve 5dk mumlari analiz et
        4. CANSLIM skoru yeterli olanlarda sinyal uret
        """
        logger.info("Otomatik tarayici baslatildi. Ilk tarama basliyor...")
        await asyncio.sleep(2)

        while self._monitoring:
            try:
                await self._run_scan_cycle()
            except Exception as e:
                logger.error(f"Tarama dongusu hatasi: {e}")

            # Her 5 dakikada bir tara (538 coin ~4.5dk suruyor)
            logger.info("Sonraki tarama 5 dakika sonra...")
            await asyncio.sleep(300)

    async def _run_scan_cycle(self):
        """Tek bir tarama dongusunu calistir - TUM COINLER."""
        # 1. Tum Binance Futures sembollerini al
        futures_list = self.binance.get_all_futures_symbols()
        if not futures_list:
            logger.warning("Sembol listesi alinamadi.")
            return

        self._futures_symbols = set(futures_list)
        tradeable = futures_list  # TUM COINLER

        await self.notifier.send_message(
            f"🔍 <b>TARAMA BASLADI</b>\n"
            f"Toplam: {len(tradeable)} coin\n"
            f"Analiz: 3dk + 5dk mumlar"
        )

        # BTC verisi (tum coinler icin ortak - 1 kez cek)
        btc_df = self.binance.get_klines("BTCUSDT", interval="3m", limit=200)
        if not btc_df.empty:
            btc_df = run_all_indicators(btc_df)

        signals_found = 0
        scanned = 0
        scan_start = time.time()

        for symbol in tradeable:
            if not self.risk.can_open_trade():
                break
            if symbol in self.risk.active_trades:
                continue

            try:
                signal = await self._analyze_coin(symbol, btc_df)
                scanned += 1
                if signal:
                    signals_found += 1
                    await self.process_signal(signal["side"], symbol)
            except Exception as e:
                logger.error(f"{symbol} analiz hatasi: {e}")

            # API rate limit (0.5sn - Binance limiti 1200 req/dk)
            await asyncio.sleep(0.5)

        scan_duration = time.time() - scan_start
        self.journal.record_scan(scanned, signals_found, scan_duration)
        logger.info(f"Tarama bitti: {scanned} coin, {signals_found} sinyal, {scan_duration:.0f}sn.")

        # Ozet rapor
        summary_j = self.journal.get_summary()
        await self.notifier.send_message(
            f"📊 <b>TARAMA TAMAMLANDI</b>\n"
            f"Taranan: {scanned} coin | Sinyal: {signals_found}\n"
            f"⏳ Sonraki tarama 3 dakika sonra.\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 Toplam: {summary_j['total_trades']} islem | "
            f"Acik: {summary_j['open']} | "
            f"Kapanan: {summary_j['closed']}\n"
            f"💰 PnL: ${summary_j['total_pnl']:+.2f} | "
            f"Win: %{summary_j['win_rate']:.0f}"
        )

    async def _analyze_coin(self, symbol: str, btc_df) -> dict | None:
        """
        Tek bir coini analiz et.
        Oncelik: 3dk mumlar (hizli sinyal). UT Bot zorunlu degil - skor karar verir.
        """
        # Vadeli islemde yoksa analiz yapma
        if self._futures_symbols and symbol not in self._futures_symbols:
            return None

        # Blacklist kontrolu
        if self.coin_lists.is_blacklisted(symbol):
            return None

        # --- 3 DAKIKALIK ANALIZ (ana timeframe) ---
        df_3m = self.binance.get_klines(symbol, interval="3m", limit=200)
        if df_3m.empty or len(df_3m) < 50:
            return None
        df_3m = run_all_indicators(df_3m)

        # --- YON BELIRLEME (sikiласtirilmis) ---
        close = df_3m["close"].iloc[-1]
        vwap = df_3m["vwap"].iloc[-1]
        ut = df_3m["ut_signal"].iloc[-1]
        ema20 = df_3m["ema_20"].iloc[-1] if "ema_20" in df_3m.columns else close
        ema50 = df_3m["ema_50"].iloc[-1] if "ema_50" in df_3m.columns else close
        rsi = df_3m["rsi"].iloc[-1]
        adx = df_3m["adx"].iloc[-1] if "adx" in df_3m.columns else 0

        # ADX < 20 = trend yok, islem acma
        if adx < 20:
            return None

        # HACIM ONAY: Son mum hacmi > 20 mum ortalama x 1.5
        vol_spike = df_3m["volume_spike"].iloc[-1] if "volume_spike" in df_3m.columns else False
        vol_ratio = df_3m["volume_ratio"].iloc[-1] if "volume_ratio" in df_3m.columns else 0
        if not vol_spike and vol_ratio < 1.2:
            return None  # Hacim onay yok, testere piyasa riski

        # Yukari sinyaller say (daha katı: 6 uzerinden 4 olmali)
        bull_signals = 0
        if ut == 1: bull_signals += 1          # UT Bot BUY
        if close > vwap: bull_signals += 1     # Fiyat > VWAP
        if close > ema20: bull_signals += 1    # Fiyat > EMA20
        if ema20 > ema50: bull_signals += 1    # EMA20 > EMA50 (trend onayi)
        if 40 <= rsi <= 65: bull_signals += 1  # RSI saglikli
        if adx > 25: bull_signals += 1         # Guclu trend

        # Asagi sinyaller (SHORT)
        bear_signals = 0
        if ut == -1: bear_signals += 1          # UT Bot SELL
        if close < vwap: bear_signals += 1      # Fiyat < VWAP
        if close < ema20: bear_signals += 1     # Fiyat < EMA20
        if ema20 < ema50: bear_signals += 1     # EMA20 < EMA50 (dusus trendi)
        if 25 <= rsi <= 55: bear_signals += 1   # RSI bearish bolge
        if adx > 25: bear_signals += 1          # Guclu trend

        # En az 4/6 sinyal olmali (eski: 3/5)
        if bull_signals >= 4:
            side = "BUY"
            if rsi > config.RSI_OVERBOUGHT:
                return None
        elif bear_signals >= 4:
            side = "SELL"
            if rsi < config.RSI_OVERSOLD:
                return None
        else:
            return None

        # --- CANSLIM SKORLAMA (min skor 70) ---
        score = self.scorer.calculate_score(
            df_3m, symbol, btc_df if btc_df is not None and not btc_df.empty else None
        )

        # SHORT icin skor esigi 70, LONG icin 75
        min_score = 70 if side == "SELL" else config.MIN_CONFIDENCE_SCORE
        if score["score"] < min_score:
            self.journal.record_rejected(symbol, score, f"Skor {score['score']} < {min_score}")
            return None

        # T (Trend) bileseni: LONG min 60, SHORT min 50
        min_trend = 50 if side == "SELL" else 60
        if score["components"].get("T", 0) < min_trend:
            self.journal.record_rejected(symbol, score, "Trend zayif")
            return None

        # --- 5DK DOGRULAMA (ZORUNLU) ---
        # 5dk timeframe'de de ayni yonde trend olmali
        try:
            df_5m = self.binance.get_klines(symbol, interval="5m", limit=200)
            if df_5m.empty or len(df_5m) < 50:
                return None
            df_5m = run_all_indicators(df_5m)

            close_5m = df_5m["close"].iloc[-1]
            vwap_5m = df_5m["vwap"].iloc[-1]
            ema20_5m = df_5m["ema_20"].iloc[-1] if "ema_20" in df_5m.columns else close_5m

            # 5dk'da da ayni yon olmali
            if side == "BUY":
                confirms_5m = close_5m > vwap_5m and close_5m > ema20_5m
            else:
                confirms_5m = close_5m < vwap_5m and close_5m < ema20_5m

            if not confirms_5m:
                self.journal.record_rejected(symbol, score, "5dk onay yok")
                return None

        except Exception:
            return None

        logger.info(
            f"SINYAL: {symbol} {side} | Skor={score['score']} | "
            f"UT={ut} | VWAP={'ustunde' if close > vwap else 'altinda'} | "
            f"RSI={rsi:.0f} | ADX={adx:.0f} | 5dk=ONAY"
        )

        return {"side": side, "symbol": symbol, "score": score, "both_tf": True}

    async def monitor_positions(self):
        """
        Sonsuz Trailing Stop ile pozisyon takibi.
        Pozisyon ASLA kapatilmaz - sadece stop yukari cekilir.
        Her %1.2 adimda stop guncellenir (%1.4 geride).
        Pozisyon SADECE stop-loss'a dusunce kapanir.
        """
        tick = 0
        while self._monitoring:
            open_count = len(self.risk.active_trades)
            if open_count == 0:
                await asyncio.sleep(5)
                continue

            for symbol in list(self.risk.active_trades.keys()):
                trade = self.risk.active_trades[symbol]
                try:
                    current_price = self.binance.get_current_price(symbol)
                except Exception:
                    continue

                if current_price == 0:
                    continue

                # Son mumun dip/tepe verisini guncelle (trailing stop icin)
                try:
                    klines = self.binance.get_klines(symbol, interval="3m", limit=2)
                    if not klines.empty and len(klines) >= 2:
                        prev = klines.iloc[-2]
                        self.risk.update_candle_data(
                            symbol, float(prev["low"]), float(prev["high"])
                        )
                except Exception:
                    pass

                should_close, reason = self.risk.should_close(symbol, current_price)

                if should_close:
                    logger.info(f"POZISYON KAPATILIYOR: {symbol} | {reason}")
                    try:
                        self.binance.cancel_all_orders(symbol)
                        self.binance.close_position(symbol, trade.side, trade.quantity)
                    except Exception as e:
                        logger.error(f"Kapatma emri hatasi ({symbol}): {e}")

                    try:
                        await self.notifier.send_close_report(
                            symbol, trade.side, trade.entry_price,
                            current_price, reason
                        )
                    except Exception as e:
                        logger.error(f"Telegram rapor hatasi ({symbol}): {e}")

                    self.journal.record_trade_close(symbol, current_price, reason)
                    pnl = self.wallet.close_trade(
                        symbol, trade.side, trade.entry_price,
                        current_price, reason
                    )
                    self.risk.close_trade(symbol, reason)

            # Her 30 saniyede durum logu
            tick += 1
            if tick % 10 == 0:
                statuses = []
                for sym, t in self.risk.active_trades.items():
                    try:
                        p = self.binance.get_current_price(sym)
                        pct = t.current_profit_pct(p)
                        statuses.append(f"{sym}:%{pct:+.1f}")
                    except Exception:
                        pass
                if statuses:
                    logger.info(f"MONITOR: {' | '.join(statuses)}")

            await asyncio.sleep(3)

    async def handle_status_command(self, update):
        """'/status' komutu icin handler."""
        balance = self.binance.get_account_balance()
        await self.notifier.send_status(
            self.risk.open_trade_count, balance
        )

    async def handle_durum_command(self):
        """/durum komutu - detayli rapor."""
        # Oncelikle KASA raporu gonder
        await self.notifier.send_message(self.wallet.get_report())

        balance = self.binance.get_account_balance()
        if balance <= 0:
            balance = 350.0

        positions = self.binance.get_open_positions()
        await self.notifier.send_full_report(
            balance, positions, self.risk.active_trades
        )

        # Journal ozeti
        s = self.journal.get_summary()
        await self.notifier.send_message(
            f"📒 <b>ISLEM GECMISI</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Toplam islem: {s['total_trades']} | Acik: {s['open']}\n"
            f"Kazanan: {s['wins']} | Kaybeden: {s['losses']}\n"
            f"Win Rate: <b>%{s['win_rate']:.0f}</b>\n"
            f"Toplam PnL: <b>${s['total_pnl']:+.2f}</b>\n"
            f"Ort. kazanc: ${s['avg_win']:.2f} | Ort. kayip: ${s['avg_loss']:.2f}\n"
            f"Reddedilen: {s['rejected_count']} | Tarama: {s['scan_count']}"
        )

        # Coin listeleri
        await self.notifier.send_message(self.coin_lists.get_report())

    async def handle_rapor_command(self):
        """/rapor komutu - anlik PDF rapor olustur ve gonder."""
        try:
            pdf_path = generate_pdf_report()
            await self.notifier.send_document(pdf_path, "Anlik Detay Raporu")
        except Exception as e:
            logger.error(f"PDF rapor hatasi: {e}")
            await self.notifier.send_message(f"PDF olusturulamadi: {e}")

    async def handle_market_command(self):
        """/market komutu - Market Roentgeni simdi tara."""
        try:
            await self.scanner.scan()
            report = self.scanner.generate_telegram_report()
            await self.notifier.send_message(report)
            self.scanner.update_watchlists()
        except Exception as e:
            logger.error(f"Market scanner hatasi: {e}")
            await self.notifier.send_message(f"Scanner hatasi: {e}")

    async def handle_watchlist_command(self):
        """/watchlist komutu."""
        await self.notifier.send_message(self.scanner.get_watchlist_report())

    def run(self):
        """Botu baslat."""
        logger.info("Bot baslatiliyor...")

        # Telegram receiver olustur
        receiver = TelegramSignalReceiver(on_signal_callback=self.process_signal)
        receiver.status_callback = self.handle_status_command
        receiver.scan_callback = self._run_scan_cycle
        receiver.durum_callback = self.handle_durum_command
        receiver.rapor_callback = self.handle_rapor_command
        receiver.market_callback = self.handle_market_command
        receiver.watchlist_callback = self.handle_watchlist_command
        app = receiver.build_app()

        async def post_init(application):
            await receiver._drop_pending(application)

            # Acik pozisyonlari yukle
            try:
                await self.recover_open_positions()
            except Exception as e:
                logger.error(f"Recovery hatasi (devam ediliyor): {e}")

            # Monitor ve scan task'larini baslat
            loop = asyncio.get_event_loop()
            loop.create_task(self._safe_monitor())
            loop.create_task(self._safe_scan())
            loop.create_task(self._safe_scanner())
            loop.create_task(self.periodic_report())
            loop.create_task(self.auto_shutdown(hours=3.0))
            logger.info(">>> MONITOR + SCAN + SCANNER + 20DK RAPOR + 3 SAAT TIMER BASLATILDI <<<")

        app.post_init = post_init

        logger.info("Telegram dinleniyor... Sinyal bekleniyor.")
        logger.info(f"Mod: {'TESTNET' if config.BINANCE_TESTNET else 'CANLI'}")
        logger.info(f"Max islem: {config.MAX_OPEN_TRADES}")
        logger.info(f"Market cap: ${config.MIN_MARKET_CAP:,.0f}-${config.MAX_MARKET_CAP:,.0f}")
        logger.info(f"Min skor: {config.MIN_CONFIDENCE_SCORE}")

        # Polling baslat
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
