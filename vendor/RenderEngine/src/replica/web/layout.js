const DEFAULT_FONT_STACK = "Georgia, serif";

export function resolveTypography(fontName = "") {
  const name = fontName.toLowerCase();
  let family = DEFAULT_FONT_STACK;
  if (name.includes("glyphlessfont")) family = "'Times New Roman', Times, serif";
  else if (name.includes("bookantiqua")) family = "'Book Antiqua', Palatino, 'Palatino Linotype', Georgia, serif";
  else if (name.includes("timesnewroman")) family = "'Times New Roman', Times, serif";
  else if (name.includes("arial")) family = "Arial, Helvetica, sans-serif";
  else if (name.includes("wingdings")) family = "Wingdings, Symbol, sans-serif";
  else if (name.includes("symbol")) family = "Symbol, serif";
  return {
    family,
    weight: name.includes("bold") ? "700" : "400",
    style: name.includes("italic") ? "italic" : "normal",
  };
}

export function normalizeWordText(word) {
  const font = (word.font || "").toLowerCase();
  if (font.includes("symbol")) return word.text.replaceAll("\uf0b7", "\u2022");
  if (font.includes("wingdings")) return word.text.replaceAll("\uf04a", "\u263a");
  return word.text;
}

function wordLines(words) {
  const sorted = [...words].sort((a, b) => a.baseline - b.baseline || a.x - b.x);
  const lines = [];
  for (const word of sorted) {
    const line = lines.at(-1);
    const tolerance = Math.max(1.25, word.size * 0.18);
    if (!line || Math.abs(line.baseline - word.baseline) > tolerance) {
      lines.push({ baseline: word.baseline, baselineTotal: word.baseline, words: [word] });
      continue;
    }
    line.words.push(word);
    line.baselineTotal += word.baseline;
    line.baseline = line.baselineTotal / line.words.length;
  }
  return lines.map(line => {
    const lineWords = line.words;
    return {
      words: lineWords,
      left: Math.min(...lineWords.map(word => Number(word.x))),
      right: Math.max(...lineWords.map(word => Number(word.x) + Number(word.width))),
      top: Math.min(...lineWords.map(word => Number(word.y))),
      bottom: Math.max(...lineWords.map(word => Number(word.y) + Number(word.height))),
    };
  });
}

function lineAssignments(lines) {
  const assignments = new Map();
  lines.forEach((line, index) => line.words.forEach(word => assignments.set(word.id, index)));
  return assignments;
}

export function groupWordsIntoLines(words) {
  return lineAssignments(wordLines(words));
}

function scanTypographyRegions(words, lines) {
  const sizes = words.map(word => Number(word.size) || 0).filter(Boolean).sort((a, b) => a - b);
  const medianSize = sizes[Math.floor(sizes.length / 2)] || 10;
  const regions = [];
  lines.forEach((line, index) => {
    const lineWords = line.words;
    const letters = lineWords.map(word => word.text).join("").replace(/[^A-Za-z]/g, "");
    const uppercaseRatio = letters.length
      ? [...letters].filter(letter => letter === letter.toUpperCase()).length / letters.length
      : 0;
    const allCapsDisplay = letters.length >= 4 && uppercaseRatio >= .85;
    const largestSize = Math.max(...lineWords.map(word => Number(word.size) || 0));
    const prominentDisplay = lineWords.length <= 14
      && largestSize >= medianSize * 1.2
      && lineWords.some(word => String(word.font).toLowerCase().includes("bold"));
    if (!allCapsDisplay && !prominentDisplay) return;

    const padding = 2.5;
    const left = Math.max(0, line.left - padding);
    const top = Math.max(0, line.top - padding);
    regions.push({
      line: index,
      wordIds: lineWords.map(word => word.id),
      x: left,
      y: top,
      width: line.right + padding - left,
      height: line.bottom + padding - top,
    });
  });
  return regions;
}

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * fraction))];
}

function scanArtworkRegions(words, page, lines) {
  if (!words.length) return [{ x: 0, y: 0, width: page.width, height: page.height, kind: "image-page" }];

  const sizes = words.map(word => Number(word.size) || 0).filter(Boolean);
  const medianSize = percentile(sizes, .5) || 10;
  const contentLeft = percentile(lines.map(line => line.left), .12);
  const contentRight = percentile(lines.map(line => line.right), .88);
  const horizontalThreshold = Math.max(38, medianSize * 3.5);
  const regions = [];

  const collectFloatRuns = (side) => {
    let run = [];
    const flush = () => {
      if (run.length < 2) { run = []; return; }
      const top = Math.max(0, run[0].top - 3);
      const bottom = Math.min(Number(page.height), run.at(-1).bottom + 3);
      const boundary = side === "left"
        ? Math.min(...run.map(line => line.left)) - 3
        : Math.max(...run.map(line => line.right)) + 3;
      const x = side === "left" ? Math.max(0, contentLeft - 3) : boundary;
      const right = side === "left" ? boundary : Math.min(Number(page.width), contentRight + 3);
      if (right - x >= horizontalThreshold && bottom - top >= medianSize * 2.5) {
        regions.push({ x, y: top, width: right - x, height: bottom - top, kind: `${side}-float` });
      }
      run = [];
    };
    for (const line of lines) {
      const hasGap = side === "left"
        ? line.left - contentLeft >= horizontalThreshold
        : contentRight - line.right >= horizontalThreshold;
      const followsRun = !run.length || line.top - run.at(-1).bottom <= medianSize * 1.25;
      if (hasGap && followsRun) run.push(line);
      else { flush(); if (hasGap) run.push(line); }
    }
    flush();
  };
  collectFloatRuns("left");
  collectFloatRuns("right");

  const verticalThreshold = Math.max(34, medianSize * 3.2);
  for (let index = 0; index < lines.length - 1; index += 1) {
    const top = lines[index].bottom + 3;
    const bottom = lines[index + 1].top - 3;
    if (bottom - top < verticalThreshold) continue;
    regions.push({
      x: Math.max(0, contentLeft - 3),
      y: top,
      width: Math.min(Number(page.width), contentRight + 3) - Math.max(0, contentLeft - 3),
      height: bottom - top,
      kind: "vertical-gap",
    });
  }
  return regions;
}

