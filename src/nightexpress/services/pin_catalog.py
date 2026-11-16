"""Which pins of each catalog chip can do what (``resources/catalog/pins.json``).

The file is generated from embassy's own chip data by ``scripts/update_pin_catalog.py``, so a pin
offered for a module is one embassy-stm32 accepts for that role.
"""

from __future__ import annotations

import json
from importlib import resources
from importlib.resources.abc import Traversable

from nightexpress.models.entities import ChipPins, I2cBusPins

PINS_FILE = "pins.json"


def load_chip_pins(directory: Traversable | None = None) -> dict[str, ChipPins]:
    """Chip id -> its pins."""
    root = directory or resources.files("nightexpress") / "resources" / "catalog"
    data = json.loads((root / PINS_FILE).read_text(encoding="utf-8"))
    return {
        chip_id: ChipPins(
            gpio=tuple(chip["gpio"]),
            adc=dict(chip["adc"]),
            i2c=tuple(
                I2cBusPins(
                    bus["peripheral"],
                    tuple(bus["scl"]),
                    tuple(bus["sda"]),
                    tuple((scl, sda) for scl, sda in bus.get("pairs", ())),
                )
                for bus in chip["i2c"]
            ),
        )
        for chip_id, chip in data["chips"].items()
    }
