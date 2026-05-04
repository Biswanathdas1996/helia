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

export type DocumentDoc = {
  _id: number;
  name: string;
  sourceType: string;
  status: "pending" | "approved" | "rejected";
  originalText: string;
  cleanedText: string;
  originalSize: number;
  finalSize: number;
  piiCount: number;
  duplicateCount: number;
  chunkCount: number;
  piiFindings: PiiFinding[];
  duplicateFindings: DuplicateFinding[];
  tags: string[];
  keywords: string[];
  createdBy: string | null;
  rejectionReason: string | null;
  createdAt: Date;
  updatedAt: Date;
};

export type ChunkDoc = {
  _id: number;
  documentId: number;
  position: number;
  content: string;
  tokenCount: number;
  createdAt: Date;
};

export type ConversationDoc = {
  _id: number;
  userId: string;
  title: string;
  createdAt: Date;
  updatedAt: Date;
};

export type Citation = {
  chunkId: number;
  documentId: number;
  documentName: string;
  snippet: string;
  score: number;
};

export type MessageDoc = {
  _id: number;
  conversationId: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  canAnswer: boolean | null;
  latencyMs: number | null;
  rating: "up" | "down" | null;
  feedbackComment: string | null;
  createdAt: Date;
};

export type TicketDoc = {
  _id: number;
  userId: string;
  subject: string;
  description: string;
  priority: "low" | "medium" | "high" | "urgent";
  status: "open" | "in_progress" | "resolved" | "closed";
  externalId: string | null;
  relatedMessageId: number | null;
  lastUpdate: string | null;
  createdAt: Date;
  updatedAt: Date;
};
