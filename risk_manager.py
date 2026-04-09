from __future__ import annotations
import time
import pandas as pd
import ta as ta_lib
from logger_setup import setup_logger
import config

logger = setup_logger("RiskManager")


class ActiveTrade:
    """
    v2.1 - Agresif Kar Korumali Trailing Stop.

    3 ASAMALI KORUMA:
      1. BREAKEVEN: %2 kara ulasinca stop = giris + %0.5 (garantici)
      2. TRAILING: Her %1 yeni tepe -> stop = onceki mumun dibi
      3. GAP KORUMA: Kayip %5 gecerse acil kapat

    ZAMAN YONETIMI:
      - Karda ise: Sinirsiz bekleme (trailing stop karar verir)
      - Zararda ise: 180dk sonra kapat (TimeOut_Loss)

    AGTUSDT VAKASI COZUMU:
      +$217 kari koruyamadik cunku breakeven yoktu.
      Simdi %2 karda stop otomatik girisa cekilir, asla zarara donemez.
    """

    def __init__(self, symbol: str, side: str, entry_price: float,
                 stop_price: float, quantity: float, time_limit_min: int):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.quantity = quantity
        self.time_limit_min = time_limit_min
        self.entry_time = time.time()
        self.highest_price = entry_price
        self.highest_profit_pct = 0.0
        self.breakeven_hit = False
        self.last_candle_low = 0.0   # Son mumun dibi (trailing icin)
        self.last_candle_high = 0.0  # Son mumun tepesi

    @property
    def elapsed_minutes(self) -> float:
        return (time.time() - self.entry_time) / 60

    def current_profit_pct(self, current_price: float) -> float:
        if self.side == "BUY":
            return ((current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100


class RiskManager:
    """v2.1 - Tum risk yonetimi."""

    def __init__(self):
        self.active_trades: dict[str, ActiveTrade] = {}

    @property
    def open_trade_count(self) -> int:
        return len(self.active_trades)

    def can_open_trade(self) -> bool:
        if self.open_trade_count >= config.MAX_OPEN_TRADES:
            logger.warning(f"Max islem limiti ({config.MAX_OPEN_TRADES}) doldu.")
            return False
        return True

    def get_adaptive_stop_loss(self, df: pd.DataFrame, entry_price: float,
                                side: str) -> dict:
        """v2.1 Adaptive Stop - daha genis ama yapisal."""
        atr = df["atr"].iloc[-1]
        volatility_pct = (atr / entry_price) * 100

        multiplier = 4.0 if volatility_pct > 1.0 else 3.5
        atr_stop_dist = atr * multiplier

        # Minimum %2.5 mesafe
        min_stop_dist = entry_price * 0.025
        atr_stop_dist = max(atr_stop_dist, min_stop_dist)

        if side == "BUY":
            recent_low = df["low"].tail(10).min()
            structural_stop = recent_low * 0.995
            final_stop = min(entry_price - atr_stop_dist, structural_stop)
        else:
            recent_high = df["high"].tail(10).max()
            structural_stop = recent_high * 1.005
            final_stop = max(entry_price + atr_stop_dist, structural_stop)

        # Zaman: zararda 180dk, karda sinirsiz
        time_limit = 180

        result = {
            "stop_price": round(final_stop, 6),
            "time_limit_min": time_limit,
            "volatility_pct": round(volatility_pct, 2),
            "multiplier": multiplier,
        }

        logger.info(
            f"Stop-Loss | Vol: %{result['volatility_pct']} | "
            f"x{multiplier} | Stop: {result['stop_price']}"
        )
        return result

    def register_trade(self, symbol: str, side: str, entry_price: float,
                       stop_price: float, quantity: float,
                       time_limit_min: int) -> ActiveTrade:
        trade = ActiveTrade(
            symbol=symbol, side=side, entry_price=entry_price,
            stop_price=stop_price, quantity=quantity,
            time_limit_min=time_limit_min,
        )
        self.active_trades[symbol] = trade
        logger.info(
            f"Islem kaydedildi: {symbol} {side} @ {entry_price} | Stop: {stop_price}"
        )
        return trade

    def update_candle_data(self, symbol: str, candle_low: float, candle_high: float):
        """Monitor'dan son mum verisini guncelle (trailing stop icin)."""
        trade = self.active_trades.get(symbol)
        if trade:
            trade.last_candle_low = candle_low
            trade.last_candle_high = candle_high

    def should_close(self, symbol: str, current_price: float) -> tuple[bool, str]:
        """
        v2.1 Pozisyon Yonetimi - 4 Katman:

        1. BREAKEVEN: %2 karda -> stop = giris + %0.5 (AGTUSDT cozumu)
        2. AGRESIF TRAILING: Her %1 yeni tepe -> stop = onceki mumun dibi
        3. GAP KORUMA: Kayip %5 gecerse acil kapat
        4. TIMEOUT: Zararda 180dk -> TimeOut_Loss

        Karda ise zaman limiti YOK - trailing stop karar verir.
        """
        trade = self.active_trades.get(symbol)
        if not trade:
            return False, ""

        profit_pct = trade.current_profit_pct(current_price)

        # --- EN IYI FIYATI GUNCELLE ---
        if trade.side == "BUY" and current_price > trade.highest_price:
            trade.highest_price = current_price
        elif trade.side == "SELL" and current_price < trade.highest_price:
            trade.highest_price = current_price

        if profit_pct > trade.highest_profit_pct:
            trade.highest_profit_pct = profit_pct

        # ============================================
        # KATMAN 1: BREAKEVEN (%2 karda)
        # AGTUSDT cozumu: +$217 kari bir daha kaybetmeyecegiz
        # ============================================
        if not trade.breakeven_hit and profit_pct >= 2.0:
            trade.breakeven_hit = True
            old_stop = trade.stop_price

            # Stop = giris + %0.5 (komisyon + kucuk kar garantisi)
            if trade.side == "BUY":
                new_stop = trade.entry_price * 1.005
            else:
                new_stop = trade.entry_price * 0.995

            # Sadece yukari cek
            if trade.side == "BUY" and new_stop > trade.stop_price:
                trade.stop_price = new_stop
            elif trade.side == "SELL" and new_stop < trade.stop_price:
                trade.stop_price = new_stop

            logger.info(
                f"BREAKEVEN: {symbol} | Kar %{profit_pct:.1f} | "
                f"Stop: {old_stop:.6f} -> {trade.stop_price:.6f} (giris+%0.5)"
            )

        # ============================================
        # KATMAN 2: AGRESIF TRAILING
        # Her %1 yeni tepe -> stop = onceki mumun dibi (veya %0.8 geride)
        # ============================================
        if trade.breakeven_hit and profit_pct >= 2.0:
            # Her %1'lik yeni tepe
            steps_above_be = int((trade.highest_profit_pct - 2.0) / 1.0)
            current_steps = int((profit_pct - 2.0) / 1.0) if profit_pct >= 2.0 else 0

            # Son mum dibi varsa onu kullan, yoksa %0.8 geride
            if trade.last_candle_low > 0 and trade.side == "BUY":
                candidate_stop = trade.last_candle_low * 0.999  # Mumun hemen altı
            elif trade.last_candle_high > 0 and trade.side == "SELL":
                candidate_stop = trade.last_candle_high * 1.001
            else:
                # Fallback: en iyi fiyattan %0.8 geride
                if trade.side == "BUY":
                    candidate_stop = trade.highest_price * (1 - 0.008)
                else:
                    candidate_stop = trade.highest_price * (1 + 0.008)

            # Stop ASLA asagi inmez
            if trade.side == "BUY" and candidate_stop > trade.stop_price:
                old = trade.stop_price
                trade.stop_price = candidate_stop
                stop_pct = trade.current_profit_pct(candidate_stop)
                logger.info(
                    f"TRAILING: {symbol} | Kar %{profit_pct:.1f} | "
                    f"Stop: {old:.6f} -> {candidate_stop:.6f} (stop kari: %{stop_pct:.1f})"
                )
            elif trade.side == "SELL" and candidate_stop < trade.stop_price:
                old = trade.stop_price
                trade.stop_price = candidate_stop
                stop_pct = trade.current_profit_pct(candidate_stop)
                logger.info(
                    f"TRAILING: {symbol} | Kar %{profit_pct:.1f} | "
                    f"Stop: {old:.6f} -> {candidate_stop:.6f} (stop kari: %{stop_pct:.1f})"
                )

        # ============================================
        # KATMAN 3: GAP KORUMA
        # ============================================
        if profit_pct < -5.0:
            pnl = (profit_pct / 100) * trade.entry_price * trade.quantity
            return True, (
                f"GAP_KORUMA (kayip %{profit_pct:.1f}) | PnL: ${pnl:.2f}"
            )

        # ============================================
        # KATMAN 4: STOP-LOSS
        # ============================================
        if trade.side == "BUY" and current_price <= trade.stop_price:
            pnl = (current_price - trade.entry_price) * trade.quantity
            label = "TRAILING_PROFIT" if trade.breakeven_hit else "STOP_LOSS"
            return True, (
                f"{label} @ {trade.stop_price:.6f} | "
                f"Kar: %{profit_pct:.2f} | PnL: ${pnl:.2f}"
            )
        elif trade.side == "SELL" and current_price >= trade.stop_price:
            pnl = (trade.entry_price - current_price) * trade.quantity
            label = "TRAILING_PROFIT" if trade.breakeven_hit else "STOP_LOSS"
            return True, (
                f"{label} @ {trade.stop_price:.6f} | "
                f"Kar: %{profit_pct:.2f} | PnL: ${pnl:.2f}"
            )

        # ============================================
        # KATMAN 5: ZAMAN YONETIMI (sadece zararda)
        # Karda ise sinirsiz bekle - trailing stop karar verir
        # ============================================
        if trade.elapsed_minutes >= trade.time_limit_min:
            pnl = (profit_pct / 100) * trade.entry_price * trade.quantity
            if profit_pct > 0:
                # KARDA: zaman limiti uzat (+60dk bonus)
                trade.time_limit_min += 60
                logger.info(
                    f"TIMEOUT_EXTEND: {symbol} karda (%{profit_pct:.1f}), "
                    f"+60dk uzatildi -> {trade.time_limit_min}dk"
                )
                return False, ""
            else:
                # ZARARDA: kapat
                return True, (
                    f"TIMEOUT_LOSS ({trade.elapsed_minutes:.0f}dk) | "
                    f"PnL: %{profit_pct:.1f} (${pnl:.2f})"
                )

        return False, ""

    def close_trade(self, symbol: str, reason: str):
        trade = self.active_trades.pop(symbol, None)
        if trade:
            logger.info(
                f"Islem kapatildi: {symbol} | {reason} | "
                f"Sure: {trade.elapsed_minutes:.1f}dk"
            )

    def calculate_position_size(self, balance: float, entry_price: float,
                                 stop_price: float, allocation_pct: int) -> float:
        """Sabit $50 marjin x 20x = $1,000 notional."""
        margin_per_trade = 50.0
        notional = margin_per_trade * config.DEFAULT_LEVERAGE

        if entry_price <= 0:
            return 0

        quantity = notional / entry_price
        logger.info(
            f"Pozisyon: ${margin_per_trade} x {config.DEFAULT_LEVERAGE}x "
            f"= ${notional} | Miktar={quantity:.4f}"
        )
        return quantity
