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

            # Kar durumuna gore T1/T2 flag'lerini ayarla
            profit_pct = trade.current_profit_pct(mark)
            if profit_pct >= 2.0:
                trade.breakeven_hit = True
                trade.stop_price = entry * 1.003 if side == "BUY" else entry * 0.997
            if profit_pct >= 4.0:
                trade.t2_hit = True
                trade.stop_price = entry * 1.015 if side == "BUY" else entry * 0.985

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

    async def process_signal(self, side: str, symbol: str, coin_profile=None):
        """
        Ana sinyal isleme pipeline'i.
        Sequential Processing: Receipt -> Analysis -> Synthesis -> Execution -> Monitoring

        coin_profile: SHORT icin scanner'dan gelen CoinProfile (CANSLIM atlanir).
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

        # Kasa kontrolu ($20 marjin var mi?)
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

        # --- 3. SKORLAMA ---
        # Scanner'da hesaplanan setup'i kullan (cift hesaplama yapma).
        # Eger coin_profile yoksa (manuel sinyal vb.) burada yeniden hesapla.
        mood = self.scanner.market_mood if self.scanner else "NOTR"

        if side == "BUY":
            from long_strategy import (
                analyze_long_setup, setup_to_score_dict as long_to_dict,
                should_open_long
            )
            if coin_profile is not None and coin_profile.long_setup:
                setup = coin_profile.long_setup
            else:
                setup = analyze_long_setup(
                    df,
                    btc_perf_1h=self.scanner.btc_perf_1h if self.scanner else 0.0,
                    price_change_24h=(coin_profile.price_change_24h
                                       if coin_profile else 0.0),
                )
            if not should_open_long(setup, mood):
                logger.info(
                    f"{symbol} LONG skoru yetersiz: {setup['score']} (mood:{mood})"
                )
                self.journal.record_rejected(
                    symbol, {"score": setup["score"], "components": {}},
                    f"LONG skor dusuk: {setup['score']}"
                )
                return
            score = long_to_dict(setup)
        else:
            from short_strategy import (
                analyze_short_setup, setup_to_score_dict as short_to_dict,
                should_open_short
            )
            if coin_profile is not None and coin_profile.short_setup:
                setup = coin_profile.short_setup
            else:
                setup = analyze_short_setup(
                    df,
                    btc_perf_1h=self.scanner.btc_perf_1h if self.scanner else 0.0,
                    price_change_24h=(coin_profile.price_change_24h
                                       if coin_profile else 0.0),
                )
            if not should_open_short(setup, mood):
                logger.info(
                    f"{symbol} SHORT skoru yetersiz: {setup['score']} (mood:{mood})"
                )
                self.journal.record_rejected(
                    symbol, {"score": setup["score"], "components": {}},
                    f"SHORT skor dusuk: {setup['score']}"
                )
                return
            score = short_to_dict(setup)

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

        # --- 5. TELEGRAM RAPOR ---
        await self.notifier.send_signal_report(
            symbol, side, score, stop_info, quantity
        )

        # --- 6. SANAL ISLEM AC (Binance YOK, sadece sanal kasa) ---
        self.risk.register_trade(
            symbol=symbol, side=side,
            entry_price=current_price,
            stop_price=stop_info["stop_price"],
            quantity=quantity,
            time_limit_min=stop_info["time_limit_min"],
        )
        self.journal.record_trade_open(
            symbol, side, current_price,
            stop_info["stop_price"], quantity, score
        )
        self.wallet.open_trade(symbol, side, current_price)
        logger.info(
            f"SANAL ISLEM ACILDI: {symbol} {side} @ {current_price} | "
            f"Kasa: ${self.wallet.total_balance:.2f}"
        )

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
                    balance = 150.0

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
                kasa_pnl_pct = ((w.total_balance - w.data['initial_balance']) / w.data['initial_balance']) * 100

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
                    f"{pos_text}",
                    category="rapor",
                )
            except Exception as e:
                logger.error(f"Periyodik rapor hatasi: {e}")

            await asyncio.sleep(1200)  # 20 dakika

    async def market_scanner_loop(self):
        """
        Scanner bazli islem sistemi.
        Her 5dk mum kapanisinda:
          1. Market Roentgeni tara
          2. STRONG BUY -> LONG ac
          3. STRONG SELL -> SHORT ac
          4. Telegram'a rapor gonder
        """
        await asyncio.sleep(10)
        while self._monitoring:
            try:
                # Sonraki 5dk mum kapanisina senkronize ol
                now = time.time()
                next_candle = (int(now / 300) + 1) * 300
                wait = next_candle - now + 5
                logger.info(f"Sonraki mum kapanisina {wait:.0f}sn...")
                await asyncio.sleep(wait)

                logger.info("Market Scanner + Islem taramasi basliyor...")
                await self.scanner.scan()
                summary = self.scanner.get_summary()

                # LONG SIGNAL - Oncelik: STRONG_BUY > BUY > NOTR,
                # sonra previously_tracked, sonra RVOL, sonra long_score
                from long_strategy import should_open_long
                long_candidates = (
                    summary.get("strong_buy", [])
                    + summary.get("buy", [])
                    + summary.get("notr", [])
                )

                def _long_priority(c):
                    cat_rank = (0 if c.category == "STRONG_BUY"
                                else 1 if c.category == "BUY" else 2)
                    tracked_rank = 0 if c.previously_tracked == "long" else 1
                    return (cat_rank, tracked_rank, -c.rvol, -c.long_score)

                long_candidates.sort(key=_long_priority)

                for coin in long_candidates:
                    if coin.rsi >= 99 or coin.long_score < 55:
                        continue
                    # Funding kontrarian filtresi: >%0.1 = LONG pahalli, atla
                    if coin.funding_rate > 0.001:
                        logger.info(
                            f"LONG atlandi ({coin.symbol}): funding "
                            f"%{coin.funding_rate*100:.3f} cok yuksek"
                        )
                        continue
                    setup = {"score": coin.long_score}
                    if not should_open_long(setup, summary.get("market_mood", "NOTR")):
                        continue
                    tracked_tag = " [TRACKED]" if coin.previously_tracked == "long" else ""
                    logger.info(
                        f"LONG SIGNAL: {coin.symbol}{tracked_tag} | "
                        f"Cat:{coin.category} RVOL:{coin.rvol} "
                        f"LongScore:{coin.long_score} F:%{coin.funding_rate*100:.3f}"
                    )
                    await self.process_signal("BUY", coin.symbol, coin_profile=coin)

                # SHORT SIGNAL - Oncelik: STRONG_SELL > SELL > NOTR,
                # sonra previously_tracked, sonra RVOL, sonra short_score
                from short_strategy import should_open_short
                short_candidates = (
                    summary.get("strong_sell", [])
                    + summary.get("sell", [])
                    + summary.get("notr", [])
                )

                def _short_priority(c):
                    cat_rank = (0 if c.category == "STRONG_SELL"
                                else 1 if c.category == "SELL" else 2)
                    tracked_rank = 0 if c.previously_tracked == "short" else 1
                    return (cat_rank, tracked_rank, -c.rvol, -c.short_score)

                short_candidates.sort(key=_short_priority)

                for coin in short_candidates:
                    if coin.rsi >= 99 or coin.short_score < 55:
                        continue
                    # Funding kontrarian filtresi: <-%0.1 = SHORT pahalli, atla
                    if coin.funding_rate < -0.001:
                        logger.info(
                            f"SHORT atlandi ({coin.symbol}): funding "
                            f"%{coin.funding_rate*100:.3f} cok dusuk"
                        )
                        continue
                    setup = {"score": coin.short_score}
                    if not should_open_short(setup, summary.get("market_mood", "NOTR")):
                        continue
                    tracked_tag = " [TRACKED]" if coin.previously_tracked == "short" else ""
                    logger.info(
                        f"SHORT SIGNAL: {coin.symbol}{tracked_tag} | "
                        f"Cat:{coin.category} RVOL:{coin.rvol} "
                        f"ShortScore:{coin.short_score} F:%{coin.funding_rate*100:.3f}"
                    )
                    await self.process_signal("SELL", coin.symbol, coin_profile=coin)

                # Telegram rapor
                report = self.scanner.generate_telegram_report()
                await self.notifier.send_message(report, category="market")

                # Watchlist guncelle
                self.scanner.update_watchlists()

            except Exception as e:
                logger.error(f"Scanner hatasi: {e}")

            await asyncio.sleep(10)  # Kisa bekleme, dongu basi senkronize ediyor

    async def _safe_scanner(self):
        """Scanner'i hata yutarak calistir."""
        while self._monitoring:
            try:
                await self.market_scanner_loop()
            except Exception as e:
                logger.error(f"Scanner crash (yeniden basliyor): {e}")
                await asyncio.sleep(30)

    # auto_scan kaldirildi - artik market_scanner_loop tek islem sistemi

    async def monitor_positions(self):
        """
        v3.0 Stop-Only + T1/T2 pozisyon takibi.
        Islem SADECE stop-loss patlarsa kapanir. Zaman limiti YOK.
        T1 (%2): breakeven | T2 (%4): kar kilidi | Trailing: kademeli daraltma.
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

                # --- $5 ZARAR: COKLU TF OYU (5m+3m+1m) / 15sn cooldown ---
                # $10 sert esik: coklu TF yine "kapat" dedi mi -> kapat
                if self.risk.should_trigger_early_warning(
                    symbol, current_price, threshold_dollars=-5.0
                ):
                    now_ts = time.time()
                    cooldown_ok = (now_ts - trade.last_exit_vote_ts) >= 15.0
                    if cooldown_ok:
                        trade.last_exit_vote_ts = now_ts
                        try:
                            tf_results = {}
                            for tf in ("5m", "3m", "1m"):
                                df_tf = self.binance.get_klines(
                                    symbol, interval=tf, limit=100
                                )
                                if df_tf.empty or len(df_tf) < 30:
                                    continue
                                df_tf = run_all_indicators(df_tf)
                                tf_results[tf] = self.risk.tf_exit_vote(
                                    df_tf, trade.side
                                )

                            if tf_results:
                                decision = self.risk.multi_tf_exit_decision(
                                    tf_results
                                )
                                pnl_usd = self.risk.get_pnl_dollars(
                                    symbol, current_price
                                )
                                hard = self.risk.should_hard_close(
                                    symbol, current_price, hard_threshold=-10.0
                                )

                                # Ilk kez tetikleniyorsa Telegram'a bildir
                                first_time = not trade.early_warning_sent
                                should_exit = (
                                    decision["decision"] == "close"
                                ) and (first_time or hard)

                                if first_time or should_exit:
                                    await self.notifier.send_multi_tf_warning(
                                        symbol, trade.side, trade.entry_price,
                                        current_price, pnl_usd,
                                        trade.current_profit_pct(current_price),
                                        tf_results, decision, hard, should_exit,
                                    )
                                    self.risk.mark_warning_sent(symbol)

                                if should_exit:
                                    reason_tag = (
                                        "HARD_EXIT" if hard else "MULTI_TF_EXIT"
                                    )
                                    reason = (
                                        f"{reason_tag} (${pnl_usd:.2f}) | "
                                        f"{decision['vote_count']}/"
                                        f"{decision['total_tf']} TF kapat"
                                    )
                                    logger.info(f"{reason_tag}: {symbol} | {reason}")
                                    self.journal.record_trade_close(
                                        symbol, current_price, reason
                                    )
                                    self.wallet.close_trade(
                                        symbol, trade.side, trade.entry_price,
                                        current_price, reason
                                    )
                                    self.risk.close_trade(symbol, reason)
                                    continue
                        except Exception as e:
                            logger.error(f"Coklu TF oyu hatasi ({symbol}): {e}")

                should_close, reason = self.risk.should_close(symbol, current_price)

                if should_close:
                    logger.info(f"SANAL KAPATILIYOR: {symbol} | {reason}")

                    # Telegram rapor
                    try:
                        await self.notifier.send_close_report(
                            symbol, trade.side, trade.entry_price,
                            current_price, reason
                        )
                    except Exception as e:
                        logger.error(f"Telegram rapor hatasi ({symbol}): {e}")

                    # Sanal kasa + journal guncelle
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
        """/durum komutu - detayli rapor (SANAL pozisyonlar)."""
        # Oncelikle KASA raporu gonder
        await self.notifier.send_message(self.wallet.get_report())

        # Sanal moda gore - Binance yerine active_trades'ten okuyoruz
        positions = []
        for sym, trade in self.risk.active_trades.items():
            try:
                mark = self.binance.get_current_price(sym)
            except Exception:
                mark = trade.entry_price
            if mark == 0:
                mark = trade.entry_price

            if trade.side == "BUY":
                pnl = (mark - trade.entry_price) * trade.quantity
            else:
                pnl = (trade.entry_price - mark) * trade.quantity

            positions.append({
                "symbol": sym,
                "side": trade.side,
                "entry_price": trade.entry_price,
                "mark_price": mark,
                "unrealized_pnl": pnl,
                "quantity": trade.quantity,
            })

        await self.notifier.send_full_report(
            self.wallet.total_balance, positions, self.risk.active_trades
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

    async def handle_resetkasa_command(self, update):
        """/resetkasa EVET onayi sonrasi kasayi sifirla."""
        try:
            # Active trades + journal + wallet temizle
            for sym in list(self.risk.active_trades.keys()):
                self.risk.close_trade(sym, "RESET")
            self.journal.reset() if hasattr(self.journal, "reset") else None
            self.wallet.reset()
            await update.message.reply_text(
                f"✅ <b>KASA SIFIRLANDI</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💼 Yeni kasa: <b>${self.wallet.total_balance:.2f}</b>\n"
                f"📂 Acik islem: 0\n"
                f"📊 Journal: temizlendi",
                parse_mode="HTML",
            )
            logger.info("KASA RESET: /resetkasa ile sifirlandi")
        except Exception as e:
            logger.error(f"Reset hatasi: {e}")
            await update.message.reply_text(f"❌ Reset hatasi: {e}")

    async def handle_pozisyonlar_command(self):
        """/pozisyonlar - acik LONG/SHORT pozisyonlar (sanal)."""
        trades = self.risk.active_trades

        if not trades:
            await self.notifier.send_message(
                f"📂 <b>POZISYONLAR</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Acik pozisyon yok.\n\n"
                f"💼 Kasa: ${self.wallet.total_balance:.2f}\n"
                f"💵 Kullanilabilir: ${self.wallet.available_balance:.2f}"
            )
            return

        long_list = []
        short_list = []
        total_pnl = 0

        for sym, trade in trades.items():
            try:
                price = self.binance.get_current_price(sym)
            except Exception:
                price = trade.entry_price

            if price == 0:
                price = trade.entry_price

            pct = trade.current_profit_pct(price)
            # Notional bazli PnL ($1000)
            notional = trade.quantity * trade.entry_price
            pnl = (pct / 100) * notional
            total_pnl += pnl

            line = {
                "symbol": sym,
                "entry": trade.entry_price,
                "current": price,
                "pct": pct,
                "pnl": pnl,
                "stop": trade.stop_price,
                "elapsed": trade.elapsed_minutes,
                "breakeven": trade.breakeven_hit,
            }

            if trade.side == "BUY":
                long_list.append(line)
            else:
                short_list.append(line)

        lines = [
            f"📂 <b>ACIK POZISYONLAR</b>",
            f"━━━━━━━━━━━━━━━━━━",
            f"💼 Kasa: ${self.wallet.total_balance:.2f}",
            f"💰 Acik PnL: <b>${total_pnl:+.2f}</b>",
            f"📊 Toplam: {len(trades)} | 🟢 LONG: {len(long_list)} | 🔴 SHORT: {len(short_list)}",
        ]

        if long_list:
            lines.append(f"\n🟢 <b>LONG POZISYONLAR ({len(long_list)})</b>")
            for p in sorted(long_list, key=lambda x: x["pnl"], reverse=True):
                coin = p["symbol"].replace("USDT", "")
                emoji = "🟢" if p["pnl"] >= 0 else "🔴"
                be_tag = " 🛡" if p["breakeven"] else ""
                lines.append(
                    f"{emoji} <b>{coin}</b>{be_tag}\n"
                    f"   Giris: {p['entry']} | Simdi: {p['current']}\n"
                    f"   PnL: <b>${p['pnl']:+.2f}</b> (%{p['pct']:+.2f})\n"
                    f"   Stop: {p['stop']:.6f} | Sure: {p['elapsed']:.0f}dk"
                )

        if short_list:
            lines.append(f"\n🔴 <b>SHORT POZISYONLAR ({len(short_list)})</b>")
            for p in sorted(short_list, key=lambda x: x["pnl"], reverse=True):
                coin = p["symbol"].replace("USDT", "")
                emoji = "🟢" if p["pnl"] >= 0 else "🔴"
                be_tag = " 🛡" if p["breakeven"] else ""
                lines.append(
                    f"{emoji} <b>{coin}</b>{be_tag}\n"
                    f"   Giris: {p['entry']} | Simdi: {p['current']}\n"
                    f"   PnL: <b>${p['pnl']:+.2f}</b> (%{p['pct']:+.2f})\n"
                    f"   Stop: {p['stop']:.6f} | Sure: {p['elapsed']:.0f}dk"
                )

        await self.notifier.send_message("\n".join(lines))

    def run(self):
        """Botu baslat."""
        logger.info("Bot baslatiliyor...")

        # Telegram receiver olustur
        receiver = TelegramSignalReceiver(on_signal_callback=self.process_signal)
        receiver.status_callback = self.handle_status_command
        receiver.scan_callback = self.handle_market_command
        receiver.durum_callback = self.handle_durum_command
        receiver.rapor_callback = self.handle_rapor_command
        receiver.market_callback = self.handle_market_command
        receiver.watchlist_callback = self.handle_watchlist_command
        receiver.pozisyonlar_callback = self.handle_pozisyonlar_command
        receiver.reset_callback = self.handle_resetkasa_command
        app = receiver.build_app()

        async def post_init(application):
            await receiver._drop_pending(application)

            # Ikinci botu (varsa) baslat - komutlar her iki botta da calissin
            app2 = receiver.build_secondary_app()
            if app2 is not None:
                try:
                    await app2.initialize()
                    await app2.bot.delete_webhook(drop_pending_updates=True)
                    await app2.start()
                    await app2.updater.start_polling(drop_pending_updates=True)
                    logger.info("Ikinci Telegram botu polling basladi.")
                except Exception as e:
                    logger.error(f"Ikinci bot baslatilamadi: {e}")

            # Recovery: aktif islemler json'dan yuklendi mi?
            recovered = list(self.risk.active_trades.keys())
            recovery_text = ""
            if recovered:
                lines = [f"\n♻️ <b>RECOVERY: {len(recovered)} pozisyon yuklendi</b>"]
                for sym in recovered:
                    t = self.risk.active_trades[sym]
                    side_txt = "LONG" if t.side == "BUY" else "SHORT"
                    be = " 🛡" if t.breakeven_hit else ""
                    lines.append(
                        f"  {sym} {side_txt}{be} @ {t.entry_price} | Stop: {t.stop_price}"
                    )
                recovery_text = "\n".join(lines)

            # Sanal kasa modu - Binance recovery yok
            await self.notifier.send_message(
                f"🤖 <b>Bot baslatildi (SANAL MOD)</b>\n"
                f"💼 Kasa: ${self.wallet.total_balance:.2f}\n"
                f"📊 Acik islem: {len(recovered)}/{config.MAX_OPEN_TRADES}\n"
                f"💵 Kullanilabilir: ${self.wallet.available_balance:.2f}"
                f"{recovery_text}"
            )

            # Monitor ve scan task'larini baslat
            loop = asyncio.get_event_loop()
            loop.create_task(self._safe_monitor())
            loop.create_task(self._safe_scanner())
            loop.create_task(self.periodic_report())
            # auto_shutdown kaldirildi - bot 7/24 calisir, pm2 yonetir
            logger.info(">>> MONITOR + SCANNER + 20DK RAPOR BASLATILDI (sonsuz) <<<")

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
