import { pgTable, serial, text, integer, timestamp, jsonb, index } from "drizzle-orm/pg-core";
import { documentsTable } from "./documents";

export const chunksTable = pgTable(
  "chunks",
  {
    id: serial("id").primaryKey(),
    documentId: integer("document_id")
      .notNull()
      .references(() => documentsTable.id, { onDelete: "cascade" }),
    position: integer("position").notNull().default(0),
    content: text("content").notNull(),
    tokenCount: integer("token_count").notNull().default(0),
    termFreq: jsonb("term_freq").$type<Record<string, number>>().notNull().default({}),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => ({
    documentIdx: index("chunks_document_idx").on(table.documentId),
  }),
);

export type Chunk = typeof chunksTable.$inferSelect;
export type InsertChunk = typeof chunksTable.$inferInsert;
