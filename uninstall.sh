#!/usr/bin/env bash
#
# uninstall.sh - GPS-disciplined NTP server uninstaller
#
# Usage:
#   sudo ./uninstall.sh [OPTIONS]
#
# Options:
#   --dry-run         Show what would be done without making changes
#   --verbose         Enable verbose output
#   --purge           Also remove installed system packages (chrony, gpsd, etc.)
#   --keep-data       Keep log and data files in /var/lib/ntpgps and /var/log/ntpgps
#   --force           Skip confirmation prompts
#   --help            Show this help message
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants (must match install.sh)
# ---------------------------------------------------------------------------
readonly SCRIPT_VERSION="1.1.0"
readonly INSTALL_PREFIX="/opt/ntpgps"
readonly CONF_DIR="/etc/ntpgps"
readonly BACKUP_DIR="/etc/ntpgps/backup"
readonly DATA_DIR="/var/lib/ntpgps"
readonly LOG_DIR="/var/log/ntpgps"
readonly VENV_DIR="${INSTALL_PREFIX}/venv"
readonly SERVICE_USER="ntpgps"
readonly SYSTEMD_DIR="/etc/systemd/system"
readonly LOGFILE="/var/log/ntpgps-uninstall.log"

readonly MANAGED_SERVICES=(
    ntpgps-web.service
)

readonly INSTALLED_SYSTEM_PACKAGES=(
    chrony
    gpsd
    gpsd-clients
    pps-tools
    setserial
)

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------
DRY_RUN=0
VERBOSE=0
PURGE=0
KEEP_DATA=0
FORCE=0

# ---------------------------------------------------------------------------
# Color helpers
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

log_info()  { echo -e "${C_GREEN}[INFO]${C_RESET} $*";    _log_raw "INFO  $*"; }
log_warn()  { echo -e "${C_YELLOW}[WARN]${C_RESET} $*" >&2; _log_raw "WARN  $*"; }
log_error() { echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2;   _log_raw "ERROR $*"; }
log_step()  { echo ""; echo -e "${C_BLUE}${C_BOLD}>>>${C_RESET} ${C_BOLD}$*${C_RESET}"; _log_raw "STEP  $*"; }
log_debug() { [[ "${VERBOSE}" -eq 1 ]] && echo -e "       $*"; _log_raw "DEBUG $*"; }
log_dry()   { echo -e "${C_YELLOW}[DRY-RUN]${C_RESET} $*"; _log_raw "DRY   $*"; }

