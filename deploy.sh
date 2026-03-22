#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────
#  Telegram → Google Drive Bot  |  Deployer
# ─────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR="$SCRIPT_DIR"
SERVICE_NAME="tgdrive-bot"
VENV_DIR="$BOT_DIR/.venv"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
BOT_USER="${SUDO_USER:-$(whoami)}"

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERR]${NC}   $*"; }
header()  { echo -e "\n${BOLD}━━━  $*  ━━━${NC}"; }

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    error "Run with sudo:  sudo bash deploy.sh"
    exit 1
fi

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Telegram → Google Drive Bot        ║"
echo "  ║   Deployment Script                  ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. System packages ────────────────────────────────────────────────────────
header "1 / 6  System packages"

apt-get update -qq
PACKAGES=(python3 python3-pip python3-venv curl git)
for pkg in "${PACKAGES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        info "$pkg already installed"
    else
        info "Installing $pkg..."
        apt-get install -y -qq "$pkg"
        success "$pkg installed"
    fi
done

# ── 2. Python virtual environment ─────────────────────────────────────────────
header "2 / 6  Python virtual environment"

if [[ -d "$VENV_DIR" ]]; then
    info "Virtualenv already exists at $VENV_DIR"
else
    python3 -m venv "$VENV_DIR"
    success "Created virtualenv at $VENV_DIR"
fi

info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"
success "All Python packages installed"

# ── 3. Environment file ───────────────────────────────────────────────────────
header "3 / 6  Environment configuration"

ENV_FILE="$BOT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
    warn ".env already exists — skipping interactive setup"
    warn "Edit $ENV_FILE manually if you need to change values"
else
    echo ""
    echo -e "${CYAN}Let's configure the bot. Press Enter to skip optional fields.${NC}"
    echo ""

    read -rp "  Telegram Bot Token (from @BotFather): " TG_TOKEN
    while [[ -z "$TG_TOKEN" ]]; do
        error "Telegram token is required."
        read -rp "  Telegram Bot Token: " TG_TOKEN
    done

    read -rp "  Allowed Telegram IDs (comma-separated, e.g. 123,456): " ALLOWED_IDS
    while [[ -z "$ALLOWED_IDS" ]]; do
        error "At least one Telegram ID is required."
        read -rp "  Allowed Telegram IDs: " ALLOWED_IDS
    done

    read -rp "  Google Drive Folder ID (optional, leave blank for root): " FOLDER_ID

    cat > "$ENV_FILE" <<EOF
TELEGRAM_TOKEN=${TG_TOKEN}
ALLOWED_IDS=${ALLOWED_IDS}
GDRIVE_CLIENT_SECRET_FILE=${BOT_DIR}/client_secret.json
GDRIVE_TOKEN_FILE=${BOT_DIR}/token.json
GDRIVE_FOLDER_ID=${FOLDER_ID}
EOF

    chmod 600 "$ENV_FILE"
    success ".env created at $ENV_FILE"
fi

# ── 4. Google OAuth2 credentials ──────────────────────────────────────────────
header "4 / 6  Google OAuth2 setup"

CLIENT_SECRET="$BOT_DIR/client_secret.json"
TOKEN_FILE="$BOT_DIR/token.json"

# Step 4a — client_secret.json
if [[ -f "$CLIENT_SECRET" ]]; then
    success "client_secret.json found"
else
    warn "client_secret.json NOT found."
    echo ""
    echo -e "  ${YELLOW}Get it from Google Cloud Console:${NC}"
    echo "  1. Go to https://console.cloud.google.com"
    echo "  2. Create/select a project → Enable Google Drive API"
    echo "  3. APIs & Services → Credentials → + Create Credentials → OAuth 2.0 Client ID"
    echo "  4. Application type: Desktop app → Create"
    echo "  5. Download JSON → rename to client_secret.json"
    echo "  6. Also go to: APIs & Services → OAuth consent screen"
    echo "     → Add your Google account email under 'Test users'"
    echo ""
    read -rp "  Paste the full path to client_secret.json (or Enter to skip): " SRC
    if [[ -n "$SRC" && -f "$SRC" ]]; then
        cp "$SRC" "$CLIENT_SECRET"
        chmod 600 "$CLIENT_SECRET"
        success "Copied to $CLIENT_SECRET"
    else
        warn "Skipped — you must place client_secret.json before running auth"
    fi
fi

# Step 4b — run auth.py if token.json is missing
if [[ -f "$TOKEN_FILE" ]]; then
    success "token.json found — already authenticated"
else
    if [[ -f "$CLIENT_SECRET" ]]; then
        echo ""
        echo -e "  ${CYAN}Now let's authenticate with your Google account.${NC}"
        echo -e "  A URL will appear — open it in your browser, approve access,"
        echo -e "  then paste the code back here."
        echo ""
        sudo -u "$BOT_USER" "$VENV_DIR/bin/python" "$BOT_DIR/auth.py"
        if [[ -f "$TOKEN_FILE" ]]; then
            success "Authentication complete — token.json saved"
        else
            error "Authentication failed — token.json was not created"
            exit 1
        fi
    else
        warn "Skipping auth — client_secret.json is missing"
        warn "Place client_secret.json and run:  python auth.py"
    fi
fi

# ── 5. Systemd service ────────────────────────────────────────────────────────
header "5 / 6  Systemd service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram → Google Drive Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python ${BOT_DIR}/bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
success "Systemd service registered and enabled"

# ── 6. Start / restart bot ────────────────────────────────────────────────────
header "6 / 6  Starting bot"

if [[ ! -f "$TOKEN_FILE" ]]; then
    error "token.json is missing — cannot start bot without Google auth"
    echo -e "  Run:  ${CYAN}python auth.py${NC}  then restart with:  ${CYAN}sudo systemctl start ${SERVICE_NAME}${NC}"
    exit 1
fi

if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Service already running — restarting..."
    systemctl restart "$SERVICE_NAME"
else
    systemctl start "$SERVICE_NAME"
fi

sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    success "Bot is running!"
else
    error "Bot failed to start. Check logs below:"
    echo ""
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager
    exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ✓ Deployment complete!${NC}"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "  ${CYAN}sudo systemctl status  ${SERVICE_NAME}${NC}   — check status"
echo -e "  ${CYAN}sudo systemctl restart ${SERVICE_NAME}${NC}   — restart bot"
echo -e "  ${CYAN}sudo systemctl stop    ${SERVICE_NAME}${NC}   — stop bot"
echo -e "  ${CYAN}sudo journalctl -u     ${SERVICE_NAME} -f${NC} — live logs"
echo ""
echo -e "  ${BOLD}If you need to re-authenticate Google:${NC}"
echo -e "  ${CYAN}python auth.py${NC}"
echo ""
