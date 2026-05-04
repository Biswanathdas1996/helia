import type { Request, Response, NextFunction, RequestHandler } from "express";
import { getAuth, clerkClient } from "@clerk/express";

export type AuthedUser = {
  userId: string;
  email: string | null;
  firstName: string | null;
  lastName: string | null;
  imageUrl: string | null;
  role: "admin" | "user";
};

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      user?: AuthedUser;
    }
  }
}

function adminEmails(): string[] {
  return (process.env.ADMIN_EMAILS ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export const requireAuth: RequestHandler = async (
  req: Request,
  res: Response,
  next: NextFunction,
) => {
  try {
    const auth = getAuth(req);
    if (!auth.userId) {
      res.status(401).json({ error: "Unauthorized", status: 401 });
      return;
    }
    const user = await clerkClient.users.getUser(auth.userId);
    const email =
      user.primaryEmailAddress?.emailAddress ??
      user.emailAddresses[0]?.emailAddress ??
      null;
    const allowList = adminEmails();
    let role: "admin" | "user" = "user";
    if (email && allowList.includes(email.toLowerCase())) role = "admin";
    if (allowList.length === 0) {
      // Bootstrap: when no allow-list set, the first user becomes admin.
      const list = await clerkClient.users.getUserList({ limit: 1, orderBy: "+created_at" });
      if (list.data[0]?.id === auth.userId) role = "admin";
    }
    req.user = {
      userId: auth.userId,
      email,
      firstName: user.firstName ?? null,
      lastName: user.lastName ?? null,
      imageUrl: user.imageUrl ?? null,
      role,
    };
    next();
  } catch (err) {
    req.log?.error({ err }, "requireAuth failed");
    res.status(401).json({ error: "Unauthorized", status: 401 });
  }
};

export const requireAdmin: RequestHandler = (req, res, next) => {
  if (req.user?.role !== "admin") {
    res.status(403).json({ error: "Admin access required", status: 403 });
    return;
  }
  next();
};
