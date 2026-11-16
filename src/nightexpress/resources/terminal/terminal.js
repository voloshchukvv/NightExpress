// Bridge between xterm.js and the Python TerminalView widget.
// Python -> JS: window.stmr.<method>(...) via runJavaScript.
// JS -> Python: slots of the `bridge` object exposed through QWebChannel.
(function () {
  'use strict';

  const isMac = /Mac/i.test(navigator.platform);
  const container = document.getElementById('container');
  let bridge = null;
  let term = null;
  let fit = null;
  let search = null;
  let searchColors = null; // set from the theme by Python before any search
  let lastSize = '';

  // App commands that must keep working while the terminal has focus. On macOS they use Cmd,
  // which shells never need; elsewhere Ctrl+letter belongs to the shell (Ctrl+B, Ctrl+W, ...),
  // so only Ctrl+Tab is taken.
  const MAC_COMMANDS = {
    's': 'save', 'S': 'save_all', 'b': 'verify', 'u': 'upload', 'M': 'serial_monitor',
    'p': 'quick_open', 'w': 'close_tab', 'j': 'toggle_bottom', '1': 'focus_tree',
    '=': 'zoom_in', '+': 'zoom_in', '-': 'zoom_out', '0': 'zoom_reset', 'f': 'find_in_panel',
  };

  function bytesFromBase64(text) {
    const binary = atob(text);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function fitToContainer() {
    if (!fit || container.clientWidth < 20 || container.clientHeight < 20) return; // hidden tab
    fit.fit();
    const size = term.cols + 'x' + term.rows;
    if (size !== lastSize) {
      lastSize = size;
      bridge.resized(term.cols, term.rows);
    }
  }

  function copySelection() {
    if (term.hasSelection()) bridge.copyText(term.getSelection());
  }

  function onKey(e) {
    if (e.type !== 'keydown') return true;
    const key = e.shiftKey && e.key.length === 1 ? e.key.toUpperCase() : e.key;
    if (e.ctrlKey && e.key === 'Tab') {
      bridge.commandRequested(e.shiftKey ? 'previous_tab' : 'next_tab');
      return false;
    }
    if (isMac && e.metaKey && !e.ctrlKey && !e.altKey) {
      const lower = key.toLowerCase();
      if (MAC_COMMANDS[key]) bridge.commandRequested(MAC_COMMANDS[key]);
      else if (lower === 'c') copySelection();
      else if (lower === 'v') bridge.pasteRequested();
      else if (lower === 'k') term.clear();
      else if (lower === 'a') term.selectAll();
      else return true;
      e.preventDefault();
      return false;
    }
    if (!isMac && e.ctrlKey && e.shiftKey && !e.altKey) {
      const lower = e.key.toLowerCase();
      if (lower === 'c') copySelection();
      else if (lower === 'v') bridge.pasteRequested();
      else if (lower === 'f') bridge.commandRequested('find_in_panel');
      else return true;
      e.preventDefault();
      return false;
    }
    return true;
  }

  function searchOptions(incremental) {
    return {
      incremental: incremental,
      decorations: {
        matchBackground: searchColors.match,
        matchOverviewRuler: searchColors.match,
        activeMatchBackground: searchColors.current,
        activeMatchColorOverviewRuler: searchColors.current,
      },
    };
  }

  window.stmr = {
    write(base64) { term.write(bytesFromBase64(base64)); },
    paste(text) { term.paste(text); },
    reset() { term.reset(); },
    focus() { term.focus(); },
    fit() { fitToContainer(); },
    setTheme(theme) { term.options.theme = theme; },
    setFontSize(size) { term.options.fontSize = size; fitToContainer(); },
    clear() { term.clear(); },
    setSearchColors(match, current) { searchColors = { match: match, current: current }; },
    // direction 0: the text changed, search from the current match; 1 / -1: next / previous.
    find(text, direction) {
      if (direction < 0) search.findPrevious(text, searchOptions(false));
      else search.findNext(text, searchOptions(direction === 0));
    },
    stopSearch() { search.clearDecorations(); term.clearSelection(); },
    text() { // the buffer as plain text (scrollback included), trailing blank lines cut
      const buffer = term.buffer.active;
      const lines = [];
      for (let i = 0; i < buffer.length; i++) {
        const line = buffer.getLine(i);
        const text = line.translateToString(true);
        // A soft-wrapped line continues the previous one: no newline between them.
        if (line.isWrapped && lines.length) lines[lines.length - 1] += text;
        else lines.push(text);
      }
      return lines.join('\n').replace(/\n+$/, '');
    },
  };

  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;
    term = new Terminal({
      cursorBlink: true,
      fontFamily: 'Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace',
      fontSize: 13,
      scrollback: 5000,
      macOptionIsMeta: true,
      allowProposedApi: true, // search highlights are decorations, still a "proposed" API
    });
    fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    search = new SearchAddon.SearchAddon({ highlightLimit: 2000 });
    term.loadAddon(search);
    search.onDidChangeResults((e) => bridge.searchResults(e.resultIndex, e.resultCount));
    term.attachCustomKeyEventHandler(onKey);
    term.open(container);
    term.onData((data) => bridge.input(data));
    term.onBinary((data) => bridge.inputBinary(data));
    new ResizeObserver(() => fitToContainer()).observe(container);
    fitToContainer();
    bridge.terminalReady(term.cols, term.rows);
  });
})();
