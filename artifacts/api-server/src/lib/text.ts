const STOP = new Set([
  "the","a","an","and","or","but","of","to","in","on","for","with","is","are",
  "was","were","be","been","being","have","has","had","do","does","did","this",
  "that","these","those","i","you","he","she","it","we","they","them","their",
  "what","which","who","whom","whose","when","where","why","how","as","at","by",
  "if","not","no","yes","so","than","then","there","here","into","from","about",
  "can","could","will","would","should","may","might","just","also","very",
]);

export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s']/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 1 && !STOP.has(t));
}

export function termFrequency(tokens: string[]): Record<string, number> {
  const tf: Record<string, number> = {};
  for (const t of tokens) tf[t] = (tf[t] ?? 0) + 1;
  return tf;
}

export function topKeywords(tf: Record<string, number>, k = 10): string[] {
  return Object.entries(tf)
    .sort((a, b) => b[1] - a[1])
    .slice(0, k)
    .map(([w]) => w);
}

export function chunkText(text: string, targetWords = 180, overlapWords = 30): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const chunks: string[] = [];
  let i = 0;
  while (i < words.length) {
    const end = Math.min(i + targetWords, words.length);
    chunks.push(words.slice(i, end).join(" "));
    if (end === words.length) break;
    i = end - overlapWords;
    if (i < 0) i = 0;
  }
  return chunks;
}

// Jaccard similarity over token sets — used for chunk-level dedup.
export function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 && b.size === 0) return 1;
  let inter = 0;
  for (const t of a) if (b.has(t)) inter++;
  const union = a.size + b.size - inter;
  return union === 0 ? 0 : inter / union;
}

// BM25 scoring across a corpus.
export type ScoredChunk = {
  chunkId: number;
  documentId: number;
  score: number;
};

export function bm25(
  query: string,
  corpus: { id: number; documentId: number; tf: Record<string, number>; len: number }[],
  k1 = 1.5,
  b = 0.75,
): ScoredChunk[] {
  if (corpus.length === 0) return [];
  const N = corpus.length;
  const avgdl = corpus.reduce((s, c) => s + c.len, 0) / N;
  const df: Record<string, number> = {};
  for (const c of corpus) for (const term of Object.keys(c.tf)) df[term] = (df[term] ?? 0) + 1;
  const qTerms = Array.from(new Set(tokenize(query)));
  const idf: Record<string, number> = {};
  for (const term of qTerms) {
    const n = df[term] ?? 0;
    idf[term] = Math.log(1 + (N - n + 0.5) / (n + 0.5));
  }
  return corpus
    .map((c) => {
      let score = 0;
      for (const term of qTerms) {
        const f = c.tf[term] ?? 0;
        if (f === 0) continue;
        const denom = f + k1 * (1 - b + (b * c.len) / (avgdl || 1));
        score += (idf[term] ?? 0) * ((f * (k1 + 1)) / denom);
      }
      return { chunkId: c.id, documentId: c.documentId, score };
    })
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score);
}
