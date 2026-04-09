---
name: Bot Mimarisi ve Moduller
description: Trading bot dosya yapisi, akis ve teknik detaylar
type: project
---

**Why:** Kullanici 24 saat bot calistiracak, her konusmada projeyi sifirdan okumamak icin.

**How to apply:** Dosya yapisi ve akisi biliyorsan direkt koda gir.

## Dosyalar
- bot.py: Ana dongu, auto_scan (3dk aralik), monitor_positions (3sn aralik), recover_open_positions
- binance_client.py: public_client (gercek veri) + client (testnet islem), hassasiyet cache, is_tradeable()
- indicators.py: UT Bot, VWAP, RSI, ADX, EMA, ATR (ta kutuphanesi, Python 3.9 uyumlu)
- scoring.py: CANSLIM 7 bilesen (C/A/V/S/L/M/T), T=%45 agirlik, sessiz birikme dedektoru
- risk_manager.py: Sonsuz trailing stop (%1.2 adim, %1.4 geride), sabit $50 marjin
- market_filter.py: Binance ticker'dan market cap tahmini (45M-75M)
- telegram_bot.py: Notifier + SignalReceiver, /durum komutu
- config.py: .env'den yuklenir

## Bilinen Sorunlar
- Python 3.9 (pandas-ta yerine ta kutuphanesi)
- Testnet'te STOP_MARKET desteklenmiyor (bot kendi takip ediyor)
- Testnet fiyatlari gercek fiyatlardan farkli (get_current_price testnet client kullanir)
- post_start hook calismadi, post_init icinde task baslatiliyor
