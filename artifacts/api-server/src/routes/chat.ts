import { Router, type IRouter, type Request, type Response } from "express";
import { CreateConversationBody, SendMessageBody } from "@workspace/api-zod";
import {
  getDb,
  nextId,
  type ConversationDoc,
  type MessageDoc,
  type DocumentDoc,
  type ChunkDoc,
  type Citation,
} from "@workspace/db";
import { requireAuth } from "../middlewares/auth";
import { chat, type ChatTurn } from "../lib/openai";

const router: IRouter = Router();

function parseId(raw: unknown): number | null {
  const n = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function serializeConversation(c: ConversationDoc, opts?: { lastPreview?: string | null; messageCount?: number }) {
  return {
    id: c._id,
    title: c.title,
    lastMessagePreview: opts?.lastPreview ?? null,
    messageCount: opts?.messageCount ?? 0,
    createdAt: c.createdAt.toISOString(),
    updatedAt: c.updatedAt.toISOString(),
  };
}

function serializeMessage(m: MessageDoc) {
  return {
    id: m._id,
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
  const db = await getDb();
  const userId = req.user!.userId;
  const convos = await db
    .collection<ConversationDoc>("conversations")
    .find({ userId })
    .sort({ updatedAt: -1 })
    .toArray();

  const countMap = new Map<number, number>();
  const previews = new Map<number, string>();
  if (convos.length > 0) {
    const ids = convos.map((c) => c._id);
    const counts = await db
      .collection<MessageDoc>("messages")
      .aggregate<{ _id: number; count: number }>([
        { $match: { conversationId: { $in: ids } } },
        { $group: { _id: "$conversationId", count: { $sum: 1 } } },
      ])
      .toArray();
    for (const c of counts) countMap.set(c._id, c.count);

    const recent = await db
      .collection<MessageDoc>("messages")
      .find({ conversationId: { $in: ids } }, { projection: { conversationId: 1, content: 1, createdAt: 1 } })
      .sort({ createdAt: -1 })
      .toArray();
    for (const r of recent) {
      if (!previews.has(r.conversationId)) previews.set(r.conversationId, r.content.slice(0, 120));
    }
  }

  res.json(
    convos.map((c) =>
      serializeConversation(c, {
        messageCount: countMap.get(c._id) ?? 0,
        lastPreview: previews.get(c._id) ?? null,
      }),
    ),
  );
});

router.post("/chat/conversations", requireAuth, async (req, res) => {
  const body = CreateConversationBody.parse(req.body ?? {});
  const db = await getDb();
  const now = new Date();
  const c: ConversationDoc = {
    _id: await nextId("conversations"),
    userId: req.user!.userId,
    title: body.title ?? "New conversation",
    createdAt: now,
    updatedAt: now,
  };
  await db.collection<ConversationDoc>("conversations").insertOne(c);
  res.status(201).json(serializeConversation(c, { messageCount: 0 }));
});

router.get("/chat/conversations/:id", requireAuth, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const db = await getDb();
  const c = await db
    .collection<ConversationDoc>("conversations")
    .findOne({ _id: id, userId: req.user!.userId });
  if (!c) {
    res.status(404).json({ error: "Conversation not found", status: 404 });
    return;
  }
  const msgs = await db
    .collection<MessageDoc>("messages")
    .find({ conversationId: id })
    .sort({ createdAt: 1 })
    .toArray();
  res.json({
    conversation: serializeConversation(c, {
      messageCount: msgs.length,
      lastPreview: msgs[msgs.length - 1]?.content.slice(0, 120) ?? null,
    }),
    messages: msgs.map(serializeMessage),
  });
});

router.delete("/chat/conversations/:id", requireAuth, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const db = await getDb();
  const r = await db
    .collection<ConversationDoc>("conversations")
    .deleteOne({ _id: id, userId: req.user!.userId });
  if (r.deletedCount === 0) {
    res.status(404).json({ error: "Conversation not found", status: 404 });
    return;
  }
  await db.collection<MessageDoc>("messages").deleteMany({ conversationId: id });
  res.status(204).send();
});

