"""Stand-in for `dfu-util` in tests.

Usage: python fake_dfu_util.py <scenario> <dfu-util args...>

Scenarios: one, two (listing), none, access, bad_verify, leave_error, slow.
The "chip" is the folder of the image: -U reads back the firmware.bin that -D was given.
"""

import sys
import time
from pathlib import Path

scenario, *args = sys.argv[1:]
print("dfu-util 0.11")
print(f"ARGS {' '.join(args)}", file=sys.stderr)

FOUND = (
    'Found DFU: [0483:df11] ver=2200, devnum=5, cfg=1, intf=0, path="1-1", alt={alt}, '
    'name="{name}", serial="{serial}"'
)
FLASH = "@Internal Flash  /0x08000000/04*016Kg,01*064Kg,03*128Kg"


def found(serial: str, path: str = "1-1") -> None:
    for alt, name in ((0, FLASH), (1, "@Option Bytes  /0x1FFFC000/01*016 e")):
        print(FOUND.format(alt=alt, name=name, serial=serial).replace('"1-1"', f'"{path}"'))


def bar(what: str) -> None:
    for percent in (0, 50, 100):
        filled = "=" * (percent // 4)
        sys.stdout.write(f"{what}\t[{filled:<25}] {percent:3}%        {percent * 10} bytes\r")
        sys.stdout.flush()
    sys.stdout.write("\n")


if "-l" in args:
    if scenario in ("one", "bad_verify", "leave_error"):
        found("356D35693131")
    elif scenario == "two":
        found("356D35693131")
        found("AAAA00000001", "1-2")
    sys.exit(0)

if scenario in ("none",):
    print("dfu-util: No DFU capable USB device available", file=sys.stderr)
    sys.exit(74)
if scenario == "access":
    print(
        "dfu-util: Cannot open DFU device 0483:df11 found on devnum 5 (LIBUSB_ERROR_ACCESS)",
        file=sys.stderr,
    )
    sys.exit(74)

if "-D" in args:
    if scenario == "slow":
        time.sleep(30)
    bar("Erase   ")
    bar("Download")
    print("File downloaded successfully")
    sys.exit(0)

if "-U" in args:
    out = Path(args[args.index("-U") + 1])
    data = (out.parent / "firmware.bin").read_bytes()
    if scenario == "bad_verify":
        data = data[:10] + bytes([data[10] ^ 0xFF]) + data[11:]
    bar("Upload  ")
    out.write_bytes(data)
    if scenario == "leave_error":
        print("dfu-util: Error during download get_status", file=sys.stderr)
        sys.exit(74)
    print("Submitting leave request...")
    sys.exit(0)
sys.exit(2)
