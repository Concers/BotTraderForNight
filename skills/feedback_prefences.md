---
name: Kullanici Tercihleri ve Geri Bildirimler
description: Ugur'un bot davranisi hakkindaki tercihleri
type: feedback
---

- Pozisyon ASLA kapatilmaz, sadece trailing stop ile kapanir
  **Why:** Kullanici karin sonsuza kadar buyumesini istiyor
  **How to apply:** TP1/TP2/TP3 yok, sadece %1.2 adimla stop yukari cekiliyor

- Telegram'dan komut yazmak istemiyor, bot tamamen otonom calismali
  **Why:** Kullanici uykuda olacak, bot kendi karar vermeli
  **How to apply:** auto_scan 3dk aralikla otomatik calisir

- Her islem sabit $50 marjin, skor farketmez
  **Why:** Risk yonetimi basit ve net olmali
  **How to apply:** calculate_position_size'da sabit 50.0, allocation_pct kullanilmiyor

- Vadeli islemlerde olmayan coinlerde islem acilmamali
  **Why:** LYNUSDT Invalid symbol hatasi yasandi
  **How to apply:** is_tradeable() kontrolu var
