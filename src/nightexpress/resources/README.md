# resources

Data shipped with the application:

- `catalog/`: `chips.json`, `boards.json`, `pins.json`, `examples.json`;
- `templates/`: Jinja2 templates of the examples;
- `peripherals/`: sensor modules for "Add peripheral" (description and Rust driver);
- `themes/`: colour themes (TOML);
- `icons/`: interface icons;
- `locales/`: interface translations (gettext `.po` / `.mo`);
- `editor/`: the page of the code editor; Monaco itself is fetched into `editor/monaco/` by `scripts/fetch_monaco.py`;
- `terminal/`: the page of the terminal tab; xterm.js is fetched into `terminal/xterm/` by `scripts/fetch_xterm.py`.
