import { RangeSetBuilder } from "@codemirror/state";
import {
  Decoration,
  DecorationSet,
  EditorView,
  ViewPlugin,
  ViewUpdate,
} from "@codemirror/view";

const lineClass = (text: string): string | null => {
  const heading = /^(#{1,6})\s/.exec(text);
  if (heading) return `cm-nomad-heading cm-nomad-heading-${heading[1].length}`;
  if (/^\s*>\s?/.test(text)) return "cm-nomad-quote";
  if (/^\s*[-*+]\s+\[[ xX]\]\s/.test(text)) return "cm-nomad-task";
  if (/^\s*```/.test(text)) return "cm-nomad-fence";
  return null;
};

const assistedDecorations = (view: EditorView): DecorationSet => {
  const builder = new RangeSetBuilder<Decoration>();
  for (let number = 1; number <= view.state.doc.lines; number += 1) {
    const line = view.state.doc.line(number);
    const className = lineClass(line.text);
    if (className) builder.add(line.from, line.from, Decoration.line({ class: className }));
  }
  return builder.finish();
};

export const markdownAssistance = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = assistedDecorations(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged) {
        this.decorations = assistedDecorations(update.view);
      }
    }
  },
  { decorations: (value) => value.decorations },
);
