import { MongoClient, type Db } from "mongodb";

let mongoClient: MongoClient | null = null;

let dbInstance: Db | null = null;
let initPromise: Promise<Db> | null = null;

async function init(): Promise<Db> {
  const mongoUri = process.env.MONGODB_URI;
  if (!mongoUri) {
    throw new Error("MONGODB_URI must be set. Did you forget to provision MongoDB?");
  }

  if (!mongoClient) {
    mongoClient = new MongoClient(mongoUri);
  }

  await mongoClient.connect();
  const db = mongoClient.db();
  // Indexes (idempotent).
  await Promise.all([
    db.collection("chunks").createIndex({ content: "text" }),
    db.collection("chunks").createIndex({ documentId: 1 }),
    db.collection("documents").createIndex({ status: 1 }),
    db.collection("documents").createIndex({ createdAt: -1 }),
    db.collection("conversations").createIndex({ userId: 1, updatedAt: -1 }),
    db.collection("messages").createIndex({ conversationId: 1, createdAt: 1 }),
    db.collection("messages").createIndex({ createdAt: -1 }),
    db.collection("tickets").createIndex({ userId: 1, createdAt: -1 }),
    db.collection("tickets").createIndex({ createdAt: -1 }),
  ]);
  dbInstance = db;
  return db;
}

export async function getDb(): Promise<Db> {
  if (dbInstance) return dbInstance;
  if (!initPromise) initPromise = init();
  return initPromise;
}

/** Atomic auto-increment counter so each collection has stable numeric IDs. */
export async function nextId(name: string): Promise<number> {
  const db = await getDb();
  const r = await db
    .collection<{ _id: string; seq: number }>("counters")
    .findOneAndUpdate(
      { _id: name },
      { $inc: { seq: 1 } },
      { upsert: true, returnDocument: "after" },
    );
  return r!.seq;
}

export { mongoClient };
export * from "./schema";
