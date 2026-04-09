import os
from dotenv import load_dotenv

load_dotenv()

# --- Binance ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Risk Yonetimi ---
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "7"))
DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "20"))
CAPITAL_PERCENT_PER_TRADE = float(os.getenv("CAPITAL_PERCENT_PER_TRADE", "14.28"))
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "45000000"))
MAX_MARKET_CAP = float(os.getenv("MAX_MARKET_CAP", "75000000"))
MIN_CONFIDENCE_SCORE = float(os.getenv("MIN_CONFIDENCE_SCORE", "75"))

# --- Teknik Parametreler ---
ATR_LENGTH = 14
UT_BOT_SENSITIVITY = 1.0
RSI_LENGTH = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
VOLUME_SPIKE_MULTIPLIER = 1.5  # %150 ortalama hacim
ADX_TREND_THRESHOLD = 25
BREAKEVEN_TRIGGER_PCT = 1.5  # %1.5 kara gecince stop girisa cekilir
TIME_STOP_MINUTES = 30
LATENCY_MAX_SECONDS = 3
