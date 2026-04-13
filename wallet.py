from __future__ import annotations
"""
Sanal Kasa Sistemi - Gercek para takibi.

Kurallar:
  - Baslangic: $150
  - Her islem: $25 marjin x 20x = $500 notional
  - Islem acildiginda kasadan $25 ayrilir
  - Islem kapatildiginda $25 + PnL kasaya doner
  - Kasa < $25 ise yeni islem acilamaz
"""

import json
import os
from datetime import datetime
from logger_setup import setup_logger

logger = setup_logger("Wallet")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
WALLET_FILE = os.path.join(DATA_DIR, "wallet.json")

INITIAL_BALANCE = 150.0
MARGIN_PER_TRADE = 25.0
LEVERAGE = 20


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


class Wallet:
    """Gercek kasa takibi."""

    def __init__(self):
        _ensure_dir()
        if os.path.exists(WALLET_FILE):
            with open(WALLET_FILE, "r") as f:
                self.data = json.load(f)
        else:
            self.data = {
                "initial_balance": INITIAL_BALANCE,
                "balance": INITIAL_BALANCE,
                "reserved": 0.0,  # Acik islemlere ayrilan marjin
                "total_pnl": 0.0,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "history": [],
                "created": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
            }
            self._save()

    def _save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        with open(WALLET_FILE, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    @property
    def available_balance(self) -> float:
        """Kullanilabilir bakiye (acik islemlere ayrilan haric)."""
        return self.data["balance"] - self.data["reserved"]

    @property
    def total_balance(self) -> float:
        """Toplam bakiye."""
        return self.data["balance"]

    def can_open_trade(self) -> bool:
        """Yeni islem icin yeterli bakiye var mi?"""
        if self.available_balance < MARGIN_PER_TRADE:
            logger.warning(
                f"KASA YETERSIZ: Kalan ${self.available_balance:.2f} < "
                f"Gerekli ${MARGIN_PER_TRADE:.2f}"
            )
            return False
        return True

    def open_trade(self, symbol: str, side: str, entry_price: float):
        """Islem acildiginda $50 marjin ayir."""
        if not self.can_open_trade():
            return False

        self.data["reserved"] += MARGIN_PER_TRADE

        self.data["history"].append({
            "time": datetime.now().isoformat(),
            "type": "OPEN",
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "margin": MARGIN_PER_TRADE,
            "balance_after": self.data["balance"],
            "available_after": self.available_balance,
        })

        self._save()
        logger.info(
            f"KASA: {symbol} {side} acildi | Marjin: ${MARGIN_PER_TRADE} | "
            f"Kalan: ${self.available_balance:.2f} / ${self.total_balance:.2f}"
        )
        return True

    def close_trade(self, symbol: str, side: str, entry_price: float,
                    close_price: float, reason: str):
        """
        Islem kapatildiginda PnL hesapla ve kasaya ekle.
        Marjin ($50) + PnL kasaya doner.
        """
        # PnL hesapla
        if side == "BUY":
            pnl_pct = ((close_price - entry_price) / entry_price) * 100
        else:  # SHORT: fiyat dustuyse kar
            pnl_pct = ((entry_price - close_price) / entry_price) * 100

        # Notional bazli PnL ($1000 notional)
        notional = MARGIN_PER_TRADE * LEVERAGE
        pnl_dollar = (pnl_pct / 100) * notional

        # Kasayi guncelle
        self.data["reserved"] -= MARGIN_PER_TRADE
        self.data["reserved"] = max(0, self.data["reserved"])
        self.data["balance"] += pnl_dollar
        self.data["total_pnl"] += pnl_dollar
        self.data["total_trades"] += 1

        if pnl_dollar > 0:
            self.data["wins"] += 1
        else:
            self.data["losses"] += 1

        self.data["history"].append({
            "time": datetime.now().isoformat(),
            "type": "CLOSE",
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "close_price": close_price,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_dollar": round(pnl_dollar, 2),
            "reason": reason,
            "balance_after": round(self.data["balance"], 2),
        })

        # Son 200 gecmis kaydi tut
        if len(self.data["history"]) > 200:
            self.data["history"] = self.data["history"][-200:]

        self._save()

        emoji = "KAR" if pnl_dollar > 0 else "ZARAR"
        logger.info(
            f"KASA: {symbol} {side} kapandi | {emoji} ${pnl_dollar:+.2f} (%{pnl_pct:+.2f}) | "
            f"Kasa: ${self.data['balance']:.2f} | Sebep: {reason}"
        )
        return pnl_dollar

    def get_report(self) -> str:
        """Telegram icin kasa raporu."""
        d = self.data
        win_rate = (d["wins"] / d["total_trades"] * 100) if d["total_trades"] > 0 else 0
        pnl_pct_total = ((d["balance"] - d["initial_balance"]) / d["initial_balance"]) * 100

        return (
            f"💼 <b>KASA DURUMU</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Baslangic: ${d['initial_balance']:.2f}\n"
            f"💰 Guncel Kasa: <b>${d['balance']:.2f}</b>\n"
            f"📊 Degisim: <b>${d['total_pnl']:+.2f}</b> (%{pnl_pct_total:+.1f})\n"
            f"🔒 Acik Islem Marjin: ${d['reserved']:.2f}\n"
            f"💵 Kullanilabilir: ${self.available_balance:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 Toplam Islem: {d['total_trades']}\n"
            f"✅ Kazanan: {d['wins']} | ❌ Kaybeden: {d['losses']}\n"
            f"🎯 Win Rate: %{win_rate:.0f}"
        )

    def reset(self, new_balance: float = INITIAL_BALANCE):
        """Kasayi sifirla."""
        self.data = {
            "initial_balance": new_balance,
            "balance": new_balance,
            "reserved": 0.0,
            "total_pnl": 0.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "history": [],
            "created": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
        }
        self._save()
        logger.info(f"KASA SIFIRLANDI: ${new_balance:.2f}")
