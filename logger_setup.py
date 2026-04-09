import logging
import os
from datetime import datetime


def setup_logger(name: str = "TraderBot") -> logging.Logger:
    """Ana logger + dosya ve konsol handler."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # Log klasoru
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Dosya handler - gun bazli
    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"bot_{today}.log"), encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)

    # Konsol handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Format
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(fmt)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# Reddedilen sinyaller icin ayri log
def setup_rejected_logger() -> logging.Logger:
    """Reddedilen sinyallerin detayli kaydi."""
    logger = logging.getLogger("RejectedSignals")
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    handler = logging.FileHandler(
        os.path.join(log_dir, f"rejected_{today}.log"), encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)
    return logger
