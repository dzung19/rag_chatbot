/**
 * Clean LaTeX math arrows and ascii arrows into neat unicode arrows for clean Markdown rendering.
 */
export function cleanLatexArrows(text: string): string {
  if (!text) return "";
  return text
    .replace(/\$\\(?:right|left|up|down)arrow\$/gi, (m) => {
      const lower = m.toLowerCase();
      if (lower.includes("right")) return "→";
      if (lower.includes("left")) return "←";
      if (lower.includes("up")) return "↑";
      return "↓";
    })
    .replace(/\\(?:right|left|up|down)arrow/gi, (m) => {
      const lower = m.toLowerCase();
      if (lower.includes("right")) return "→";
      if (lower.includes("left")) return "←";
      if (lower.includes("up")) return "↑";
      return "↓";
    })
    .replace(/\$\\(?:Right|Left)arrow\$/g, (m) => (m.includes("Right") ? "⇒" : "⇐"))
    .replace(/\\(?:Right|Left)arrow/g, (m) => (m.includes("Right") ? "⇒" : "⇐"))
    .replace(/\$\\to\$/g, "→")
    .replace(/\\to\b/g, "→")
    .replace(/-->/g, "→")
    .replace(/->/g, "→")
    .replace(/==>/g, "⇒")
    .replace(/=>/g, "⇒");
}
