// Bridge between the Monaco editor and the Python MonacoEditor widget.
// Python -> JS: calls window.stmr.<method>(...) via runJavaScript.
// JS -> Python: calls slots of the `bridge` object exposed through QWebChannel.
(function () {
  'use strict';

  const models = new Map(); // path -> monaco.editor.ITextModel
  const needsTop = new Set(); // documents opened while the editor had no size yet
  let bridge = null;
  let editor = null;
  let activePath = null;
  let minimapWanted = true; // the user's setting; a narrow editor hides the minimap anyway

  const MINIMAP_MIN_WIDTH = 640; // px of editor width below which the minimap hides
  const SEVERITY = { error: 8, warning: 4, note: 2, help: 1 }; // monaco.MarkerSeverity

  function defineToml(monaco) {
    monaco.languages.register({ id: 'toml', extensions: ['.toml'], aliases: ['TOML'] });
    monaco.languages.setLanguageConfiguration('toml', {
      comments: { lineComment: '#' },
      brackets: [['[', ']'], ['{', '}']],
      autoClosingPairs: [
        { open: '[', close: ']' }, { open: '{', close: '}' },
        { open: '"', close: '"' }, { open: "'", close: "'" },
      ],
    });
    monaco.languages.setMonarchTokensProvider('toml', {
      tokenizer: {
        root: [
          [/^\s*\[\[?[^\]]*\]\]?/, 'type'],
          [/#.*$/, 'comment'],
          [/[A-Za-z0-9_.-]+(?=\s*=)/, 'key'],
          [/"""/, 'string', '@mlstring'],
          [/"([^"\\]|\\.)*"/, 'string'],
          [/'[^']*'/, 'string'],
          [/\b(true|false)\b/, 'keyword'],
          [/[+-]?(0x[0-9A-Fa-f_]+|\d[\d_]*(\.\d+)?([eE][+-]?\d+)?)/, 'number'],
        ],
        mlstring: [[/"""/, 'string', '@pop'], [/./, 'string']],
      },
    });
  }

  // The colours come from the application (resources/themes/*.toml), never from this file:
  // `theme` is { base, inherit, rules, colors } as monaco.editor.defineTheme takes it.
  const THEME = 'stmr';
  function applyTheme(theme) {
    monaco.editor.defineTheme(THEME, theme);
    monaco.editor.setTheme(THEME);
  }

  function addShortcuts(monaco) {
    const K = monaco.KeyMod, C = monaco.KeyCode;
    const isMac = /Mac/i.test(navigator.platform);
    const commands = [
      [K.CtrlCmd | C.KeyS, 'save'],
      [K.Shift | K.Alt | C.KeyF, 'format'],
      [K.CtrlCmd | K.Shift | C.KeyS, 'save_all'],
      [K.CtrlCmd | C.KeyB, 'verify'],
      [K.CtrlCmd | C.KeyU, 'upload'],
      [K.CtrlCmd | K.Shift | C.KeyM, 'serial_monitor'],
      [K.CtrlCmd | C.KeyP, 'quick_open'],
      [K.CtrlCmd | C.KeyW, 'close_tab'],
      [K.CtrlCmd | C.KeyJ, 'toggle_bottom'],
      [K.CtrlCmd | C.Digit1, 'focus_tree'],
      [K.CtrlCmd | C.Equal, 'zoom_in'],
      [K.CtrlCmd | K.Shift | C.Equal, 'zoom_in'],
      [K.CtrlCmd | C.Minus, 'zoom_out'],
      [K.CtrlCmd | C.Digit0, 'zoom_reset'],
      // F8 jumps through cargo's problems in every file, not only Monaco's markers in this one.
      [C.F8, 'next_problem'],
      [K.Shift | C.F8, 'previous_problem'],
      // Ctrl+Tab on every platform (WinCtrl is the Control key on macOS).
      [(isMac ? K.WinCtrl : K.CtrlCmd) | C.Tab, 'next_tab'],
      [(isMac ? K.WinCtrl : K.CtrlCmd) | K.Shift | C.Tab, 'previous_tab'],
    ];
    for (const [keys, name] of commands) {
      editor.addCommand(keys, () => bridge.commandRequested(name));
    }
  }

  function scrollNewDocumentToTop() {
    // A model shown while the widget was hidden (height 0) keeps a bogus scroll position.
    if (activePath && needsTop.has(activePath) && editor.getLayoutInfo().height > 0) {
      needsTop.delete(activePath);
      editor.setPosition({ lineNumber: 1, column: 1 });
      editor.setScrollPosition({ scrollTop: 0, scrollLeft: 0 });
    }
  }

  // A narrow editor needs its width for code more than for the minimap.
  function fitMinimap(width) {
    const enabled = minimapWanted && width >= MINIMAP_MIN_WIDTH;
    if (editor.getOption(monaco.editor.EditorOption.minimap).enabled !== enabled) {
      editor.updateOptions({ minimap: { enabled } });
    }
  }

  function setActive(path) {
    const model = models.get(path);
    if (!model || activePath === path) return;
    activePath = path;
    editor.setModel(model);
    scrollNewDocumentToTop();
  }

  window.stmr = {
    openDocument(path, text, language) {
      let model = models.get(path);
      if (!model) {
        model = monaco.editor.createModel(text, language, monaco.Uri.file(path));
        model.onDidChangeContent(() => {
          bridge.contentChanged(path, model.getValue(), model.getAlternativeVersionId());
        });
        models.set(path, model);
        needsTop.add(path);
      }
      setActive(path);
    },
    replaceText(path, text) {
      // Used after reloading a file from disk and after rustfmt; keeps undo history.
      const model = models.get(path);
      if (model && model.getValue() !== text) {
        model.pushEditOperations([], [{ range: model.getFullModelRange(), text }], () => null);
      }
    },
    showDocument(path) { setActive(path); },
    closeDocument(path) {
      const model = models.get(path);
      if (!model) return;
      models.delete(path);
      if (activePath === path) { activePath = null; editor.setModel(null); }
      model.dispose();
    },
    setMarkers(path, markers) {
      const model = models.get(path);
      if (!model) return;
      monaco.editor.setModelMarkers(model, 'cargo', markers.map((m) => ({
        severity: SEVERITY[m.level] || 2,
        message: m.message,
        code: m.code || undefined,
        startLineNumber: m.line,
        startColumn: m.column,
        endLineNumber: m.line,
        endColumn: model.getLineMaxColumn(Math.min(m.line, model.getLineCount())),
      })));
    },
    reveal(path, line, column) {
      setActive(path);
      needsTop.delete(path);
      const position = { lineNumber: line, column: column };
      editor.setPosition(position);
      editor.revealPositionInCenter(position);
      editor.focus();
    },
    setTheme(theme) { applyTheme(theme); },
    setFontSize(size) { editor.updateOptions({ fontSize: size }); },
    setBracketPairGuides(enabled) { editor.updateOptions({ guides: { bracketPairs: enabled } }); },
    setMinimap(enabled) {
      minimapWanted = enabled;
      fitMinimap(editor.getLayoutInfo().width);
    },
    focus() { editor.focus(); },
    trigger(actionId) {
      // Edit-menu actions (undo, find, ...) run inside Monaco.
      editor.focus();
      if (actionId === 'undo' || actionId === 'redo') {
        editor.trigger('menu', actionId, null);
        return;
      }
      const action = editor.getAction(actionId);
      if (action) action.run();
    },
  };

  // Workers cannot be started from file:// pages; Monaco then runs the editor worker
  // in the main thread, which is fine for firmware-sized files.
  self.MonacoEnvironment = { getWorker: () => { throw new Error('workers disabled'); } };

  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    require.config({ paths: { vs: 'monaco/vs' } });
    // The theme is asked for before the editor exists, so it never shows in other colours.
    require(['vs/editor/editor.main'], () => bridge.initialTheme((theme) => {
      defineToml(monaco);
      applyTheme(JSON.parse(theme));
      editor = monaco.editor.create(document.getElementById('container'), {
        model: null,
        theme: THEME,
        automaticLayout: true,
        fontFamily: 'Menlo, "JetBrains Mono", Consolas, "DejaVu Sans Mono", monospace',
        fontSize: 13,
        minimap: { enabled: true },
        bracketPairColorization: { enabled: true },
        guides: { bracketPairs: false, indentation: true },
        renderWhitespace: 'selection',
        smoothScrolling: true,
        stickyScroll: { enabled: true },
        tabSize: 4,
        insertSpaces: true,
        fixedOverflowWidgets: true,
        // Context menus in a shadow root cannot see the standalone Monaco theme styles.
        useShadowDOM: false,
      });
      editor.onDidChangeCursorPosition((e) => {
        bridge.cursorChanged(e.position.lineNumber, e.position.column);
      });
      editor.onDidLayoutChange(scrollNewDocumentToTop);
      editor.onDidLayoutChange((e) => fitMinimap(e.width));
      fitMinimap(editor.getLayoutInfo().width);
      addShortcuts(monaco);
      bridge.editorReady();
    }));
  });
})();
