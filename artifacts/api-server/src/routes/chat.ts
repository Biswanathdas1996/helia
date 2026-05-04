import { Router, type IRouter, type Request, type Response } from "express";
import { and, eq, sql, inArray } from "drizzle-orm";
import {
  CreateConversationBody,
  SendMessageBody,
} from "@workspace/api-zod";
import {
  db,
  conversationsTable,
  messagesTable,
  documentsTable,
  chunksTable,
  type Citation,
} from "@workspace/db";
import { requireAuth } from "../middlewares/auth";
import { bm25 } from "../lib/text";
import { chat, type ChatTurn } from "../lib/openai";

const router: IRouter = Router();

function serializeConversation(c: typeof conversationsTable.$inferSelect, opts?: { lastPreview?: string | null; messageCount?: number }) {
  return {
    id: c.id,
    title: c.title,
    lastMessagePreview: opts?.lastPreview ?? null,
    messageCount: opts?.messageCount ?? 0,
    createdAt: c.createdAt.toISOString(),
    updatedAt: c.updatedAt.toISOString(),
  };
}

function serializeMessage(m: typeof messagesTable.$inferSelect) {
  return {
    id: m.id,
    conversationId: m.conversationId,
    role: m.role,
    content: m.content,
    citations: m.citations,
    canAnswer: m.canAnswer,
    latencyMs: m.latencyMs,
    rating: m.rating,
    createdAt: m.createdAt.toISOString(),
  };
}

router.get("/chat/conversations", requireAuth, async (req, res) => {
  const userId = req.user!.userId;
  const convos = await db
    .select()
    .from(conversationsTable)
    .where(eq(conversationsTable.userId, userId))
    .orderBy(sql`${conversationsTable.updatedAt} DESC`);

  const countMap = new Map<number, number>();
  const previews = new Map<number, string>();
  if (convos.length > 0) {
    const ids = convos.map((c) => c.id);
    const counts = await db
      .select({
        conversationId: messagesTable.conversationId,
        count: sql<number>`count(*)::int`.as("count"),
      })
      .from(messagesTable)
      .where(inArray(messagesTable.conversationId, ids))
      .groupBy(messagesTable.conversationId);
    for (const c of counts) countMap.set(c.conversationId, c.count);

    const recent = await db
      .select({ conversationId: messagesTable.conversationId, content: messagesTable.content, createdAt: messagesTable.createdAt })
      .from(messagesTable)
      .where(inArray(messagesTable.conversationId, ids))
      .orderBy(sql`${messagesTable.createdAt} DESC`);
    for (const r of recent) {
      if (!previews.has(r.conversationId)) previews.set(r.conversationId, r.content.slice(0, 120));
    }
  }

  res.json(
    convos.map((c) =>
      serializeConversation(c, {
        messageCount: countMap.get(c.id) ?? 0,
        lastPreview: previews.get(c.id) ?? null,
      }),
    ),
  );
});

router.post("/chat/conversations", requireAuth, async (req, res) => {
  const body = CreateConversationBody.parse(req.body ?? {});
  const [c] = await db
    .insert(conversationsTable)
    .values({
      userId: req.user!.userId,
      title: body.title ?? "New conversation",
    })
    .returning();
  res.status(201).json(serializeConversation(c, { messageCount: 0 }));
});

router.get("/chat/conversations/:id", requireAuth, async (req, res) => {
  const id = Number(req.params.id);
  const [c] = await db
    .select()
    .from(conversationsTable)
    .where(and(eq(conversationsTable.id, id), eq(conversationsTable.userId, req.user!.userId)));
  if (!c) {
    res.status(404).json({ error: "Conversation not found", status: 404 });
    return;
  }
  const msgs = await db
    .select()
    .from(messagesTable)
    .where(eq(messagesTable.conversationId, id))
    .orderBy(messagesTable.createdAt);
  res.json({
    conversation: serializeConversation(c, {
      messageCount: msgs.length,
      lastPreview: msgs[msgs.length - 1]?.content.slice(0, 120) ?? null,
    }),
    messages: msgs.map(serializeMessage),
  });
});

router.delete("/chat/conversations/:id", requireAuth, async (req, res) => {
  const id = Number(req.params.id);
  await db
    .delete(conversationsTable)
    .where(and(eq(conversationsTable.id, id), eq(conversationsTable.userId, req.user!.userId)));
  res.status(204).send();
});

