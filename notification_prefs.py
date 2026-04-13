from __future__ import annotations
"""
Chat basina bildirim kategorileri.
Her Telegram hedefi (primary/secondary) hangi bildirimleri almak istedigini
/bildirim komutu ile kendisi yonetir. Varsayilan: hepsi acik.
"""

import json
import os
from logger_setup import setup_logger

logger = setup_logger("NotifPrefs")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PREFS_FILE = os.path.join(DATA_DIR, "notification_prefs.json")

CATEGORIES = ("market", "rapor", "uyari", "sinyal", "kapanma")

CATEGORY_DESC = {
    "market": "5dk Market Rontgeni raporu",
    "rapor": "20dk periyodik durum raporu",
    "uyari": "$5/$10 coklu TF zarar uyarilari",
    "sinyal": "Yeni pozisyon acildiginda bildirim",
    "kapanma": "Pozisyon kapaninca bildirim",
}


class NotificationPrefs:
    """Chat basina bildirim on/off ayarlarini JSON'da tutar."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.prefs: dict[str, dict[str, bool]] = {}
        self._load()

    def _load(self):
        if not os.path.exists(PREFS_FILE):
            return
        try:
            with open(PREFS_FILE, "r") as f:
                self.prefs = json.load(f)
        except Exception as e:
            logger.error(f"Bildirim ayarlari yuklenemedi: {e}")
            self.prefs = {}

    def _save(self):
        try:
            with open(PREFS_FILE, "w") as f:
                json.dump(self.prefs, f, indent=2)
        except Exception as e:
            logger.error(f"Bildirim ayarlari kaydedilemedi: {e}")

    def _ensure(self, chat_id: str) -> dict[str, bool]:
        """Chat icin varsayilan (tumu acik) olustur."""
        cid = str(chat_id)
        if cid not in self.prefs:
            self.prefs[cid] = {cat: True for cat in CATEGORIES}
            self._save()
        # Eksik kategori varsa tamamla (gelecekte yeni kategori eklenirse)
        changed = False
        for cat in CATEGORIES:
            if cat not in self.prefs[cid]:
                self.prefs[cid][cat] = True
                changed = True
        if changed:
            self._save()
        return self.prefs[cid]

    def is_enabled(self, chat_id: str, category: str) -> bool:
        """Chat bu kategoriyi aliyor mu?"""
        if category not in CATEGORIES:
            return True  # Taninmayan kategori: varsayilan ac
        return self._ensure(chat_id).get(category, True)

    def set(self, chat_id: str, category: str, enabled: bool) -> bool:
        if category not in CATEGORIES:
            return False
        self._ensure(chat_id)[category] = enabled
        self._save()
        return True

    def set_all(self, chat_id: str, enabled: bool):
        self.prefs[str(chat_id)] = {cat: enabled for cat in CATEGORIES}
        self._save()

    def get_report(self, chat_id: str) -> str:
        p = self._ensure(chat_id)
        lines = [
            "🔔 <b>BILDIRIM AYARLARIN</b>",
            "━━━━━━━━━━━━━━━━━━",
        ]
        for cat in CATEGORIES:
            emoji = "✅" if p[cat] else "❌"
            lines.append(f"{emoji} <b>{cat}</b> — {CATEGORY_DESC[cat]}")
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("Degistirmek icin: /bildirim &lt;tip&gt; ac|kapat")
        lines.append("Ornekler:")
        lines.append("  /bildirim market kapat")
        lines.append("  /bildirim hepsi ac")
        return "\n".join(lines)