# ---------------------------------------------------------------------------
# run()
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
            --dry-run)    DRY_RUN=1 ;;
            --verbose)    VERBOSE=1 ;;
            --purge)      PURGE=1 ;;
            --keep-data)  KEEP_DATA=1 ;;
            --force)      FORCE=1 ;;
            --help|-h)
                head -n 16 "${BASH_SOURCE[0]}" | tail -n +3 | sed 's/^# \?//'
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
# Pre-flight
# ---------------------------------------------------------------------------
preflight() {
    if [[ "$(id -u)" -ne 0 ]]; then
        log_error "This script must be run as root (sudo)."
        exit 1
    fi

    if [[ ! -d "${INSTALL_PREFIX}" && ! -f "${SYSTEMD_DIR}/ntpgps-web.service" ]]; then
        log_error "No ntpgps installation found at ${INSTALL_PREFIX}."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------
confirm_uninstall() {
    if [[ "${FORCE}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
        return 0
    fi

    echo ""
    echo -e "${C_BOLD}ntpgps Uninstaller v${SCRIPT_VERSION}${C_RESET}"
    echo ""
    echo "  This will remove:"
    echo "    - ntpgps application from ${INSTALL_PREFIX}"
    echo "    - Configuration files in ${CONF_DIR}"
    echo "    - Systemd service files"
    echo "    - Service user '${SERVICE_USER}'"
    if [[ "${KEEP_DATA}" -eq 0 ]]; then
        echo "    - Log files in ${LOG_DIR}"
        echo "    - Data files in ${DATA_DIR}"
    fi
    if [[ "${PURGE}" -eq 1 ]]; then
        echo "    - System packages: ${INSTALLED_SYSTEM_PACKAGES[*]}"
    fi
    echo ""
    echo "  Backed-up configurations will be restored if available."
    echo ""
    read -r -p "Proceed? [y/N] " answer
    if [[ "${answer}" != "y" && "${answer}" != "Y" ]]; then
        log_info "Uninstall cancelled."
        exit 0
    fi
}

# ---------------------------------------------------------------------------
# Step 1: Stop and disable services
# ---------------------------------------------------------------------------
stop_services() {
    log_step "Stopping and disabling services"

    for svc in "${MANAGED_SERVICES[@]}"; do
        if systemctl is-active --quiet "${svc}" 2>/dev/null; then
            run systemctl stop "${svc}"
            log_info "Stopped ${svc}"
        fi
        if systemctl is-enabled --quiet "${svc}" 2>/dev/null; then
            run systemctl disable "${svc}"
            log_info "Disabled ${svc}"
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 2: Remove systemd unit files
# ---------------------------------------------------------------------------
remove_service_files() {
    log_step "Removing systemd service files"

    for svc in "${MANAGED_SERVICES[@]}"; do
        local unit_file="${SYSTEMD_DIR}/${svc}"
        if [[ -f "${unit_file}" ]]; then
            run rm -f "${unit_file}"
            log_info "Removed ${unit_file}"
        fi
    done

    run systemctl daemon-reload
}

# ---------------------------------------------------------------------------
# Step 3: Restore original configurations
# ---------------------------------------------------------------------------
restore_configs() {
    log_step "Restoring original configurations"

    # Find the most recent backup directory
    if [[ ! -d "${BACKUP_DIR}" ]]; then
        log_warn "No backup directory found at ${BACKUP_DIR}. Skipping restore."
        return 0
    fi

    local latest_backup
    latest_backup="$(ls -1d "${BACKUP_DIR}"/*/ 2>/dev/null | sort | tail -1 || true)"

    if [[ -z "${latest_backup}" ]]; then
        log_warn "No backups found. Skipping restore."
        return 0
    fi

    log_info "Restoring from: ${latest_backup}"

    # Restore chrony.conf
    if [[ -f "${latest_backup}/chrony.conf.bak" ]]; then
        local target="/etc/chrony/chrony.conf"
        [[ -d /etc/chrony ]] || target="/etc/chrony.conf"
        run cp -a "${latest_backup}/chrony.conf.bak" "${target}"
        log_info "Restored ${target}"
    fi

    # Restore gpsd
    if [[ -f "${latest_backup}/gpsd.bak" ]]; then
        run cp -a "${latest_backup}/gpsd.bak" "/etc/default/gpsd"
        log_info "Restored /etc/default/gpsd"
    fi

    # Restart chrony and gpsd with restored configs
    if [[ "${DRY_RUN}" -eq 0 ]]; then
        systemctl restart chrony.service 2>/dev/null || \
            systemctl restart chronyd.service 2>/dev/null || true
        systemctl restart gpsd.service 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Step 4: Remove application files
# ---------------------------------------------------------------------------
remove_application() {
    log_step "Removing application files"

    if [[ -d "${INSTALL_PREFIX}" ]]; then
        run rm -rf "${INSTALL_PREFIX}"
        log_info "Removed ${INSTALL_PREFIX}"
    fi

    if [[ -d "${CONF_DIR}" ]]; then
        run rm -rf "${CONF_DIR}"
        log_info "Removed ${CONF_DIR}"
    fi
}

# ---------------------------------------------------------------------------
# Step 5: Remove data and log files
# ---------------------------------------------------------------------------
remove_data() {
    log_step "Removing data and log files"

    if [[ "${KEEP_DATA}" -eq 1 ]]; then
        log_info "Keeping data and logs as requested (--keep-data)."
        return 0
    fi

    if [[ -d "${DATA_DIR}" ]]; then
        run rm -rf "${DATA_DIR}"
        log_info "Removed ${DATA_DIR}"
    fi

    if [[ -d "${LOG_DIR}" ]]; then
        run rm -rf "${LOG_DIR}"
        log_info "Removed ${LOG_DIR}"
    fi
}

# ---------------------------------------------------------------------------
# Step 6: Remove service user
# ---------------------------------------------------------------------------
remove_user() {
    log_step "Removing service user"

    if id "${SERVICE_USER}" &>/dev/null; then
        run userdel "${SERVICE_USER}" 2>/dev/null || true
        log_info "Removed user ${SERVICE_USER}"
    else
        log_debug "User ${SERVICE_USER} does not exist."
    fi

    if getent group "${SERVICE_USER}" &>/dev/null; then
        run groupdel "${SERVICE_USER}" 2>/dev/null || true
        log_info "Removed group ${SERVICE_USER}"
    fi
}

# ---------------------------------------------------------------------------
# Step 7: Optionally purge system packages
# ---------------------------------------------------------------------------
purge_packages() {
    log_step "System packages"

    if [[ "${PURGE}" -eq 0 ]]; then
        log_info "Keeping system packages (chrony, gpsd, etc.)."
        log_info "To remove them, re-run with --purge."
        return 0
    fi

    log_warn "Removing system packages: ${INSTALLED_SYSTEM_PACKAGES[*]}"

    for pkg in "${INSTALLED_SYSTEM_PACKAGES[@]}"; do
        if dpkg -l "${pkg}" 2>/dev/null | grep -q '^ii'; then
            run apt-get purge -y -qq "${pkg}"
            log_info "Purged ${pkg}"
        fi
    done

    run apt-get autoremove -y -qq
    log_info "System packages removed."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"

    mkdir -p "$(dirname "${LOGFILE}")" 2>/dev/null || true
    _log_raw "=== ntpgps uninstaller v${SCRIPT_VERSION} started ==="

    echo -e "${C_BOLD}ntpgps uninstaller v${SCRIPT_VERSION}${C_RESET}"

    preflight
    confirm_uninstall

    stop_services
    remove_service_files
    restore_configs
    remove_application
    remove_data
    remove_user
    purge_packages

    echo ""
    log_info "Uninstallation complete."
    log_info "Log: ${LOGFILE}"

    if [[ "${PURGE}" -eq 0 ]]; then
        echo ""
        log_info "Note: chrony and gpsd were left installed."
        log_info "Run with --purge to also remove system packages."
    fi

    _log_raw "=== Uninstallation finished ==="
}

main "$@"
