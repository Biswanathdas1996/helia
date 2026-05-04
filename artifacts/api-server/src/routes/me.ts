import { Router, type IRouter } from "express";
import { GetMeResponse } from "@workspace/api-zod";
import { requireAuth } from "../middlewares/auth";

const router: IRouter = Router();

router.get("/me", requireAuth, (req, res) => {
  const u = req.user!;
  res.json(
    GetMeResponse.parse({
      userId: u.userId,
      email: u.email,
      firstName: u.firstName,
      lastName: u.lastName,
      imageUrl: u.imageUrl,
      role: u.role,
    }),
  );
});

export default router;
