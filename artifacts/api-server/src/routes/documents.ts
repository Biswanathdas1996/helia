import { Router, type IRouter, type Request, type Response } from "express";
import { CreateDocumentBody, RejectDocumentBody } from "@workspace/api-zod";
import {
  getDb,
  nextId,
  type DocumentDoc,
  type ChunkDoc,
  type DuplicateFinding,
} from "@workspace/db";
import { requireAuth, requireAdmin } from "../middlewares/auth";
import { detectAndMaskPii } from "../lib/pii";
import { tokenize, termFrequency, topKeywords, chunkText, jaccard } from "../lib/text";

const router: IRouter = Router();

function serializeDoc(doc: DocumentDoc) {
  return {
    id: doc._id,
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

function parseId(raw: unknown): number | null {
  const n = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

router.get("/documents", requireAuth, requireAdmin, async (_req, res) => {
  const db = await getDb();
  const rows = await db
    .collection<DocumentDoc>("documents")
    .find({})
    .sort({ createdAt: -1 })
    .toArray();
  res.json(rows.map(serializeDoc));
});

router.post("/documents", requireAuth, requireAdmin, async (req: Request, res: Response) => {
  const body = CreateDocumentBody.parse(req.body);
  const db = await getDb();
  const { cleaned, findings } = detectAndMaskPii(body.content);
  const originalSize = body.content.length;
  const finalSize = cleaned.length;

  const rawChunks = chunkText(cleaned);
  const newSets = rawChunks.map((c) => new Set(tokenize(c)));

  // Cross-document dedup against approved documents only.
  const approvedDocs = await db
    .collection<DocumentDoc>("documents")
    .find({ status: "approved" }, { projection: { _id: 1, name: 1 } })
    .toArray();
  const docNameById = new Map(approvedDocs.map((d) => [d._id, d.name]));
  const approvedIds = approvedDocs.map((d) => d._id);
  const existing = approvedIds.length === 0
    ? []
    : await db
        .collection<ChunkDoc>("chunks")
        .find({ documentId: { $in: approvedIds } }, { projection: { _id: 1, content: 1, documentId: 1 } })
        .toArray();
  const existingPrepared = existing.map((c) => ({
    id: c._id,
    documentId: c.documentId,
    set: new Set(tokenize(c.content)),
  }));

  const duplicateFindings: DuplicateFinding[] = [];
  const keepIdx: number[] = [];
  for (let i = 0; i < rawChunks.length; i++) {
    let isDup = false;
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
  const overallTf = termFrequency(tokenize(cleaned));
  const keywords = topKeywords(overallTf, 12);
  const now = new Date();

  const docId = await nextId("documents");
  const doc: DocumentDoc = {
    _id: docId,
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
    rejectionReason: null,
    createdAt: now,
    updatedAt: now,
  };
  await db.collection<DocumentDoc>("documents").insertOne(doc);

  if (keptChunks.length > 0) {
    const chunkDocs: ChunkDoc[] = [];
    for (let idx = 0; idx < keptChunks.length; idx++) {
      const content = keptChunks[idx];
      const tokens = tokenize(content);
      chunkDocs.push({
        _id: await nextId("chunks"),
        documentId: docId,
        position: idx,
        content,
        tokenCount: tokens.length,
        createdAt: now,
      });
    }
    await db.collection<ChunkDoc>("chunks").insertMany(chunkDocs);
  }

  res.status(201).json(serializeDoc(doc));
});

router.get("/documents/:id", requireAuth, requireAdmin, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const db = await getDb();
  const doc = await db.collection<DocumentDoc>("documents").findOne({ _id: id });
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
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const db = await getDb();
  await db.collection<ChunkDoc>("chunks").deleteMany({ documentId: id });
  await db.collection<DocumentDoc>("documents").deleteOne({ _id: id });
  res.status(204).send();
});

router.post("/documents/:id/approve", requireAuth, requireAdmin, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const db = await getDb();
  const r = await db.collection<DocumentDoc>("documents").findOneAndUpdate(
    { _id: id },
    { $set: { status: "approved", rejectionReason: null, updatedAt: new Date() } },
    { returnDocument: "after" },
  );
  if (!r) {
    res.status(404).json({ error: "Document not found", status: 404 });
    return;
  }
  res.json(serializeDoc(r));
});

router.post("/documents/:id/reject", requireAuth, requireAdmin, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const body = RejectDocumentBody.parse(req.body ?? {});
  const db = await getDb();
  const r = await db.collection<DocumentDoc>("documents").findOneAndUpdate(
    { _id: id },
    { $set: { status: "rejected", rejectionReason: body.reason ?? null, updatedAt: new Date() } },
    { returnDocument: "after" },
  );
  if (!r) {
    res.status(404).json({ error: "Document not found", status: 404 });
    return;
  }
  res.json(serializeDoc(r));
});

export default router;
