from __future__ import annotations
import time
import json
import os
import pandas as pd
import ta as ta_lib
from logger_setup import setup_logger
import config

logger = setup_logger("RiskManager")

ACTIVE_TRADES_FILE = os.path.join(
    os.path.dirname(__file__), "data", "active_trades.json"
)

# ==================== STOP-LOSS KONFIG ====================
# ATR x MULTIPLIER = stop mesafesi. Yuksek volatilite icin gevsetilir.
# v2.1 DERS: 1.5x cok yakin (20x'te anlik tetikleniyor), 3.5x+ uygun.
ATR_MULTIPLIER_LOW_VOL = 3.5   # Volatilite <= %1.0
ATR_MULTIPLIER_HIGH_VOL = 4.0  # Volatilite > %1.0
MIN_STOP_DISTANCE_PCT = 0.025  # Minimum %2.5 mesafe (20x koruma)
STRUCTURAL_BUFFER = 0.005      # Yapisal stop: son 10 mum dip/tepe +-%0.5
STRUCTURAL_LOOKBACK = 10       # Kac mum geriye bakilsin
# ==========================================================


class ActiveTrade:
    """
    v3.0 - Stop-Only + T1/T2 Kar Koruma Sistemi.

    ISLEM SADECE STOP LOSS ILE KAPANIR - zaman limiti YOK.

    T1-T2 KORUMA:
      1. T1 (%2 kar): Stop -> giris + %0.3 (breakeven, komisyon korumasi)
      2. T2 (%4 kar): Stop -> giris + %1.5 (kar kilitleme)
      3. TRAILING: T2 sonrasi her yeni zirvede stop daraltilir
      4. GAP KORUMA: Kayip %5 gecerse acil kapat (piyasa cokusu)
    """

    def __init__(self, symbol: str, side: str, entry_price: float,
                 stop_price: float, quantity: float, time_limit_min: int):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.quantity = quantity
        self.time_limit_min = time_limit_min  # Artik kullanilmiyor, uyumluluk icin
        self.entry_time = time.time()
        self.highest_price = entry_price
        self.highest_profit_pct = 0.0
        self.breakeven_hit = False
        self.t2_hit = False
        self.last_candle_low = 0.0
        self.last_candle_high = 0.0
        # $5 zarar erken uyarisi ilk tetiklemede True; sonraki oylamalar yine calisir
        self.early_warning_sent = False
        # Coklu TF oyu arasinda 15sn cooldown icin
        self.last_exit_vote_ts = 0.0
        # Giris TF'si (5m varsayilan - scanner 5dk mum kapanisinda aciyor)
        self.entry_tf = "5m"

    @property
    def elapsed_minutes(self) -> float:
        return (time.time() - self.entry_time) / 60

    def current_profit_pct(self, current_price: float) -> float:
        if self.side == "BUY":
            return ((current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100


class RiskManager:
    """v3.0 - Stop-Only + T1/T2 risk yonetimi."""

    def __init__(self):
        self.active_trades: dict[str, ActiveTrade] = {}
        self._load()

    def _save(self):
        """active_trades'i JSON'a kaydet (restart sonrasi recovery icin)."""
        try:
            os.makedirs(os.path.dirname(ACTIVE_TRADES_FILE), exist_ok=True)
            data = {}
            for sym, t in self.active_trades.items():
                data[sym] = {
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "stop_price": t.stop_price,
                    "quantity": t.quantity,
                    "time_limit_min": t.time_limit_min,
                    "entry_time": t.entry_time,
                    "highest_price": t.highest_price,
                    "highest_profit_pct": t.highest_profit_pct,
                    "breakeven_hit": t.breakeven_hit,
                    "t2_hit": t.t2_hit,
                    "last_candle_low": t.last_candle_low,
                    "last_candle_high": t.last_candle_high,
                    "early_warning_sent": t.early_warning_sent,
                    "last_exit_vote_ts": t.last_exit_vote_ts,
                    "entry_tf": t.entry_tf,
                }
            with open(ACTIVE_TRADES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"active_trades kaydedilemedi: {e}")

    def _load(self):
        """Onceki active_trades'i JSON'dan yukle."""
        if not os.path.exists(ACTIVE_TRADES_FILE):
            return
        try:
            with open(ACTIVE_TRADES_FILE, "r") as f:
                data = json.load(f)
            for sym, td in data.items():
                trade = ActiveTrade(
                    symbol=td["symbol"],
                    side=td["side"],
                    entry_price=td["entry_price"],
                    stop_price=td["stop_price"],
                    quantity=td["quantity"],
                    time_limit_min=td["time_limit_min"],
                )
                trade.entry_time = td.get("entry_time", time.time())
                trade.highest_price = td.get("highest_price", trade.entry_price)
                trade.highest_profit_pct = td.get("highest_profit_pct", 0.0)
                trade.breakeven_hit = td.get("breakeven_hit", False)
                trade.t2_hit = td.get("t2_hit", False)
                trade.last_candle_low = td.get("last_candle_low", 0.0)
                trade.last_candle_high = td.get("last_candle_high", 0.0)
                trade.early_warning_sent = td.get("early_warning_sent", False)
                trade.last_exit_vote_ts = td.get("last_exit_vote_ts", 0.0)
                trade.entry_tf = td.get("entry_tf", "5m")
                self.active_trades[sym] = trade
            if self.active_trades:
                logger.info(
                    f"RECOVERY: {len(self.active_trades)} aktif islem yuklendi: "
                    f"{', '.join(self.active_trades.keys())}"
                )
        except Exception as e:
            logger.error(f"active_trades yuklenemedi: {e}")

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
        """
        ATR-based Adaptive Stop (v2.1).

        Mantik:
          1. ATR x multiplier (vol >%1 -> 4.0x, degilse 3.5x)
          2. Minimum %2.5 mesafe zorunlu (20x koruma)
          3. Yapisal stop: son 10 mumun dip/tepe +/- %0.5
          4. ATR stop ve yapisal stop'tan DAHA GENIS olani kullanilir
        """
        atr = float(df["atr"].iloc[-1])
        volatility_pct = (atr / entry_price) * 100

        # ATR bazli mesafe
        multiplier = (ATR_MULTIPLIER_HIGH_VOL if volatility_pct > 1.0
                      else ATR_MULTIPLIER_LOW_VOL)
        atr_stop_dist = atr * multiplier

        # Minimum mesafe zorunlulugu
        min_stop_dist = entry_price * MIN_STOP_DISTANCE_PCT
        if atr_stop_dist < min_stop_dist:
            atr_stop_dist = min_stop_dist

        # Yapisal stop (son N mumun dip/tepe +/- buffer)
        if side == "BUY":
            recent_low = float(df["low"].tail(STRUCTURAL_LOOKBACK).min())
            structural_stop = recent_low * (1 - STRUCTURAL_BUFFER)
            atr_based_stop = entry_price - atr_stop_dist
            final_stop = min(atr_based_stop, structural_stop)
        else:
            recent_high = float(df["high"].tail(STRUCTURAL_LOOKBACK).max())
            structural_stop = recent_high * (1 + STRUCTURAL_BUFFER)
            atr_based_stop = entry_price + atr_stop_dist
            final_stop = max(atr_based_stop, structural_stop)

        # Toplam stop mesafesi yuzdesi
        stop_distance_pct = abs(entry_price - final_stop) / entry_price * 100

        time_limit = 180  # Zaman: zararda 180dk, karda sinirsiz

        result = {
            "stop_price": round(final_stop, 6),
            "time_limit_min": time_limit,
            "volatility_pct": round(volatility_pct, 2),
            "multiplier": multiplier,
            "atr": round(atr, 6),
            "stop_distance_pct": round(stop_distance_pct, 2),
        }

        logger.info(
            f"Stop-Loss | ATR:{atr:.6f} Vol:%{result['volatility_pct']} "
            f"x{multiplier} | Stop:{result['stop_price']} "
            f"(mesafe %{result['stop_distance_pct']})"
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
        self._save()
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
        v3.0 Stop-Only + T1/T2 Pozisyon Yonetimi.

        ISLEM SADECE STOP LOSS PATLARSA KAPANIR.
        Zaman limiti YOK - stop karar verir.

        T1-T2 KORUMA (karda stop yukarı cekilir):
          1. T1 (%2 kar): Stop -> giris + %0.3 (breakeven)
          2. T2 (%4 kar): Stop -> giris + %1.5 (kar kilitleme)
          3. TRAILING: T2 sonrasi kademeli daraltma
          4. GAP KORUMA: %-5 acil kapatma (piyasa cokusu)
        """
        trade = self.active_trades.get(symbol)
        if not trade:
            return False, ""

        prev_stop = trade.stop_price
        prev_breakeven = trade.breakeven_hit
        prev_t2 = trade.t2_hit
        profit_pct = trade.current_profit_pct(current_price)

        # --- EN IYI FIYATI GUNCELLE ---
        if trade.side == "BUY" and current_price > trade.highest_price:
            trade.highest_price = current_price
        elif trade.side == "SELL" and current_price < trade.highest_price:
            trade.highest_price = current_price

        if profit_pct > trade.highest_profit_pct:
            trade.highest_profit_pct = profit_pct

        # ============================================
        # KATMAN 1: GAP KORUMA (%-5 acil cikis)
        # ============================================
        if profit_pct < -5.0:
            pnl = (profit_pct / 100) * trade.entry_price * trade.quantity
            return True, (
                f"GAP_KORUMA (kayip %{profit_pct:.1f}) | PnL: ${pnl:.2f}"
            )

        # ============================================
        # KATMAN 2: T1 - BREAKEVEN (%2 kar)
        # Stop = giris + %0.3 (komisyon korumasi)
        # ============================================
        if not trade.breakeven_hit and profit_pct >= 2.0:
            trade.breakeven_hit = True

            if trade.side == "BUY":
                new_stop = trade.entry_price * 1.003
            else:
                new_stop = trade.entry_price * 0.997

            if trade.side == "BUY" and new_stop > trade.stop_price:
                trade.stop_price = new_stop
            elif trade.side == "SELL" and new_stop < trade.stop_price:
                trade.stop_price = new_stop

            logger.info(
                f"T1 BREAKEVEN: {symbol} | Kar %{profit_pct:.1f} | "
                f"Stop -> {trade.stop_price:.6f} (giris+%0.3)"
            )

        # ============================================
        # KATMAN 3: T2 - KAR KILITLEME (%4 kar)
        # Stop = giris + %1.5 (kar korunuyor)
        # ============================================
        if not trade.t2_hit and profit_pct >= 4.0:
            trade.t2_hit = True

            if trade.side == "BUY":
                new_stop = trade.entry_price * 1.015
            else:
                new_stop = trade.entry_price * 0.985

            if trade.side == "BUY" and new_stop > trade.stop_price:
                trade.stop_price = new_stop
            elif trade.side == "SELL" and new_stop < trade.stop_price:
                trade.stop_price = new_stop

            logger.info(
                f"T2 KAR KILIDI: {symbol} | Kar %{profit_pct:.1f} | "
                f"Stop -> {trade.stop_price:.6f} (giris+%1.5)"
            )

        # ============================================
        # KATMAN 4: KADEMELI TRAILING (T2 sonrasi)
        # Kar arttikca trail daralir, stop yalniz yukari gider
        #
        # %4-7 kar   -> %1.5 trail
        # %7-12 kar  -> %1.2 trail
        # %12-20 kar -> %0.8 trail
        # %20+ kar   -> %0.5 trail (dar koruma)
        # ============================================
        if trade.t2_hit:
            if profit_pct >= 20:
                trail_pct = 0.5
            elif profit_pct >= 12:
                trail_pct = 0.8
            elif profit_pct >= 7:
                trail_pct = 1.2
            else:
                trail_pct = 1.5

            if trade.side == "BUY":
                candidate_stop = trade.highest_price * (1 - trail_pct / 100)
            else:
                candidate_stop = trade.highest_price * (1 + trail_pct / 100)

            # Stop ASLA asagi inmez
            if trade.side == "BUY" and candidate_stop > trade.stop_price:
                old = trade.stop_price
                trade.stop_price = candidate_stop
                stop_pct = trade.current_profit_pct(candidate_stop)
                logger.info(
                    f"TRAILING: {symbol} | Kar %{profit_pct:.1f} | "
                    f"Trail:%{trail_pct} | Stop: {old:.6f} -> {candidate_stop:.6f} "
                    f"(stop kari: %{stop_pct:.1f})"
                )
            elif trade.side == "SELL" and candidate_stop < trade.stop_price:
                old = trade.stop_price
                trade.stop_price = candidate_stop
                stop_pct = trade.current_profit_pct(candidate_stop)
                logger.info(
                    f"TRAILING: {symbol} | Kar %{profit_pct:.1f} | "
                    f"Trail:%{trail_pct} | Stop: {old:.6f} -> {candidate_stop:.6f} "
                    f"(stop kari: %{stop_pct:.1f})"
                )

        # Degisiklik varsa kaydet
        if (trade.stop_price != prev_stop or
                trade.breakeven_hit != prev_breakeven or
                trade.t2_hit != prev_t2):
            self._save()

        # ============================================
        # KATMAN 5: STOP-LOSS (TEK CIKIS NOKTASI)
        # Islem SADECE stop-loss'a dusunce kapanir.
        # Zaman limiti YOK.
        # ============================================
        if trade.side == "BUY" and current_price <= trade.stop_price:
            pnl = (current_price - trade.entry_price) * trade.quantity
            if trade.t2_hit:
                label = "T2_TRAILING"
            elif trade.breakeven_hit:
                label = "T1_BREAKEVEN"
            else:
                label = "STOP_LOSS"
            return True, (
                f"{label} @ {trade.stop_price:.6f} | "
                f"Kar: %{profit_pct:.2f} | PnL: ${pnl:.2f}"
            )
        elif trade.side == "SELL" and current_price >= trade.stop_price:
            pnl = (trade.entry_price - current_price) * trade.quantity
            if trade.t2_hit:
                label = "T2_TRAILING"
            elif trade.breakeven_hit:
                label = "T1_BREAKEVEN"
            else:
                label = "STOP_LOSS"
            return True, (
                f"{label} @ {trade.stop_price:.6f} | "
                f"Kar: %{profit_pct:.2f} | PnL: ${pnl:.2f}"
            )

        return False, ""

    def get_pnl_dollars(self, symbol: str, current_price: float) -> float:
        """Aktif islemin dolar bazinda PnL'ini dondurur."""
        trade = self.active_trades.get(symbol)
        if not trade:
            return 0.0
        if trade.side == "BUY":
            return (current_price - trade.entry_price) * trade.quantity
        return (trade.entry_price - current_price) * trade.quantity

    def should_trigger_early_warning(self, symbol: str, current_price: float,
                                     threshold_dollars: float = -5.0) -> bool:
        """
        $5 zarar esigine dusuldu mu? early_warning flag'i ilk geciste isaretlenir
        ama bu fonksiyon her $5 altinda True doner (surekli yeniden oylama icin).
        Kapatma karari coklu TF oyundan gelir - bu fonksiyon sadece kapi aci.
        """
        trade = self.active_trades.get(symbol)
        if not trade:
            return False
        pnl = self.get_pnl_dollars(symbol, current_price)
        return pnl <= threshold_dollars

    def should_hard_close(self, symbol: str, current_price: float,
                          hard_threshold: float = -10.0) -> bool:
        """$10 zarar sert esigi - coklu TF kapat derse burada anlik kapatma onayi."""
        pnl = self.get_pnl_dollars(symbol, current_price)
        return pnl <= hard_threshold

    def mark_warning_sent(self, symbol: str):
        """Erken uyari gonderildi - tekrar tetiklenmesin."""
        trade = self.active_trades.get(symbol)
        if trade:
            trade.early_warning_sent = True
            self._save()

    def tf_exit_vote(self, df: pd.DataFrame, side: str) -> dict:
        """
        Tek bir timeframe icin "pozisyon kapat" oylamasi.
        Kriterler: trend (EMA yapisi), RSI sinyali, RVOL (hacim gucu).
        Her kriter -/0/+ puan uretir. Toplam <= -1 ise "kapat" oyu.

        Returns: {
          'vote': 'close' | 'hold',
          'score': int (- kapat, + tut),
          'trend': -1/0/+1 (- karsi, + lehine),
          'rsi': -1/0/+1,
          'rvol': float (hacim katsayisi),
          'reasons': list[str]
        }
        """
        try:
            last = df.iloc[-1]
            close = float(last["close"])
            rsi = float(last.get("rsi", 50))
            ema_f = float(last.get("ema_20", close))
            ema_s = float(last.get("ema_50", close))
            cur_vol = float(last["volume"])
            avg_vol = float(df["volume"].tail(20).mean())
            rvol = cur_vol / avg_vol if avg_vol > 0 else 1.0

            # --- TREND (EMA + fiyat konumu) ---
            if side == "BUY":
                if close > ema_f and ema_f > ema_s:
                    trend = 1
                elif close < ema_f and ema_f < ema_s:
                    trend = -1
                else:
                    trend = 0
            else:  # SELL
                if close < ema_f and ema_f < ema_s:
                    trend = 1
                elif close > ema_f and ema_f > ema_s:
                    trend = -1
                else:
                    trend = 0

            # --- RSI ---
            if side == "BUY":
                if rsi < 40:
                    rsi_sig = -1
                elif rsi > 55:
                    rsi_sig = 1
                else:
                    rsi_sig = 0
            else:
                if rsi > 60:
                    rsi_sig = -1
                elif rsi < 45:
                    rsi_sig = 1
                else:
                    rsi_sig = 0

            # --- RVOL agirlik ---
            # Yuksek hacim karsi yonde -> daha negatif
            # Dusuk hacim karsi yonde -> hafif negatif (kesilecek gibi)
            rvol_mult = 1.5 if rvol >= 2.0 else 1.0 if rvol >= 1.0 else 0.5

            raw = trend + rsi_sig
            score = raw * rvol_mult

            reasons: list[str] = []
            if trend == -1:
                reasons.append("Trend karsi")
            if rsi_sig == -1:
                reasons.append(f"RSI zayif ({rsi:.0f})")
            if trend == 1:
                reasons.append("Trend lehine")
            if rsi_sig == 1:
                reasons.append(f"RSI guclu ({rsi:.0f})")
            if rvol >= 2.0:
                reasons.append(f"Yuksek hacim x{rvol:.1f}")

            # -1 ve altinda: kapat oyu (hacim katsayisi negatifligi buyutur)
            vote = "close" if score <= -1 else "hold"

            return {
                "vote": vote,
                "score": round(score, 2),
                "trend": trend,
                "rsi_sig": rsi_sig,
                "rsi": round(rsi, 1),
                "rvol": round(rvol, 2),
                "reasons": reasons,
            }
        except Exception as e:
            logger.error(f"tf_exit_vote hatasi: {e}")
            return {"vote": "hold", "score": 0, "trend": 0,
                    "rsi_sig": 0, "rsi": 0, "rvol": 1.0, "reasons": []}

    def multi_tf_exit_decision(self, tf_results: dict) -> dict:
        """
        Birden fazla timeframe (5m/3m/1m) oyu birlestirir.
        2+ TF "close" oyu -> kapat.
        tf_results: {'5m': {...vote...}, '3m': {...}, '1m': {...}}
        """
        close_votes = [tf for tf, r in tf_results.items() if r["vote"] == "close"]
        decision = "close" if len(close_votes) >= 2 else "hold"
        return {
            "decision": decision,
            "close_votes": close_votes,
            "vote_count": len(close_votes),
            "total_tf": len(tf_results),
        }

    def analyze_trend_health(self, df: pd.DataFrame, side: str) -> dict:
        """
        $8 zararda cagrilir. Trend bozulmus mu karar verir.

        Bozulma kriterleri (LONG icin - SHORT tersi):
          1. RSI < 45 (momentum zayif)
          2. ADX dusuyor VE < 20 (trend zayifliyor)
          3. EMA9 < EMA21 (kisa vade bearish)
          4. Son 3 mumun 2+ tanesi karsi yonde
          5. Hacim ortalamadan yuksek (panik satis)

        3+ kriter bozuksa: trend kirildi -> healthy=False (kapat)
        """
        try:
            last = df.iloc[-1]
            rsi = float(last.get("rsi", 50))
            adx = float(last.get("adx", 20))
            ema_fast = float(last.get("ema_20", last["close"]))
            ema_slow = float(last.get("ema_50", last["close"]))
            close = float(last["close"])
            vol = float(last["volume"])
            vol_avg = float(df["volume"].tail(20).mean())
            vol_ratio = vol / vol_avg if vol_avg > 0 else 1.0

            # ADX yonu (son 5 mum)
            adx_series = df["adx"].tail(5) if "adx" in df.columns else None
            adx_falling = (
                adx_series is not None and
                adx_series.iloc[-1] < adx_series.iloc[0]
            )

            # Son 3 mum: karsi yonde kac tane?
            last3 = df.tail(3)
            if side == "BUY":
                opposing = int((last3["close"] < last3["open"]).sum())
            else:
                opposing = int((last3["close"] > last3["open"]).sum())

            reasons: list[str] = []
            broken = 0

            if side == "BUY":
                if rsi < 45:
                    broken += 1
                    reasons.append(f"RSI zayif ({rsi:.0f})")
                if adx < 20 and adx_falling:
                    broken += 1
                    reasons.append(f"ADX dusuyor ({adx:.0f})")
                if close < ema_fast and ema_fast < ema_slow:
                    broken += 1
                    reasons.append("Fiyat EMA altinda, bearish")
                if opposing >= 2:
                    broken += 1
                    reasons.append(f"{opposing}/3 kirmizi mum")
                if vol_ratio > 1.5 and opposing >= 2:
                    broken += 1
                    reasons.append(f"Panik satis (vol x{vol_ratio:.1f})")
            else:  # SELL/SHORT
                if rsi > 55:
                    broken += 1
                    reasons.append(f"RSI guclu ({rsi:.0f})")
                if adx < 20 and adx_falling:
                    broken += 1
                    reasons.append(f"ADX dusuyor ({adx:.0f})")
                if close > ema_fast and ema_fast > ema_slow:
                    broken += 1
                    reasons.append("Fiyat EMA ustunde, bullish")
                if opposing >= 2:
                    broken += 1
                    reasons.append(f"{opposing}/3 yesil mum")
                if vol_ratio > 1.5 and opposing >= 2:
                    broken += 1
                    reasons.append(f"Panik alis (vol x{vol_ratio:.1f})")

            # Son mum govde yonu
            last_candle = "bullish" if last["close"] > last["open"] else "bearish"

            return {
                "healthy": broken < 3,
                "broken_count": broken,
                "rsi": round(rsi, 1),
                "adx": round(adx, 1),
                "adx_falling": adx_falling,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "ema_trend": "up" if ema_fast > ema_slow else "down",
                "vol_ratio": round(vol_ratio, 2),
                "last_candle": last_candle,
                "opposing_candles": opposing,
                "reasons": reasons,
            }
        except Exception as e:
            logger.error(f"analyze_trend_health hatasi: {e}")
            return {
                "healthy": True, "broken_count": 0, "reasons": [],
                "rsi": 0, "adx": 0, "adx_falling": False,
                "ema_trend": "unknown", "vol_ratio": 1.0,
                "last_candle": "unknown", "opposing_candles": 0,
            }

    def close_trade(self, symbol: str, reason: str):
        trade = self.active_trades.pop(symbol, None)
        if trade:
            self._save()
            logger.info(
                f"Islem kapatildi: {symbol} | {reason} | "
                f"Sure: {trade.elapsed_minutes:.1f}dk"
            )

    def calculate_position_size(self, balance: float, entry_price: float,
                                 stop_price: float, allocation_pct: int) -> float:
        """Sabit $20 marjin x 20x = $400 notional. Kasa $150 icin 7 islem * $20 = $140."""
        margin_per_trade = 20.0
        notional = margin_per_trade * config.DEFAULT_LEVERAGE

        if entry_price <= 0:
            return 0

        quantity = notional / entry_price
        logger.info(
            f"Pozisyon: ${margin_per_trade} x {config.DEFAULT_LEVERAGE}x "
            f"= ${notional} | Miktar={quantity:.4f}"
        )
        return quantity
