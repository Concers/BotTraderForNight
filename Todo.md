1. Sunucu ve Altyapı Hazırlığı (VPS)

[ ] Zaman Senkronizasyonu: sudo timedatectl set-ntp true komutuyla saati Binance ile eşitle (Kritik!).

[ ] Python Ortamı: python3-venv ile izole bir sanal ortam oluştur.

[ ] PM2 Kurulumu: Botun 7/24 çalışması ve çökerse yeniden kalkması için Node.js ve PM2'yi kur.

2. API ve Veri Entegrasyonu
[ ] Binance API: Sadece "Futures" yetkisi verilmiş (Para çekme kapalı!) API anahtarlarını al.

[ ] Telegram Bot Token: @BotFather üzerinden kendi botunu oluştur ve Token'ını sakla.

[ ] CoinMarketCap API: 45M$-75M$ filtrelemesi için ücretsiz bir API key al.

3. İndikatör ve Analiz Katmanı (indicators.py)
[ ] UT Bot Alerts: Pine Script'teki ATR Trailing Stop mantığını Python'a dök.

[ ] VWAP & RSI: pandas-ta ile hacim ağırlıklı fiyatı ve aşırı alım/satım bölgesini hesapla.

[ ] Hacim Filtresi: Son 20 mumun ortalama hacminin üzerindeki "patlamaları" tespit et.

[ ] Trend Gücü (ADX/Slope): Trendin sadece yönünü değil, "dikliğini" hesaplayan kontrolü ekle.

4. Risk Yönetimi ve Akıllı Stop-Loss (risk_manager.py)
[ ] Adaptive Stop-Loss: Coinin volatilitesine (ATR %) göre stop mesafesini belirleyen fonksiyonu yaz.

[ ] Max Open Trades: Global bir sayaçla aynı anda en fazla 10 işlem kuralını koy.

[ ] Breakeven (Başabaş): Fiyat %1-1.5 kâra geçtiğinde stopu giriş seviyesine çeken "takipçi" kodunu ekle.

[ ] Time Stop: 20-30 dakika boyunca hedefe gitmeyen işlemi kapatacak zamanlayıcıyı kur.

5. Ana Bot Döngüsü (bot.py)
[ ] Drop Pending Updates: Bot açıldığında Telegram'da birikmiş eski sinyalleri görmezden gelmesini sağla.

[ ] Latency (Gecikme) Kontrolü: Mesajın geliş milisaniyesi ile sunucu zamanını karşılaştır (3 sn kuralı).

[ ] Market Cap Filtresi: Sadece 45M$-75M$ arası coinler için "İşlem Aç" emrini tetikle.

6. Test ve Canlıya Alma
[ ] Binance Testnet: Stratejiyi önce gerçek para harcamadan Testnet üzerinde en az 24 saat dene.

[ ] Hata Loglama: logging kütüphanesiyle tüm hataları ve reddedilen sinyalleri bir dosyaya kaydet.

[ ] Canlıya Geçiş: PM2 ile botu başlat: pm2 start bot.py --interpreter python3.

Botun Çalışma Akış Şeması (Özet):
Sinyal Gelir: Telegram -> Python.

Hızlı Filtre: Gecikme < 3sn? Market Cap 45M-75M? Mevcut işlem < 10? (Hayırsa İptal).

Analiz: UT Bot Yeşil? Fiyat > VWAP? RSI < 70? (Hayırsa İptal).

Hesaplama: ATR'ye bak -> Stop-Loss noktasını coine göre ayarla.

Emir: Binance Futures -> Market Giriş + Stop Market Emri.

Takip: Fiyat %1.5 çıkarsa stopu girişe çek; 30 dk geçerse kapat.

Bu liste, hazırladığın botun piyasadaki çoğu amatör bottan daha güvenli ve profesyonel çalışmasını sağlayacaktır. Hazırlık aşamasında takıldığın bir modül (örneğin "hacim filtresini nasıl yazacağım" gibi) olursa hemen sorabilirsin. Başarılar dilerim!

Harika! Botunun sadece bir sinyal uygulayıcısı değil, karar verici bir analist gibi çalışması için bu profesyonel framework'ü README.md dosyana ekleyelim. Bu, hem senin için bir anayasa olacak hem de projenin profesyonelliğini bir üst seviyeye taşıyacak.Aşağıdaki metni README.md dosyana kopyalayabilirsin:🧠 Karar Mekanizması: Sistematik Analiz ve SkorlamaBu bot, her bir sinyali işleme almadan önce William O'Neil'ın CANSLIM modeli ile Profesyonel Teknik Analiz Framework'ünü harmanlayan bir süzgeçten geçirir.🛡️ 1. Adım: Veri Odaklı Onay (Objective Assessment)Bot, kişisel yargılardan uzak, sadece grafik üzerindeki observable (gözlemlenebilir) verileri işler:Trend Analizi: UT Bot Yönü + EMA 20/50 Eğimi.Hacim Onayı (V - Volume Spike): Son 3 mumdaki hacim patlaması, önceki 20 mumun ortalamasından %150 daha büyük olmalıdır.Seviye Analizi: Fiyatın VWAP ve direnç bölgelerine olan mesafesi (Proximity Check).📈 2. Adım: Hibrit CANSLIM + V Skoru (Component Weights)Sinyal, aşağıdaki ağırlıklara göre bir Güven Skoru (Confidence Score) alır:HarfBileşenAğırlıkAçıklamaCCurrent Momentum%15RSI ve Fiyat ivmesi.AATR / Volatility%15Coinin hırçınlığı ve stop-loss mesafesi.VVolume Spike%25Ana Filtre: Patlayıcı hacim girişi.SSupply/Demand%15Orderbook derinliği ve mum iğne boyları.LLeadership / RS%25Marketin geneline göre son 1 saatteki güç.MMarket Context%5BTC ve ETH genel trend yönü.🛠️ 3. Adım: Sequential Processing (Ardışık İşleme)Bot, analizi şu sistematik sıra ile yürütür:Receipt: Sinyali yakala ve milisaniye kontrolünü yap.Analysis: İndikatör hesaplamalarını yap (indicators.py).Synthesis: Skorlama yap. Eğer Skor > 75 ise devam et.Execution: Adaptive Stop-Loss ve Market Cap (45M-75M) filtresini uygula.Monitoring: Pozisyonu Time-Stop (30dk) ve Breakeven kuralları ile takip et.🧩 4. Adım: Olasılık Temelli Senaryolar (Probabilistic Scenarios)Bot, işlemlerini sadece "Al/Sat" olarak değil, risk seviyelerine göre açar:High Confidence (Skor 90+): Tam bakiye (Max allocation).Moderate Confidence (Skor 75-89): %50 bakiye ile temkinli giriş.Low Confidence (Skor < 75): İşlem reddedilir, log dosyasına kaydedilir.⚠️ Analiz Disiplini"Chart analysis is based exclusively on technical data visible. No bias, no emotion."Botun "Analiz Günlüğü" (Log), reddedilen her sinyalin hangi kriterden (örneğin: "Insufficient Volume Spike" veya "Near Major Resistance") dolayı elendiğini detaylıca raporlar.