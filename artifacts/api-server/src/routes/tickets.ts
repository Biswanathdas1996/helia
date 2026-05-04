import { Router, type IRouter } from "express";
import { and, eq, sql } from "drizzle-orm";
import { CreateTicketBody, UpdateTicketBody } from "@workspace/api-zod";
import { db, ticketsTable } from "@workspace/db";
import { requireAuth } from "../middlewares/auth";

const router: IRouter = Router();

function serialize(t: typeof ticketsTable.$inferSelect) {
  return {
    id: t.id,
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
  const isAdmin = req.user!.role === "admin";
  const rows = isAdmin
    ? await db.select().from(ticketsTable).orderBy(sql`${ticketsTable.createdAt} DESC`)
    : await db
        .select()
        .from(ticketsTable)
        .where(eq(ticketsTable.userId, req.user!.userId))
        .orderBy(sql`${ticketsTable.createdAt} DESC`);
  res.json(rows.map(serialize));
});

router.post("/tickets", requireAuth, async (req, res) => {
  const body = CreateTicketBody.parse(req.body);
  const [t] = await db
    .insert(ticketsTable)
    .values({
      userId: req.user!.userId,
      subject: body.subject,
      description: body.description,
      priority: body.priority,
      relatedMessageId: body.relatedMessageId ?? null,
      externalId: `HEL-${Math.floor(Math.random() * 90000) + 10000}`,
      lastUpdate: "Ticket opened",
    })
    .returning();
  res.status(201).json(serialize(t));
});

router.get("/tickets/:id", requireAuth, async (req, res) => {
  const id = Number(req.params.id);
  const [t] = await db.select().from(ticketsTable).where(eq(ticketsTable.id, id));
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
  const id = Number(req.params.id);
  const body = UpdateTicketBody.parse(req.body);
  const [existing] = await db.select().from(ticketsTable).where(eq(ticketsTable.id, id));
  if (!existing) {
    res.status(404).json({ error: "Ticket not found", status: 404 });
    return;
  }
  // Admin can change status; user can only update their own with last-message text.
  const isAdmin = req.user!.role === "admin";
  if (!isAdmin && existing.userId !== req.user!.userId) {
    res.status(403).json({ error: "Forbidden", status: 403 });
    return;
  }
  const updates: Partial<typeof ticketsTable.$inferInsert> = {};
  if (body.status !== undefined && isAdmin) updates.status = body.status;
  if (body.lastUpdate !== undefined) updates.lastUpdate = body.lastUpdate;
  if (Object.keys(updates).length === 0) {
    res.json(serialize(existing));
    return;
  }
  const [t] = await db.update(ticketsTable).set(updates).where(eq(ticketsTable.id, id)).returning();
  res.json(serialize(t));
});

export default router;