router.post("/chat/conversations/:id/messages", requireAuth, async (req: Request, res: Response) => {
  const id = Number(req.params.id);
  const body = SendMessageBody.parse(req.body);

  const [c] = await db
    .select()
    .from(conversationsTable)
    .where(and(eq(conversationsTable.id, id), eq(conversationsTable.userId, req.user!.userId)));
  if (!c) {
    res.status(404).json({ error: "Conversation not found", status: 404 });
    return;
  }

  const started = Date.now();
  const [userMsg] = await db
    .insert(messagesTable)
    .values({ conversationId: id, role: "user", content: body.content })
    .returning();

  // Auto-title on first user message.
  if (c.title === "New conversation") {
    const title = body.content.slice(0, 60).replace(/\s+/g, " ").trim();
    await db.update(conversationsTable).set({ title }).where(eq(conversationsTable.id, id));
  } else {
    await db.update(conversationsTable).set({ updatedAt: new Date() }).where(eq(conversationsTable.id, id));
  }

  // Retrieve top chunks via BM25 across approved documents only.
  const approvedDocs = await db
    .select({ id: documentsTable.id, name: documentsTable.name })
    .from(documentsTable)
    .where(eq(documentsTable.status, "approved"));
  const docNameById = new Map(approvedDocs.map((d) => [d.id, d.name]));
  const approvedIds = approvedDocs.map((d) => d.id);

  let citations: Citation[] = [];
  let context = "";
  if (approvedIds.length > 0) {
    const chunks = await db
      .select()
      .from(chunksTable)
      .where(inArray(chunksTable.documentId, approvedIds));
    const corpus = chunks.map((ch) => ({
      id: ch.id,
      documentId: ch.documentId,
      tf: ch.termFreq,
      len: ch.tokenCount,
    }));
    const scored = bm25(body.content, corpus).slice(0, 5);
    const chunkById = new Map(chunks.map((ch) => [ch.id, ch]));
    citations = scored.map((s) => {
      const ch = chunkById.get(s.chunkId)!;
      return {
        chunkId: ch.id,
        documentId: ch.documentId,
        documentName: docNameById.get(ch.documentId) ?? "Untitled",
        snippet: ch.content.slice(0, 280),
        score: Number(s.score.toFixed(3)),
      };
    });
    context = scored
      .map((s, i) => {
        const ch = chunkById.get(s.chunkId)!;
        return `[${i + 1}] (${docNameById.get(ch.documentId)}) ${ch.content}`;
      })
      .join("\n\n");
  }

  // Pull short conversation history for context.
  const history = await db
    .select()
    .from(messagesTable)
    .where(eq(messagesTable.conversationId, id))
    .orderBy(messagesTable.createdAt);
  const recent = history.slice(-6);

  const sys: ChatTurn = {
    role: "system",
    content: `You are Helia, an AI customer support assistant. Answer the user's question using ONLY the numbered context snippets below.
- Cite sources inline using [n] notation matching the snippets you used.
- If the answer cannot be found in the context, set canAnswer to false and explain that the support team can be contacted via a ticket.
- Keep answers concise, friendly, and accurate.

Respond as JSON with this exact shape:
{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }

Context:
${context || "(no documents indexed yet)"}`,
  };

  const turns: ChatTurn[] = [
    sys,
    ...recent.slice(0, -1).map<ChatTurn>((m) => ({
      role: m.role === "assistant" ? "assistant" : "user",
      content: m.content,
    })),
    { role: "user", content: body.content },
  ];

  let answer = "";
  let canAnswer: boolean | null = null;
  let usedIdx: number[] = [];
  try {
    const raw = await chat(turns, { json: true });
    const parsed = JSON.parse(raw) as { answer?: string; canAnswer?: boolean; usedCitations?: number[] };
    answer = parsed.answer ?? "";
    canAnswer = typeof parsed.canAnswer === "boolean" ? parsed.canAnswer : null;
    usedIdx = Array.isArray(parsed.usedCitations) ? parsed.usedCitations : [];
  } catch (err) {
    req.log?.error({ err }, "LLM call failed");
    answer = "I'm having trouble reaching the model right now. Please try again in a moment, or open a support ticket.";
    canAnswer = false;
  }

  const filteredCitations = usedIdx.length > 0
    ? usedIdx.map((n) => citations[n - 1]).filter((x): x is Citation => Boolean(x))
    : citations;

  const [assistantMsg] = await db
    .insert(messagesTable)
    .values({
      conversationId: id,
      role: "assistant",
      content: answer,
      citations: filteredCitations,
      canAnswer,
      latencyMs: Date.now() - started,
    })
    .returning();

  res.json({
    userMessage: serializeMessage(userMsg),
    assistantMessage: serializeMessage(assistantMsg),
  });
});

export default router;
