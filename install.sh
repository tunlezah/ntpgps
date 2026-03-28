#!/usr/bin/env bash
#
# install.sh - GPS-disciplined NTP server installer
#
# Supports: Raspberry Pi OS (Bookworm+), Ubuntu Server 24.04, 26.04
# Architectures: arm64, amd64
#
# Usage:
#   sudo ./install.sh [OPTIONS]
#
# Options:
#   --dry-run       Show what would be done without making changes
#   --verbose       Enable verbose output
#   --force         Skip confirmation prompts
#   --upgrade       Upgrade existing installation in place
#   --help          Show this help message
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
readonly SCRIPT_VERSION="1.0.0"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly INSTALL_PREFIX="/opt/ntpgps"
readonly CONF_DIR="/etc/ntpgps"
readonly BACKUP_DIR="/etc/ntpgps/backup"
readonly DATA_DIR="/var/lib/ntpgps"
readonly LOG_DIR="/var/log/ntpgps"
readonly VENV_DIR="${INSTALL_PREFIX}/venv"
readonly SERVICE_USER="ntpgps"
readonly SERVICE_GROUP="ntpgps"
readonly SYSTEMD_DIR="/etc/systemd/system"
readonly LOGFILE="/var/log/ntpgps-install.log"

readonly SUPPORTED_OS_IDS="debian ubuntu raspbian"
readonly SUPPORTED_UBUNTU_VERSIONS="24.04 26.04"
readonly MIN_DEBIAN_VERSION="12"   # Bookworm
readonly MIN_DISK_MB=200
readonly REQUIRED_SYSTEM_PACKAGES=(
    chrony
    gpsd
    gpsd-clients
    python3
    python3-venv
    python3-pip
    pps-tools
    setserial
)
readonly CONFLICTING_PACKAGES=(
    ntp
    ntpd
    ntpsec
    openntpd
)
readonly PYTHON_PACKAGES=(
    flask
    flask-sock
    pyyaml
    simple-websocket
)

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
DRY_RUN=0
VERBOSE=0
FORCE=0
UPGRADE=0
DETECTED_OS_ID=""
DETECTED_OS_VERSION=""
DETECTED_ARCH=""
DETECTED_PLATFORM=""   # "rpi" or "generic"
INSTALL_STEP=0
TOTAL_STEPS=9

# ---------------------------------------------------------------------------
# Color helpers (disabled when stdout is not a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RED='\033[0;31m'
    C_GREEN='\033[0;32m'
    C_YELLOW='\033[1;33m'
    C_BLUE='\033[0;34m'
    C_BOLD='\033[1m'
    C_RESET='\033[0m'
else
    C_RED='' C_GREEN='' C_YELLOW='' C_BLUE='' C_BOLD='' C_RESET=''
fi

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log_raw() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${ts}] $*" >> "${LOGFILE}" 2>/dev/null || true
}

log_info() {
    echo -e "${C_GREEN}[INFO]${C_RESET} $*"
    _log_raw "INFO  $*"
}

log_warn() {
    echo -e "${C_YELLOW}[WARN]${C_RESET} $*" >&2
    _log_raw "WARN  $*"
}

log_error() {
    echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2
    _log_raw "ERROR $*"
}

log_step() {
    INSTALL_STEP=$((INSTALL_STEP + 1))
    echo ""
    echo -e "${C_BLUE}${C_BOLD}[${INSTALL_STEP}/${TOTAL_STEPS}]${C_RESET} ${C_BOLD}$*${C_RESET}"
    _log_raw "STEP  [${INSTALL_STEP}/${TOTAL_STEPS}] $*"
}

log_debug() {
    if [[ "${VERBOSE}" -eq 1 ]]; then
        echo -e "       $*"
    fi
    _log_raw "DEBUG $*"
}

log_dry() {
    echo -e "${C_YELLOW}[DRY-RUN]${C_RESET} $*"
    _log_raw "DRY   $*"
}