export function analyzeScanPage(words, page) {
  const lines = wordLines(words);
  const typographyRegions = scanTypographyRegions(words, lines);
  return {
    artworkRegions: scanArtworkRegions(words, page, lines),
    lineAssignments: lineAssignments(lines),
    typographyRegions,
    sourceWordIds: new Set(typographyRegions.flatMap(region => region.wordIds)),
  };
}

export function computeBalancedLine(items) {
  const sorted = [...items].sort((a, b) => a.x - b.x);
  if (!sorted.length) return [];
  if (sorted.length === 1) {
    const item = sorted[0];
    return [{ ...item, renderedX: item.x + (item.width - item.naturalWidth) / 2, renderedWidth: item.naturalWidth, scale: 1 }];
  }

  const first = sorted[0];
  const last = sorted.at(-1);
  const start = first.x;
  const available = Math.max(1, last.x + last.width - start);
  const sizes = sorted.map(item => item.size).sort((a, b) => a - b);
  const medianSize = sizes[Math.floor(sizes.length / 2)];
  const minimumGap = Math.max(1.5, medianSize * 0.24);
  const naturalTotal = sorted.reduce((sum, item) => sum + item.naturalWidth, 0);
  const required = naturalTotal + minimumGap * (sorted.length - 1);
  const scale = Math.min(1, available / required);
  const renderedWidths = sorted.map(item => item.naturalWidth * scale);
  const remaining = Math.max(0, available - renderedWidths.reduce((sum, width) => sum + width, 0));
  const originalGaps = sorted.slice(0, -1).map((item, index) =>
    Math.max(0.1, sorted[index + 1].x - item.x - item.width)
  );
  const gapWeight = originalGaps.reduce((sum, gap) => sum + gap, 0);
  const baseGap = Math.min(minimumGap * scale, remaining / (sorted.length - 1));
  const distributable = Math.max(0, remaining - baseGap * (sorted.length - 1));

  let cursor = start;
  return sorted.map((item, index) => {
    const result = { ...item, renderedX: cursor, renderedWidth: renderedWidths[index], scale };
    cursor += renderedWidths[index];
    if (index < originalGaps.length) cursor += baseGap + distributable * originalGaps[index] / gapWeight;
    return result;
  });
}

export class ReplicaTextLayout {
  constructor(root) {
    this.root = root;
    this.document = root.ownerDocument;
    this.context = this.document.createElement("canvas").getContext("2d");
  }

  measure(element) {
    const style = this.document.defaultView.getComputedStyle(element);
    this.context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    return this.context.measureText(element.textContent || " ").width;
  }

  boxes(line = null) {
    const selector = line == null ? ".replica-word-box" : `.replica-word-box[data-line="${line}"]`;
    return [...this.root.querySelectorAll(selector)];
  }

  fitExact(line = null) {
    for (const box of this.boxes(line)) {
      const text = box.querySelector(".replica-word-text");
      const naturalWidth = this.measure(text);
      box.style.left = `${box.dataset.x}px`;
      box.style.width = `${box.dataset.width}px`;
      text.style.transform = `scaleX(${naturalWidth > 0 ? Number(box.dataset.width) / naturalWidth : 1})`;
    }
  }

  balance(line = null) {
    const groups = new Map();
    for (const box of this.boxes(line)) {
      const key = box.dataset.line;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(box);
    }
    for (const boxes of groups.values()) {
      const results = computeBalancedLine(boxes.map(box => {
        const text = box.querySelector(".replica-word-text");
        text.style.transform = "none";
        return {
          box,
          text,
          x: Number(box.dataset.x),
          width: Number(box.dataset.width),
          size: Number(box.dataset.size),
          naturalWidth: this.measure(text),
        };
      }));
      for (const item of results) {
        item.box.style.left = `${item.renderedX}px`;
        item.box.style.width = `${item.renderedWidth}px`;
        item.text.style.transform = `scaleX(${item.scale})`;
      }
    }
  }

  apply({ mode, sourceVisible, line = null }) {
    if (mode === "scan" && !sourceVisible) this.balance(line);
    else this.fitExact(line);
  }
}
