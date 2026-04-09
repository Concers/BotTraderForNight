from __future__ import annotations
"""
Trade Journal - Islem gecmisi, whitelist/blacklist, performans takibi.
Bot 24 saat calistiktan sonra analiz icin kullanilir.
"""

import json
import os
import time
from datetime import datetime
from logger_setup import setup_logger

logger = setup_logger("Journal")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
JOURNAL_FILE = os.path.join(DATA_DIR, "trade_journal.json")
LISTS_FILE = os.path.join(DATA_DIR, "coin_lists.json")
STATS_FILE = os.path.join(DATA_DIR, "daily_stats.json")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path: str) -> dict:
    _ensure_dir()
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def _save_json(path: str, data: dict):
    _ensure_dir()
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


class TradeJournal:
    """Tum islemlerin kaydi - ne aldi, ne satti, neden reddetti."""

    def __init__(self):
        self.data = _load_json(JOURNAL_FILE)
        if "trades" not in self.data:
            self.data["trades"] = []
        if "rejected" not in self.data:
            self.data["rejected"] = []
        if "scans" not in self.data:
            self.data["scans"] = []

    def _save(self):
        _save_json(JOURNAL_FILE, self.data)

    def record_trade_open(self, symbol: str, side: str, entry_price: float,
                          stop_price: float, quantity: float, score: dict):
        """Islem acildiginda kaydet."""
        trade = {
            "id": len(self.data["trades"]) + 1,
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "quantity": quantity,
            "margin": 50.0,
            "notional": quantity * entry_price,
            "score": score.get("score", 0),
            "components": score.get("components", {}),
            "decision": score.get("decision", ""),
            "open_time": datetime.now().isoformat(),
            "close_time": None,
            "close_price": None,
            "pnl": None,
            "pnl_pct": None,
            "close_reason": None,
            "status": "OPEN",
        }
        self.data["trades"].append(trade)
        self._save()
        logger.info(f"JOURNAL: Islem #{trade['id']} acildi - {symbol} {side} @ {entry_price}")
        return trade["id"]

    def record_trade_close(self, symbol: str, close_price: float, reason: str):
        """Islem kapatildiginda guncelle."""
        for trade in reversed(self.data["trades"]):
            if trade["symbol"] == symbol and trade["status"] == "OPEN":
                trade["close_time"] = datetime.now().isoformat()
                trade["close_price"] = close_price
                trade["close_reason"] = reason
                trade["status"] = "CLOSED"

                if trade["side"] == "BUY":
                    trade["pnl_pct"] = ((close_price - trade["entry_price"])
                                        / trade["entry_price"]) * 100
                else:
                    trade["pnl_pct"] = ((trade["entry_price"] - close_price)
                                        / trade["entry_price"]) * 100

                trade["pnl"] = (trade["pnl_pct"] / 100) * trade["notional"]

                self._save()
                logger.info(
                    f"JOURNAL: Islem #{trade['id']} kapandi - {symbol} | "
                    f"PnL: ${trade['pnl']:.2f} (%{trade['pnl_pct']:.2f}) | {reason}"
                )

                # Blacklist/whitelist guncelle
                coin_lists = CoinLists()
                coin_lists.update_from_trade(symbol, trade["pnl"], trade["pnl_pct"])
                return
        logger.warning(f"JOURNAL: {symbol} icin acik islem bulunamadi")

    def record_rejected(self, symbol: str, score: dict, reason: str):
        """Reddedilen sinyali kaydet."""
        entry = {
            "symbol": symbol,
            "time": datetime.now().isoformat(),
            "score": score.get("score", 0),
            "components": score.get("components", {}),
            "reason": reason,
        }
        self.data["rejected"].append(entry)
        # Cok buyumemesi icin son 500 kayit tut
        if len(self.data["rejected"]) > 500:
            self.data["rejected"] = self.data["rejected"][-500:]
        self._save()

    def record_scan(self, total_coins: int, signals: int, duration_sec: float):
        """Tarama sonucunu kaydet."""
        entry = {
            "time": datetime.now().isoformat(),
            "total_coins": total_coins,
            "signals": signals,
            "duration_sec": round(duration_sec, 1),
        }
        self.data["scans"].append(entry)
        if len(self.data["scans"]) > 500:
            self.data["scans"] = self.data["scans"][-500:]
        self._save()

    def get_summary(self) -> dict:
        """Gunluk ozet."""
        trades = self.data["trades"]
        closed = [t for t in trades if t["status"] == "CLOSED"]
        open_trades = [t for t in trades if t["status"] == "OPEN"]

        wins = [t for t in closed if (t["pnl"] or 0) > 0]
        losses = [t for t in closed if (t["pnl"] or 0) <= 0]

        total_pnl = sum(t["pnl"] or 0 for t in closed)
        total_wins = sum(t["pnl"] or 0 for t in wins)
        total_losses = sum(t["pnl"] or 0 for t in losses)

        return {
            "total_trades": len(trades),
            "open": len(open_trades),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(closed) * 100) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "total_wins": round(total_wins, 2),
            "total_losses": round(total_losses, 2),
            "avg_win": round(total_wins / len(wins), 2) if wins else 0,
            "avg_loss": round(total_losses / len(losses), 2) if losses else 0,
            "best_trade": max(closed, key=lambda t: t["pnl"] or 0) if closed else None,
            "worst_trade": min(closed, key=lambda t: t["pnl"] or 0) if closed else None,
            "rejected_count": len(self.data["rejected"]),
            "scan_count": len(self.data["scans"]),
        }


