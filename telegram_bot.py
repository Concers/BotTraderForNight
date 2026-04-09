import asyncio
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from logger_setup import setup_logger
import config

logger = setup_logger("TelegramBot")


class TelegramNotifier:
    """Telegram mesaj gonderici."""

    def __init__(self):
        self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        self.chat_id = config.TELEGRAM_CHAT_ID

    async def send_message(self, text: str):
        """Mesaj gonder."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Telegram mesaj gonderilemedi: {e}")

    async def send_document(self, file_path: str, caption: str = ""):
        """PDF/dosya gonder."""
        try:
            with open(file_path, "rb") as f:
                await self.bot.send_document(
                    chat_id=self.chat_id,
                    document=f,
                    caption=caption,
                )
        except Exception as e:
            logger.error(f"Telegram dosya gonderilemedi: {e}")

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
        await self.send_message(msg)

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
        await self.send_message(msg)

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

    def build_app(self) -> Application:
        """Telegram Application olustur."""
        self.app = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .build()
        )
        # Drop pending updates - eski birikimis mesajlari atla
        self.app.post_init = self._drop_pending

        self.app.add_handler(CommandHandler("start", self.handle_start))
        self.app.add_handler(CommandHandler("status", self.handle_status))
        self.app.add_handler(CommandHandler("durum", self.handle_durum))
        self.app.add_handler(CommandHandler("rapor", self.handle_rapor))
        self.app.add_handler(CommandHandler("market", self.handle_market))
        self.app.add_handler(CommandHandler("watchlist", self.handle_watchlist))
        self.app.add_handler(CommandHandler("scan", self.handle_scan))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        return self.app

    async def _drop_pending(self, app: Application):
        """Eski birikimis mesajlari temizle."""
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Eski birikimis mesajlar silindi.")
