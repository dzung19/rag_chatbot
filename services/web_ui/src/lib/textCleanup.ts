/**
 * Automatically reconcile table summary counts for business-compare reports
 * to prevent LLM hallucinations on line counting.
 */
export function reconcileCompareSummaryCounts(text: string): string {
  if (!text) return "";

  // 1. Table 1 (TỔNG HỢP ĐIỂM ĐÚNG)
  const t1Match = /##\s*1\.\s*TỔNG\s*HỢP\s*ĐIỂM\s*ĐÚNG([\s\S]*?)(?=##\s*2|$)/i.exec(text);
  if (t1Match) {
    const t1Text = t1Match[1];
    const lines = t1Text.split("\n");
    const t1Rows = lines.filter((line) => {
      const trimmed = line.trim();
      return (
        trimmed.startsWith("|") &&
        !/^\|\s*[-:]+\s*\|/.test(trimmed) &&
        !/\bSTT\b/i.test(trimmed)
      );
    });
    const actualDungCount = t1Rows.length;
    if (actualDungCount > 0) {
      const updatedT1 = t1Text.replace(
        /(\*?\*?Tổng\s+số\s+điểm\s+đúng\s*:\s*\*?\*?\s*)\d+(\*?\*?)/i,
        `$1${actualDungCount}$2`
      );
      text =
        text.slice(0, t1Match.index + t1Match[0].length - t1Text.length) +
        updatedT1 +
        text.slice(t1Match.index + t1Match[0].length);
    }
  }

  // 2. Table 2 (ĐIỂM CẦN XỬ LÝ)
  const t2Match = /##\s*2\.\s*ĐIỂM\s*CẦN\s*XỬ\s*LÝ([\s\S]*)$/i.exec(text);
  if (t2Match) {
    const t2Text = t2Match[1];
    const lines = t2Text.split("\n");
    const t2Rows = lines.filter((line) => {
      const trimmed = line.trim();
      return (
        trimmed.startsWith("|") &&
        !/^\|\s*[-:]+\s*\|/.test(trimmed) &&
        !/\bSTT\b/i.test(trimmed)
      );
    });

    let saiCount = 0;
    let missingCount = 0;
    let unknownCount = 0;

    for (const r of t2Rows) {
      const parts = r.split("|").map((p) => p.trim());
      const statusCol = parts.length >= 6 ? parts[5].toUpperCase() : r.toUpperCase();
      if (statusCol.includes("KHÔNG TÌM THẤY")) {
        missingCount++;
      } else if (statusCol.includes("KHÔNG HIỂU")) {
        unknownCount++;
      } else if (statusCol.includes("SAI")) {
        saiCount++;
      }
    }

    const updatedT2 = t2Text.replace(
      /(\*?\*?Tổng\s+số\s+điểm\s+SAI\s*:\s*\*?\*?\s*)\d+.*?(KHÔNG\s+TÌM\s+THẤY\s*:\s*\*?\*?\s*)\d+.*?(KHÔNG\s+HIỂU\s*:\s*\*?\*?\s*)\d+(\*?\*?)/i,
      `**Tổng số điểm SAI: ${saiCount} | KHÔNG TÌM THẤY: ${missingCount} | KHÔNG HIỂU: ${unknownCount}**`
    );
    text =
      text.slice(0, t2Match.index + t2Match[0].length - t2Text.length) +
      updatedT2 +
      text.slice(t2Match.index + t2Match[0].length);
  }

  return text;
}

/**
 * Clean LaTeX math arrows and ascii arrows into neat unicode arrows for clean Markdown rendering.
 */
export function cleanLatexArrows(text: string): string {
  if (!text) return "";
  const reconciled = reconcileCompareSummaryCounts(text);
  return reconciled
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
