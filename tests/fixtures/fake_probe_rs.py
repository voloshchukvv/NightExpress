"""Stand-in for `probe-rs` in tests.

Usage: python fake_probe_rs.py <scenario> <probe-rs args...>
Scenarios: one, two, none, wrong_chip, verify_fail, busy, not_responding, slow
`attach` prints a few defmt lines and keeps running until it is stopped, as probe-rs does.
"""

import sys
import time

scenario, command, *args = sys.argv[1:]
sys.stderr.write(f"ARGS {command} {' '.join(args)}\n")
sys.stderr.flush()

STLINK = "[0]: STLink V2 -- 0483:3748:2C3B15157416303030303032 (ST-LINK)"
STLINK_2 = "[1]: STLink V2 -- 0483:3748:0000000000000000AAAAAA (ST-LINK)"


def fail(message: str) -> None:
    sys.stderr.write(f"Error: {message}\n")
    sys.exit(1)


if command == "list":
    if scenario == "none":
        print("No debug probes were found.")
    else:
        print("The following debug probes were found:")
        print(STLINK)
        if scenario == "two":
            print(STLINK_2)
    sys.exit(0)

if scenario == "none":
    fail("No connected probes were found.")
if scenario == "busy":
    fail("Failed to open the debug probe.\n\nCaused by:\n    The debug probe is already in use.")
if scenario == "not_responding":
    fail(
        "Connecting to the chip was unsuccessful.\n\n"
        "Caused by:\n    0: An ARM specific error occurred."
    )

if command == "read":
    print("e0042000: 20036410" if scenario == "wrong_chip" else "e0042000: 10006412")
    sys.exit(0)

if command == "download":
    if scenario == "slow":
        time.sleep(30)
    if scenario == "verify_fail":
        fail("Verification failed: the flash contents do not match.")
    sys.stderr.write("      Finished in 0.43s\n")
    sys.exit(0)

if command == "attach":
    for number in range(3):
        print(f"[INFO ] tick {number}", flush=True)
    while True:  # probe-rs reads RTT until it is stopped
        time.sleep(0.05)

fail(f"unexpected command {command}")
