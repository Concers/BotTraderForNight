#!/bin/bash
# TradeBot VPS kurulum scripti (Ubuntu 22.04+)
# Kullanim: sudo bash install.sh
# Calisma dizini: bu script'in oldugu yer = projenin /deploy klasoru
set -euo pipefail

# ===== Konfigurasyon =====
TRADEBOT_USER="tradebot"
TRADEBOT_HOME="/home/${TRADEBOT_USER}"
PROJECT_DIR="${TRADEBOT_HOME}/BotTraderForNight"
SERVICE_FILE="/etc/systemd/system/tradebot.service"

log() { echo -e "\033[1;34m[INSTALL]\033[0m $*"; }
err() { echo -e "\033[1;31m[HATA]\033[0m $*" >&2; exit 1; }

# Root kontrolu
if [[ $EUID -ne 0 ]]; then
   err "Bu scripti sudo ile calistirmalisin: sudo bash install.sh"
fi

# ===== 1. Sistem paketleri =====
log "Sistem paketleri guncelleniyor..."
apt update
apt install -y python3 python3-venv python3-pip git curl ca-certificates

# ===== 2. tradebot kullanicisi =====
if ! id -u "${TRADEBOT_USER}" >/dev/null 2>&1; then
    log "Kullanici '${TRADEBOT_USER}' olusturuluyor..."
    useradd -m -s /bin/bash "${TRADEBOT_USER}"
else
    log "Kullanici '${TRADEBOT_USER}' zaten var."
fi

# ===== 3. Proje klasorunu kontrol et =====
if [[ ! -d "${PROJECT_DIR}" ]]; then
    err "Proje bulunamadi: ${PROJECT_DIR}
    Once projeyi su sekilde klonla:
    sudo -u ${TRADEBOT_USER} git clone <repo-url> ${PROJECT_DIR}"
fi

# ===== 4. Venv + requirements =====
log "Python venv hazirlaniyor..."
sudo -u "${TRADEBOT_USER}" bash -c "
    cd '${PROJECT_DIR}' &&
    python3 -m venv .venv &&
    .venv/bin/pip install --upgrade pip &&
    .venv/bin/pip install -r requirements.txt
"

# ===== 5. logs + data klasorleri =====
log "Calisma klasorleri olusturuluyor..."
sudo -u "${TRADEBOT_USER}" mkdir -p "${PROJECT_DIR}/logs" "${PROJECT_DIR}/data" "${PROJECT_DIR}/reports"

# ===== 6. .env kontrolu =====
if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    log ".env bulunamadi - .env.example'dan kopyalaniyor..."
    sudo -u "${TRADEBOT_USER}" cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    log "⚠️  ${PROJECT_DIR}/.env dosyasini duzenleyip API anahtarlarini ekle!"
fi

# ===== 7. systemd service =====
log "systemd servisi kuruluyor..."
cp "${PROJECT_DIR}/deploy/tradebot.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable tradebot.service

log "✅ Kurulum tamamlandi!"
echo ""
echo "Sonraki adimlar:"
echo "  1. .env dosyasini duzenle: sudo -u ${TRADEBOT_USER} nano ${PROJECT_DIR}/.env"
echo "  2. Botu baslat:           sudo systemctl start tradebot"
echo "  3. Loglari izle:          sudo journalctl -u tradebot -f"
echo "  4. Durumu gor:            sudo systemctl status tradebot"
echo "  5. Durdur:                sudo systemctl stop tradebot"
echo "  6. Guncelle + yeniden bas:"
echo "     sudo -u ${TRADEBOT_USER} bash -c 'cd ${PROJECT_DIR} && git pull && .venv/bin/pip install -r requirements.txt'"
echo "     sudo systemctl restart tradebot"
