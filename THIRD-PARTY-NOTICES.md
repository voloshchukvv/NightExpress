# Third-party notices

NightExpress itself is released under the MIT License; its project templates,
peripheral drivers, the code generated from them and the reference firmware
are under 0BSD (see `LICENSE`).
It uses the third-party components listed below. Each of them stays under its
own license; nothing here changes those terms.

## 1. Files shipped inside this repository

| Component | Where | License | Copyright |
|---|---|---|---|
| [Lucide](https://lucide.dev) icons | `src/nightexpress/resources/icons/lucide/`; also `icons/check.svg`, `icons/chevron_up.svg`, `icons/chevron_down.svg` (Lucide "check", "chevron-up", "chevron-down" with another stroke width) | ISC; icons derived from Feather are MIT | Lucide Icons and Contributors; Cole Bemis (Feather) |
| Rust logo, SVG from [Simple Icons](https://simpleicons.org) | `src/nightexpress/resources/icons/brand/rust.svg` | Logo: [CC BY](https://rustfoundation.org/policy/rust-trademark-policy) (Rust Foundation); the Simple Icons SVG file: CC0 1.0 | Rust Foundation; see also "Trademarks" below |
| Pin data derived from [`stm32-metapac`](https://github.com/embassy-rs/stm32-data) 21.0.0 (`scripts/update_pin_catalog.py`) | `src/nightexpress/resources/catalog/pins.json` | MIT OR Apache-2.0 | The stm32-data / Embassy project contributors |

The full license texts are kept next to the files:
`src/nightexpress/resources/icons/lucide/LICENSE` and
`src/nightexpress/resources/icons/brand/LICENSE.md`. `stm32-metapac` is
licensed under the MIT license or the Apache License 2.0, at your option; see
<https://github.com/embassy-rs/stm32-data>.

## 2. Files fetched by scripts and shipped inside built packages

These are not stored in git. `scripts/fetch_monaco.py` and
`scripts/fetch_xterm.py` download them from the npm registry (checked by
sha512), and the wheel includes them together with their license files.

| Component | Version | Where | License | Copyright |
|---|---|---|---|---|
| [Monaco Editor](https://github.com/microsoft/monaco-editor) | 0.57.0 | `src/nightexpress/resources/editor/monaco/` | MIT | Microsoft Corporation |
| [xterm.js](https://github.com/xtermjs/xterm.js) (`@xterm/xterm`) | 6.0.0 | `src/nightexpress/resources/terminal/xterm/` | MIT | The xterm.js authors; SourceLair Private Company; Christopher Jeffrey |
| xterm.js fit addon (`@xterm/addon-fit`) | 0.11.0 | `src/nightexpress/resources/terminal/xterm/` | MIT | The xterm.js authors |
| xterm.js search addon (`@xterm/addon-search`) | 0.16.0 | `src/nightexpress/resources/terminal/xterm/` | MIT | The xterm.js authors |

License texts: `monaco/LICENSE`, `monaco/ThirdPartyNotices.txt` (components
bundled inside Monaco), `xterm/LICENSE-xterm`, `xterm/LICENSE-addon-fit`,
`xterm/LICENSE-addon-search`.

## 3. Python dependencies (installed by pip, not bundled)

| Package | License |
|---|---|
| [PySide6](https://doc.qt.io/qtforpython-6/) / Shiboken6 (Qt for Python, including Qt WebEngine) | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; used here under LGPL-3.0 |
| [pyserial](https://github.com/pyserial/pyserial) | BSD-3-Clause |
| [pyelftools](https://github.com/eliben/pyelftools) | Public domain (Unlicense) |
| [tomlkit](https://github.com/python-poetry/tomlkit) | MIT |
| [Jinja2](https://github.com/pallets/jinja) and MarkupSafe | BSD-3-Clause |
| [Mistune](https://github.com/lepture/mistune) | BSD-3-Clause |
| [Pygments](https://pygments.org/) | BSD-2-Clause |
| [pywinpty](https://github.com/andfoy/pywinpty) (Windows only) | MIT |

### Note on Qt / PySide6 (LGPL-3.0)

NightExpress imports PySide6 as a separate, dynamically loaded library and does
not modify it. Qt is a product of The Qt Company Ltd. and other contributors.
Its source code is available at <https://code.qt.io> and
<https://download.qt.io>, and the LGPL-3.0 text at
<https://www.gnu.org/licenses/lgpl-3.0.html>.

Anyone who redistributes NightExpress in a form that *contains* Qt/PySide6
(for example a PyInstaller, Nuitka or `.app`/`.exe` bundle) must follow
LGPL-3.0 for those libraries: ship the LGPL-3.0 and GPL-3.0 texts, keep the Qt
libraries replaceable by the user, and pass on the third-party notices of Qt
and of Chromium (part of Qt WebEngine).

## 4. Tools and crates used at run time (not distributed with NightExpress)

NightExpress calls tools the user installs separately, and the projects it
generates download their dependencies with Cargo.

| Component | License |
|---|---|
| Rust toolchain: `rustup`, `cargo`, `rustc`, `rustfmt` | MIT OR Apache-2.0 |
| [probe-rs](https://probe.rs) | MIT OR Apache-2.0 |
| [dfu-util](https://dfu-util.sourceforge.net) (flashing over USB DFU) | GPL-2.0-or-later; run as a separate program, not linked |
| [embassy-stm32](https://embassy.dev) | MIT OR Apache-2.0 |
| `cortex-m`, `cortex-m-rt`, `embedded-hal-nb` | MIT OR Apache-2.0 |
| `embedded-hal`, `embedded-hal-bus` (added with sensor modules) | MIT OR Apache-2.0 |
| [`defmt`](https://defmt.ferrous-systems.com), `defmt-rtt`, `panic-probe` | MIT OR Apache-2.0 |
| `panic-halt` | MIT OR Apache-2.0 |

## Trademarks

NightExpress is an independent project. It is not affiliated with, sponsored
by, or endorsed by STMicroelectronics, the Rust Foundation, Microsoft or The Qt
Company.

- STM32 and ST-LINK are trademarks of STMicroelectronics.
- Rust, Cargo and the Rust logo are trademarks of the Rust Foundation. The
  logo is used only to indicate that the software works with the Rust
  language.
- Qt is a trademark of The Qt Company Ltd.

All other names belong to their respective owners.
