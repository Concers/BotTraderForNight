from __future__ import annotations
"""
Trading kurallari yukleyici:
  - No-trade zones (saatlere gore yasak pencereler)
  - Funding rate hard filter
data/trading_rules.json'dan okur. Bu dosya TR (UTC+3) saatinde calisir.
"""

import json
import os
from logger_setup import setup_logger
from time_utils import tr_now

logger = setup_logger("TradingRules")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RULES_FILE = os.path.join(DATA_DIR, "trading_rules.json")


class TradingRules:
    def __init__(self):
        self.zones: list[dict] = []
        self.funding_threshold: float = 0.0015
        self.blacklist_reason: str = ""
        self._load()

    def _load(self):
        try:
            with open(RULES_FILE, "r") as f:
                data = json.load(f).get("trading_rules", {})
            self.zones = data.get("no_trade_zones", [])
            fr_filter = data.get("funding_rate_filter", {})
            self.funding_threshold = float(
                fr_filter.get("pause_token_if_abs_funding_above", 0.0015)
            )
            self.blacklist_reason = fr_filter.get(
                "blacklist_reason", "Yuksek slippage riski"
            )
            logger.info(
                f"Trading rules yuklendi: {len(self.zones)} no-trade zone, "
                f"funding threshold %{self.funding_threshold*100:.3f}"
            )
        except FileNotFoundError:
            logger.warning(f"Trading rules dosyasi yok: {RULES_FILE}")
        except Exception as e:
            logger.error(f"Trading rules yuklenemedi: {e}")

    def is_no_trade_zone(self) -> tuple[bool, str]:
        """
        Su an no-trade zone icinde mi (TR saati)?
        Returns: (in_zone: bool, event_name: str)
        """
        now = tr_now()
        cur_min = now.hour * 60 + now.minute

        for zone in self.zones:
            try:
                sh, sm = zone["start"].split(":")
                eh, em = zone["end"].split(":")
                start_min = int(sh) * 60 + int(sm)
                end_min = int(eh) * 60 + int(em)
                if start_min <= cur_min <= end_min:
                    return True, zone.get("event", "Unknown")
            except Exception:
                continue
        return False, ""

    def is_funding_blocked(self, funding_rate: float) -> bool:
        """abs(funding) > esik ise True (islem yapma)."""
        return abs(funding_rate) > self.funding_threshold
