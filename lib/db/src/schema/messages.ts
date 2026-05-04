import { pgTable, serial, text, integer, timestamp, jsonb, boolean, index } from "drizzle-orm/pg-core";
import { conversationsTable } from "./conversations";

export type Citation = {
  chunkId: number;
  documentId: number;
  documentName: string;
  snippet: string;
  score: number;
};

export const messagesTable = pgTable(
  "messages",
  {
    id: serial("id").primaryKey(),
    conversationId: integer("conversation_id")
      .notNull()
      .references(() => conversationsTable.id, { onDelete: "cascade" }),
    role: text("role").notNull(),
    content: text("content").notNull(),
    citations: jsonb("citations").$type<Citation[]>().notNull().default([]),
    canAnswer: boolean("can_answer"),
    latencyMs: integer("latency_ms"),
    rating: text("rating"),
    feedbackComment: text("feedback_comment"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    convoIdx: index("messages_conversation_idx").on(table.conversationId),
  }),
);

export type Message = typeof messagesTable.$inferSelect;
export type InsertMessage = typeof messagesTable.$inferInsert;
