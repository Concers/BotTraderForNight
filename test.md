Bu ETH işleminizdeki başarıyı (18k+ PNL) standart bir bot algoritmasına dönüştürmek için, girişten önceki 100 mumu bir "karar matrisi" gibi tarayan V2 Analiz Sistemi'ni hazırladım.

Bu sistem, sadece anlık fiyata bakmaz; son 100 mumu (3 dakikalık periyotta yaklaşık 5 saat) tarayarak trendin "gerçek" olup olmadığını skorlar.

Proje Yapısı: analysis_v2.py
Bu dosya, önceki 1-2-3-4 maddelerindeki (UT Bot, VWAP, RSI, Hacim/Market Cap) mantığı birleştirir.

Python
import pandas as pd
import pandas_ta as ta
import numpy as np

class TradingAnalyzerV2:
    def __init__(self, sensitivity=1, atr_period=10):
        self.sensitivity = sensitivity
        self.atr_period = atr_period

    def calculate_ut_bot(self, df):
        # UT Bot Alerts Mantığı (Pine Script v4'ten çevrildi)
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)
        n_loss = self.sensitivity * df['ATR']
        
        src = df['close']
        trailing_stop = np.zeros(len(df))
        
        for i in range(1, len(df)):
            prev_ts = trailing_stop[i-1]
            if src.iloc[i] > prev_ts and src.iloc[i-1] > prev_ts:
                trailing_stop[i] = max(prev_ts, src.iloc[i] - n_loss.iloc[i])
            elif src.iloc[i] < prev_ts and src.iloc[i-1] < prev_ts:
                trailing_stop[i] = min(prev_ts, src.iloc[i] + n_loss.iloc[i])
            else:
                trailing_stop[i] = src.iloc[i] - n_loss.iloc[i] if src.iloc[i] > prev_ts else src.iloc[i] + n_loss.iloc[i]
        
        df['ut_ts'] = trailing_stop
        df['ut_signal'] = np.where(df['close'] > df['ut_ts'], "BUY", "SELL")
        return df

    def analyze_100_signals(self, df):
        """
        Giriş öncesi 100 mumluk derin analiz ve skorlama (v2)
        """
        # 1. Temel İndikatörler
        df = self.calculate_ut_bot(df)
        df['VWAP'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
        df['RSI'] = ta.rsi(df['close'], length=14)
        df['EMA20'] = ta.ema(df['close'], length=20)
        df['EMA_Slope'] = ta.slope(df['EMA20'])
        
        # 2. 100 Mum Karar Matrisi
        window = df.tail(100)
        
        # Skorlama Başlıyor
        score = 0
        
        # KURAL 1: Trend İstikrarı (UT Bot 100 mumun ne kadarında BUY?)
        buy_stability = (window['ut_signal'] == "BUY").sum()
        if buy_stability > 65: score += 30 # Güçlü Trend
        elif buy_stability > 50: score += 15 # Zayıf Trend
        
        # KURAL 2: VWAP ve Hacim Uyumu
        above_vwap_ratio = (window['close'] > window['VWAP']).sum()
        if above_vwap_ratio > 70: score += 25 # Hacimli destek alanı
        
        # KURAL 3: RSI Momentum (Son 100 mumda aşırı alım/satım dengesi)
        avg_rsi = window['RSI'].mean()
        if 45 <= avg_rsi <= 60: score += 20 # Sağlıklı yükseliş alanı
        
        # KURAL 4: EMA Eğimi (Trendin dikliği)
        current_slope = window['EMA_Slope'].iloc[-1]
        if current_slope > 0: score += 25
        
        # 3. Sonuç ve Karar
        is_confirmed = score >= 75 # %75 güven eşiği
        
        # Uyarlanabilir Stop Mesafesi (ATR % bazlı)
        current_atr = window['ATR'].iloc[-1]
        volatility_pct = (current_atr / window['close'].iloc[-1]) * 100
        stop_multiplier = 2.5 if volatility_pct > 1.2 else 1.8
        
        return {
            "is_confirmed": is_confirmed,
            "score": score,
            "suggested_stop": window['close'].iloc[-1] - (current_atr * stop_multiplier),
            "trend_stability": buy_stability,
            "volatility": round(volatility_pct, 4)
        }

# ÖRNEK KULLANIM (Main Bot içinde)
# analyzer = TradingAnalyzerV2()
# report = analyzer.analyze_100_signals(df_last_100_klines)
# if report['is_confirmed']:
#     execute_order(symbol, report['suggested_stop'])