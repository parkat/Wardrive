#!/usr/bin/env python3
"""
HackRF One scanner stub.
Logs to hackrf_obs table once the hardware arrives and SoapySDR is configured.
TODO: replace the stub with a real FFT sweep + peak detection flowgraph.
"""

import argparse
import logging
import sys
import time

log = logging.getLogger("hackrf_scanner")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--center-mhz", type=float, default=433.0)
    parser.add_argument("--span-mhz", type=float, default=100.0)
    parser.add_argument("--sample-rate-mhz", type=float, default=20.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        import SoapySDR  # noqa: F401
    except ImportError:
        log.error("SoapySDR not installed. Install: sudo apt install python3-soapysdr gr-osmosdr")
        sys.exit(1)

    try:
        import SoapySDR
        devs = SoapySDR.Device.enumerate({"driver": "hackrf"})
        if not devs:
            log.error("No HackRF device found. Check USB connection.")
            sys.exit(1)
        log.info("HackRF found: %s", devs[0])
    except Exception as exc:
        log.error("HackRF init failed: %s", exc)
        sys.exit(1)

    log.warning("HackRF scanner is a stub — real implementation pending hardware delivery")
    log.info("Would sweep %.1f MHz ± %.1f MHz at %.1f Msps",
             args.center_mhz, args.span_mhz / 2, args.sample_rate_mhz)

    # Keep alive so the supervisor sees a running process
    try:
        while True:
            log.info("HackRF stub heartbeat (no real scanning)")
            time.sleep(60)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
