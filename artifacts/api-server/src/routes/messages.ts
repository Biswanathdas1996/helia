import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { RateMessageBody } from "@workspace/api-zod";
import { db, messagesTable, conversationsTable } from "@workspace/db";
import { requireAuth } from "../middlewares/auth";

const router: IRouter = Router();

router.post("/messages/:id/feedback", requireAuth, async (req, res) => {
  const id = Number(req.params.id);
  const body = RateMessageBody.parse(req.body);
  const [msg] = await db.select().from(messagesTable).where(eq(messagesTable.id, id));
  if (!msg) {
    res.status(404).json({ error: "Message not found", status: 404 });
    return;
  }
  const [convo] = await db
    .select()
    .from(conversationsTable)
    .where(eq(conversationsTable.id, msg.conversationId));
  if (!convo || convo.userId !== req.user!.userId) {
    res.status(403).json({ error: "Forbidden", status: 403 });
    return;
  }
  const [updated] = await db
    .update(messagesTable)
    .set({ rating: body.rating, feedbackComment: body.comment ?? null })
    .where(eq(messagesTable.id, id))
    .returning();
  res.json({
    id: updated.id,
    conversationId: updated.conversationId,
    role: updated.role,
    content: updated.content,
    citations: updated.citations,
    canAnswer: updated.canAnswer,
    latencyMs: updated.latencyMs,
    rating: updated.rating,
    createdAt: updated.createdAt.toISOString(),
  });
});

export default router;
