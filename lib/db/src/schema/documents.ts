import { pgTable, serial, text, integer, timestamp, jsonb } from "drizzle-orm/pg-core";

export type PiiFinding = {
  type: string;
  value: string;
  replacement: string;
};

export type DuplicateFinding = {
  snippet: string;
  similarity: number;
  matchedDocumentId: number | null;
  matchedDocumentName: string | null;
};

export const documentsTable = pgTable("documents", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  sourceType: text("source_type").notNull().default("text"),
  status: text("status").notNull().default("pending"),
  originalText: text("original_text").notNull(),
  cleanedText: text("cleaned_text").notNull().default(""),
  originalSize: integer("original_size").notNull().default(0),
  finalSize: integer("final_size").notNull().default(0),
  piiCount: integer("pii_count").notNull().default(0),
  duplicateCount: integer("duplicate_count").notNull().default(0),
  chunkCount: integer("chunk_count").notNull().default(0),
  piiFindings: jsonb("pii_findings").$type<PiiFinding[]>().notNull().default([]),
  duplicateFindings: jsonb("duplicate_findings").$type<DuplicateFinding[]>().notNull().default([]),
  tags: text("tags").array().notNull().default([]),
  keywords: text("keywords").array().notNull().default([]),
  createdBy: text("created_by"),
  rejectionReason: text("rejection_reason"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow()
    .$onUpdate(() => new Date()),
});

export type DocumentRow = typeof documentsTable.$inferSelect;
export type InsertDocument = typeof documentsTable.$inferInsert;
