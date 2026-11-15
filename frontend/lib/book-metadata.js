const YEAR = /\b(18\d{2}|19\d{2}|20\d{2})\b/;
const NOISE = /\b(?:anna'?s\s+archive|internet\s+archive|archive\s+org|z\s+library|libgen|pdf|ebook|text|scan)\b/gi;
const HASH = /\b[0-9a-f]{16,}\b/gi;

export function cleanBookText(value = "") {
  return value
    .normalize("NFKC")
    .replace(/[’‘‛]/g, "'")
    .replace(/\.(?:pdf|epub)$/i, "")
    .replaceAll("@", " at ")
    .replaceAll("_", " ")
    .replace(HASH, " ")
    .replace(NOISE, " ")
    .replace(/[^\p{L}\p{N}\s']/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function metadataFromFilename(fileName) {
  const stem = fileName.replace(/\.(?:pdf|epub)$/i, "");
  let titleSource = stem;
  let author = null;
  for (const match of stem.matchAll(/\(([^()]*)\)/g)) {
    const names = match[1].split(/\s*(?:,|&|\band\b)\s*/i).map(cleanBookText).filter(Boolean);
    if (names.length >= 2 && names.every(name => name.split(/\s+/).length <= 6 && !/\d/.test(name))) {
      author = names.join(" and ");
      titleSource = stem.slice(0, match.index);
      break;
    }
  }
  const parts = titleSource.split(/\s*(?:--+|—+|–+)\s*/).map(cleanBookText).filter(Boolean);
  const year = stem.match(YEAR)?.[1] || null;
  author ||= parts.slice(1).find(part => !YEAR.test(part) && !/\d/.test(part) && part.split(/\s+/).length <= 7) || null;
  return { title: parts[0] || cleanBookText(stem) || "Untitled document", author, year };
}

export function metadataForRecord(documentRecord) {
  const inferred = metadataFromFilename(documentRecord.file_name);
  return {
    title: documentRecord.display_title || inferred.title,
    author: documentRecord.author || inferred.author,
    year: documentRecord.publication_year || inferred.year,
  };
}

export function bookByline({ author, publication_year: publicationYear, year } = {}) {
  return [author ? `By ${author}` : null, publicationYear || year || null].filter(Boolean).join(" · ");
}
