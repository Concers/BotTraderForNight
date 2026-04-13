import asyncio
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from logger_setup import setup_logger
from notification_prefs import NotificationPrefs, CATEGORIES
import config

logger = setup_logger("TelegramBot")


class TelegramNotifier:
    """Telegram mesaj gonderici. Chat basina bildirim kategorisi filtresi uygular."""

    def __init__(self):
        # (bot, chat_id, label) listesi - ikinci bot opsiyonel
        self.targets = [
            (Bot(token=config.TELEGRAM_BOT_TOKEN),
             config.TELEGRAM_CHAT_ID, "primary")
        ]
        if config.TELEGRAM_BOT_TOKEN_2 and config.TELEGRAM_CHAT_ID_2:
            self.targets.append(
                (Bot(token=config.TELEGRAM_BOT_TOKEN_2),
                 config.TELEGRAM_CHAT_ID_2, "secondary")
            )
            logger.info("Ikinci Telegram botu aktif.")

        # Geri uyumluluk (varolan kodda kullanilan)
        self.bot = self.targets[0][0]
        self.chat_id = self.targets[0][1]

        # Bildirim ayarlari (chat basina kategori on/off)
        self.prefs = NotificationPrefs()

    async def send_message(self, text: str, category: str = None):
        """
        Mesaji hedeflere gonder. category verilirse, o kategoriyi kapatmis
        chat'ler mesaji almaz. category=None: her zaman gider (sistem mesaji).
        """
        for bot, chat_id, label in self.targets:
            if category and not self.prefs.is_enabled(chat_id, category):
                continue
            try:
                await bot.send_message(
                    chat_id=chat_id, text=text, parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Telegram mesaj gonderilemedi ({label}): {e}")

    async def send_to_chat(self, chat_id: str, text: str):
        """Belirli bir chat'e mesaj gonder (komut cevabi icin). Filtre uygulanmaz."""
        for bot, cid, label in self.targets:
            if str(cid) == str(chat_id):
                try:
                    await bot.send_message(
                        chat_id=cid, text=text, parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"send_to_chat hatasi ({label}): {e}")
                return

    async def send_document(self, file_path: str, caption: str = ""):
        """PDF/dosyayi tum hedef botlara gonder."""
        for bot, chat_id, label in self.targets:
            try:
                with open(file_path, "rb") as f:
                    await bot.send_document(
                        chat_id=chat_id, document=f, caption=caption,
                    )
            except Exception as e:
                logger.error(f"Telegram dosya gonderilemedi ({label}): {e}")

    async def send_signal_report(self, symbol: str, side: str, score: dict,
                                  stop_info: dict, quantity: float):
        """Sinyal detay raporu gonder."""
        components = score["components"]
        msg = (
            f"{'🟢' if side == 'BUY' else '🔴'} <b>{symbol} {side}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>CANSLIM Skoru: {score['score']}/100</b>\n"
            f"   C (Momentum) : {components['C']}\n"
            f"   A (Volatilite): {components['A']}\n"
            f"   V (Hacim)     : {components['V']}\n"
            f"   S (Arz/Talep) : {components['S']}\n"
            f"   L (RS Gucu)   : {components['L']}\n"
            f"   M (Market)    : {components['M']}\n"
            f"   T (Trend)     : {components['T']}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Karar: <b>{score['decision']}</b>\n"
            f"🛡️ Stop-Loss: {stop_info['stop_price']}\n"
            f"⏱️ Zaman Limiti: {stop_info['time_limit_min']}dk\n"
            f"📦 Miktar: {quantity:.4f}\n"
            f"💹 Volatilite: %{stop_info['volatility_pct']}"
        )
        await self.send_message(msg, category="sinyal")

    async def send_close_report(self, symbol: str, side: str, entry_price: float,
                                 exit_price: float, reason: str):
        """Pozisyon kapanma raporu."""
        if side == "BUY":
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100

        emoji = "✅" if pnl_pct > 0 else "❌"
        msg = (
            f"{emoji} <b>POZISYON KAPANDI: {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 Giris: {entry_price}\n"
            f"📍 Cikis: {exit_price}\n"
            f"📈 PnL: <b>%{pnl_pct:.2f}</b>\n"
            f"📋 Sebep: {reason}"
        )
        await self.send_message(msg, category="kapanma")

    async def send_early_warning(self, symbol: str, side: str,
                                  entry_price: float, current_price: float,
                                  pnl_dollars: float, pnl_pct: float,
                                  analysis: dict, auto_closing: bool):
        """
        $8 zarar erken uyarisi - detayli trend saglik raporu.
        auto_closing=True ise bot otomatik kapatiyor.
        """
        side_txt = "LONG" if side == "BUY" else "SHORT"
        header = "🚨 ERKEN KAPATMA" if auto_closing else "⚠️ ERKEN UYARI"
        verdict = (
            "Trend kirildi, pozisyon kapatiliyor."
            if auto_closing else
            "Trend hala saglikli, pozisyon tutuluyor."
        )

        reasons_txt = (
            "\n".join(f"   • {r}" for r in analysis.get("reasons", []))
            or "   • Bozulma sinyali yok"
        )

        msg = (
            f"{header}: <b>{symbol} {side_txt}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💸 Zarar: <b>${pnl_dollars:+.2f}</b> (%{pnl_pct:+.2f})\n"
            f"📍 Giris: {entry_price} | Simdi: {current_price}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔬 <b>TREND SAGLIK</b>\n"
            f"   RSI: {analysis['rsi']} | ADX: {analysis['adx']}"
            f"{' ⬇' if analysis.get('adx_falling') else ''}\n"
            f"   EMA trend: {analysis['ema_trend']}\n"
            f"   Son mum: {analysis['last_candle']} | "
            f"Karsi: {analysis['opposing_candles']}/3\n"
            f"   Hacim: x{analysis['vol_ratio']}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Bozulma ({analysis['broken_count']}/5):</b>\n"
            f"{reasons_txt}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 {verdict}"
        )
        await self.send_message(msg, category="uyari")

    async def send_multi_tf_warning(self, symbol: str, side: str,
                                     entry_price: float, current_price: float,
                                     pnl_dollars: float, pnl_pct: float,
                                     tf_results: dict, decision: dict,
                                     hard: bool, closing: bool):
        """
        $5 zarar coklu TF (5m/3m/1m) oy raporu.
        closing=True: kapatiliyor. hard=True: $10 sert esik asildi.
        """
        side_txt = "LONG" if side == "BUY" else "SHORT"
        if closing:
            header = "🚨 SERT KAPATMA" if hard else "🚨 COKLU TF KAPATMA"
        else:
            header = "⚠️ $5 ZARAR UYARISI"

        lines = [
            f"{header}: <b>{symbol} {side_txt}</b>",
            f"━━━━━━━━━━━━━━━━━━",
            f"💸 Zarar: <b>${pnl_dollars:+.2f}</b> (%{pnl_pct:+.2f})",
            f"📍 Giris: {entry_price} | Simdi: {current_price}",
            f"━━━━━━━━━━━━━━━━━━",
            f"🗳️ <b>OYLAMA: {decision['vote_count']}/{decision['total_tf']} TF kapat</b>",
        ]

        for tf, r in tf_results.items():
            vote_emoji = "❌" if r["vote"] == "close" else "✅"
            trend_txt = "+" if r["trend"] > 0 else "-" if r["trend"] < 0 else "0"
            rsi_txt = "+" if r["rsi_sig"] > 0 else "-" if r["rsi_sig"] < 0 else "0"
            lines.append(
                f"  {vote_emoji} <b>{tf}</b> | Trend:{trend_txt} "
                f"RSI:{rsi_txt}({r['rsi']}) RVOL:{r['rvol']}x "
                f"Skor:{r['score']}"
            )

        lines.append(f"━━━━━━━━━━━━━━━━━━")
        if closing:
            tag = " ($10 sert esik)" if hard else ""
            lines.append(f"🎯 Pozisyon kapatiliyor{tag}.")
        else:
            lines.append(f"🎯 Trend tutuluyor, 15sn sonra yeniden oylanacak.")

        await self.send_message("\n".join(lines), category="uyari")

    async def send_rejected_report(self, symbol: str, score: dict):
        """Reddedilen sinyal raporu."""
        components = score["components"]
        weak = [f"{k}={v}" for k, v in components.items() if v < 50]
        msg = (
            f"⛔ <b>SINYAL REDDEDILDI: {symbol}</b>\n"
            f"Skor: {score['score']}/100 (Min: {config.MIN_CONFIDENCE_SCORE})\n"
            f"Zayif: {', '.join(weak)}"
        )
        await self.send_message(msg)

    async def send_status(self, open_trades: int, balance: float):
        """Bot durum raporu (eski, basit)."""
        msg = (
            f"📊 <b>BOT DURUMU</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Bakiye: {balance:.2f} USDT\n"
            f"📂 Acik Islem: {open_trades}/{config.MAX_OPEN_TRADES}\n"
            f"⚙️ Mod: {'TESTNET' if config.BINANCE_TESTNET else 'CANLI'}"
        )
        await self.send_message(msg)

    async def send_full_report(self, balance: float, positions: list,
                                trades: dict):
        """
        /durum komutu icin detayli rapor.
        positions: Binance'ten acik pozisyonlar
        trades: risk manager'daki aktif islemler
        """
        total_pnl = sum(p["unrealized_pnl"] for p in positions)
        total_notional = sum(p["quantity"] * p["entry_price"] for p in positions)

        emoji_pnl = "🟢" if total_pnl >= 0 else "🔴"

        lines = [
            f"📊 <b>DETAYLI DURUM RAPORU</b>",
            f"━━━━━━━━━━━━━━━━━━",
            f"💰 Bakiye: <b>{balance:.2f} USDT</b>",
            f"{emoji_pnl} Toplam PnL: <b>${total_pnl:+.2f}</b>",
            f"📂 Acik Pozisyon: <b>{len(positions)}</b>/{config.MAX_OPEN_TRADES}",
            f"💵 Toplam Notional: ${total_notional:,.2f}",
            f"⚙️ Mod: {'TESTNET' if config.BINANCE_TESTNET else 'CANLI'}",
            f"━━━━━━━━━━━━━━━━━━",
        ]

        if positions:
            # Karda olanlari uste, zararda olanlari alta sirala
            sorted_pos = sorted(positions, key=lambda p: p["unrealized_pnl"], reverse=True)

            for p in sorted_pos:
                sym = p["symbol"]
                side = "LONG" if p["side"] == "BUY" else "SHORT"
                pnl = p["unrealized_pnl"]
                entry = p["entry_price"]
                mark = p["mark_price"]
                qty = p["quantity"]

                # Kar yuzdesini hesapla
                if p["side"] == "BUY":
                    pct = ((mark - entry) / entry) * 100 if entry > 0 else 0
                else:
                    pct = ((entry - mark) / entry) * 100 if entry > 0 else 0

                # Stop bilgisi
                trade = trades.get(sym)
                stop_info = f"Stop: {trade.stop_price:.6f}" if trade else ""

                icon = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"\n{icon} <b>{sym}</b> {side}\n"
                    f"   Giris: {entry} | Simdi: {mark}\n"
                    f"   PnL: <b>${pnl:+.2f}</b> (%{pct:+.1f})\n"
                    f"   {stop_info}"
                )

            # Kar/zarar ozeti
            kar_count = sum(1 for p in positions if p["unrealized_pnl"] >= 0)
            zarar_count = len(positions) - kar_count
            lines.append(f"\n━━━━━━━━━━━━━━━━━━")
            lines.append(f"✅ Karda: {kar_count} | ❌ Zararda: {zarar_count}")
        else:
            lines.append("\nAcik pozisyon yok.")

        await self.send_message("\n".join(lines))