# ---------------------------------------------------------------------------
# Cleanup trap -- runs on any exit
# ---------------------------------------------------------------------------
cleanup() {
    local rc=$?
    if [[ ${rc} -ne 0 && ${INSTALL_STEP} -gt 0 ]]; then
        echo ""
        log_error "Installation failed at step ${INSTALL_STEP}/${TOTAL_STEPS} (exit code ${rc})."
        log_error "Review the log: ${LOGFILE}"
        log_error "To retry, fix the issue and re-run this script."
        log_error "To undo partial changes, run: sudo ./uninstall.sh"
    fi
    return 0
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# run() -- execute a command (or skip in dry-run mode)
# ---------------------------------------------------------------------------
run() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        log_dry "$*"
        return 0
    fi
    log_debug "exec: $*"
    "$@"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)  DRY_RUN=1 ;;
            --verbose)  VERBOSE=1 ;;
            --force)    FORCE=1 ;;
            --upgrade)  UPGRADE=1 ;;
            --help|-h)
                head -n 18 "${BASH_SOURCE[0]}" | tail -n +3 | sed 's/^# \?//'
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
        shift
    done
}

# ---------------------------------------------------------------------------
# Pre-flight: detect OS, architecture, platform
# ---------------------------------------------------------------------------
detect_os() {
    # Source os-release for ID, VERSION_ID, VERSION_CODENAME
    if [[ ! -f /etc/os-release ]]; then
        log_error "/etc/os-release not found. Cannot detect operating system."
        exit 1
    fi
    # shellcheck source=/dev/null
    . /etc/os-release

    DETECTED_OS_ID="${ID:-unknown}"
    DETECTED_OS_VERSION="${VERSION_ID:-unknown}"

    log_info "Detected OS: ${PRETTY_NAME:-${DETECTED_OS_ID} ${DETECTED_OS_VERSION}}"
}

detect_arch() {
    DETECTED_ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
    # Normalise
    case "${DETECTED_ARCH}" in
        aarch64) DETECTED_ARCH="arm64" ;;
        x86_64)  DETECTED_ARCH="amd64" ;;
    esac
    log_info "Detected architecture: ${DETECTED_ARCH}"
}

