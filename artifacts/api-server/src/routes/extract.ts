import { Router, type IRouter } from "express";
import multer from "multer";
import { requireAuth, requireAdmin } from "../middlewares/auth";
import { extractTextFromBase64 } from "../lib/pwc-ai";

const router: IRouter = Router();

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 50 * 1024 * 1024 }, // 50 MB
});

const MIME_MAP: Record<string, string> = {
  // Images
  "image/png": "image/png",
  "image/jpeg": "image/jpeg",
  "image/jpg": "image/jpeg",
  "image/webp": "image/webp",
  "image/gif": "image/gif",
  "image/tiff": "image/tiff",
  // Documents — pass MIME through so model knows what it is
  "application/pdf": "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword": "application/msword",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-powerpoint": "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel": "application/vnd.ms-excel",
  // Plain text — just return as-is
  "text/plain": "text/plain",
  "text/markdown": "text/plain",
  "text/csv": "text/plain",
  "application/json": "text/plain",
};

function extToMime(filename: string): string | null {
  const ext = filename.split(".").pop()?.toLowerCase();
  const map: Record<string, string> = {
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    doc: "application/msword",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ppt: "application/vnd.ms-powerpoint",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    xls: "application/vnd.ms-excel",
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    webp: "image/webp",
    gif: "image/gif",
    tiff: "image/tiff",
    tif: "image/tiff",
    txt: "text/plain",
    md: "text/plain",
    csv: "text/plain",
    json: "text/plain",
  };
  return ext ? (map[ext] ?? null) : null;
}

router.post(
  "/documents/extract",
  requireAuth,
  requireAdmin,
  upload.single("file"),
  async (req, res) => {
    const file = req.file;
    if (!file) {
      res.status(400).json({ error: "No file uploaded", status: 400 });
      return;
    }

    const detectedMime =
      MIME_MAP[file.mimetype] ??
      MIME_MAP[extToMime(file.originalname) ?? ""] ??
      null;

    if (!detectedMime) {
      res.status(415).json({
        error: `Unsupported file type: ${file.mimetype}. Supported: PDF, DOCX, PPTX, XLSX, images, TXT, MD, CSV, JSON.`,
        status: 415,
      });
      return;
    }

    // Plain text — decode directly, no LLM needed
    if (detectedMime === "text/plain") {
      const text = file.buffer.toString("utf-8");
      res.json({ text, filename: file.originalname });
      return;
    }

    try {
      const base64 = file.buffer.toString("base64");
      const text = await extractTextFromBase64(detectedMime, base64, file.originalname);
      res.json({ text, filename: file.originalname });
    } catch (err) {
      req.log?.error({ err }, "File extraction failed");
      const msg = err instanceof Error ? err.message : "Extraction failed";
      res.status(502).json({ error: msg, status: 502 });
    }
  },
);

export default router;