class TelegramSignalReceiver:
    """
    Telegram'dan gelen sinyalleri yakalayan handler.
    Sinyal formati: BUY ETHUSDT veya SELL BTCUSDT
    """

    def __init__(self, on_signal_callback):
        self.on_signal = on_signal_callback
        self.app = None
        # /resetkasa onay bekleyen chat'ler: chat_id -> timestamp (30sn timeout)
        self._reset_pending: dict[str, float] = {}
        # Bildirim tercihleri - TelegramNotifier ile ayni dosyaya yazar/okur
        self.prefs = NotificationPrefs()

    async def handle_message(self, update: Update,
                              context: ContextTypes.DEFAULT_TYPE):
        """Gelen mesaji isle."""
        if not update.message or not update.message.text:
            return

        text = update.message.text.strip().upper()
        msg_time = update.message.date.timestamp()

        # Gecikme kontrolu
        import time
        latency = time.time() - msg_time
        if latency > config.LATENCY_MAX_SECONDS:
            logger.warning(
                f"Mesaj gecikti: {latency:.1f}sn > {config.LATENCY_MAX_SECONDS}sn | "
                f"Mesaj: {text}"
            )
            return

        # /resetkasa ikinci asama: "EVET" onayi
        import time as _t
        chat_id = str(update.message.chat_id)
        if text == "EVET" and chat_id in self._reset_pending:
            ts = self._reset_pending.pop(chat_id)
            if _t.time() - ts <= 30:
                if hasattr(self, "reset_callback") and self.reset_callback:
                    await self.reset_callback(update)
                else:
                    await update.message.reply_text("❌ Reset callback tanimli degil.")
            else:
                await update.message.reply_text(
                    "⏱️ Onay suresi doldu (30sn). Tekrar /resetkasa yazin."
                )
            return

        # Sinyal parse: "BUY ETHUSDT" veya "SELL BTCUSDT"
        parts = text.split()
        if len(parts) >= 2 and parts[0] in ("BUY", "SELL"):
            side = parts[0]
            symbol = parts[1]
            logger.info(f"Sinyal alindi: {side} {symbol} (gecikme: {latency:.2f}sn)")
            await self.on_signal(side, symbol)
        else:
            logger.debug(f"Taninmayan mesaj: {text}")

    async def handle_start(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
        """'/start' komutu."""
        await update.message.reply_text(
            "🤖 Trading Bot aktif!\n\n"
            "Otomatik tarama: 3dk arayla CMC 45M-75M coinler\n"
            "Analiz: 3dk + 5dk mumlar\n\n"
            "Manuel sinyal:\n"
            "  BUY ETHUSDT\n"
            "  SELL BTCUSDT\n\n"
            "Komutlar:\n"
            "  /status - Bot durumu\n"
            "  /scan - Taramayi simdi baslat\n"
            "  /trades - Acik islemler"
        )

    async def handle_status(self, update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
        """/status komutu - bot durumunu sor."""
        if hasattr(self, "status_callback") and self.status_callback:
            await self.status_callback(update)

    async def handle_scan(self, update: Update,
                           context: ContextTypes.DEFAULT_TYPE):
        """/scan komutu - taramayi simdi tetikle."""
        if hasattr(self, "scan_callback") and self.scan_callback:
            await update.message.reply_text("🔍 Tarama baslatiliyor...")
            await self.scan_callback()

    async def handle_durum(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
        """/durum komutu - detayli rapor."""
        if hasattr(self, "durum_callback") and self.durum_callback:
            await self.durum_callback()

    async def handle_rapor(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
        """/rapor komutu - PDF rapor olustur ve gonder."""
        if hasattr(self, "rapor_callback") and self.rapor_callback:
            await update.message.reply_text("📄 PDF rapor hazirlaniyor...")
            await self.rapor_callback()

    async def handle_market(self, update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
        """/market komutu - Market Roentgeni."""
        if hasattr(self, "market_callback") and self.market_callback:
            await update.message.reply_text("🔬 Market taramasi basliyor...")
            await self.market_callback()

    async def handle_watchlist(self, update: Update,
                                context: ContextTypes.DEFAULT_TYPE):
        """/watchlist komutu."""
        if hasattr(self, "watchlist_callback") and self.watchlist_callback:
            await self.watchlist_callback()

    async def handle_pozisyonlar(self, update: Update,
                                  context: ContextTypes.DEFAULT_TYPE):
        """/pozisyonlar komutu - acik LONG/SHORT pozisyonlar."""
        if hasattr(self, "pozisyonlar_callback") and self.pozisyonlar_callback:
            await self.pozisyonlar_callback()

    async def handle_resetkasa(self, update: Update,
                                context: ContextTypes.DEFAULT_TYPE):
        """/resetkasa - iki asamali onayla kasayi sifirla. Sadece primary chat."""
        import time as _t
        chat_id = str(update.message.chat_id)
        primary_id = str(config.TELEGRAM_CHAT_ID)
        if chat_id != primary_id:
            await update.message.reply_text(
                "⛔ Bu komut sadece ana hesaptan kullanilabilir."
            )
            return

        self._reset_pending[chat_id] = _t.time()
        await update.message.reply_text(
            "⚠️ <b>KASA SIFIRLAMA ONAYI</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Wallet, acik islemler ve journal sifirlanacak.\n"
            "Onaylamak icin 30sn icinde <b>EVET</b> yazin.",
            parse_mode="HTML",
        )

    async def handle_bildirim(self, update: Update,
                               context: ContextTypes.DEFAULT_TYPE):
        """/bildirim - bildirim ayarlarini gor/degistir. Sadece komutu yazan chat'i etkiler."""
        chat_id = str(update.message.chat_id)
        args = (update.message.text or "").strip().split()[1:]

        # Argumansiz: mevcut ayarlari goster
        if not args:
            await update.message.reply_text(
                self.prefs.get_report(chat_id), parse_mode="HTML"
            )
            return

        if len(args) < 2:
            await update.message.reply_text(
                "Kullanim:\n"
                "  /bildirim &lt;tip&gt; ac|kapat\n"
                "  /bildirim hepsi ac|kapat\n\n"
                "Tipler: " + ", ".join(CATEGORIES),
                parse_mode="HTML",
            )
            return

        cat = args[0].lower()
        action = args[1].lower()
        if action not in ("ac", "aç", "kapat"):
            await update.message.reply_text("Islem: 'ac' veya 'kapat' olmali.")
            return

        enabled = action in ("ac", "aç")

        if cat == "hepsi":
            self.prefs.set_all(chat_id, enabled)
            durum = "ACILDI" if enabled else "KAPATILDI"
            await update.message.reply_text(
                f"🔔 Tum bildirimler {durum}.\n\n"
                + self.prefs.get_report(chat_id),
                parse_mode="HTML",
            )
            return

        if cat not in CATEGORIES:
            await update.message.reply_text(
                f"Gecersiz tip: {cat}\nTipler: " + ", ".join(CATEGORIES)
            )
            return

        self.prefs.set(chat_id, cat, enabled)
        durum = "✅ ACILDI" if enabled else "❌ KAPATILDI"
        await update.message.reply_text(
            f"{durum}: <b>{cat}</b>\n\n" + self.prefs.get_report(chat_id),
            parse_mode="HTML",
        )

    async def handle_komutlar(self, update: Update,
                               context: ContextTypes.DEFAULT_TYPE):
        """/komutlar - tum komutlari orneklerle listele."""
        text = (
            "📋 <b>TUM KOMUTLAR</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>Bilgi</b>\n"
            "• /durum — Detayli kasa + pozisyon raporu\n"
            "• /pozisyonlar — Acik LONG/SHORT pozisyonlar\n"
            "  (Kisa: /p)\n"
            "• /status — Kisa bot durumu\n"
            "• /rapor — Anlik PDF rapor olustur\n"
            "\n"
            "<b>Market</b>\n"
            "• /market — Market Rontgeni simdi tara\n"
            "• /scan — Taramayi elle tetikle\n"
            "• /watchlist — Watchlist raporu\n"
            "\n"
            "<b>Sinyal (sadece metin)</b>\n"
            "• BUY ETHUSDT — Manuel LONG\n"
            "• SELL BTCUSDT — Manuel SHORT\n"
            "\n"
            "<b>Bildirim Ayarlari</b>\n"
            "• /bildirim — Mevcut ayarlari goster\n"
            "• /bildirim market kapat — Market rontgeni mesajlarini kapat\n"
            "• /bildirim rapor ac — 20dk raporlari ac\n"
            "• /bildirim uyari kapat — $5/$10 uyari mesajlarini kapat\n"
            "• /bildirim sinyal kapat — Yeni pozisyon mesajlarini kapat\n"
            "• /bildirim kapanma kapat — Pozisyon kapanma mesajlarini kapat\n"
            "• /bildirim hepsi kapat — Hepsini kapat\n"
            "• /bildirim hepsi ac — Hepsini ac\n"
            "\n"
            "<b>Kasa</b>\n"
            "• /resetkasa — Kasayi sifirla (sadece ana hesap)\n"
            "  Onay: 30sn icinde EVET yaz\n"
            "\n"
            "• /komutlar — Bu liste\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    def _register_handlers(self, app: Application):
        """Handler'lari bir Application'a kaydet (primary + secondary ortak)."""
        app.add_handler(CommandHandler("start", self.handle_start))
        app.add_handler(CommandHandler("status", self.handle_status))
        app.add_handler(CommandHandler("durum", self.handle_durum))
        app.add_handler(CommandHandler("rapor", self.handle_rapor))
        app.add_handler(CommandHandler("market", self.handle_market))
        app.add_handler(CommandHandler("watchlist", self.handle_watchlist))
        app.add_handler(CommandHandler("pozisyonlar", self.handle_pozisyonlar))
        app.add_handler(CommandHandler("p", self.handle_pozisyonlar))
        app.add_handler(CommandHandler("scan", self.handle_scan))
        app.add_handler(CommandHandler("resetkasa", self.handle_resetkasa))
        app.add_handler(CommandHandler("bildirim", self.handle_bildirim))
        app.add_handler(CommandHandler("komutlar", self.handle_komutlar))
        app.add_handler(CommandHandler("help", self.handle_komutlar))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

    def build_app(self) -> Application:
        """Telegram Application olustur (birincil bot)."""
        self.app = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .build()
        )
        # Drop pending updates - eski birikimis mesajlari atla
        self.app.post_init = self._drop_pending
        self._register_handlers(self.app)
        return self.app

    def build_secondary_app(self):
        """Ikinci bot Application'ini olustur. Token yoksa None doner."""
        if not config.TELEGRAM_BOT_TOKEN_2:
            return None
        app2 = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN_2)
            .build()
        )
        self._register_handlers(app2)
        self.app2 = app2
        return app2

    async def _drop_pending(self, app: Application):
        """Eski birikimis mesajlari temizle."""
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Eski birikimis mesajlar silindi.")
