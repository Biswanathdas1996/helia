import { Router, type IRouter } from "express";
import healthRouter from "./health";
import meRouter from "./me";
import documentsRouter from "./documents";
import chatRouter from "./chat";
import messagesRouter from "./messages";
import ticketsRouter from "./tickets";
import adminRouter from "./admin";

const router: IRouter = Router();

router.use(healthRouter);
router.use(meRouter);
router.use(adminRouter);
router.use(documentsRouter);
router.use(chatRouter);
router.use(messagesRouter);
router.use(ticketsRouter);

export default router;