class CoinLists:
    """Whitelist / Blacklist yonetimi."""

    def __init__(self):
        self.data = _load_json(LISTS_FILE)
        if "whitelist" not in self.data:
            self.data["whitelist"] = {}
        if "blacklist" not in self.data:
            self.data["blacklist"] = {}
        if "stats" not in self.data:
            self.data["stats"] = {}

    def _save(self):
        _save_json(LISTS_FILE, self.data)

    def update_from_trade(self, symbol: str, pnl: float, pnl_pct: float):
        """Islem sonucuna gore coin istatistiklerini guncelle."""
        if symbol not in self.data["stats"]:
            self.data["stats"][symbol] = {
                "trades": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "last_trade": None,
            }

        s = self.data["stats"][symbol]
        s["trades"] += 1
        s["total_pnl"] = round(s["total_pnl"] + pnl, 2)
        s["last_trade"] = datetime.now().isoformat()

        if pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1

        # Otomatik whitelist/blacklist
        # 3+ islem ve %70+ win rate -> whitelist
        if s["trades"] >= 3 and (s["wins"] / s["trades"]) >= 0.7:
            self.data["whitelist"][symbol] = {
                "reason": f"Win rate: {s['wins']}/{s['trades']} | PnL: ${s['total_pnl']}",
                "added": datetime.now().isoformat(),
            }
            # Blacklist'teyse cikar
            self.data["blacklist"].pop(symbol, None)
            logger.info(f"WHITELIST: {symbol} eklendi ({s['wins']}/{s['trades']} win)")

        # 3+ islem ve %70+ loss rate -> blacklist
        if s["trades"] >= 3 and (s["losses"] / s["trades"]) >= 0.7:
            self.data["blacklist"][symbol] = {
                "reason": f"Loss rate: {s['losses']}/{s['trades']} | PnL: ${s['total_pnl']}",
                "added": datetime.now().isoformat(),
            }
            self.data["whitelist"].pop(symbol, None)
            logger.info(f"BLACKLIST: {symbol} eklendi ({s['losses']}/{s['trades']} loss)")

        self._save()

    def is_blacklisted(self, symbol: str) -> bool:
        return symbol in self.data["blacklist"]

    def is_whitelisted(self, symbol: str) -> bool:
        return symbol in self.data["whitelist"]

    def get_report(self) -> str:
        """Whitelist/blacklist raporu."""
        wl = self.data["whitelist"]
        bl = self.data["blacklist"]
        stats = self.data["stats"]

        lines = ["📋 <b>COIN LISTELERI</b>\n━━━━━━━━━━━━━━━━━━"]

        if wl:
            lines.append("\n✅ <b>WHITELIST</b> (guvenilir)")
            for sym, info in wl.items():
                lines.append(f"  {sym}: {info['reason']}")
        else:
            lines.append("\n✅ Whitelist: bos (henuz veri yok)")

        if bl:
            lines.append("\n❌ <b>BLACKLIST</b> (kacinilacak)")
            for sym, info in bl.items():
                lines.append(f"  {sym}: {info['reason']}")
        else:
            lines.append("\n❌ Blacklist: bos")

        # En cok islem yapilan coinler
        if stats:
            sorted_stats = sorted(stats.items(), key=lambda x: x[1]["trades"], reverse=True)[:10]
            lines.append("\n📊 <b>EN AKTIF COINLER</b>")
            for sym, s in sorted_stats:
                wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
                lines.append(
                    f"  {sym}: {s['trades']} islem | "
                    f"{s['wins']}W/{s['losses']}L (%{wr:.0f}) | ${s['total_pnl']:+.2f}"
                )

        return "\n".join(lines)
