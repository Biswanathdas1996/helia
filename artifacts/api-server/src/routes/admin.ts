import { Router, type IRouter } from "express";
import { sql, eq, desc } from "drizzle-orm";
import {
  db,
  documentsTable,
  chunksTable,
  messagesTable,
  ticketsTable,
} from "@workspace/db";
import { requireAuth, requireAdmin } from "../middlewares/auth";

const router: IRouter = Router();

router.get("/admin/stats", requireAuth, requireAdmin, async (_req, res) => {
  const [docCounts] = await db
    .select({
      total: sql<number>`count(*)::int`,
      approved: sql<number>`sum(case when ${documentsTable.status} = 'approved' then 1 else 0 end)::int`,
      pending: sql<number>`sum(case when ${documentsTable.status} = 'pending' then 1 else 0 end)::int`,
      pii: sql<number>`coalesce(sum(${documentsTable.piiCount}), 0)::int`,
      dupes: sql<number>`coalesce(sum(${documentsTable.duplicateCount}), 0)::int`,
    })
    .from(documentsTable);
  const [chunkCount] = await db.select({ c: sql<number>`count(*)::int` }).from(chunksTable);
  const [queryStats] = await db
    .select({
      total: sql<number>`sum(case when ${messagesTable.role} = 'assistant' then 1 else 0 end)::int`,
      avgLatency: sql<number>`coalesce(avg(${messagesTable.latencyMs}), 0)::int`,
      up: sql<number>`sum(case when ${messagesTable.rating} = 'up' then 1 else 0 end)::int`,
      rated: sql<number>`sum(case when ${messagesTable.rating} is not null then 1 else 0 end)::int`,
    })
    .from(messagesTable);
  const [ticketStats] = await db
    .select({
      total: sql<number>`count(*)::int`,
      open: sql<number>`sum(case when ${ticketsTable.status} in ('open','in_progress') then 1 else 0 end)::int`,
    })
    .from(ticketsTable);

  res.json({
    totalDocuments: docCounts?.total ?? 0,
    approvedDocuments: docCounts?.approved ?? 0,
    pendingDocuments: docCounts?.pending ?? 0,
    totalChunks: chunkCount?.c ?? 0,
    totalQueries: queryStats?.total ?? 0,
    totalTickets: ticketStats?.total ?? 0,
    openTickets: ticketStats?.open ?? 0,
    piiRemovedTotal: docCounts?.pii ?? 0,
    duplicateChunksRemoved: docCounts?.dupes ?? 0,
    avgLatencyMs: queryStats?.avgLatency ?? 0,
    helpfulRate: queryStats && queryStats.rated > 0 ? Number((queryStats.up / queryStats.rated).toFixed(2)) : 0,
  });
});

router.get("/admin/stats/trend", requireAuth, requireAdmin, async (_req, res) => {
  const days = 14;
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  const queries = await db
    .select({
      date: sql<string>`to_char(date_trunc('day', ${messagesTable.createdAt}), 'YYYY-MM-DD')`.as("date"),
      n: sql<number>`count(*)::int`,
    })
    .from(messagesTable)
    .where(sql`${messagesTable.role} = 'assistant' AND ${messagesTable.createdAt} >= now() - interval '14 days'`)
    .groupBy(sql`date_trunc('day', ${messagesTable.createdAt})`);
  const docs = await db
    .select({
      date: sql<string>`to_char(date_trunc('day', ${documentsTable.createdAt}), 'YYYY-MM-DD')`.as("date"),
      n: sql<number>`count(*)::int`,
    })
    .from(documentsTable)
    .where(sql`${documentsTable.createdAt} >= now() - interval '14 days'`)
    .groupBy(sql`date_trunc('day', ${documentsTable.createdAt})`);

  const qMap = new Map(queries.map((q) => [q.date, q.n]));
  const dMap = new Map(docs.map((d) => [d.date, d.n]));

  const out: { date: string; queries: number; documents: number }[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    out.push({ date: key, queries: qMap.get(key) ?? 0, documents: dMap.get(key) ?? 0 });
  }
  res.json(out);
});

router.get("/admin/activity", requireAuth, requireAdmin, async (_req, res) => {
  const recentDocs = await db
    .select()
    .from(documentsTable)
    .orderBy(desc(documentsTable.updatedAt))
    .limit(10);
  const recentMsgs = await db
    .select()
    .from(messagesTable)
    .where(eq(messagesTable.role, "assistant"))
    .orderBy(desc(messagesTable.createdAt))
    .limit(10);
  const recentTickets = await db
    .select()
    .from(ticketsTable)
    .orderBy(desc(ticketsTable.createdAt))
    .limit(10);

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
      id: `doc-${d.id}-create`,
      kind: "document_uploaded",
      title: `Uploaded "${d.name}"`,
      subtitle: `${d.chunkCount} chunks · ${d.piiCount} PII removed`,
      actor: d.createdBy,
      createdAt: d.createdAt.toISOString(),
    });
    if (d.status === "approved") {
      items.push({
        id: `doc-${d.id}-approve`,
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
      id: `msg-${m.id}`,
      kind: "query_answered",
      title: m.content.slice(0, 80),
      subtitle: `${m.citations.length} sources · ${m.latencyMs ?? 0}ms`,
      actor: null,
      createdAt: m.createdAt.toISOString(),
    });
  }
  for (const t of recentTickets) {
    items.push({
      id: `tkt-${t.id}`,
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
