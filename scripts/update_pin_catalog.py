#!/usr/bin/env python3
"""Write resources/catalog/pins.json: which pins of each catalog chip can do what.

The source is stm32-metapac, the chip data embassy-stm32 itself is built from, so a pin offered
in "Add peripheral" is one embassy accepts. Run it after adding a chip or updating embassy:

    .venv/bin/python scripts/update_pin_catalog.py [path/to/stm32-metapac-X.Y.Z]

Without a path the newest stm32-metapac in the Cargo registry (~/.cargo/registry) is used; it is
there after any reference project has been built.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "nightexpress" / "resources" / "catalog"

_PERIPHERAL_RE = re.compile(r'Peripheral \{\s*name: "(?P<name>\w+)",')
_PIN_RE = re.compile(r'PeripheralPin \{\s*pin: "(?P<pin>\w+)",\s*signal: "(?P<signal>\w+)",')
_AFIO_VALUE_RE = re.compile(r"PeripheralAfioValue \{\s*value: \d+,\s*pins: &\[(?P<pins>[^\]]*)\]")
_CHIP_PIN_RE = re.compile(r'Pin \{ name: "(?P<pin>\w+)" \}')


def find_metapac() -> Path:
    found = sorted(
        Path.home().glob(".cargo/registry/src/*/stm32-metapac-*"),
        key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.name.rsplit("-", 1)[-1])),
    )
    if not found:
        sys.exit("stm32-metapac not found: build a reference project first, or pass its path")
    return found[-1]


def peripherals(text: str) -> dict[str, str]:
    """Peripheral name -> its block of the metadata."""
    starts = [(m.start(), m["name"]) for m in _PERIPHERAL_RE.finditer(text)]
    blocks = {}
    for (start, name), (end, _next) in zip(starts, [*starts[1:], (len(text), "")], strict=True):
        blocks[name] = text[start:end]
    return blocks


def chip_pins(metapac: Path, feature: str) -> dict[str, object]:
    chips = metapac / "src" / "chips"
    head = (chips / feature / "metadata.rs").read_text(encoding="utf-8")
    included = re.search(r'include!\("\.\./(?P<file>[\w.]+)"\)', head)
    text = (chips / included["file"]).read_text(encoding="utf-8") if included else head
    gpio = [m["pin"] for m in _CHIP_PIN_RE.finditer(text[text.find("static PINS") :])]
    i2c, adc = [], {}
    for name, block in peripherals(text).items():
        pins = [(m["pin"], m["signal"]) for m in _PIN_RE.finditer(block)]
        if re.fullmatch(r"I2C\d", name):
            bus: dict[str, object] = {
                "peripheral": name,
                "scl": [p for p, s in pins if s == "SCL" and p in gpio],
                "sda": [p for p, s in pins if s == "SDA" and p in gpio],
            }
            # STM32F1: the pins move together (AFIO remap): only these SCL/SDA pairs work.
            groups = [re.findall(r'"(\w+)"', m["pins"]) for m in _AFIO_VALUE_RE.finditer(block)]
            pairs = [
                [scl, sda]
                for group in groups
                for scl in group
                for sda in group
                if scl in bus["scl"] and sda in bus["sda"]  # type: ignore[operator]
            ]
            if pairs:
                bus["pairs"] = pairs
            if bus["scl"] and bus["sda"]:
                i2c.append(bus)
        elif re.fullmatch(r"ADC\d", name):
            for pin, signal in pins:
                if pin in gpio and re.fullmatch(r"IN\d+", signal):
                    adc.setdefault(pin, f"{name}_{signal}")
    return {"gpio": gpio, "adc": adc, "i2c": i2c}


def main() -> None:
    metapac = Path(sys.argv[1]) if len(sys.argv) > 1 else find_metapac()
    chips = json.loads((CATALOG / "chips.json").read_text(encoding="utf-8"))["chips"]
    result = {
        "schema_version": 1,
        "source": metapac.name,
        "chips": {chip["id"]: chip_pins(metapac, chip["embassy_feature"]) for chip in chips},
    }
    out = CATALOG / "pins.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {len(result['chips'])} chips from {metapac.name}")


if __name__ == "__main__":
    main()