detect_platform() {
    # Method 1: /proc/device-tree/model (most reliable on Pi)
    if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
        DETECTED_PLATFORM="rpi"
    # Method 2: os-release ID is raspbian
    elif [[ "${DETECTED_OS_ID}" == "raspbian" ]]; then
        DETECTED_PLATFORM="rpi"
    # Method 3: check for vcgencmd (Pi-specific)
    elif command -v vcgencmd &>/dev/null; then
        DETECTED_PLATFORM="rpi"
    else
        DETECTED_PLATFORM="generic"
    fi
    log_info "Detected platform: ${DETECTED_PLATFORM}"
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
preflight_checks() {
    local fail=0

    # Must be root
    if [[ "$(id -u)" -ne 0 ]]; then
        log_error "This script must be run as root (sudo)."
        exit 1
    fi

    # OS check
    local os_ok=0
    for valid_id in ${SUPPORTED_OS_IDS}; do
        if [[ "${DETECTED_OS_ID}" == "${valid_id}" ]]; then
            os_ok=1
            break
        fi
    done
    if [[ ${os_ok} -eq 0 ]]; then
        log_error "Unsupported OS: ${DETECTED_OS_ID}. Supported: ${SUPPORTED_OS_IDS}"
        fail=1
    fi

    # Version check
    if [[ "${DETECTED_OS_ID}" == "ubuntu" ]]; then
        local ver_ok=0
        for v in ${SUPPORTED_UBUNTU_VERSIONS}; do
            if [[ "${DETECTED_OS_VERSION}" == "${v}" ]]; then
                ver_ok=1
                break
            fi
        done
        if [[ ${ver_ok} -eq 0 ]]; then
            log_error "Unsupported Ubuntu version: ${DETECTED_OS_VERSION}. Supported: ${SUPPORTED_UBUNTU_VERSIONS}"
            fail=1
        fi
    fi
    if [[ "${DETECTED_OS_ID}" == "debian" || "${DETECTED_OS_ID}" == "raspbian" ]]; then
        if [[ "${DETECTED_OS_VERSION%%.*}" -lt "${MIN_DEBIAN_VERSION}" ]]; then
            log_error "Debian/Raspbian ${DETECTED_OS_VERSION} too old. Need >= ${MIN_DEBIAN_VERSION} (Bookworm)."
            fail=1
        fi
    fi

    # Architecture check
    if [[ "${DETECTED_ARCH}" != "arm64" && "${DETECTED_ARCH}" != "amd64" && "${DETECTED_ARCH}" != "armhf" ]]; then
        log_error "Unsupported architecture: ${DETECTED_ARCH}. Supported: arm64, amd64, armhf"
        fail=1
    fi

    # Disk space
    local avail_mb
    avail_mb=$(df --output=avail -BM / | tail -1 | tr -d ' M')
    if [[ "${avail_mb}" -lt "${MIN_DISK_MB}" ]]; then
        log_error "Insufficient disk space: ${avail_mb} MB available, ${MIN_DISK_MB} MB required."
        fail=1
    else
        log_debug "Disk space: ${avail_mb} MB available"
    fi

    # Existing installation
    if [[ -d "${INSTALL_PREFIX}" && "${UPGRADE}" -eq 0 ]]; then
        log_error "Existing installation found at ${INSTALL_PREFIX}."
        log_error "Use --upgrade to upgrade, or run uninstall.sh first."
        fail=1
    fi

    # Conflicting packages
    for pkg in "${CONFLICTING_PACKAGES[@]}"; do
        if dpkg -l "${pkg}" 2>/dev/null | grep -q '^ii'; then
            log_error "Conflicting package installed: ${pkg}. Remove it first:"
            log_error "  sudo systemctl stop ${pkg} && sudo apt-get purge -y ${pkg}"
            fail=1
        fi
    done

    if [[ ${fail} -ne 0 ]]; then
        log_error "Pre-flight checks failed. Aborting."
        exit 1
    fi
    log_info "All pre-flight checks passed."
}

# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------
confirm_install() {
    if [[ "${FORCE}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
        return 0
    fi

    echo ""
    echo -e "${C_BOLD}GPS-disciplined NTP Server Installer v${SCRIPT_VERSION}${C_RESET}"
    echo ""
    echo "  OS:           ${DETECTED_OS_ID} ${DETECTED_OS_VERSION}"
    echo "  Architecture: ${DETECTED_ARCH}"
    echo "  Platform:     ${DETECTED_PLATFORM}"
    echo "  Install to:   ${INSTALL_PREFIX}"
    echo "  Mode:         $([ "${UPGRADE}" -eq 1 ] && echo "Upgrade" || echo "Fresh install")"
    echo ""
    read -r -p "Proceed with installation? [y/N] " answer
    if [[ "${answer}" != "y" && "${answer}" != "Y" ]]; then
        log_info "Installation cancelled by user."
        exit 0
    fi
}

# ---------------------------------------------------------------------------
# Step 1: Backup existing configuration
# ---------------------------------------------------------------------------
backup_configs() {
    log_step "Backing up existing configurations"

    local timestamp
    timestamp="$(date '+%Y%m%d-%H%M%S')"
    local bdir="${BACKUP_DIR}/${timestamp}"

    run mkdir -p "${bdir}"

    local files_to_backup=(
        /etc/chrony/chrony.conf
        /etc/chrony.conf
        /etc/default/gpsd
    )

    local found=0
    for f in "${files_to_backup[@]}"; do
        if [[ -f "${f}" ]]; then
            run cp -a "${f}" "${bdir}/$(basename "${f}").bak"
            log_info "Backed up ${f}"
            found=1
        fi
    done

    # Also back up any existing systemd unit we manage
    if [[ -f "${SYSTEMD_DIR}/ntpgps-web.service" ]]; then
        run cp -a "${SYSTEMD_DIR}/ntpgps-web.service" "${bdir}/"
        found=1
    fi

    if [[ ${found} -eq 0 ]]; then
        log_info "No existing configurations to back up."
    else
        log_info "Backups stored in ${bdir}"
    fi
}

# ---------------------------------------------------------------------------
# Step 2: Install system packages
# ---------------------------------------------------------------------------
install_system_packages() {
    log_step "Installing system packages"

    # Test network connectivity (apt update will fail without it)
    if ! run apt-get update -qq 2>/dev/null; then
        log_error "apt-get update failed. Check network connectivity."
        log_error "If offline, pre-install these packages and re-run with --force:"
        log_error "  ${REQUIRED_SYSTEM_PACKAGES[*]}"
        exit 1
    fi

    local to_install=()
    for pkg in "${REQUIRED_SYSTEM_PACKAGES[@]}"; do
        if dpkg -l "${pkg}" 2>/dev/null | grep -q '^ii'; then
            log_debug "Already installed: ${pkg}"
        else
            to_install+=("${pkg}")
        fi
    done

    if [[ ${#to_install[@]} -eq 0 ]]; then
        log_info "All required system packages are already installed."
        return 0
    fi

    log_info "Installing: ${to_install[*]}"
    if ! run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${to_install[@]}"; then
        log_error "Package installation failed."
        exit 1
    fi
    log_info "System packages installed."
}

# ---------------------------------------------------------------------------
# Step 3: Create service user and directories
# ---------------------------------------------------------------------------
create_user_and_dirs() {
    log_step "Creating service user and directories"

    if ! id "${SERVICE_USER}" &>/dev/null; then
        run useradd --system --no-create-home --home-dir "${INSTALL_PREFIX}" \
            --shell /usr/sbin/nologin "${SERVICE_USER}"
        log_info "Created system user: ${SERVICE_USER}"
    else
        log_debug "User ${SERVICE_USER} already exists."
    fi

    # Add the service user to dialout and gpio groups (for GPS serial access)
    run usermod -aG dialout "${SERVICE_USER}" 2>/dev/null || true
    if getent group gpio &>/dev/null; then
        run usermod -aG gpio "${SERVICE_USER}" 2>/dev/null || true
    fi

    local dirs=("${INSTALL_PREFIX}" "${CONF_DIR}" "${BACKUP_DIR}" "${DATA_DIR}" "${LOG_DIR}")
    for d in "${dirs[@]}"; do
        run mkdir -p "${d}"
    done
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${DATA_DIR}" "${LOG_DIR}"
    log_info "Directories created."
}

# ---------------------------------------------------------------------------
# Step 4: Set up Python virtual environment
# ---------------------------------------------------------------------------
setup_python_venv() {
    log_step "Setting up Python virtual environment"

    if [[ -d "${VENV_DIR}" && "${UPGRADE}" -eq 1 ]]; then
        log_info "Upgrading existing venv."
    fi

    run python3 -m venv --system-site-packages "${VENV_DIR}"
    run "${VENV_DIR}/bin/pip" install --quiet --upgrade pip setuptools wheel

    log_info "Installing Python packages: ${PYTHON_PACKAGES[*]}"
    if ! run "${VENV_DIR}/bin/pip" install --quiet "${PYTHON_PACKAGES[@]}"; then
        log_error "Python package installation failed."
        log_error "If offline, pre-download wheels into ${INSTALL_PREFIX}/wheels/ and re-run."
        exit 1
    fi

    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${VENV_DIR}"
    log_info "Python environment ready at ${VENV_DIR}"
}

# ---------------------------------------------------------------------------
# Step 5: Deploy application files
# ---------------------------------------------------------------------------
deploy_application() {
    log_step "Deploying application files"

    local app_dir="${INSTALL_PREFIX}/app"
    run mkdir -p "${app_dir}"

    # Copy everything from the source tree except .git, install/uninstall scripts
    if [[ -d "${SCRIPT_DIR}" ]]; then
        run rsync -a --delete \
            --exclude='.git' \
            --exclude='install.sh' \
            --exclude='uninstall.sh' \
            --exclude='*.pyc' \
            --exclude='__pycache__' \
            "${SCRIPT_DIR}/" "${app_dir}/"
        log_info "Application files deployed to ${app_dir}"
    else
        log_warn "No application source directory found at ${SCRIPT_DIR}"
    fi

    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${app_dir}"

    # Record installed version for future upgrades
    echo "${SCRIPT_VERSION}" > "${CONF_DIR}/version"

    # Create default config if not present
    if [[ ! -f "${CONF_DIR}/config.yaml" ]]; then
        cat > "${CONF_DIR}/config.yaml" <<'CONFIG_EOF'
# NTP GPS Server Configuration
version: "1.0.0"

server:
  host: "0.0.0.0"
  port: 8800
  debug: false

gps:
  gpsd_host: "127.0.0.1"
  gpsd_port: 2947
  device: "/dev/ttyACM0"
  min_satellites_for_valid_fix: 4
  max_pdop_for_valid_fix: 6.0
  min_signal_strength_db: 15

ntp:
  chrony_config_path: "/etc/chrony/chrony.conf"
  network_servers:
    - "0.au.pool.ntp.org"
    - "1.au.pool.ntp.org"
    - "2.au.pool.ntp.org"
    - "3.au.pool.ntp.org"
  local_stratum: 10

source_selection:
  mode: "auto"
  gps_loss_timeout_minutes: 15
  flap_hold_time_minutes: 10
  holdover_max_minutes: 120

display:
  timezone: "Australia/Canberra"
  theme: "system"
CONFIG_EOF
        log_info "Created default configuration at ${CONF_DIR}/config.yaml"
    fi
}

# ---------------------------------------------------------------------------
# Step 6: Configure gpsd
# ---------------------------------------------------------------------------
configure_gpsd() {
    log_step "Configuring gpsd"

    local gpsd_device="/dev/ttyAMA0"
    local gpsd_pps="/dev/pps0"

    # On Raspberry Pi, the primary UART is /dev/ttyAMA0 (or serial0 symlink)
    # On generic hardware, the user may have a USB GPS (ttyUSB0 or ttyACM0)
    if [[ "${DETECTED_PLATFORM}" != "rpi" ]]; then
        # Try to find a USB GPS
        if [[ -e /dev/ttyUSB0 ]]; then
            gpsd_device="/dev/ttyUSB0"
        elif [[ -e /dev/ttyACM0 ]]; then
            gpsd_device="/dev/ttyACM0"
        else
            gpsd_device="/dev/ttyUSB0"
            log_warn "No GPS device found. Defaulting to ${gpsd_device}."
            log_warn "Edit /etc/default/gpsd after installation if needed."
        fi
    fi

    local gpsd_conf="/etc/default/gpsd"
    cat > "${CONF_DIR}/gpsd.conf" <<GPSD_EOF
# gpsd configuration -- managed by ntpgps installer
# Original backed up in ${BACKUP_DIR}/
START_DAEMON="true"
USBAUTO="false"
DEVICES="${gpsd_device} ${gpsd_pps}"
GPSD_OPTIONS="-n -b"
GPSD_SOCKET="/var/run/gpsd.sock"
GPSD_EOF

    run cp "${CONF_DIR}/gpsd.conf" "${gpsd_conf}"
    log_info "gpsd configured: device=${gpsd_device}"

    # On Raspberry Pi, ensure the serial port is available:
    #   - Disable serial console (if not already done)
    #   - Enable UART in config.txt
    if [[ "${DETECTED_PLATFORM}" == "rpi" ]]; then
        local boot_config=""
        if [[ -f /boot/firmware/config.txt ]]; then
            boot_config="/boot/firmware/config.txt"
        elif [[ -f /boot/config.txt ]]; then
            boot_config="/boot/config.txt"
        fi

        if [[ -n "${boot_config}" ]]; then
            # Enable UART and PPS if not already set
            local needs_rewrite=0
            if ! grep -q "^enable_uart=1" "${boot_config}" 2>/dev/null; then
                echo "enable_uart=1" >> "${boot_config}"
                needs_rewrite=1
            fi
            if ! grep -q "^dtoverlay=pps-gpio" "${boot_config}" 2>/dev/null; then
                echo "dtoverlay=pps-gpio,gpiopin=18" >> "${boot_config}"
                needs_rewrite=1
            fi
            if [[ ${needs_rewrite} -eq 1 ]]; then
                log_warn "Modified ${boot_config} to enable UART and PPS."
                log_warn "A reboot is required for these changes to take effect."
            fi
        fi

        # Disable serial console on the UART
        if [[ -f /boot/firmware/cmdline.txt ]]; then
            run sed -i 's/ console=serial0,[0-9]\+//g; s/console=ttyAMA0,[0-9]\+ //g' \
                /boot/firmware/cmdline.txt
        elif [[ -f /boot/cmdline.txt ]]; then
            run sed -i 's/ console=serial0,[0-9]\+//g; s/console=ttyAMA0,[0-9]\+ //g' \
                /boot/cmdline.txt
        fi
    fi
}

# ---------------------------------------------------------------------------
# Step 7: Configure chrony
# ---------------------------------------------------------------------------
configure_chrony() {
    log_step "Configuring chrony for GPS discipline"

    # Determine chrony.conf location (Debian/Ubuntu use /etc/chrony/chrony.conf)
    local chrony_conf=""
    if [[ -d /etc/chrony ]]; then
        chrony_conf="/etc/chrony/chrony.conf"
    else
        chrony_conf="/etc/chrony.conf"
    fi

    cat > "${CONF_DIR}/chrony.conf" <<'CHRONY_EOF'
# chrony.conf -- GPS-disciplined NTP server
# Managed by ntpgps installer. Original backed up.

# GPS NMEA via shared-memory from gpsd (refclock SHM 0)
# offset: typical NMEA sentence delay (~0.2s, tune for your receiver)
# refid NMEA, stratum 1 when combined with PPS, noselect alone
refclock SHM 0  refid NMEA  offset 0.200  delay 0.2  noselect

# PPS via shared-memory from gpsd (refclock SHM 1)
# PPS is precise to microseconds; prefer it, lock to NMEA for time-of-day
refclock SHM 1  refid PPS  precision 1e-7  lock NMEA  poll 3  trust  prefer

# Fallback NTP pools (used when GPS is unavailable)
pool 0.pool.ntp.org iburst maxsources 2
pool 1.pool.ntp.org iburst maxsources 2

# Allow NTP clients on local network
allow 0.0.0.0/0
allow ::/0

# Serve time even when not synchronised (for isolated networks)
local stratum 10 orphan

# Record tracking, statistics
driftfile /var/lib/chrony/chrony.drift
logdir /var/log/chrony
log tracking measurements statistics refclocks

# Step the clock on startup if off by more than 1 second
makestep 1.0 3

# Enable kernel PPS discipline if available
lock_all

# Rate limiting for external clients
ratelimit interval 1 burst 16
CHRONY_EOF

    run cp "${CONF_DIR}/chrony.conf" "${chrony_conf}"
    log_info "chrony configured at ${chrony_conf}"
}

# ---------------------------------------------------------------------------
# Step 8: Install and configure systemd services
# ---------------------------------------------------------------------------
install_services() {
    log_step "Installing systemd service files"

    # Web backend service
    cat > "${SYSTEMD_DIR}/ntpgps-web.service" <<SERVICE_EOF
[Unit]
Description=NTP GPS Server - GPS-disciplined NTP with web interface
Documentation=https://github.com/tunlezah/ntpgps
After=network.target gpsd.service chrony.service
Wants=gpsd.service chrony.service

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_PREFIX}/app
Environment=PATH=${VENV_DIR}/bin:/usr/bin:/bin
Environment=PYTHONPATH=${INSTALL_PREFIX}/app
ExecStart=${VENV_DIR}/bin/python3 -m ntpgps.main -c ${CONF_DIR}/config.yaml
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ntpgps-web

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    run systemctl daemon-reload
    log_info "Systemd service files installed."
}

# ---------------------------------------------------------------------------
# Step 9: Enable and start services, verify
# ---------------------------------------------------------------------------
enable_and_verify() {
    log_step "Enabling and starting services"

    if [[ "${DRY_RUN}" -eq 1 ]]; then
        log_dry "systemctl enable/start gpsd chronyd ntpgps-web"
        return 0
    fi

    # gpsd
    systemctl enable gpsd.service
    systemctl restart gpsd.service
    log_info "gpsd enabled and started."

    # chrony
    systemctl enable chrony.service 2>/dev/null || systemctl enable chronyd.service 2>/dev/null || true
    systemctl restart chrony.service 2>/dev/null || systemctl restart chronyd.service 2>/dev/null || true
    log_info "chrony enabled and started."

    # ntpgps web (only start if the app entry point exists)
    if [[ -d "${INSTALL_PREFIX}/app/ntpgps" ]]; then
        systemctl enable ntpgps-web.service
        systemctl start ntpgps-web.service
        log_info "ntpgps-web enabled and started."
    else
        log_warn "Application not found at ${INSTALL_PREFIX}/app/ntpgps"
        log_warn "ntpgps-web service installed but not started."
        log_warn "Deploy your application and run: sudo systemctl start ntpgps-web"
    fi

    # Verification
    echo ""
    log_info "Post-install verification:"

    local all_ok=1

    if systemctl is-active --quiet gpsd.service; then
        log_info "  gpsd:    running"
    else
        log_warn "  gpsd:    NOT running (GPS device may not be connected)"
        all_ok=0
    fi

    if systemctl is-active --quiet chrony.service 2>/dev/null || \
       systemctl is-active --quiet chronyd.service 2>/dev/null; then
        log_info "  chrony:  running"
    else
        log_warn "  chrony:  NOT running"
        all_ok=0
    fi

    if systemctl is-active --quiet ntpgps-web.service 2>/dev/null; then
        log_info "  web UI:  running on port 8080"
    else
        log_warn "  web UI:  NOT running (see note above)"
    fi

    # Check chrony sources
    if command -v chronyc &>/dev/null; then
        echo ""
        log_info "Chrony sources:"
        chronyc sources 2>/dev/null | head -20 || true
    fi

    echo ""
    if [[ ${all_ok} -eq 1 ]]; then
        log_info "Installation completed successfully."
    else
        log_warn "Installation completed with warnings. Check messages above."
    fi

    if [[ "${DETECTED_PLATFORM}" == "rpi" ]]; then
        echo ""
        log_warn "IMPORTANT: If UART/PPS settings were modified in config.txt,"
        log_warn "a reboot is required: sudo reboot"
    fi

    echo ""
    log_info "Logs:          ${LOG_DIR}/"
    log_info "Configuration: ${CONF_DIR}/"
    log_info "Backups:       ${BACKUP_DIR}/"
    log_info "Install log:   ${LOGFILE}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"

    # Ensure log directory exists before we try to write to it
    mkdir -p "$(dirname "${LOGFILE}")" 2>/dev/null || true

    _log_raw "=== ntpgps installer v${SCRIPT_VERSION} started ==="
    _log_raw "Command: $0 $*"

    echo -e "${C_BOLD}ntpgps installer v${SCRIPT_VERSION}${C_RESET}"

    detect_os
    detect_arch
    detect_platform

    preflight_checks
    confirm_install

    backup_configs
    install_system_packages
    create_user_and_dirs
    setup_python_venv
    deploy_application
    configure_gpsd
    configure_chrony
    install_services
    enable_and_verify

    _log_raw "=== Installation finished successfully ==="
}

main "$@"
