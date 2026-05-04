import { Router, type IRouter } from "express";
import {
  getDb,
  type DocumentDoc,
  type ChunkDoc,
  type MessageDoc,
  type TicketDoc,
} from "@workspace/db";
import { requireAuth, requireAdmin } from "../middlewares/auth";

const router: IRouter = Router();

router.get("/admin/stats", requireAuth, requireAdmin, async (_req, res) => {
  const db = await getDb();
  const docs = db.collection<DocumentDoc>("documents");
  const chunks = db.collection<ChunkDoc>("chunks");
  const msgs = db.collection<MessageDoc>("messages");
  const tickets = db.collection<TicketDoc>("tickets");

  const [docAgg] = await docs
    .aggregate<{
      total: number;
      approved: number;
      pending: number;
      pii: number;
      dupes: number;
    }>([
      {
        $group: {
          _id: null,
          total: { $sum: 1 },
          approved: { $sum: { $cond: [{ $eq: ["$status", "approved"] }, 1, 0] } },
          pending: { $sum: { $cond: [{ $eq: ["$status", "pending"] }, 1, 0] } },
          pii: { $sum: "$piiCount" },
          dupes: { $sum: "$duplicateCount" },
        },
      },
    ])
    .toArray();

  const totalChunks = await chunks.countDocuments({});

  const [msgAgg] = await msgs
    .aggregate<{ total: number; avgLatency: number; up: number; rated: number }>([
      { $match: { role: "assistant" } },
      {
        $group: {
          _id: null,
          total: { $sum: 1 },
          avgLatency: { $avg: { $ifNull: ["$latencyMs", 0] } },
          up: { $sum: { $cond: [{ $eq: ["$rating", "up"] }, 1, 0] } },
          rated: { $sum: { $cond: [{ $ifNull: ["$rating", false] }, 1, 0] } },
        },
      },
    ])
    .toArray();

  const totalTickets = await tickets.countDocuments({});
  const openTickets = await tickets.countDocuments({ status: { $in: ["open", "in_progress"] } });

  res.json({
    totalDocuments: docAgg?.total ?? 0,
    approvedDocuments: docAgg?.approved ?? 0,
    pendingDocuments: docAgg?.pending ?? 0,
    totalChunks,
    totalQueries: msgAgg?.total ?? 0,
    totalTickets,
    openTickets,
    piiRemovedTotal: docAgg?.pii ?? 0,
    duplicateChunksRemoved: docAgg?.dupes ?? 0,
    avgLatencyMs: Math.round(msgAgg?.avgLatency ?? 0),
    helpfulRate: msgAgg && msgAgg.rated > 0 ? Number((msgAgg.up / msgAgg.rated).toFixed(2)) : 0,
  });
});

router.get("/admin/stats/trend", requireAuth, requireAdmin, async (_req, res) => {
  const db = await getDb();
  const since = new Date();
  since.setUTCDate(since.getUTCDate() - 13);
  since.setUTCHours(0, 0, 0, 0);

  const groupByDay = (collection: string, match: Record<string, unknown>) =>
    db
      .collection(collection)
      .aggregate<{ _id: string; n: number }>([
        { $match: { createdAt: { $gte: since }, ...match } },
        {
          $group: {
            _id: { $dateToString: { format: "%Y-%m-%d", date: "$createdAt", timezone: "UTC" } },
            n: { $sum: 1 },
          },
        },
      ])
      .toArray();

  const [queries, docs] = await Promise.all([
    groupByDay("messages", { role: "assistant" }),
    groupByDay("documents", {}),
  ]);

  const qMap = new Map(queries.map((q) => [q._id, q.n]));
  const dMap = new Map(docs.map((d) => [d._id, d.n]));

  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  const out: { date: string; queries: number; documents: number }[] = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    out.push({ date: key, queries: qMap.get(key) ?? 0, documents: dMap.get(key) ?? 0 });
  }
  res.json(out);
});

router.get("/admin/activity", requireAuth, requireAdmin, async (_req, res) => {
  const db = await getDb();
  const [recentDocs, recentMsgs, recentTickets] = await Promise.all([
    db.collection<DocumentDoc>("documents").find({}).sort({ updatedAt: -1 }).limit(10).toArray(),
    db.collection<MessageDoc>("messages").find({ role: "assistant" }).sort({ createdAt: -1 }).limit(10).toArray(),
    db.collection<TicketDoc>("tickets").find({}).sort({ createdAt: -1 }).limit(10).toArray(),
  ]);

  type Item = {
    id: string;
    kind: "document_uploaded" | "document_approved" | "query_answered" | "ticket_opened";
    title: string;
    subtitle: string | null;
    actor: string | null;
    createdAt: string;
  };
  const items: Item[] = [];
  for (const d of recentDocs) {
    items.push({
      id: `doc-${d._id}-create`,
      kind: "document_uploaded",
      title: `Uploaded "${d.name}"`,
      subtitle: `${d.chunkCount} chunks · ${d.piiCount} PII removed`,
      actor: d.createdBy,
      createdAt: d.createdAt.toISOString(),
    });
    if (d.status === "approved") {
      items.push({
        id: `doc-${d._id}-approve`,
        kind: "document_approved",
        title: `Approved "${d.name}"`,
        subtitle: null,
        actor: d.createdBy,
        createdAt: d.updatedAt.toISOString(),
      });
    }
  }
  for (const m of recentMsgs) {
    items.push({
      id: `msg-${m._id}`,
      kind: "query_answered",
      title: m.content.slice(0, 80),
      subtitle: `${m.citations.length} sources · ${m.latencyMs ?? 0}ms`,
      actor: null,
      createdAt: m.createdAt.toISOString(),
    });
  }
  for (const t of recentTickets) {
    items.push({
      id: `tkt-${t._id}`,
      kind: "ticket_opened",
      title: t.subject,
      subtitle: `${t.priority} priority · ${t.status}`,
      actor: t.userId,
      createdAt: t.createdAt.toISOString(),
    });
  }
  items.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  res.json(items.slice(0, 25));
});

export default router;
