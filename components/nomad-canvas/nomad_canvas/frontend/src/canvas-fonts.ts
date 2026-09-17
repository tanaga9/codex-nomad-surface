import {
  DEFAULT_THEME,
  defaultAddFontsFromNode,
  type RichTextFontVisitor,
  type TLFontFace,
  type TLThemes,
} from "tldraw";
import yomogiUrl from "./fonts/Yomogi-Regular.woff2";

const japaneseDrawFace: TLFontFace = {
  family: "Nomad Yomogi",
  src: { url: yomogiUrl, format: "woff2" },
};

// Include shared Japanese punctuation and full-width / half-width forms.
const japaneseTextPattern =
  /[\p{Script_Extensions=Han}\p{Script_Extensions=Hiragana}\p{Script_Extensions=Katakana}\u3000-\u303f\uff00-\uffef]/u;

export const CANVAS_THEMES: Partial<TLThemes> = {
  default: {
    ...DEFAULT_THEME,
    fonts: {
      ...DEFAULT_THEME.fonts,
      draw: {
        ...DEFAULT_THEME.fonts.draw,
        fontFamily: "'tldraw_draw', 'Nomad Yomogi', sans-serif",
        faces: [...(DEFAULT_THEME.fonts.draw.faces ?? []), japaneseDrawFace],
      },
    },
  },
};

// Built-in styles scan rich text separately from theme faces. Register Yomogi
// here too so measurement, font loading, and SVG embedding use the same face.
export const addCanvasFontsFromNode: RichTextFontVisitor = (
  node,
  state,
  addFont,
) => {
  const next = defaultAddFontsFromNode(node, state, addFont);
  // Inspect text leaves, not parent textContent: code spans use another font.
  if (
    next.family === "tldraw_draw" &&
    node.isText &&
    japaneseTextPattern.test(node.text ?? "")
  ) {
    addFont(japaneseDrawFace);
  }
  return next;
};
