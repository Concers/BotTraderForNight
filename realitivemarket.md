Market Crawler" mimarisidir.

Bu sistem, bilgisayarın başında olmasan bile arka planda marketi elekten geçirir, 45M-75M aralığına denk gelen hacimli coinleri bulur ve senin v2 skorlama sisteminden geçenleri sana raporlar veya işleme girer.

İşte bu sürekli takip sisteminin (Crawler v2) teknik kurulum planı:

1. Market Crawler Mantığı (5 Dakikalık Döngü)
Bu yapı, her 5 dakikada bir "Uyanır", marketi tarar ve "Uyur". Hiçbir sinyali kaçırmaz.

Python
import time
from datetime import datetime

def market_scanner_loop():
    print(f"[{datetime.now()}] Tarama Başlatıldı...")
    
    # 1. ADIM: Tüm marketin 24 saatlik özetini al (Rate Limit dostu)
    # Bu bize "Hangi coin hareketli?" ve "Hacmi ne kadar?" bilgisini verir.
    all_tickers = binance_client.ticker_24hr_price_change()
    
    # 2. ADIM: Hacim Filtresi (Market Cap yerine 24s Hacim)
    # 45M-75M Market Cap'li coinler genelde 3M$ - 15M$ arası günlük hacim yapar.
    candidates = []
    for ticker in all_tickers:
        symbol = ticker['symbol']
        quote_volume = float(ticker['quoteVolume']) # USDT bazlı 24s hacim
        
        if "USDT" in symbol and 3_000_000 <= quote_volume <= 15_000_000:
            candidates.append(symbol)
    
    print(f"{len(candidates)} adet potansiyel coin bulundu. Derin analize geçiliyor...")

    # 3. ADIM: Derin Analiz (Senin v2 Skorlama Sistemin)
    for symbol in candidates:
        # v2 Analiz fonksiyonunu çağır (UT Bot, RSI, VWAP, RVOL)
        report = analyzer_v2.analyze_100_signals(symbol)
        
        if report['is_confirmed']:
            print(f"🔥 FIRSAT BULDUM: {symbol} | Skor: {report['score']}")
            # Burada işleme gir veya bildirim gönder
            execute_trade_v2(symbol, report)
        
        # Rate limit yememek için kısa bir es (0.1 sn)
        time.sleep(0.1)

# --- ANA DÖNGÜ ---
while True:
    try:
        market_scanner_loop()
    except Exception as e:
        print(f"Hata oluştu: {e}")
    
    print("5 dakika bekleniyor...")
    time.sleep(300) # 300 saniye = 5 dakika
2. Süreklilik İçin PM2 Yapılandırması
Bu kodu bir dosyaya (örneğin scanner.py) kaydettikten sonra, bilgisayarını kapatsan bile (serverda) veya terminali kapatsan bile çalışmaya devam etmesi için PM2 ile şu şekilde başlatmalısın:

Bash
pm2 start scanner.py --name "market-scanner" --interpreter python3
3. Bu Sistemin "Sürekli Takip" Avantajları
Gözden Kaçmaz: 200+ coini her 5 dakikada bir kontrol eder. Manuel olarak bir insanın yapması imkansızdır.

Duygusuz Karar: Senin bugünkü raporundaki "duygusal" veya "rastgele" işlemler yerine, sadece matematiksel olarak 75+ skor alanları seçer.

Otomatik Filtre: Hacmi 15M doların üzerine çıkan (artık Small Cap olmaktan çıkan) veya 3M doların altına düşen (tamamen ölü) coinleri listeden otomatik atar.

4. V2.1 İyileştirmesi: "Önceki 100 Sinyal" Analizi
Sistemi sürekli takip moduna aldığında, analyze_100_signals fonksiyonun her taramada o coinin geçmiş 5 saatini (100 mum x 3 dk) kontrol eder.

Eğer coin son 5 saattir düzenli yükseliyorsa skor yükselir.

Eğer coin son 5 saatte çok fazla iğne (wick) attıysa skor düşer ve seni hatalı işlemden korur.

Şu anki durum: Bilgisayarını kapatmadan önce bu "Döngü (While True)" mantığını koduna ekleyip PM2 ile başlatırsan, sen 1 saat sonra geri geldiğinde botun o süre zarfında markette neler bulduğunu loglardan (pm2 logs) okuyabilirsin.

Bu yapıyı scanner.py olarak ayırıp ana botundan bağımsız çalıştırmak ister misin? (Böylece biri sadece tarar, diğeri sadece işlem yönetir).