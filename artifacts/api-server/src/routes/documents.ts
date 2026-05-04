import { Router, type IRouter, type Request, type Response } from "express";
import { and, eq, inArray, sql } from "drizzle-orm";
import {
  CreateDocumentBody,
  RejectDocumentBody,
} from "@workspace/api-zod";
import {
  db,
  documentsTable,
  chunksTable,
  type DuplicateFinding,
} from "@workspace/db";
import { requireAuth, requireAdmin } from "../middlewares/auth";
import { detectAndMaskPii } from "../lib/pii";
import { tokenize, termFrequency, topKeywords, chunkText, jaccard } from "../lib/text";

const router: IRouter = Router();

function serializeDoc(doc: typeof documentsTable.$inferSelect) {
  return {
    id: doc.id,
    name: doc.name,
    sourceType: doc.sourceType,
    status: doc.status,
    originalSize: doc.originalSize,
    finalSize: doc.finalSize,
    piiCount: doc.piiCount,
    duplicateCount: doc.duplicateCount,
    chunkCount: doc.chunkCount,
    tags: doc.tags,
    keywords: doc.keywords,
    createdBy: doc.createdBy,
    rejectionReason: doc.rejectionReason,
    createdAt: doc.createdAt.toISOString(),
    updatedAt: doc.updatedAt.toISOString(),
  };
}

router.get("/documents", requireAuth, requireAdmin, async (_req, res) => {
  const rows = await db.select().from(documentsTable).orderBy(sql`${documentsTable.createdAt} DESC`);
  res.json(rows.map(serializeDoc));
});

router.post("/documents", requireAuth, requireAdmin, async (req: Request, res: Response) => {
  const body = CreateDocumentBody.parse(req.body);
  const { cleaned, findings } = detectAndMaskPii(body.content);
  const originalSize = body.content.length;
  const finalSize = cleaned.length;

  const rawChunks = chunkText(cleaned);
  const newSets = rawChunks.map((c) => new Set(tokenize(c)));

  // Cross-document dedup: only compare against approved documents' chunks.
  const approvedDocs = await db
    .select({ id: documentsTable.id, name: documentsTable.name })
    .from(documentsTable)
    .where(eq(documentsTable.status, "approved"));
  const docNameById = new Map(approvedDocs.map((d) => [d.id, d.name]));
  const existing = approvedDocs.length === 0
    ? []
    : await db
        .select({
          id: chunksTable.id,
          content: chunksTable.content,
          documentId: chunksTable.documentId,
        })
        .from(chunksTable)
        .where(inArray(chunksTable.documentId, approvedDocs.map((d) => d.id)));
  const existingPrepared = existing.map((c) => ({
    id: c.id,
    documentId: c.documentId,
    set: new Set(tokenize(c.content)),
    snippet: c.content.slice(0, 160),
  }));

  const duplicateFindings: DuplicateFinding[] = [];
  const keepIdx: number[] = [];
  for (let i = 0; i < rawChunks.length; i++) {
    let isDup = false;
    // Within-doc dedup
    for (const k of keepIdx) {
      const sim = jaccard(newSets[i], newSets[k]);
      if (sim >= 0.85) {
        isDup = true;
        duplicateFindings.push({
          snippet: rawChunks[i].slice(0, 160),
          similarity: Number(sim.toFixed(3)),
          matchedDocumentId: null,
          matchedDocumentName: "(within this document)",
        });
        break;
      }
    }
    if (!isDup) {
      // Cross-doc dedup
      for (const e of existingPrepared) {
        const sim = jaccard(newSets[i], e.set);
        if (sim >= 0.85) {
          isDup = true;
          duplicateFindings.push({
            snippet: rawChunks[i].slice(0, 160),
            similarity: Number(sim.toFixed(3)),
            matchedDocumentId: e.documentId,
            matchedDocumentName: docNameById.get(e.documentId) ?? null,
          });
          break;
        }
      }
    }
    if (!isDup) keepIdx.push(i);
  }

  const keptChunks = keepIdx.map((i) => rawChunks[i]);
  const overallTokens = tokenize(cleaned);
  const overallTf = termFrequency(overallTokens);
  const keywords = topKeywords(overallTf, 12);

  const [doc] = await db
    .insert(documentsTable)
    .values({
      name: body.name,
      sourceType: body.sourceType,
      status: "pending",
      originalText: body.content,
      cleanedText: cleaned,
      originalSize,
      finalSize,
      piiCount: findings.length,
      duplicateCount: duplicateFindings.length,
      chunkCount: keptChunks.length,
      piiFindings: findings,
      duplicateFindings,
      tags: body.tags ?? [],
      keywords,
      createdBy: req.user?.email ?? req.user?.userId ?? null,
    })
    .returning();

  if (keptChunks.length > 0) {
    await db.insert(chunksTable).values(
      keptChunks.map((content, idx) => {
        const tokens = tokenize(content);
        return {
          documentId: doc.id,
          position: idx,
          content,
          tokenCount: tokens.length,
          termFreq: termFrequency(tokens),
        };
      }),
    );
  }

  res.status(201).json(serializeDoc(doc));
});

router.get("/documents/:id", requireAuth, requireAdmin, async (req, res) => {
  const id = Number(req.params.id);
  const [doc] = await db.select().from(documentsTable).where(eq(documentsTable.id, id));
  if (!doc) {
    res.status(404).json({ error: "Document not found", status: 404 });
    return;
  }
  res.json({
    ...serializeDoc(doc),
    originalText: doc.originalText,
    cleanedText: doc.cleanedText,
    piiFindings: doc.piiFindings,
    duplicateFindings: doc.duplicateFindings,
  });
});

router.delete("/documents/:id", requireAuth, requireAdmin, async (req, res) => {
  const id = Number(req.params.id);
  await db.delete(documentsTable).where(eq(documentsTable.id, id));
  res.status(204).send();
});

router.post("/documents/:id/approve", requireAuth, requireAdmin, async (req, res) => {
  const id = Number(req.params.id);
  const [doc] = await db
    .update(documentsTable)
    .set({ status: "approved", rejectionReason: null })
    .where(eq(documentsTable.id, id))
    .returning();
  if (!doc) {
    res.status(404).json({ error: "Document not found", status: 404 });
    return;
  }
  res.json(serializeDoc(doc));
});

router.post("/documents/:id/reject", requireAuth, requireAdmin, async (req, res) => {
  const id = Number(req.params.id);
  const body = RejectDocumentBody.parse(req.body ?? {});
  const [doc] = await db
    .update(documentsTable)
    .set({ status: "rejected", rejectionReason: body.reason ?? null })
    .where(and(eq(documentsTable.id, id)))
    .returning();
  if (!doc) {
    res.status(404).json({ error: "Document not found", status: 404 });
    return;
  }
  res.json(serializeDoc(doc));
});

export default router;