router.post("/chat/conversations/:id/messages", requireAuth, async (req: Request, res: Response) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const body = SendMessageBody.parse(req.body);
  const db = await getDb();

  const c = await db
    .collection<ConversationDoc>("conversations")
    .findOne({ _id: id, userId: req.user!.userId });
  if (!c) {
    res.status(404).json({ error: "Conversation not found", status: 404 });
    return;
  }

  const started = Date.now();
  const userMsg: MessageDoc = {
    _id: await nextId("messages"),
    conversationId: id,
    role: "user",
    content: body.content,
    citations: [],
    canAnswer: null,
    latencyMs: null,
    rating: null,
    feedbackComment: null,
    createdAt: new Date(),
  };
  await db.collection<MessageDoc>("messages").insertOne(userMsg);

  if (c.title === "New conversation") {
    const title = body.content.slice(0, 60).replace(/\s+/g, " ").trim();
    await db.collection<ConversationDoc>("conversations").updateOne(
      { _id: id },
      { $set: { title, updatedAt: new Date() } },
    );
  } else {
    await db.collection<ConversationDoc>("conversations").updateOne({ _id: id }, { $set: { updatedAt: new Date() } });
  }

  // Retrieve top chunks via Mongo $text search across approved docs only.
  const approvedDocs = await db
    .collection<DocumentDoc>("documents")
    .find({ status: "approved" }, { projection: { _id: 1, name: 1 } })
    .toArray();
  const docNameById = new Map(approvedDocs.map((d) => [d._id, d.name]));
  const approvedIds = approvedDocs.map((d) => d._id);

  let citations: Citation[] = [];
  let context = "";
  if (approvedIds.length > 0) {
    let scored: (ChunkDoc & { score: number })[] = [];
    try {
      scored = await db
        .collection<ChunkDoc>("chunks")
        .find(
          { documentId: { $in: approvedIds }, $text: { $search: body.content } },
          {
            projection: {
              _id: 1,
              documentId: 1,
              position: 1,
              content: 1,
              tokenCount: 1,
              createdAt: 1,
              score: { $meta: "textScore" },
            },
          },
        )
        .sort({ score: { $meta: "textScore" } })
        .limit(5)
        .toArray() as (ChunkDoc & { score: number })[];
    } catch (err) {
      req.log?.warn({ err }, "$text search failed; falling back to recent chunks");
    }
    if (scored.length === 0) {
      scored = (await db
        .collection<ChunkDoc>("chunks")
        .find({ documentId: { $in: approvedIds } })
        .sort({ _id: -1 })
        .limit(5)
        .toArray()).map((c) => ({ ...c, score: 0 }));
    }
    citations = scored.map((c) => ({
      chunkId: c._id,
      documentId: c.documentId,
      documentName: docNameById.get(c.documentId) ?? "Untitled",
      snippet: c.content.slice(0, 280),
      score: Number((c.score ?? 0).toFixed(3)),
    }));
    context = scored
      .map((c, i) => `[${i + 1}] (${docNameById.get(c.documentId)}) ${c.content}`)
      .join("\n\n");
  }

  const recent = await db
    .collection<MessageDoc>("messages")
    .find({ conversationId: id })
    .sort({ createdAt: 1 })
    .toArray();
  const recentSlice = recent.slice(-6);

  const sys: ChatTurn = {
    role: "system",
    content: `You are Helia, an AI customer support assistant. Answer the user's question using ONLY the numbered context snippets below.
- Cite sources inline using [n] notation matching the snippets you used.
- If the answer cannot be found in the context, set canAnswer to false and suggest opening a support ticket.
- Keep answers concise, friendly, and accurate.

Respond as JSON with this exact shape:
{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }

Context:
${context || "(no documents indexed yet)"}`,
  };

  const turns: ChatTurn[] = [
    sys,
    ...recentSlice.slice(0, -1).map<ChatTurn>((m) => ({
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

  const assistantMsg: MessageDoc = {
    _id: await nextId("messages"),
    conversationId: id,
    role: "assistant",
    content: answer,
    citations: filteredCitations,
    canAnswer,
    latencyMs: Date.now() - started,
    rating: null,
    feedbackComment: null,
    createdAt: new Date(),
  };
  await db.collection<MessageDoc>("messages").insertOne(assistantMsg);

  res.json({
    userMessage: serializeMessage(userMsg),
    assistantMessage: serializeMessage(assistantMsg),
  });
});

export default router;
