"""Main entry point for NTP GPS Server."""

import argparse
import logging
import signal
import sys

from ntpgps import __version__
from ntpgps.config.settings import Config
from ntpgps.server import NTPGPSServer
from ntpgps.web.app import create_app, set_server


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)
    # Reduce noise from libraries
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"NTP GPS Server v{__version__} - GPS-disciplined NTP server with web interface"
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to configuration file (default: /etc/ntpgps/config.yaml)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        help="Web server port (overrides config)",
    )
    parser.add_argument(
        "--host",
        help="Web server bind address (overrides config)",
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug mode",
    )
    parser.add_argument(
        "--generate-chrony-config",
        action="store_true",
        help="Generate chrony.conf and exit",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"NTP GPS Server v{__version__}",
    )

    args = parser.parse_args()
    setup_logging(args.debug)

    logger = logging.getLogger(__name__)
    logger.info("NTP GPS Server v%s starting", __version__)

    try:
        config = Config(args.config)
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    # Generate chrony config mode
    if args.generate_chrony_config:
        from ntpgps.ntp.chrony import ChronyManager
        mgr = ChronyManager()
        content = mgr.generate_config(
            gps_shm_unit=config.get("ntp.gps_refclock_shm_unit", 0),
            pps_shm_unit=config.get("ntp.pps_refclock_shm_unit", 1),
            gps_offset=config.get("ntp.gps_offset", 0.0),
            gps_delay=config.get("ntp.gps_delay", 0.2),
            gps_precision=config.get("ntp.gps_precision", 1e-1),
            pps_precision=config.get("ntp.pps_precision", 1e-7),
            network_servers=config.get("ntp.network_servers"),
            local_stratum=config.get("ntp.local_stratum", 10),
        )
        print(content)
        sys.exit(0)

    # Create server
    server = NTPGPSServer(config)
    set_server(server)

    # Handle shutdown
    def shutdown(signum, frame):
        logger.info("Received signal %s, shutting down", signum)
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start services
    server.start()

    # Start web server
    app = create_app()
    host = args.host or config.get("server.host", "0.0.0.0")
    port = args.port or config.get("server.port", 8800)
    debug = args.debug or config.get("server.debug", False)

    logger.info("Web interface: http://%s:%d", host, port)

    try:
        app.run(host=host, port=port, debug=debug, use_reloader=False)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
