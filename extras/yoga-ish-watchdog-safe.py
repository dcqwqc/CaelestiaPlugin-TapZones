#!/usr/bin/env python3
"""Safely recover iio-sensor-proxy after repeated accelerometer sysfs failures.

Safety contract:

* Identical accelerometer readings are normal for a stationary laptop. They are
  successful reads and never trigger an Intel ISH reset.
* An absent accelerometer is not a read failure and never triggers a PCI reset.
* The only recovery action is restarting ``iio-sensor-proxy.service``, and that
  happens only after three consecutive failures while reading an already found
  accelerometer's sysfs attributes.

This is deliberately a standalone, optional watchdog. It does not reset Intel
ISH, unbind drivers, or write to PCI configuration or reset interfaces.
"""

import argparse
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple


SYSFS_IIO_ROOT = Path("/sys/bus/iio/devices")
FAILURE_THRESHOLD = 3
IIO_SENSOR_PROXY_SERVICE = "iio-sensor-proxy.service"


def discover_accelerometer(root: Path = SYSFS_IIO_ROOT) -> Optional[Path]:
    """Return the current accel_3d device, or None when it is absent."""
    try:
        devices = sorted(root.glob("iio:device*"))
    except OSError:
        return None
    for device in devices:
        try:
            if (device / "name").read_text().strip() == "accel_3d":
                return device
        except OSError:
            continue
    return None


def read_accelerometer(device: Path) -> Tuple[float, float, float]:
    """Read one sample. Any sysfs I/O or malformed-value error is a failure."""
    scale = float((device / "in_accel_scale").read_text().strip())
    return tuple(
        float((device / f"in_accel_{axis}_raw").read_text().strip()) * scale
        for axis in "xyz"
    )


def restart_iio_sensor_proxy() -> None:
    """Perform the watchdog's sole recovery action."""
    subprocess.run(
        ["systemctl", "restart", IIO_SENSOR_PROXY_SERVICE],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass(frozen=True)
class PollResult:
    state: str
    consecutive_read_failures: int
    restarted: bool = False


class SafeIioWatchdog:
    """Count only consecutive sysfs read failures for a discovered device."""

    def __init__(
        self,
        discover: Callable[[], Optional[Path]] = discover_accelerometer,
        read: Callable[[Path], Tuple[float, float, float]] = read_accelerometer,
        restart: Callable[[], None] = restart_iio_sensor_proxy,
        failure_threshold: int = FAILURE_THRESHOLD,
    ) -> None:
        if failure_threshold < 2:
            raise ValueError("failure_threshold must require repeated failures")
        self._discover = discover
        self._read = read
        self._restart = restart
        self._failure_threshold = failure_threshold
        self._consecutive_read_failures = 0

    def poll(self) -> PollResult:
        device = self._discover()
        if device is None:
            # Discovery says no device is present. It is not evidence that a
            # discovered device's sysfs files failed to read.
            self._consecutive_read_failures = 0
            return PollResult("accelerometer-unavailable", 0)

        try:
            self._read(device)
        except (OSError, ValueError):
            self._consecutive_read_failures += 1
            if self._consecutive_read_failures < self._failure_threshold:
                return PollResult("sysfs-read-failed", self._consecutive_read_failures)
            self._restart()
            self._consecutive_read_failures = 0
            return PollResult("iio-sensor-proxy-restarted", 0, restarted=True)

        # Do not compare readings: repeated identical stationary samples are
        # valid and must never be treated as a hardware failure.
        self._consecutive_read_failures = 0
        return PollResult("ok", 0)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between polls (default: 5)")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    watchdog = SafeIioWatchdog()
    while True:
        result = watchdog.poll()
        if result.state != "ok":
            print(f"tapzones safe IIO watchdog: {result.state}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
