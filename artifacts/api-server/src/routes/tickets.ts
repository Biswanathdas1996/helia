import { Router, type IRouter } from "express";
import { CreateTicketBody, UpdateTicketBody } from "@workspace/api-zod";
import { getDb, nextId, type TicketDoc } from "@workspace/db";
import { requireAuth } from "../middlewares/auth";

const router: IRouter = Router();

function parseId(raw: unknown): number | null {
  const n = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function serialize(t: TicketDoc) {
  return {
    id: t._id,
    subject: t.subject,
    description: t.description,
    priority: t.priority,
    status: t.status,
    externalId: t.externalId,
    createdBy: t.userId,
    relatedMessageId: t.relatedMessageId,
    lastUpdate: t.lastUpdate,
    createdAt: t.createdAt.toISOString(),
    updatedAt: t.updatedAt.toISOString(),
  };
}

router.get("/tickets", requireAuth, async (req, res) => {
  const db = await getDb();
  const isAdmin = req.user!.role === "admin";
  const filter = isAdmin ? {} : { userId: req.user!.userId };
  const rows = await db
    .collection<TicketDoc>("tickets")
    .find(filter)
    .sort({ createdAt: -1 })
    .toArray();
  res.json(rows.map(serialize));
});

router.post("/tickets", requireAuth, async (req, res) => {
  const body = CreateTicketBody.parse(req.body);
  const db = await getDb();
  const now = new Date();
  const t: TicketDoc = {
    _id: await nextId("tickets"),
    userId: req.user!.userId,
    subject: body.subject,
    description: body.description,
    priority: body.priority,
    status: "open",
    externalId: `HEL-${Math.floor(Math.random() * 90000) + 10000}`,
    relatedMessageId: body.relatedMessageId ?? null,
    lastUpdate: "Ticket opened",
    createdAt: now,
    updatedAt: now,
  };
  await db.collection<TicketDoc>("tickets").insertOne(t);
  res.status(201).json(serialize(t));
});

router.get("/tickets/:id", requireAuth, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const db = await getDb();
  const t = await db.collection<TicketDoc>("tickets").findOne({ _id: id });
  if (!t) {
    res.status(404).json({ error: "Ticket not found", status: 404 });
    return;
  }
  if (req.user!.role !== "admin" && t.userId !== req.user!.userId) {
    res.status(403).json({ error: "Forbidden", status: 403 });
    return;
  }
  res.json(serialize(t));
});

router.patch("/tickets/:id", requireAuth, async (req, res) => {
  const id = parseId(req.params.id);
  if (id === null) {
    res.status(400).json({ error: "Invalid id", status: 400 });
    return;
  }
  const body = UpdateTicketBody.parse(req.body);
  const db = await getDb();
  const existing = await db.collection<TicketDoc>("tickets").findOne({ _id: id });
  if (!existing) {
    res.status(404).json({ error: "Ticket not found", status: 404 });
    return;
  }
  const isAdmin = req.user!.role === "admin";
  if (!isAdmin && existing.userId !== req.user!.userId) {
    res.status(403).json({ error: "Forbidden", status: 403 });
    return;
  }
  const updates: Partial<TicketDoc> = { updatedAt: new Date() };
  if (body.status !== undefined && isAdmin) updates.status = body.status;
  if (body.lastUpdate !== undefined) updates.lastUpdate = body.lastUpdate;
  const r = await db
    .collection<TicketDoc>("tickets")
    .findOneAndUpdate({ _id: id }, { $set: updates }, { returnDocument: "after" });
  res.json(serialize(r ?? existing));
});

export default router;
