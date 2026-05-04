import { Router, type IRouter } from "express";
import { RateMessageBody } from "@workspace/api-zod";
import { getDb, type MessageDoc, type ConversationDoc } from "@workspace/db";
import { requireAuth } from "../middlewares/auth";

const router: IRouter = Router();

router.post("/messages/:id/feedback", requireAuth, async (req, res) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id) || id <= 0) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const body = RateMessageBody.parse(req.body);
  const db = await getDb();
  const msg = await db.collection<MessageDoc>("messages").findOne({ _id: id });
  if (!msg) {
    res.status(404).json({ error: "Message not found", status: 404 });
    return;
  }
  const convo = await db.collection<ConversationDoc>("conversations").findOne({ _id: msg.conversationId });
  if (!convo || convo.userId !== req.user!.userId) {
    res.status(403).json({ error: "Forbidden", status: 403 });
    return;
  }
  const r = await db.collection<MessageDoc>("messages").findOneAndUpdate(
    { _id: id },
    { $set: { rating: body.rating, feedbackComment: body.comment ?? null } },
    { returnDocument: "after" },
  );
  if (!r) {
    res.status(404).json({ error: "Message not found", status: 404 });
    return;
  }
  res.json({
    id: r._id,
    conversationId: r.conversationId,
    role: r.role,
    content: r.content,
    citations: r.citations,
    canAnswer: r.canAnswer,
    latencyMs: r.latencyMs,
    rating: r.rating,
    createdAt: r.createdAt.toISOString(),
  });
});

export default router;
