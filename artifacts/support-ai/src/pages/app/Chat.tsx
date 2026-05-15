import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useLocation, useParams } from "wouter";
import {
  useListConversations,
  useGetConversation,
  useCreateConversation,
  useDeleteConversation,
  useListTickets,
  useRateMessage,
  getListConversationsQueryKey,
  getGetConversationQueryKey,
} from "@workspace/api-client-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  Bot,
  FileText,
  Send,
  ThumbsDown,
  ThumbsUp,
  Ticket,
  AlertCircle,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Share2,
  CheckCircle2,
  AlertTriangle,
  History,
  ChevronRight,
  Trash2,
  Paperclip,
  Star,
  X,
  User,
  Brain,
  Lightbulb,
  Search,
  BookOpen,
  Mic,
  MicOff,
  Volume2,
  Keyboard,
  Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const TICKET_AFTER_FINAL_VERDICT =
  "Yes, please create a support ticket — the guided steps did not fully resolve my issue.";
const BULLET_RE = /^[-*]\s+/;
const ORDERED_RE = /^\d+\.\s+/;
const CITATION_RE = /\[(\d+)\]/g;
const BOLD_RE = /\*\*(.+?)\*\*/g;
const TICKET_ID_RE = /ticket\s*#\s*(\d+)/i;
const ASSISTANT_BUBBLE_CLASS =
  "relative isolate w-full overflow-hidden rounded-[1.625rem] rounded-tl-lg border border-slate-200/70 bg-[linear-gradient(135deg,rgba(255,255,255,0.96),rgba(248,250,252,0.92)_55%,rgba(239,246,255,0.78))] px-5 py-4 text-sm text-foreground shadow-[0_1px_1px_rgba(15,23,42,0.03),0_18px_45px_rgba(15,23,42,0.08)] ring-1 ring-white/80 backdrop-blur-xl transition-shadow duration-300 before:absolute before:inset-x-6 before:top-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-white before:to-transparent before:content-[''] hover:shadow-[0_1px_1px_rgba(15,23,42,0.03),0_22px_55px_rgba(15,23,42,0.11)] dark:border-white/10 dark:bg-[linear-gradient(135deg,rgba(15,23,42,0.88),rgba(15,23,42,0.78)_58%,rgba(30,41,59,0.68))] dark:ring-white/10 dark:shadow-black/30 dark:before:via-white/30";

function extractAssistantAnswer(content: string): string {
  const text = content.trim();
  if (!text) {
    return content;
  }

  const candidates: string[] = [text];
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenced?.[1]) {
    candidates.push(fenced[1].trim());
  }

  const firstBrace = text.indexOf("{");
  const lastBrace = text.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    candidates.push(text.slice(firstBrace, lastBrace + 1));
  }

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate) as { answer?: unknown };
      if (
        typeof parsed.answer === "string" &&
        parsed.answer.trim().length > 0
      ) {
        return parsed.answer.trim();
      }
    } catch {
      // Ignore malformed JSON and continue rendering plain text.
    }
  }

  return content;
}

function looksUnanswerable(content: string): boolean {
  const text = extractAssistantAnswer(content).trim().toLowerCase();
  if (!text) {
    return true;
  }

  const patterns = [
    "do not contain information",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "not enough information",
    "don't have enough information",
    "couldn't find a confident answer",
    "open a support ticket",
    "having trouble reaching the model",
  ];

  return patterns.some((pattern) => text.includes(pattern));
}

type MemoryGraphNode = {
  id: string;
  label: string;
  type: "user" | "memory" | "concept";
};

type MemoryGraphEdge = {
  source: string;
  target: string;
  type: string;
};

type MemoryGraphResponse = {
  query: string;
  memoryCount: number;
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
};

type MemoryGraphPoint = {
  x: number;
  y: number;
};

type MemoryGraphLayout = {
  width: number;
  height: number;
  positionedNodes: Array<MemoryGraphNode & { position: MemoryGraphPoint }>;
  positionedEdges: Array<
    MemoryGraphEdge & { from: MemoryGraphPoint; to: MemoryGraphPoint }
  >;
};

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function hash01(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 2 ** 32;
}

/** Fruchterman–Reingold–style layout: organic graph, anchored user node at top center. */
function buildMemoryGraphLayout(
  nodes: MemoryGraphNode[] | undefined,
  edges: MemoryGraphEdge[] | undefined,
): MemoryGraphLayout {
  const safeNodes = nodes ?? [];
  const safeEdges = edges ?? [];
  const width = 980;
  const height = 430;
  const pad = 52;
  const userAnchor: MemoryGraphPoint = { x: width / 2, y: 76 };

  if (safeNodes.length === 0) {
    return {
      width,
      height,
      positionedNodes: [],
      positionedEdges: [],
    };
  }

  const area = width * height;
  const k = Math.sqrt(area / Math.max(safeNodes.length, 1));
  const iterations = 110;
  const firstUser = safeNodes.find((n) => n.type === "user");

  type SimPoint = MemoryGraphPoint & { dx: number; dy: number };
  const pos = new Map<string, SimPoint>();

  let idx = 0;
  for (const n of safeNodes) {
    if (firstUser && n.id === firstUser.id) {
      pos.set(n.id, { ...userAnchor, dx: 0, dy: 0 });
      continue;
    }
    const t = idx * GOLDEN_ANGLE;
    const u = hash01(n.id);
    const radius = 95 + u * 105;
    const cx = width / 2;
    const cy = height * 0.48;
    pos.set(n.id, {
      x: cx + Math.cos(t) * radius,
      y: cy + Math.sin(t) * radius * 0.82,
      dx: 0,
      dy: 0,
    });
    idx += 1;
  }

  const getP = (id: string) => pos.get(id);

  for (let iter = 0; iter < iterations; iter += 1) {
    const temperature = Math.max(
      0.12,
      2.2 * Math.pow(1 - iter / iterations, 1.65),
    );

    for (const p of pos.values()) {
      p.dx = 0;
      p.dy = 0;
    }

    for (let a = 0; a < safeNodes.length; a += 1) {
      for (let b = a + 1; b < safeNodes.length; b += 1) {
        const na = safeNodes[a];
        const nb = safeNodes[b];
        const pa = getP(na.id);
        const pb = getP(nb.id);
        if (!pa || !pb) continue;

        let vx = pa.x - pb.x;
        let vy = pa.y - pb.y;
        const dist = Math.max(6, Math.hypot(vx, vy));
        const rep = (k * k) / dist;
        vx = (vx / dist) * rep;
        vy = (vy / dist) * rep;
        pa.dx += vx;
        pa.dy += vy;
        pb.dx -= vx;
        pb.dy -= vy;
      }
    }

    for (const e of safeEdges) {
      const pa = getP(e.source);
      const pb = getP(e.target);
      if (!pa || !pb) continue;
      let vx = pb.x - pa.x;
      let vy = pb.y - pa.y;
      const dist = Math.max(6, Math.hypot(vx, vy));
      const att = (dist * dist) / k;
      vx = (vx / dist) * att * 0.42;
      vy = (vy / dist) * att * 0.42;
      pa.dx += vx;
      pa.dy += vy;
      pb.dx -= vx;
      pb.dy -= vy;
    }

    if (firstUser) {
      const pu = getP(firstUser.id);
      if (pu) {
        pu.dx += (userAnchor.x - pu.x) * 0.65;
        pu.dy += (userAnchor.y - pu.y) * 0.65;
      }
    }

    const cx = width / 2;
    const cy = height * 0.5;
    for (const n of safeNodes) {
      if (firstUser && n.id === firstUser.id) continue;
      const p = getP(n.id);
      if (!p) continue;
      p.dx += (cx - p.x) * 0.024;
      p.dy += (cy - p.y) * 0.024;
    }

    for (const n of safeNodes) {
      const p = getP(n.id);
      if (!p) continue;
      const mag = Math.hypot(p.dx, p.dy) || 0;
      const lim = temperature * 18;
      const s = mag > lim ? lim / mag : 1;
      p.x += p.dx * s * 0.045;
      p.y += p.dy * s * 0.045;
      p.x = Math.min(width - pad, Math.max(pad, p.x));
      p.y = Math.min(height - pad - 58, Math.max(pad + 32, p.y));
    }
  }

  if (firstUser) {
    const pu = getP(firstUser.id);
    if (pu) {
      pu.x = userAnchor.x;
      pu.y = userAnchor.y;
    }
  }

  const positions = new Map<string, MemoryGraphPoint>();
  for (const n of safeNodes) {
    const p = getP(n.id);
    if (p) {
      positions.set(n.id, { x: p.x, y: p.y });
    }
  }

  const positionedNodes = safeNodes
    .filter((node) => positions.has(node.id))
    .map((node) => ({
      ...node,
      position: positions.get(node.id) as MemoryGraphPoint,
    }));

  const positionedEdges = safeEdges
    .map((edge) => {
      const from = positions.get(edge.source);
      const to = positions.get(edge.target);
      if (!from || !to) {
        return null;
      }
      return {
        ...edge,
        from,
        to,
      };
    })
    .filter(Boolean) as Array<
    MemoryGraphEdge & { from: MemoryGraphPoint; to: MemoryGraphPoint }
  >;

  return {
    width,
    height,
    positionedNodes,
    positionedEdges,
  };
}

/** Curved directed edge with a slight perpendicular lift so bundles read as separate arcs. */
function memoryGraphEdgePath(
  from: MemoryGraphPoint,
  to: MemoryGraphPoint,
  edgeIndex: number,
  endTrim = 24,
): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.max(8, Math.hypot(dx, dy));
  const ux = dx / len;
  const uy = dy / len;
  const trim = Math.min(endTrim, len * 0.32);
  const ax = from.x + ux * trim;
  const ay = from.y + uy * trim;
  const bx = to.x - ux * trim;
  const by = to.y - uy * trim;
  const span = Math.max(6, Math.hypot(bx - ax, by - ay));
  const nx = -(by - ay) / span;
  const ny = (bx - ax) / span;
  const side = edgeIndex % 2 === 0 ? 1 : -1;
  const lift = 26 + (edgeIndex % 5) * 4;
  const mx = (ax + bx) / 2 + nx * lift * side;
  const my = (ay + by) / 2 + ny * lift * side;
  return `M ${ax} ${ay} Q ${mx} ${my} ${bx} ${by}`;
}

function renderInlineCitations(
  text: string,
  keyPrefix: string,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let idx = 0;
  let match: RegExpExecArray | null = null;

  while ((match = CITATION_RE.exec(text)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    const n = match[1];
    if (start > last) {
      parts.push(text.slice(last, start));
    }
    parts.push(
      <span
        key={`${keyPrefix}-cite-${idx}`}
        className="inline-flex items-center rounded-md border border-primary/25 bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary align-middle mx-0.5"
      >
        [{n}]
      </span>,
    );
    last = end;
    idx += 1;
  }

  if (last < text.length) {
    parts.push(text.slice(last));
  }

  return parts.length > 0 ? parts : [text];
}

function renderFormattedInline(
  text: string,
  keyPrefix: string,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let idx = 0;
  let match: RegExpExecArray | null = null;

  BOLD_RE.lastIndex = 0;
  while ((match = BOLD_RE.exec(text)) !== null) {
    const start = match.index;
    const end = start + match[0].length;

    if (start > last) {
      parts.push(
        ...renderInlineCitations(
          text.slice(last, start),
          `${keyPrefix}-text-${idx}`,
        ),
      );
    }

    parts.push(
      <strong
        key={`${keyPrefix}-strong-${idx}`}
        className="font-semibold text-foreground"
      >
        {renderInlineCitations(match[1], `${keyPrefix}-strong-content-${idx}`)}
      </strong>,
    );

    last = end;
    idx += 1;
  }

  if (last < text.length) {
    parts.push(...renderInlineCitations(text.slice(last), `${keyPrefix}-tail`));
  }

  return parts.length > 0
    ? parts
    : renderInlineCitations(text, `${keyPrefix}-plain`);
}

function renderParagraph(
  text: string,
  keyPrefix: string,
  className = "whitespace-pre-wrap",
): React.ReactNode {
  return <p className={className}>{renderFormattedInline(text, keyPrefix)}</p>;
}

function splitAssistantSegments(content: string): string[] {
  const normalized = extractAssistantAnswer(content);
  const blocks = normalized
    .split(/\n{2,}/)
    .map((b) => b.trim())
    .filter(Boolean);

  if (blocks.length === 0) {
    return [];
  }

  const segments: string[] = [];
  let current: string[] = [];

  for (const block of blocks) {
    const lines = block
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    const isHeadingOnly =
      Boolean(lines[0]?.match(/^\*\*(.+?)\*\*$/)) && lines.length === 1;

    if (isHeadingOnly) {
      if (current.length > 0) {
        segments.push(current.join("\n\n"));
      }
      current = [block];
      continue;
    }

    current.push(block);
  }

  if (current.length > 0) {
    segments.push(current.join("\n\n"));
  }

  return segments;
}

function renderAssistantContent(content: string): React.ReactNode {
  const blocks = content
    .split(/\n{2,}/)
    .map((b) => b.trim())
    .filter(Boolean);

  return (
    <div className="space-y-3.5 text-[15px] leading-7 text-foreground/95">
      {blocks.map((block, blockIdx) => {
        const lines = block
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean);

        const headingMatch = lines[0]?.match(/^\*\*(.+?)\*\*$/);
        if (headingMatch && lines.length > 1) {
          const body = lines.slice(1).join("\n");
          return (
            <section
              key={`section-${blockIdx}`}
              className="space-y-2 rounded-2xl border border-border/50 bg-muted/[0.35] px-4 py-3 ring-1 ring-black/[0.02]"
            >
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary/80">
                {headingMatch[1]}
              </h4>
              {renderParagraph(
                body,
                `section-${blockIdx}`,
                "whitespace-pre-wrap text-[15px] leading-7 text-foreground/90",
              )}
            </section>
          );
        }

        if (lines.length > 0 && lines.every((line) => BULLET_RE.test(line))) {
          return (
            <ul
              key={`ul-${blockIdx}`}
              className="list-disc pl-5 space-y-1.5 marker:text-primary"
            >
              {lines.map((line, liIdx) => (
                <li key={`li-${blockIdx}-${liIdx}`}>
                  {renderFormattedInline(
                    line.replace(BULLET_RE, ""),
                    `ul-${blockIdx}-${liIdx}`,
                  )}
                </li>
              ))}
            </ul>
          );
        }

        if (lines.length > 0 && lines.every((line) => ORDERED_RE.test(line))) {
          return (
            <ol
              key={`ol-${blockIdx}`}
              className="list-decimal pl-5 space-y-1.5 marker:text-primary"
            >
              {lines.map((line, liIdx) => (
                <li key={`oli-${blockIdx}-${liIdx}`}>
                  {renderFormattedInline(
                    line.replace(ORDERED_RE, ""),
                    `ol-${blockIdx}-${liIdx}`,
                  )}
                </li>
              ))}
            </ol>
          );
        }

        return (
          <div key={`p-${blockIdx}`}>
            {renderParagraph(block, `p-${blockIdx}`)}
          </div>
        );
      })}
    </div>
  );
}

function citationMetadataItems(
  cite: ChatCitation,
): Array<{ label: string; value: string }> {
  return [
    { label: "File", value: cite.metadata?.fileName ?? cite.documentName },
    {
      label: "Page",
      value:
        cite.metadata?.pageNumber != null
          ? String(cite.metadata.pageNumber)
          : "N/A",
    },
    {
      label: "Position",
      value:
        cite.metadata?.chunkPosition != null
          ? String(cite.metadata.chunkPosition)
          : "N/A",
    },
    {
      label: "Tokens",
      value:
        cite.metadata?.tokenCount != null
          ? String(cite.metadata.tokenCount)
          : "N/A",
    },
    { label: "Source", value: cite.metadata?.sourceType ?? "Knowledge base" },
    { label: "Score", value: cite.score.toFixed(3) },
  ];
}

type StreamingMessage = {
  id: number;
  conversationId: number;
  role: "user" | "assistant";
  kind?:
    | "answer"
    | "clarification_question"
    | "ticket_offer"
    | "ticket_created"
    | null;
  ticketId?: number | null;
  content: string;
  citations: ChatCitation[];
  rating?: "up" | "down" | null;
  canAnswer?: boolean | null;
  imageDataUrl?: string | null;
  createdAt: string;
};

const IMAGE_CONTEXT_RE = /^\[Image context:[\s\S]*?\]\s*\n*/;

function stripImageContext(content: string): string {
  return content.replace(IMAGE_CONTEXT_RE, "").trim();
}

type ChatCitation = {
  chunkId: number;
  documentId: number;
  documentName: string;
  snippet: string;
  score: number;
  metadata?: {
    fileName?: string;
    pageNumber?: number | null;
    keyPhrases?: string[];
    chunkPosition?: number | null;
    tokenCount?: number | null;
    sourceType?: string | null;
  };
};

type StreamingAssistantMessage = StreamingMessage & {
  role: "assistant";
  citations: ChatCitation[];
  done: boolean;
};

type ProcessStepStatus = "started" | "completed" | "error";

type ProcessStep = {
  name: string;
  status: ProcessStepStatus;
};

/** Maps SSE process step names (see api-server chat routes) to concise UI copy. */
const PROCESS_STEP_LABELS: Record<string, string> = {
  "Saving user prompt": "Saving your message",
  "Updating conversation metadata": "Updating this conversation",
  "Checking ticket escalation intent": "Reviewing escalation options",
  "Checking ticket creation consent": "Confirming ticket creation intent",
  "Creating support ticket": "Creating the support ticket",
  "Preparing escalation guidance": "Preparing guidance",
  "Enhancing user query": "Refining your question",
  "Retrieving relevant knowledge": "Searching verified documentation",
  "Loading user memory": "Applying saved context",
  "Reviewing investigation memory": "Reviewing what we already know",
  "Planning next best action": "Deciding whether to ask or answer",
  "Updating investigation memory": "Saving the latest investigation state",
  "Asking a clarifying question": "Asking the most relevant follow-up",
  "Composing grounded answer": "Preparing the grounded answer",
  "Generating grounded answer": "Drafting the grounded answer",
  "Saving assistant response": "Saving the reply",
  Completed: "Wrapping up",
  "Streaming failed": "Could not complete this response",
};

function messageKindOf(msg: {
  kind?: string | null;
  canAnswer?: boolean | null;
}): "answer" | "clarification_question" | "ticket_offer" | "ticket_created" {
  if (
    msg.kind === "clarification_question" ||
    msg.kind === "ticket_offer" ||
    msg.kind === "ticket_created"
  ) {
    return msg.kind;
  }
  return "answer";
}

function createdTicketIdOf(msg: {
  ticketId?: number | null;
  content: string;
}): number | null {
  if (
    typeof msg.ticketId === "number" &&
    Number.isInteger(msg.ticketId) &&
    msg.ticketId > 0
  ) {
    return msg.ticketId;
  }
  const match = msg.content.match(TICKET_ID_RE);
  if (!match) {
    return null;
  }
  const parsed = Number.parseInt(match[1], 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

const TICKET_FEEDBACK_STORAGE_PREFIX = "helia-chat-star-rating";

function readStoredStarRating(
  conversationId: number | undefined,
): number | null {
  if (!conversationId) return null;
  try {
    const raw = sessionStorage.getItem(
      `${TICKET_FEEDBACK_STORAGE_PREFIX}-${conversationId}`,
    );
    if (!raw) return null;
    const n = Number.parseInt(raw, 10);
    return n >= 1 && n <= 5 ? n : null;
  } catch {
    return null;
  }
}

function starsToMessageRating(stars: number): "up" | "down" {
  return stars >= 3 ? "up" : "down";
}

function presentationForProcessStep(step: ProcessStep): {
  caption: string;
  label: string;
  ariaLabel: string;
} {
  const label = PROCESS_STEP_LABELS[step.name] ?? step.name;
  switch (step.status) {
    case "started":
      return {
        caption: "In progress",
        label,
        ariaLabel: `Assistant is working: ${label}`,
      };
    case "completed":
      return {
        caption: "Completed",
        label,
        ariaLabel: `Step completed: ${label}`,
      };
    case "error":
      return {
        caption: "Attention needed",
        label,
        ariaLabel: `Something went wrong: ${label}`,
      };
    default:
      return { caption: "Status", label, ariaLabel: label };
  }
}

type SseEvent = { event: string; data: string };

async function* iterSseEvents(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let event = "message";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:"))
            dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length > 0) {
          yield { event, data: dataLines.join("\n") };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

type VoiceChatPanelProps = {
  isListening: boolean;
  isSpeaking: boolean;
  isStreaming: boolean;
  interimTranscript: string;
  voiceError: string | null;
  speechSupported: boolean;
  onToggleMic: () => void;
  onStopSpeaking: () => void;
};

function VoiceChatPanel({
  isListening,
  isSpeaking,
  isStreaming,
  interimTranscript,
  voiceError,
  speechSupported,
  onToggleMic,
  onStopSpeaking,
}: VoiceChatPanelProps) {
  const status: {
    label: string;
    hint: string;
    tone: "idle" | "listening" | "thinking" | "speaking";
  } = isListening
    ? {
        label: "Listening…",
        hint: interimTranscript
          ? `“${interimTranscript}”`
          : "Speak when you're ready. I'll respond out loud.",
        tone: "listening",
      }
    : isStreaming
      ? {
          label: "Thinking…",
          hint: "Generating a spoken reply.",
          tone: "thinking",
        }
      : isSpeaking
        ? {
            label: "Helia is speaking…",
            hint: "Tap the mic to interrupt and respond.",
            tone: "speaking",
          }
        : {
            label: "Tap the mic to talk",
            hint: speechSupported
              ? "I'll listen, then reply out loud. Tap again to stop."
              : "Voice chat needs a browser with SpeechRecognition (Chrome/Edge).",
            tone: "idle",
          };

  const busy = isStreaming;
  const micActive = isListening;
  const showStopSpeaking = isSpeaking && !isListening;

  return (
    <div className="flex w-full flex-col items-center gap-3">
      <div className="relative flex h-[88px] w-[88px] items-center justify-center">
        {(micActive || isSpeaking) && (
          <>
            <span
              aria-hidden
              className={cn(
                "absolute inset-0 rounded-full opacity-60 blur-md animate-ping",
                micActive
                  ? "bg-red-500/40"
                  : isSpeaking
                    ? "bg-primary/40"
                    : "bg-transparent",
              )}
            />
            <span
              aria-hidden
              className={cn(
                "absolute inset-2 rounded-full opacity-50",
                micActive
                  ? "bg-red-500/30"
                  : isSpeaking
                    ? "bg-primary/30"
                    : "bg-transparent",
              )}
            />
          </>
        )}
        <button
          type="button"
          onClick={onToggleMic}
          disabled={busy || !speechSupported}
          aria-pressed={micActive}
          aria-label={
            micActive ? "Stop listening" : "Tap to speak to Helia AI"
          }
          className={cn(
            "relative z-[1] flex h-16 w-16 items-center justify-center rounded-full text-white shadow-lg transition-transform focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-primary/40 disabled:cursor-not-allowed disabled:opacity-60",
            micActive
              ? "bg-red-500 hover:bg-red-500/90 ring-4 ring-red-500/20"
              : isSpeaking
                ? "bg-primary hover:bg-primary/90 ring-4 ring-primary/15"
                : "bg-primary hover:bg-primary/90",
            !busy && "hover:scale-[1.02] active:scale-[0.98]",
          )}
        >
          {busy ? (
            <Loader2 className="h-7 w-7 animate-spin" />
          ) : micActive ? (
            <MicOff className="h-7 w-7" />
          ) : isSpeaking ? (
            <Volume2 className="h-7 w-7" />
          ) : (
            <Mic className="h-7 w-7" />
          )}
        </button>
      </div>

      <div className="min-h-[40px] text-center">
        <p
          className={cn(
            "text-sm font-semibold",
            status.tone === "listening" && "text-red-500",
            status.tone === "speaking" && "text-primary",
            status.tone === "thinking" && "text-foreground",
            status.tone === "idle" && "text-foreground",
          )}
        >
          {status.label}
        </p>
        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
          {status.hint}
        </p>
      </div>

      {showStopSpeaking && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7 gap-1.5 rounded-full px-3 text-[11px]"
          onClick={onStopSpeaking}
        >
          <Square className="h-3 w-3" />
          Stop voice
        </Button>
      )}

      {voiceError && (
        <p className="max-w-md text-center text-[11px] text-destructive">
          {voiceError}
        </p>
      )}
    </div>
  );
}

export default function Chat() {
  const [location, setLocation] = useLocation();
  const params = useParams<{ id?: string }>();
  const currentId = params.id ? parseInt(params.id, 10) : undefined;

  const { data: conversations, isLoading: loadingConvos } =
    useListConversations();
  const { data: tickets } = useListTickets();
  const activeTickets =
    tickets?.filter((t) => t.status !== "closed" && t.status !== "resolved") ||
    [];

  const { data: activeConvo, isLoading: loadingActive } = useGetConversation(
    currentId as number,
    {
      query: {
        enabled: !!currentId,
        queryKey: getGetConversationQueryKey(currentId as number),
      },
    },
  );

  const createConvo = useCreateConversation();
  const rateMessage = useRateMessage();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const deleteConvo = useDeleteConversation({
    mutation: {
      onSuccess: (_data, variables) => {
        const { id } = variables;
        queryClient.invalidateQueries({
          queryKey: getListConversationsQueryKey(),
        });
        queryClient.removeQueries({ queryKey: getGetConversationQueryKey(id) });
        queryClient.removeQueries({ queryKey: ["chat-memory-graph", id] });
        if (currentId === id) {
          setLocation("/app");
        }
        setDeleteTarget(null);
        toast({ title: "Conversation deleted" });
      },
      onError: () => {
        toast({
          title: "Failed to delete conversation",
          variant: "destructive",
        });
      },
    },
  });

  const [input, setInput] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
  const [imageDataUrl, setImageDataUrl] = useState<string | null>(null);
  const [isDescribingImage, setIsDescribingImage] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const [isConversationsOpen, setIsConversationsOpen] = useState(true);
  const [memoryGraphOpen, setMemoryGraphOpen] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingUserMessage, setStreamingUserMessage] =
    useState<StreamingMessage | null>(null);
  const [streamingAssistantMessage, setStreamingAssistantMessage] =
    useState<StreamingAssistantMessage | null>(null);
  const [processSteps, setProcessSteps] = useState<ProcessStep[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: number;
    title: string;
  } | null>(null);
  const [clearMemoryOpen, setClearMemoryOpen] = useState(false);
  const [isClearingMemory, setIsClearingMemory] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // ----- Voice chat state -----
  type ChatMode = "text" | "voice";
  const [chatMode, setChatMode] = useState<ChatMode>("text");
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [interimTranscript, setInterimTranscript] = useState("");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const speechRecognitionRef = useRef<any>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioObjectUrlRef = useRef<string | null>(null);
  const chatModeRef = useRef<ChatMode>("text");
  const shouldAutoListenRef = useRef<boolean>(false);
  const speechSupported = useMemo(() => {
    if (typeof window === "undefined") return false;
    return Boolean(
      (window as any).SpeechRecognition ||
        (window as any).webkitSpeechRecognition,
    );
  }, []);

  useEffect(() => {
    chatModeRef.current = chatMode;
  }, [chatMode]);

  const memoryGraph = useQuery<MemoryGraphResponse>({
    queryKey: ["chat-memory-graph", currentId],
    enabled: Boolean(currentId) && memoryGraphOpen,
    queryFn: async () => {
      const res = await fetch(
        `/api/chat/conversations/${currentId}/memory-graph`,
        {
          credentials: "include",
        },
      );
      if (!res.ok) {
        throw new Error("Failed to load memory graph");
      }
      return (await res.json()) as MemoryGraphResponse;
    },
    staleTime: 60_000,
  });

  const memoryLayout = useMemo(
    () =>
      buildMemoryGraphLayout(memoryGraph.data?.nodes, memoryGraph.data?.edges),
    [memoryGraph.data?.nodes, memoryGraph.data?.edges],
  );

  const activeProcessStep = useMemo(() => {
    if (processSteps.length === 0) return null;
    const started = [...processSteps]
      .reverse()
      .find((s) => s.status === "started");
    if (started) return started;
    const failed = [...processSteps]
      .reverse()
      .find((s) => s.status === "error");
    if (failed) return failed;
    return processSteps[processSteps.length - 1] ?? null;
  }, [processSteps]);

  const activeProcessPresentation = useMemo(
    () =>
      activeProcessStep ? presentationForProcessStep(activeProcessStep) : null,
    [activeProcessStep],
  );

  const ticketCreatedMessage = useMemo(() => {
    if (!activeConvo?.messages?.length) {
      return null;
    }
    const created = activeConvo.messages.filter(
      (m) =>
        m.role === "assistant" &&
        messageKindOf(m as { kind?: string | null }) === "ticket_created",
    );
    return created[created.length - 1] ?? null;
  }, [activeConvo?.messages]);

  const chatEndedWithTicket = Boolean(ticketCreatedMessage);

  const [conversationStarDraft, setConversationStarDraft] = useState<
    number | null
  >(null);
  const [storedStarRating, setStoredStarRating] = useState<number | null>(() =>
    readStoredStarRating(currentId),
  );

  const [starRowHover, setStarRowHover] = useState<number | null>(null);

  useEffect(() => {
    setStoredStarRating(readStoredStarRating(currentId));
    setConversationStarDraft(null);
    setStarRowHover(null);
  }, [currentId]);

  const conversationFeedbackDone =
    storedStarRating != null ||
    (ticketCreatedMessage != null &&
      ticketCreatedMessage.rating !== undefined &&
      ticketCreatedMessage.rating !== null);

  const filledStarsReadonly =
    storedStarRating ??
    (ticketCreatedMessage?.rating === "up"
      ? 5
      : ticketCreatedMessage?.rating === "down"
        ? 2
        : null);

  const starHighlight =
    starRowHover ??
    conversationStarDraft ??
    (conversationFeedbackDone ? filledStarsReadonly : null);

  const submitConversationStarRating = async () => {
    if (!currentId || !ticketCreatedMessage || conversationStarDraft == null) {
      return;
    }
    try {
      await rateMessage.mutateAsync({
        id: ticketCreatedMessage.id,
        data: {
          rating: starsToMessageRating(conversationStarDraft),
          comment: `Conversation rating: ${conversationStarDraft}/5`,
        },
      });
      try {
        sessionStorage.setItem(
          `${TICKET_FEEDBACK_STORAGE_PREFIX}-${currentId}`,
          String(conversationStarDraft),
        );
      } catch {
        // ignore quota / privacy mode
      }
      setStoredStarRating(conversationStarDraft);
      await queryClient.invalidateQueries({
        queryKey: getGetConversationQueryKey(currentId),
      });
      toast({ title: "Thanks for your feedback" });
    } catch {
      toast({ title: "Could not save feedback", variant: "destructive" });
    }
  };

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [
    activeConvo?.messages,
    isStreaming,
    streamingAssistantMessage?.content,
    processSteps,
  ]);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  // ----- Voice chat helpers -----
  const stopSpeaking = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      try {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      } catch {
        // ignore
      }
    }
    if (audioObjectUrlRef.current) {
      URL.revokeObjectURL(audioObjectUrlRef.current);
      audioObjectUrlRef.current = null;
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        // ignore
      }
    }
    setIsSpeaking(false);
  }, []);

  const pickIndianEnglishFemaleVoice = useCallback(
    (voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null => {
      if (!voices?.length) return null;

      // Known Indian English female voice names across major TTS engines.
      // Microsoft (Windows/Edge): "Heera", "Kalpana", "Neerja", "Aarohi"
      // Apple (macOS/iOS):        "Veena", "Rishi" (male), "Lekha" (hi-IN)
      // Google (Chrome/Android):  "Google English (India)", "Google हिन्दी"
      // Samsung / others:         "Raveena", "Indian English Female"
      const femaleNameHints = [
        "heera",
        "kalpana",
        "neerja",
        "aarohi",
        "veena",
        "raveena",
        "lekha",
        "priya",
        "shruti",
        "deepa",
        "asha",
        "indian english female",
        "female",
      ];

      const isIndianEnglish = (v: SpeechSynthesisVoice) => {
        const lang = (v.lang || "").toLowerCase();
        return lang === "en-in" || lang.startsWith("en-in");
      };

      const looksFemale = (v: SpeechSynthesisVoice) => {
        const name = (v.name || "").toLowerCase();
        if (name.includes("male") && !name.includes("female")) return false;
        return femaleNameHints.some((hint) => name.includes(hint));
      };

      // 1) Indian English + clearly female by name.
      const inFemale = voices.find((v) => isIndianEnglish(v) && looksFemale(v));
      if (inFemale) return inFemale;

      // 2) Any Indian English voice (Google's en-IN default is female-sounding).
      const inAny = voices.find(isIndianEnglish);
      if (inAny) return inAny;

      // 3) Hindi voice (hi-IN) — still Indian accent when speaking English words.
      const hiFemale = voices.find(
        (v) =>
          (v.lang || "").toLowerCase().startsWith("hi-in") && looksFemale(v),
      );
      if (hiFemale) return hiFemale;
      const hiAny = voices.find((v) =>
        (v.lang || "").toLowerCase().startsWith("hi-in"),
      );
      if (hiAny) return hiAny;

      // 4) Last resort: any English female voice.
      const enFemale = voices.find(
        (v) =>
          (v.lang || "").toLowerCase().startsWith("en") && looksFemale(v),
      );
      if (enFemale) return enFemale;

      return voices[0] ?? null;
    },
    [],
  );

  const speakWithBrowserTts = useCallback(
    (text: string) => {
      if (typeof window === "undefined" || !("speechSynthesis" in window)) {
        setIsSpeaking(false);
        setVoiceError(
          "Voice playback is unavailable: this browser has no built-in speech synthesis.",
        );
        return;
      }

      const synth = window.speechSynthesis;

      const speakOnce = () => {
        try {
          synth.cancel();
          const utter = new SpeechSynthesisUtterance(text);
          const chosen = pickIndianEnglishFemaleVoice(synth.getVoices());
          if (chosen) {
            utter.voice = chosen;
            utter.lang = chosen.lang || "en-IN";
          } else {
            utter.lang = "en-IN";
          }
          // Slightly slower, slightly warmer pitch produces a more natural,
          // polite Indian English cadence rather than a flat robotic delivery.
          utter.rate = 0.95;
          utter.pitch = 1.05;
          utter.volume = 1;
          utter.onend = () => {
            setIsSpeaking(false);
            if (
              chatModeRef.current === "voice" &&
              shouldAutoListenRef.current &&
              !chatEndedWithTicket
            ) {
              startListeningRef.current?.();
            }
          };
          utter.onerror = () => {
            setIsSpeaking(false);
          };
          synth.speak(utter);
        } catch (err) {
          setIsSpeaking(false);
          setVoiceError(
            (err as Error)?.message ?? "Could not play voice response.",
          );
        }
      };

      // Chrome populates voices asynchronously; wait for them once if needed
      // so the Indian English voice is actually picked instead of the default.
      const voices = synth.getVoices();
      if (!voices || voices.length === 0) {
        const handleVoicesChanged = () => {
          synth.removeEventListener("voiceschanged", handleVoicesChanged);
          speakOnce();
        };
        synth.addEventListener("voiceschanged", handleVoicesChanged);
        // Safety fallback — some browsers never fire the event.
        window.setTimeout(() => {
          synth.removeEventListener("voiceschanged", handleVoicesChanged);
          speakOnce();
        }, 400);
      } else {
        speakOnce();
      }
    },
    [chatEndedWithTicket, pickIndianEnglishFemaleVoice],
  );

  const stopListening = useCallback(() => {
    const rec = speechRecognitionRef.current;
    shouldAutoListenRef.current = false;
    if (rec) {
      try {
        rec.stop();
      } catch {
        // ignore
      }
    }
    setIsListening(false);
    setInterimTranscript("");
  }, []);

  const speakText = useCallback(
    async (text: string) => {
      const clean = (text || "").trim();
      if (!clean) return;
      try {
        stopSpeaking();
        setIsSpeaking(true);
        setVoiceError(null);
        const res = await fetch("/api/chat/tts", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: clean }),
        });
        if (!res.ok) {
          // Server signalled a hard failure — fall back to the browser's
          // built-in speech synthesis so the user still hears the reply.
          speakWithBrowserTts(clean);
          return;
        }

        const contentType = (res.headers.get("content-type") || "").toLowerCase();
        if (contentType.includes("application/json")) {
          // Backend told us to use the browser's SpeechSynthesis API
          // (e.g. ElevenLabs returned 402 on a free plan).
          let payload: { useBrowserTts?: boolean; text?: string } = {};
          try {
            payload = await res.json();
          } catch {
            payload = {};
          }
          if (payload?.useBrowserTts) {
            speakWithBrowserTts(payload.text || clean);
            return;
          }
          // Unexpected JSON shape — still fall back so the user hears something.
          speakWithBrowserTts(clean);
          return;
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        audioObjectUrlRef.current = url;
        let audio = audioRef.current;
        if (!audio) {
          audio = new Audio();
          audioRef.current = audio;
        }
        audio.src = url;
        await audio.play().catch((err) => {
          throw err;
        });
      } catch (err) {
        // Network or playback error — last-ditch fallback to browser TTS.
        try {
          speakWithBrowserTts(clean);
        } catch {
          setIsSpeaking(false);
          setVoiceError(
            (err as Error)?.message ?? "Could not play voice response.",
          );
        }
      }
    },
    [stopSpeaking, speakWithBrowserTts],
  );

  const startListening = useCallback(() => {
    if (!speechSupported) {
      setVoiceError(
        "Voice input is not supported in this browser. Try Chrome or Edge.",
      );
      return;
    }
    if (isListening) return;
    setVoiceError(null);

    const Ctor =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;
    const rec = new Ctor();
    rec.lang = navigator.language || "en-US";
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    let finalText = "";

    rec.onresult = (event: any) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalText += transcript;
        } else {
          interim += transcript;
        }
      }
      setInterimTranscript(interim);
    };

    rec.onerror = (event: any) => {
      const code = event?.error ?? "unknown";
      if (code === "no-speech" || code === "aborted") {
        // benign, ignore
      } else if (code === "not-allowed" || code === "service-not-allowed") {
        setVoiceError("Microphone access was denied. Allow it in your browser settings.");
      } else {
        setVoiceError(`Voice input error: ${code}`);
      }
    };

    rec.onend = () => {
      setIsListening(false);
      setInterimTranscript("");
      speechRecognitionRef.current = null;
      const transcript = finalText.trim();
      if (transcript) {
        void sendVoiceMessageRef.current?.(transcript);
      } else if (shouldAutoListenRef.current) {
        // Re-arm listening after a brief pause if nothing was captured
        window.setTimeout(() => {
          if (
            chatModeRef.current === "voice" &&
            shouldAutoListenRef.current &&
            !isSpeaking
          ) {
            startListeningRef.current?.();
          }
        }, 400);
      }
    };

    try {
      rec.start();
      speechRecognitionRef.current = rec;
      setIsListening(true);
    } catch (err) {
      setVoiceError((err as Error)?.message ?? "Could not start listening.");
      setIsListening(false);
    }
  }, [isListening, isSpeaking, speechSupported]);

  // Forward refs so callbacks can call each other without circular deps
  const sendVoiceMessageRef = useRef<((t: string) => Promise<void>) | null>(
    null,
  );
  const startListeningRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    startListeningRef.current = startListening;
  }, [startListening]);

  // Hook the audio element's lifecycle
  useEffect(() => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
    }
    const audio = audioRef.current;
    const handleEnded = () => {
      setIsSpeaking(false);
      if (audioObjectUrlRef.current) {
        URL.revokeObjectURL(audioObjectUrlRef.current);
        audioObjectUrlRef.current = null;
      }
      if (
        chatModeRef.current === "voice" &&
        shouldAutoListenRef.current &&
        !chatEndedWithTicket
      ) {
        startListeningRef.current?.();
      }
    };
    const handleError = () => {
      setIsSpeaking(false);
    };
    audio.addEventListener("ended", handleEnded);
    audio.addEventListener("error", handleError);
    return () => {
      audio.removeEventListener("ended", handleEnded);
      audio.removeEventListener("error", handleError);
    };
  }, [chatEndedWithTicket]);

  // Cleanup voice state when leaving voice mode or unmounting
  useEffect(() => {
    if (chatMode !== "voice") {
      shouldAutoListenRef.current = false;
      stopListening();
      stopSpeaking();
    }
    return () => {
      shouldAutoListenRef.current = false;
    };
  }, [chatMode, stopListening, stopSpeaking]);

  useEffect(() => {
    return () => {
      stopListening();
      stopSpeaking();
    };
  }, [stopListening, stopSpeaking]);

  const streamMessage = useCallback(
    async (convoId: number, content: string, imageDataUrl?: string | null) => {
      const controller = new AbortController();
      abortControllerRef.current?.abort();
      abortControllerRef.current = controller;

      setIsStreaming(true);
      setStreamingUserMessage(null);
      setStreamingAssistantMessage(null);
      setProcessSteps([]);

      try {
        const response = await fetch(
          `/api/chat/conversations/${convoId}/messages/stream`,
          {
            method: "POST",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
            },
            body: JSON.stringify({
              content,
              imageDataUrl: imageDataUrl ?? null,
            }),
            signal: controller.signal,
          },
        );

        if (!response.ok || !response.body) {
          throw new Error(`Stream request failed: ${response.status}`);
        }

        let assistantAccum = "";
        let assistantCitations: StreamingAssistantMessage["citations"] = [];

        for await (const evt of iterSseEvents(response, controller.signal)) {
          let payload: unknown;
          try {
            payload = JSON.parse(evt.data);
          } catch {
            continue;
          }

          if (evt.event === "user") {
            setStreamingUserMessage(payload as StreamingMessage);
          } else if (evt.event === "citations") {
            assistantCitations =
              (payload as StreamingAssistantMessage["citations"]) ?? [];
            setStreamingAssistantMessage((prev) => ({
              id: prev?.id ?? -1,
              conversationId: convoId,
              role: "assistant",
              content: prev?.content ?? "",
              citations: assistantCitations,
              createdAt: prev?.createdAt ?? new Date().toISOString(),
              canAnswer: null,
              done: false,
            }));
          } else if (evt.event === "token") {
            const delta = (payload as { delta?: string }).delta ?? "";
            assistantAccum += delta;
            const snapshot = assistantAccum;
            setStreamingAssistantMessage((prev) => ({
              id: prev?.id ?? -1,
              conversationId: convoId,
              role: "assistant",
              content: snapshot,
              citations: prev?.citations ?? assistantCitations,
              createdAt: prev?.createdAt ?? new Date().toISOString(),
              canAnswer: null,
              done: false,
            }));
          } else if (evt.event === "process") {
            const step = payload as {
              name?: string;
              status?: ProcessStepStatus;
            };
            const name = typeof step.name === "string" ? step.name.trim() : "";
            if (!name) continue;
            const status: ProcessStepStatus =
              step.status === "completed" || step.status === "error"
                ? step.status
                : "started";
            setProcessSteps((prev) => {
              const existing = prev.findIndex((s) => s.name === name);
              if (existing === -1) {
                return [...prev, { name, status }];
              }
              const next = [...prev];
              next[existing] = { name, status };
              return next;
            });
          } else if (evt.event === "done") {
            const final = payload as StreamingMessage & {
              citations?: StreamingAssistantMessage["citations"];
              canAnswer?: boolean | null;
            };
            setStreamingAssistantMessage({
              ...final,
              role: "assistant",
              citations: final.citations ?? assistantCitations,
              done: true,
            });
            setProcessSteps((prev) => {
              const existing = prev.findIndex((s) => s.name === "Completed");
              if (existing === -1) {
                return [...prev, { name: "Completed", status: "completed" }];
              }
              const next = [...prev];
              next[existing] = { name: "Completed", status: "completed" };
              return next;
            });
            if (chatModeRef.current === "voice") {
              const finalContent =
                typeof final.content === "string"
                  ? extractAssistantAnswer(final.content)
                  : assistantAccum
                    ? extractAssistantAnswer(assistantAccum)
                    : "";
              if (finalContent) {
                void speakText(finalContent);
              }
            }
          }
        }

        await queryClient.invalidateQueries({
          queryKey: getGetConversationQueryKey(convoId),
        });
        await queryClient.invalidateQueries({
          queryKey: getListConversationsQueryKey(),
        });
      } catch (err) {
        if ((err as { name?: string })?.name === "AbortError") return;
        setProcessSteps((prev) => [
          ...prev,
          { name: "Streaming failed", status: "error" },
        ]);
        toast({ title: "Failed to send message", variant: "destructive" });
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
        setIsStreaming(false);
        setStreamingUserMessage(null);
        setStreamingAssistantMessage(null);
        setProcessSteps([]);
      }
    },
    [queryClient, toast, speakText],
  );

  const sendVoiceMessage = useCallback(
    async (transcript: string) => {
      const content = transcript.trim();
      if (!content) return;
      if (isStreaming || chatEndedWithTicket) return;
      stopSpeaking();
      let convoId = currentId;
      if (!convoId) {
        try {
          const newConvo = await createConvo.mutateAsync({
            data: { title: content.substring(0, 40) },
          });
          convoId = newConvo.id;
          setLocation(`/app/conversations/${convoId}`);
          queryClient.invalidateQueries({
            queryKey: getListConversationsQueryKey(),
          });
        } catch (err) {
          toast({
            title: "Failed to create conversation",
            variant: "destructive",
          });
          return;
        }
      }
      await streamMessage(convoId, content);
    },
    [
      chatEndedWithTicket,
      createConvo,
      currentId,
      isStreaming,
      queryClient,
      setLocation,
      stopSpeaking,
      streamMessage,
      toast,
    ],
  );

  useEffect(() => {
    sendVoiceMessageRef.current = sendVoiceMessage;
  }, [sendVoiceMessage]);

  const toggleVoiceListening = useCallback(() => {
    if (chatEndedWithTicket) return;
    if (isSpeaking) {
      stopSpeaking();
    }
    if (isListening) {
      stopListening();
    } else {
      shouldAutoListenRef.current = true;
      startListening();
    }
  }, [
    chatEndedWithTicket,
    isListening,
    isSpeaking,
    startListening,
    stopListening,
    stopSpeaking,
  ]);

  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    if (!file) return;
    setImageFile(file);
    const url = URL.createObjectURL(file);
    setImagePreviewUrl(url);
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") {
        setImageDataUrl(result);
      }
    };
    reader.readAsDataURL(file);
    // Reset so the same file can be re-selected
    e.target.value = "";
  };

  const clearImage = () => {
    setImageFile(null);
    setImageDataUrl(null);
    if (imagePreviewUrl) {
      URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!input.trim() && !imageFile) || isStreaming || chatEndedWithTicket)
      return;

    let content = input;
    setInput("");
    const pendingImage = imageFile;
    const pendingImageDataUrl = imageDataUrl;
    clearImage();

    // If an image was attached, describe it first and prepend the description
    if (pendingImage) {
      setIsDescribingImage(true);
      try {
        const formData = new FormData();
        formData.append("file", pendingImage);
        const res = await fetch("/api/chat/image-describe", {
          method: "POST",
          credentials: "include",
          body: formData,
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.detail ?? "Image description failed");
        }
        const { description } = (await res.json()) as { description: string };
        if (description) {
          content = content.trim()
            ? `[Image context: ${description}]\n\n${content.trim()}`
            : `[Image context: ${description}]`;
        }
      } catch (err) {
        toast({
          title: "Image could not be described",
          description: (err as Error).message,
          variant: "destructive",
        });
        return;
      } finally {
        setIsDescribingImage(false);
      }
    }

    if (!content.trim()) return;

    let convoId = currentId;
    if (!convoId) {
      try {
        const newConvo = await createConvo.mutateAsync({
          data: { title: content.substring(0, 40) },
        });
        convoId = newConvo.id;
        setLocation(`/app/conversations/${convoId}`);
        queryClient.invalidateQueries({
          queryKey: getListConversationsQueryKey(),
        });
      } catch (err) {
        toast({
          title: "Failed to create conversation",
          variant: "destructive",
        });
        return;
      }
    }

    await streamMessage(convoId, content, pendingImageDataUrl);
  };

  const handleRate = async (messageId: number, rating: "up" | "down") => {
    if (!currentId) return;
    try {
      await rateMessage.mutateAsync({ id: messageId, data: { rating } });
      queryClient.invalidateQueries({
        queryKey: getGetConversationQueryKey(currentId),
      });
    } catch (err) {
      toast({ title: "Failed to rate message", variant: "destructive" });
    }
  };

  const requestTicketAfterFinalVerdict = useCallback(async () => {
    if (!currentId || isStreaming) return;
    await streamMessage(currentId, TICKET_AFTER_FINAL_VERDICT);
  }, [currentId, isStreaming, streamMessage]);

  const handleConfirmDeleteConversation = () => {
    if (!deleteTarget) return;
    deleteConvo.mutate({ id: deleteTarget.id });
  };

  const handleClearMemory = async () => {
    setIsClearingMemory(true);
    try {
      const res = await fetch("/api/me/memory", {
        method: "DELETE",
        credentials: "include",
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          body?.detail ?? body?.error ?? "Failed to clear memory",
        );
      }

      queryClient.removeQueries({
        predicate: (query) => {
          const [firstKey] = query.queryKey;
          return (
            typeof firstKey === "string" &&
            firstKey.startsWith("/api/chat/conversations")
          );
        },
      });
      queryClient.removeQueries({
        predicate: (query) =>
          Array.isArray(query.queryKey) &&
          query.queryKey[0] === "chat-memory-graph",
      });
      await queryClient.invalidateQueries({
        queryKey: getListConversationsQueryKey(),
      });
      setLocation("/app");
      setClearMemoryOpen(false);
      toast({ title: "Agent memory cleared" });
    } catch {
      toast({
        title: "Failed to clear memory",
        description: "Long-term or local memory could not be fully cleared.",
        variant: "destructive",
      });
    } finally {
      setIsClearingMemory(false);
    }
  };

  return (
    <div className="flex h-full bg-background">
      <AlertDialog
        open={clearMemoryOpen}
        onOpenChange={(open) => {
          if (!isClearingMemory) {
            setClearMemoryOpen(open);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear all saved memory?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes your saved conversations and the long-term memory
              stored for your account, including Mem0. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isClearingMemory}>
              Cancel
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              disabled={isClearingMemory}
              onClick={handleClearMemory}
            >
              {isClearingMemory ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="mr-2 h-4 w-4" />
              )}
              Clear memory
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget ? (
                <>
                  <span className="line-clamp-2 font-medium text-foreground">
                    {deleteTarget.title}
                  </span>{" "}
                  will be removed permanently. This cannot be undone.
                </>
              ) : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteConvo.isPending}>
              Cancel
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              disabled={deleteConvo.isPending}
              onClick={handleConfirmDeleteConversation}
            >
              {deleteConvo.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : null}
              Delete
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* Secondary Sidebar */}
      <div
        className={`border-r border-border bg-muted/20 h-full overflow-hidden transition-all duration-200 ${
          isConversationsOpen ? "w-80" : "w-0 border-r-0"
        }`}
      >
        <div className="w-80 h-full flex flex-col bg-gradient-to-b from-muted/40 via-muted/25 to-background">
          <div className="p-3 border-b border-border/80">
            <div className="flex flex-col gap-2">
              <Button
                className={cn(
                  "w-full justify-start gap-2 rounded-xl h-11 font-medium shadow-sm",
                  currentId
                    ? "border border-border/60 bg-background hover:bg-background/90"
                    : null,
                )}
                variant={!currentId ? "default" : "outline"}
                onClick={() => setLocation("/app")}
              >
                <MessageSquarePlus className="h-4 w-4 shrink-0" />
                New conversation
              </Button>
              <Button
                type="button"
                variant="outline"
                className="w-full justify-start gap-2 rounded-xl h-10 border-border/60 bg-background/80 hover:bg-destructive/5 hover:text-destructive"
                onClick={() => setClearMemoryOpen(true)}
                disabled={isClearingMemory}
              >
                <Trash2 className="h-4 w-4 shrink-0" />
                Clear saved memory
              </Button>
            </div>
          </div>

          {activeTickets.length > 0 && (
            <div className="mx-3 mt-3 mb-1 rounded-xl border border-primary/15 bg-primary/[0.06] px-3 py-2.5">
              <h3 className="text-[11px] font-semibold text-primary uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <AlertCircle className="h-3 w-3 shrink-0" />
                Open tickets
                <span className="ml-auto rounded-full bg-primary/15 px-1.5 py-0 text-[10px] tabular-nums font-bold">
                  {activeTickets.length}
                </span>
              </h3>
              <div className="flex flex-col gap-0.5">
                {activeTickets.slice(0, 3).map((ticket) => (
                  <Button
                    key={ticket.id}
                    variant="ghost"
                    className="w-full justify-start gap-2 h-8 px-2 rounded-lg text-xs text-foreground/85 hover:bg-background/80 hover:text-foreground"
                    onClick={() => setLocation(`/app/tickets/${ticket.id}`)}
                  >
                    <Ticket className="h-3 w-3 shrink-0 text-primary/70" />
                    <span className="truncate font-medium">
                      {ticket.subject}
                    </span>
                  </Button>
                ))}
              </div>
            </div>
          )}

          <ScrollArea className="flex-1">
            <div className="flex flex-col gap-2 px-3 py-3">
              <div className="flex items-center gap-2 rounded-xl border border-border/45 bg-background/55 px-2.5 py-2 backdrop-blur-sm">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-muted/60 ring-1 ring-border/35">
                  <History className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                    Recent
                  </span>
                  <p className="truncate text-[10px] text-muted-foreground/75">
                    Pick up where you left off
                  </p>
                </div>
                {!loadingConvos && conversations && conversations.length > 0 ? (
                  <span className="shrink-0 rounded-full bg-muted/70 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-foreground ring-1 ring-border/35">
                    {conversations.length}
                  </span>
                ) : null}
              </div>

              {loadingConvos ? (
                <div className="flex flex-col gap-1.5">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div
                      key={i}
                      className="rounded-xl border border-border/35 bg-background/45 p-2.5 space-y-2"
                    >
                      <div className="flex gap-2">
                        <Skeleton className="h-7 w-7 shrink-0 rounded-lg" />
                        <div className="flex-1 space-y-1.5">
                          <Skeleton className="h-3 w-[82%] rounded-md" />
                          <Skeleton className="h-2.5 w-full rounded-md" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : conversations?.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-border/65 bg-muted/15 px-4 py-9 text-center">
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-muted/70 to-muted/40 shadow-inner ring-1 ring-border/30">
                    <MessageSquare className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <p className="text-sm font-semibold text-foreground">
                    No conversations yet
                  </p>
                  <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed px-1">
                    Start a new thread — your history will show up here.
                  </p>
                </div>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {conversations?.map((convo) => {
                    const active = currentId === convo.id;
                    let timeLabel = "";
                    try {
                      timeLabel = formatDistanceToNow(
                        new Date(convo.updatedAt),
                        { addSuffix: true },
                      );
                    } catch {
                      timeLabel = "";
                    }
                    return (
                      <div
                        key={convo.id}
                        className={cn(
                          "group relative w-full min-w-0 max-w-full rounded-xl border font-normal whitespace-normal",
                          "transition-[transform,background-color,border-color,box-shadow] duration-200 ease-out",
                          active
                            ? "border-primary/25 bg-background shadow-[0_6px_20px_-16px_rgba(15,23,42,0.35)] ring-1 ring-primary/10 dark:shadow-none"
                            : "border-transparent bg-transparent hover:border-border/45 hover:bg-background/55",
                        )}
                      >
                        <button
                          type="button"
                          className={cn(
                            "flex min-w-0 w-full gap-2 overflow-hidden rounded-xl px-2.5 py-2 pr-9 text-left outline-none transition-colors duration-200",
                            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                            active ? "" : "hover:bg-transparent",
                          )}
                          onClick={() =>
                            setLocation(`/app/conversations/${convo.id}`)
                          }
                        >
                          <div
                            className={cn(
                              "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1 transition-colors duration-200",
                              active
                                ? "bg-primary/12 text-primary ring-primary/20"
                                : "bg-muted/50 text-muted-foreground ring-border/30 group-hover:bg-muted/80 group-hover:text-foreground/80",
                            )}
                          >
                            <MessageSquare className="h-3.5 w-3.5" />
                          </div>
                          <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                            <div className="flex min-w-0 w-full items-start justify-between gap-2">
                              <span
                                className={cn(
                                  "min-w-0 flex-1 text-[13px] font-semibold leading-tight tracking-tight line-clamp-1",
                                  active
                                    ? "text-foreground"
                                    : "text-foreground/92",
                                )}
                              >
                                {convo.title || "New conversation"}
                              </span>
                              <div className="flex shrink-0 items-center gap-0.5">
                                {timeLabel ? (
                                  <span className="max-w-[4.75rem] truncate pt-px text-[10px] font-medium tabular-nums text-muted-foreground">
                                    {timeLabel}
                                  </span>
                                ) : null}
                                <ChevronRight
                                  className={cn(
                                    "h-3.5 w-3.5 shrink-0 text-muted-foreground/40 transition-opacity duration-200",
                                    active
                                      ? "opacity-70"
                                      : "opacity-0 group-hover:opacity-60",
                                  )}
                                  aria-hidden
                                />
                              </div>
                            </div>
                            {convo.lastMessagePreview ? (
                              <p className="text-[11px] leading-tight text-muted-foreground line-clamp-1">
                                {convo.lastMessagePreview}
                              </p>
                            ) : (
                              <p className="text-[11px] text-muted-foreground/65 italic leading-tight">
                                No preview yet
                              </p>
                            )}
                          </div>
                        </button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          disabled={deleteConvo.isPending}
                          className={cn(
                            "absolute right-1.5 top-1.5 z-[1] h-7 w-7 shrink-0 rounded-lg",
                            "text-muted-foreground/55 hover:bg-destructive/10 hover:text-destructive",
                            "opacity-0 pointer-events-none transition-opacity duration-200 group-hover:opacity-100 group-hover:pointer-events-auto",
                            "focus-visible:opacity-100 focus-visible:pointer-events-auto",
                          )}
                          aria-label={`Delete conversation: ${convo.title || "New conversation"}`}
                          title="Delete conversation"
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setDeleteTarget({
                              id: convo.id,
                              title:
                                (convo.title || "New conversation").trim() ||
                                "New conversation",
                            });
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col h-full bg-background relative">
        <div className="border-b border-border/70 bg-background/90 backdrop-blur-sm">
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-3 flex items-center justify-between gap-3">
            <div className="min-w-0 flex items-start gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 shrink-0"
                onClick={() => setIsConversationsOpen((open) => !open)}
                aria-label={
                  isConversationsOpen
                    ? "Collapse conversation list"
                    : "Expand conversation list"
                }
                title={
                  isConversationsOpen
                    ? "Collapse conversation list"
                    : "Expand conversation list"
                }
              >
                {isConversationsOpen ? (
                  <PanelLeftClose className="h-4 w-4" />
                ) : (
                  <PanelLeftOpen className="h-4 w-4" />
                )}
              </Button>
              <div className="min-w-0">
                <h2 className="text-sm font-semibold truncate">
                  {activeConvo?.conversation?.title || "Conversation"}
                </h2>
                <p className="text-xs text-muted-foreground">
                  Visualize user memory and related concepts for this thread.
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setMemoryGraphOpen(true)}
              disabled={!currentId}
              className="shrink-0"
            >
              <Share2 className="h-4 w-4 mr-2" />
              Memory Graph
            </Button>
          </div>
        </div>

        {!currentId && (
          <div className="absolute inset-0 flex flex-col items-center justify-center p-8 text-center pointer-events-none">
            <div className="w-16 h-16 bg-primary/10 rounded-2xl flex items-center justify-center mb-6">
              <Bot className="h-8 w-8 text-primary" />
            </div>
            <h2 className="text-2xl font-semibold mb-2">
              Tell me what is going wrong
            </h2>
            <p className="text-muted-foreground max-w-md">
              I will investigate the issue, ask only the follow-up questions
              that matter, and answer from verified documentation when I have
              enough evidence.
            </p>
            <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4 max-w-2xl w-full pointer-events-auto">
              {[
                "How do I reset my password?",
                "What are the API rate limits?",
                "How to integrate with Salesforce?",
                "Where is the billing dashboard?",
              ].map((q) => (
                <Button
                  key={q}
                  variant="outline"
                  className="h-auto py-3 justify-start text-left text-sm font-normal text-muted-foreground hover:text-foreground"
                  onClick={() => {
                    setInput(q);
                    // document.getElementById('chat-input')?.focus();
                  }}
                >
                  {q}
                </Button>
              ))}
            </div>
          </div>
        )}

        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-4 md:p-8 space-y-6"
        >
          {currentId && loadingActive && !activeConvo && (
            <div className="flex justify-center items-center h-full">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {activeConvo?.messages.map((msg, i) => (
            <div
              key={msg.id}
              className={`flex gap-4 max-w-4xl mx-auto ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {msg.role === "assistant" && (
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-1">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
              )}

              <div
                className={`flex flex-col gap-2 max-w-[80%] ${msg.role === "user" ? "items-end" : "items-start"}`}
              >
                {msg.role === "assistant" &&
                  messageKindOf(
                    msg as { kind?: string | null; canAnswer?: boolean | null },
                  ) !== "answer" && (
                    <Badge
                      variant="secondary"
                      className="text-[11px] font-medium"
                    >
                      {messageKindOf(
                        msg as {
                          kind?: string | null;
                          canAnswer?: boolean | null;
                        },
                      ) === "clarification_question"
                        ? "Investigating"
                        : messageKindOf(
                              msg as {
                                kind?: string | null;
                                canAnswer?: boolean | null;
                              },
                            ) === "ticket_created"
                          ? "Ticket created"
                          : "Escalation option"}
                    </Badge>
                  )}
                {msg.role === "assistant" ? (
                  <div className="w-full space-y-3">
                    {splitAssistantSegments(msg.content).map(
                      (segment, segmentIdx, segments) => {
                        const rewritten =
                          (
                            msg as { rewrittenQuery?: string | null }
                          ).rewrittenQuery?.trim() ?? "";
                        const citeList = msg.citations ?? [];
                        const showSourcesAccordion =
                          segmentIdx === segments.length - 1 &&
                          (rewritten.length > 0 || citeList.length > 0);

                        return (
                          <motion.div
                            key={`${msg.id}-segment-${segmentIdx}`}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{
                              duration: 0.22,
                              delay: segmentIdx * 0.08,
                              ease: "easeOut",
                            }}
                            className={ASSISTANT_BUBBLE_CLASS}
                          >
                            {renderAssistantContent(segment)}
                            {showSourcesAccordion && (
                              <Accordion
                                type="single"
                                collapsible
                                className="mt-3 w-full border-t border-border/40 pt-1"
                              >
                                <AccordionItem
                                  value={`sources-${msg.id}`}
                                  className="border-0"
                                >
                                  <AccordionTrigger className="group py-2 text-left text-muted-foreground hover:no-underline hover:text-foreground [&>svg]:h-3.5 [&>svg]:w-3.5 [&>svg]:text-muted-foreground/60">
                                    <div className="flex min-w-0 flex-1 items-center gap-2">
                                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-muted-foreground/70 transition-colors group-hover:text-primary/80">
                                        {rewritten.length > 0 ? (
                                          <Search className="h-3.5 w-3.5" />
                                        ) : (
                                          <BookOpen className="h-3.5 w-3.5" />
                                        )}
                                      </span>
                                      <span className="min-w-0 flex-1 truncate">
                                        <span className="text-xs font-medium">
                                          {rewritten.length > 0 &&
                                          citeList.length > 0
                                            ? "Sources & query"
                                            : rewritten.length > 0
                                              ? "Search query"
                                              : "Sources"}
                                        </span>
                                        <span className="mx-1.5 text-muted-foreground/45">
                                          -
                                        </span>
                                        <span className="text-[11px] font-normal text-muted-foreground/75">
                                          {rewritten.length > 0 &&
                                          citeList.length > 0
                                            ? `${citeList.length} source${citeList.length === 1 ? "" : "s"} matched`
                                            : rewritten.length > 0
                                              ? "View rewritten query"
                                              : `${citeList.length} source${citeList.length === 1 ? "" : "s"}`}
                                        </span>
                                      </span>
                                    </div>
                                  </AccordionTrigger>
                                  <AccordionContent className="pb-1 pt-1">
                                    <div className="space-y-3">
                                      {rewritten.length > 0 && (
                                        <div className="rounded-xl border border-border/60 bg-background/45 px-3 py-2.5">
                                          <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-primary/75">
                                            Searching for
                                          </p>
                                          <p className="text-xs leading-relaxed text-foreground/85">
                                            {rewritten}
                                          </p>
                                        </div>
                                      )}
                                      {citeList.length > 0 && (
                                        <div className="rounded-2xl border border-slate-200/70 bg-white/45 p-2.5 shadow-inner shadow-slate-950/[0.03] ring-1 ring-white/70 dark:border-white/10 dark:bg-white/[0.04] dark:ring-white/10">
                                          <div className="mb-2 flex items-center justify-between gap-3 px-1">
                                            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/75">
                                              Referenced sources
                                            </p>
                                            <span className="h-px flex-1 bg-gradient-to-r from-border/70 to-transparent" />
                                          </div>
                                          <div className="flex flex-wrap gap-2">
                                            {citeList.map((cite, idx) => (
                                              <Dialog key={idx}>
                                                <DialogTrigger asChild>
                                                  <button
                                                    type="button"
                                                    className="group inline-flex h-9 max-w-full cursor-pointer items-center gap-2 whitespace-nowrap rounded-full border border-slate-200/80 bg-background/85 py-1 pl-1.5 pr-3 text-xs font-medium text-foreground/80 shadow-sm shadow-slate-950/[0.03] ring-1 ring-white/60 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/35 hover:bg-primary/[0.08] hover:text-foreground hover:shadow-md hover:shadow-primary/10 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 dark:border-white/10 dark:bg-white/[0.06] dark:ring-white/10 dark:hover:bg-primary/15"
                                                  >
                                                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary ring-1 ring-primary/15 transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                                                      {idx + 1}
                                                    </span>
                                                    <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60 transition-colors group-hover:text-primary" />
                                                    <span className="min-w-0 max-w-[13rem] truncate">
                                                      {cite.documentName}
                                                    </span>
                                                    <span className="hidden rounded-full bg-muted/70 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground/80 sm:inline-flex">
                                                      {cite.score.toFixed(2)}
                                                    </span>
                                                  </button>
                                                </DialogTrigger>
                                                <DialogContent className="max-h-[90vh] w-[min(46rem,calc(100vw-2rem))] overflow-hidden rounded-3xl border-border/70 bg-background p-0 shadow-2xl shadow-slate-950/10 sm:max-w-[46rem] dark:shadow-black/35">
                                                  <DialogHeader className="relative overflow-hidden border-b border-border/60 bg-gradient-to-br from-muted/50 via-background/90 to-primary/[0.06] px-5 pb-4 pt-5 pr-12 text-left sm:px-6 sm:pt-6">
                                                    <div
                                                      className="pointer-events-none absolute right-0 top-0 h-28 w-28 translate-x-8 -translate-y-10 rounded-full bg-primary/10 blur-2xl"
                                                      aria-hidden
                                                    />
                                                    <div className="relative flex items-start gap-3">
                                                      <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15">
                                                        <FileText className="h-4 w-4" />
                                                      </span>
                                                      <div className="min-w-0 flex-1">
                                                        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                                                          Source excerpt
                                                        </p>
                                                        <DialogTitle className="mt-1 break-words text-base font-semibold leading-snug text-foreground">
                                                          {cite.documentName}
                                                        </DialogTitle>
                                                        <DialogDescription className="mt-1 text-xs text-muted-foreground">
                                                          Relevance score{" "}
                                                          {cite.score.toFixed(
                                                            2,
                                                          )}
                                                        </DialogDescription>
                                                      </div>
                                                    </div>
                                                  </DialogHeader>
                                                  <div className="max-h-[calc(90vh-8.5rem)] overflow-y-auto scroll-smooth bg-gradient-to-b from-background/40 to-muted/20 p-4 sm:p-6">
                                                    <article className="rounded-2xl border border-border/60 bg-background/85 p-4 shadow-inner shadow-slate-950/[0.03] ring-1 ring-white/50 sm:p-5 dark:ring-white/5">
                                                      <div className="mb-3 flex items-center justify-between gap-3">
                                                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-primary/80">
                                                          Matched passage
                                                        </p>
                                                        <span className="h-px flex-1 bg-gradient-to-r from-border/80 to-transparent" />
                                                      </div>
                                                      <p className="whitespace-pre-wrap break-words text-sm leading-7 text-foreground/90 sm:text-[15px]">
                                                        {cite.snippet}
                                                      </p>
                                                    </article>

                                                    <div className="mt-5 space-y-3">
                                                      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                                                        Metadata
                                                      </p>
                                                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                                        {citationMetadataItems(
                                                          cite,
                                                        ).map((item) => (
                                                          <div
                                                            key={item.label}
                                                            className="min-w-0 rounded-xl border border-border/55 bg-background/70 px-3 py-2"
                                                          >
                                                            <p className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground/80">
                                                              {item.label}
                                                            </p>
                                                            <p
                                                              className="mt-0.5 truncate text-xs font-medium text-foreground"
                                                              title={item.value}
                                                            >
                                                              {item.value}
                                                            </p>
                                                          </div>
                                                        ))}
                                                      </div>
                                                      {cite.metadata
                                                        ?.keyPhrases &&
                                                      cite.metadata.keyPhrases
                                                        .length > 0 ? (
                                                        <div className="rounded-xl border border-border/55 bg-background/70 px-3 py-2.5">
                                                          <p className="mb-2 text-[9px] font-semibold uppercase tracking-wide text-muted-foreground/80">
                                                            Key phrases
                                                          </p>
                                                          <div className="flex flex-wrap gap-1.5">
                                                            {cite.metadata.keyPhrases
                                                              .slice(0, 8)
                                                              .map((phrase) => (
                                                                <Badge
                                                                  key={phrase}
                                                                  variant="secondary"
                                                                  className="rounded-full px-2 py-0 text-[10px] font-medium"
                                                                >
                                                                  {phrase}
                                                                </Badge>
                                                              ))}
                                                          </div>
                                                        </div>
                                                      ) : null}
                                                    </div>
                                                  </div>
                                                </DialogContent>
                                              </Dialog>
                                            ))}
                                          </div>
                                        </div>
                                      )}
                                    </div>
                                  </AccordionContent>
                                </AccordionItem>
                              </Accordion>
                            )}
                          </motion.div>
                        );
                      },
                    )}
                  </div>
                ) : (
                  <div className="flex flex-col items-end gap-2">
                    {(msg as { imageDataUrl?: string | null }).imageDataUrl && (
                      <img
                        src={
                          (msg as { imageDataUrl?: string | null })
                            .imageDataUrl as string
                        }
                        alt="Attached image"
                        className="max-h-56 max-w-xs rounded-2xl border border-border object-cover shadow-sm"
                      />
                    )}
                    {stripImageContext(msg.content) && (
                      <div className="px-4 py-3 rounded-2xl rounded-tr-sm text-sm bg-primary text-primary-foreground shadow-sm">
                        <div className="whitespace-pre-wrap leading-relaxed">
                          {stripImageContext(msg.content)}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {msg.role === "assistant" &&
                  messageKindOf(
                    msg as { kind?: string | null; canAnswer?: boolean | null },
                  ) === "answer" &&
                  !(msg as { finalVerdict?: boolean }).finalVerdict && (
                    <div className="flex w-full justify-end gap-2 mt-1">
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className={`h-6 w-6 rounded-full ${msg.rating === "up" ? "text-green-600 bg-green-50 dark:bg-green-900/20" : "text-muted-foreground"}`}
                          onClick={() => handleRate(msg.id, "up")}
                        >
                          <ThumbsUp className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className={`h-6 w-6 rounded-full ${msg.rating === "down" ? "text-destructive bg-destructive/10" : "text-muted-foreground"}`}
                          onClick={() => handleRate(msg.id, "down")}
                        >
                          <ThumbsDown className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                  )}

                {msg.role === "assistant" &&
                  messageKindOf(
                    msg as { kind?: string | null; canAnswer?: boolean | null },
                  ) === "answer" &&
                  (msg as { finalVerdict?: boolean }).finalVerdict && (
                    <motion.div
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.22, ease: "easeOut" }}
                      className="mt-3 w-full max-w-md rounded-2xl border border-slate-200/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(248,250,252,0.92))] px-4 py-4 shadow-[0_8px_24px_rgba(15,23,42,0.06)] ring-1 ring-white/60 backdrop-blur-sm"
                    >
                      <p className="text-sm font-semibold text-foreground tracking-tight">
                        Did this resolve your issue?
                      </p>
                      <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                        Quick feedback helps us improve these answers. If you
                        are still stuck, we can open a ticket for a human
                        teammate.
                      </p>
                      <div className="flex flex-wrap items-center gap-2 mt-3">
                        <Button
                          type="button"
                          variant={msg.rating === "up" ? "default" : "outline"}
                          size="sm"
                          className="gap-1.5 rounded-full h-9"
                          disabled={rateMessage.isPending}
                          onClick={() => handleRate(msg.id, "up")}
                        >
                          <ThumbsUp className="h-3.5 w-3.5" />
                          Helpful
                        </Button>
                        <Button
                          type="button"
                          variant={
                            msg.rating === "down" ? "destructive" : "outline"
                          }
                          size="sm"
                          className="gap-1.5 rounded-full h-9"
                          disabled={rateMessage.isPending}
                          onClick={() => handleRate(msg.id, "down")}
                        >
                          <ThumbsDown className="h-3.5 w-3.5" />
                          Not resolved
                        </Button>
                      </div>
                      <div className="mt-3.5 pt-3 border-t border-border/60">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="w-full sm:w-auto gap-2 rounded-xl"
                          disabled={isStreaming}
                          onClick={() => {
                            void requestTicketAfterFinalVerdict();
                          }}
                        >
                          <Ticket className="h-3.5 w-3.5 shrink-0" />
                          Open a support ticket
                        </Button>
                      </div>
                    </motion.div>
                  )}

                {msg.role === "assistant" &&
                  messageKindOf(
                    msg as { kind?: string | null; canAnswer?: boolean | null },
                  ) === "ticket_created" &&
                  createdTicketIdOf(
                    msg as { ticketId?: number | null; content: string },
                  ) && (
                    <div className="mt-2 bg-secondary/50 border border-secondary p-3 rounded-lg flex items-center justify-between w-full">
                      <span className="text-sm text-foreground/80">
                        Your escalation ticket is open and ready for follow-up.
                      </span>
                      <Button
                        size="sm"
                        onClick={() =>
                          setLocation(
                            `/app/tickets/${createdTicketIdOf(msg as { ticketId?: number | null; content: string })}`,
                          )
                        }
                      >
                        Open Ticket
                      </Button>
                    </div>
                  )}
              </div>
            </div>
          ))}

          {streamingUserMessage && (
            <div className="flex gap-4 max-w-4xl mx-auto justify-end">
              <div className="flex flex-col gap-2 max-w-[80%] items-end">
                {streamingUserMessage.imageDataUrl && (
                  <img
                    src={streamingUserMessage.imageDataUrl}
                    alt="Attached image"
                    className="max-h-56 max-w-xs rounded-2xl border border-border object-cover shadow-sm"
                  />
                )}
                {stripImageContext(streamingUserMessage.content) && (
                  <div className="px-4 py-3 rounded-2xl rounded-tr-sm text-sm bg-primary text-primary-foreground shadow-sm">
                    <div className="whitespace-pre-wrap leading-relaxed">
                      {stripImageContext(streamingUserMessage.content)}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {streamingAssistantMessage &&
            streamingAssistantMessage.content.length > 0 && (
              <div className="flex gap-4 max-w-4xl mx-auto justify-start">
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-1">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
                <div className="flex flex-col gap-2 max-w-[80%] items-start">
                  <div className="w-full space-y-3">
                    {splitAssistantSegments(
                      streamingAssistantMessage.content,
                    ).map((segment, segmentIdx, segments) => (
                      <motion.div
                        key={`stream-segment-${segmentIdx}`}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{
                          duration: 0.22,
                          delay: segmentIdx * 0.08,
                          ease: "easeOut",
                        }}
                        className={ASSISTANT_BUBBLE_CLASS}
                      >
                        {renderAssistantContent(segment)}
                        {segmentIdx === segments.length - 1 && (
                          <span className="inline-block w-2 h-4 ml-0.5 align-middle bg-primary/60 animate-pulse rounded-sm" />
                        )}
                      </motion.div>
                    ))}
                  </div>
                  {streamingAssistantMessage.citations.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {streamingAssistantMessage.citations.map((cite, idx) => (
                        <Badge
                          key={`stream-cite-${idx}`}
                          variant="outline"
                          className="text-xs py-0 h-6 font-normal text-muted-foreground border-border/70 bg-background/80"
                        >
                          [{idx + 1}] {cite.documentName}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

          {isStreaming && activeProcessStep && activeProcessPresentation && (
            <div className="max-w-4xl mx-auto">
              <output
                className={cn(
                  "flex items-center gap-3 rounded-xl border border-border/50 bg-card/80 px-4 py-3 shadow-sm backdrop-blur-md",
                  "ring-1 ring-black/[0.03] dark:ring-white/[0.06]",
                )}
                aria-live="polite"
                aria-label={activeProcessPresentation.ariaLabel}
              >
                <div
                  className={cn(
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted/50 shadow-inner ring-1 ring-border/40",
                    activeProcessStep.status === "error" &&
                      "bg-destructive/10 ring-destructive/20",
                  )}
                >
                  {activeProcessStep.status === "completed" ? (
                    <CheckCircle2
                      className="h-4 w-4 text-emerald-600 dark:text-emerald-500"
                      aria-hidden
                    />
                  ) : activeProcessStep.status === "error" ? (
                    <AlertTriangle
                      className="h-4 w-4 text-destructive"
                      aria-hidden
                    />
                  ) : (
                    <Loader2
                      className="h-4 w-4 animate-spin text-primary"
                      aria-hidden
                    />
                  )}
                </div>
                <div className="min-w-0 flex-1 overflow-hidden">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.div
                      key={`${activeProcessStep.name}-${activeProcessStep.status}`}
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -4 }}
                      transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                      className="space-y-0.5"
                    >
                      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                        {activeProcessPresentation.caption}
                      </p>
                      <p className="truncate text-sm font-medium leading-snug text-foreground">
                        {activeProcessPresentation.label}
                      </p>
                    </motion.div>
                  </AnimatePresence>
                </div>
              </output>
            </div>
          )}

          {isStreaming &&
            (!streamingAssistantMessage ||
              streamingAssistantMessage.content.length === 0) && (
              <div className="flex gap-4 max-w-4xl mx-auto justify-start">
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-1">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
                <div className="p-4 rounded-2xl bg-muted/50 border border-border rounded-tl-sm flex items-center gap-1">
                  <div
                    className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce"
                    style={{ animationDelay: "0ms" }}
                  />
                  <div
                    className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce"
                    style={{ animationDelay: "150ms" }}
                  />
                  <div
                    className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce"
                    style={{ animationDelay: "300ms" }}
                  />
                </div>
              </div>
            )}
        </div>

        <div className="border-t border-border/80 bg-background/95 px-3 py-2 md:px-8 md:py-2.5 backdrop-blur-sm">
          {chatEndedWithTicket && currentId && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="max-w-4xl mx-auto mb-2 rounded-xl border border-border/60 bg-muted/25 px-3 py-2 shadow-sm ring-1 ring-black/[0.02] dark:ring-white/[0.04]"
            >
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                <div className="flex min-w-0 items-start gap-2">
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/15">
                    <Ticket className="h-3.5 w-3.5" aria-hidden />
                  </div>
                  <div className="min-w-0 space-y-0.5">
                    <p className="text-xs font-semibold leading-tight text-foreground">
                      Rate this conversation
                    </p>
                    <p className="text-[11px] leading-snug text-muted-foreground">
                      Ticket created — chat closed. How did we do?
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                  <div
                    className="flex items-center gap-0"
                    role="group"
                    aria-label="Conversation rating 1 to 5 stars"
                    onMouseLeave={() => setStarRowHover(null)}
                  >
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        type="button"
                        disabled={
                          conversationFeedbackDone || rateMessage.isPending
                        }
                        className={cn(
                          "rounded-md p-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                          !conversationFeedbackDone && "hover:bg-background/80",
                          (conversationFeedbackDone || rateMessage.isPending) &&
                            "cursor-default opacity-70",
                        )}
                        title={`${n} of 5`}
                        onClick={() => {
                          if (!conversationFeedbackDone) {
                            setConversationStarDraft(n);
                          }
                        }}
                        onMouseEnter={() => {
                          if (!conversationFeedbackDone) {
                            setStarRowHover(n);
                          }
                        }}
                      >
                        <Star
                          className={cn(
                            "h-[1.125rem] w-[1.125rem] transition-colors",
                            starHighlight != null && n <= starHighlight
                              ? "fill-amber-400 text-amber-500"
                              : "text-muted-foreground/40",
                          )}
                        />
                      </button>
                    ))}
                  </div>
                  {!conversationFeedbackDone ? (
                    <Button
                      type="button"
                      size="sm"
                      className="h-7 gap-1 rounded-md px-2.5 text-[11px] font-medium"
                      disabled={
                        conversationStarDraft == null || rateMessage.isPending
                      }
                      onClick={() => {
                        void submitConversationStarRating();
                      }}
                    >
                      {rateMessage.isPending ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : null}
                      Submit
                    </Button>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-500">
                      <CheckCircle2 className="h-3 w-3 shrink-0" />
                      Saved
                    </span>
                  )}
                </div>
              </div>
            </motion.div>
          )}
          {chatEndedWithTicket ? (
            <div className="max-w-4xl mx-auto flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-border/60 bg-muted/15 py-1.5 px-2 text-[11px] text-muted-foreground">
              <Ticket className="h-3 w-3 shrink-0 opacity-70" aria-hidden />
              <span>
                Replies are disabled — continue in your support ticket.
              </span>
            </div>
          ) : (
            <div className="max-w-4xl mx-auto flex flex-col gap-2">
              <div
                role="tablist"
                aria-label="Chat input mode"
                className="mx-auto inline-flex items-center gap-1 rounded-full border border-border/70 bg-muted/40 p-1 text-xs shadow-sm backdrop-blur-sm"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={chatMode === "text"}
                  onClick={() => setChatMode("text")}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-medium transition-colors",
                    chatMode === "text"
                      ? "bg-background text-foreground shadow-sm ring-1 ring-border/80"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Keyboard className="h-3.5 w-3.5" />
                  Text chat
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={chatMode === "voice"}
                  onClick={() => setChatMode("voice")}
                  disabled={!speechSupported}
                  title={
                    speechSupported
                      ? "Voice chat"
                      : "Voice chat not supported in this browser"
                  }
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                    chatMode === "voice"
                      ? "bg-background text-foreground shadow-sm ring-1 ring-border/80"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Mic className="h-3.5 w-3.5" />
                  Voice chat
                </button>
              </div>

              {chatMode === "text" ? (
                <form
                  onSubmit={handleSubmit}
                  className="flex flex-col gap-2"
                >
                  {imagePreviewUrl && (
                    <div className="relative inline-flex self-start">
                      <img
                        src={imagePreviewUrl}
                        alt="Attached image preview"
                        className="h-20 w-auto max-w-[160px] rounded-lg border border-border object-cover shadow-sm"
                      />
                      <button
                        type="button"
                        onClick={clearImage}
                        className="absolute -top-2 -right-2 h-5 w-5 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center shadow"
                        aria-label="Remove image"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                  <div className="relative flex items-center">
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="image/png,image/jpeg,image/webp,image/gif"
                      className="hidden"
                      onChange={handleImageChange}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      disabled={isStreaming || isDescribingImage}
                      className="absolute left-2 h-8 w-8 rounded-lg text-muted-foreground hover:text-foreground"
                      aria-label="Attach image"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      {isDescribingImage ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Paperclip className="h-4 w-4" />
                      )}
                    </Button>
                    <Input
                      id="chat-input"
                      ref={chatInputRef}
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      placeholder="Describe the issue. I will investigate and ask follow-ups only when needed..."
                      className="pl-11 pr-12 py-6 text-base rounded-xl shadow-sm bg-background border-input"
                      autoComplete="off"
                      disabled={isStreaming || isDescribingImage}
                    />
                    <Button
                      type="submit"
                      size="icon"
                      disabled={
                        (!input.trim() && !imageFile) ||
                        isStreaming ||
                        isDescribingImage ||
                        createConvo.isPending
                      }
                      className="absolute right-2 h-10 w-10 rounded-lg"
                    >
                      <Send className="h-4 w-4" />
                    </Button>
                  </div>
                </form>
              ) : (
                <VoiceChatPanel
                  isListening={isListening}
                  isSpeaking={isSpeaking}
                  isStreaming={isStreaming}
                  interimTranscript={interimTranscript}
                  voiceError={voiceError}
                  speechSupported={speechSupported}
                  onToggleMic={toggleVoiceListening}
                  onStopSpeaking={stopSpeaking}
                />
              )}
            </div>
          )}
          <p className="max-w-4xl mx-auto mt-1.5 text-center text-[10px] leading-tight text-muted-foreground/90">
            Helia AI can make mistakes. Verify important information.
          </p>
        </div>

        <Dialog open={memoryGraphOpen} onOpenChange={setMemoryGraphOpen}>
          <DialogContent className="max-w-5xl w-[95vw] p-0 overflow-hidden">
            <DialogHeader className="px-6 pt-6 pb-3">
              <DialogTitle>Memory Graph</DialogTitle>
              <DialogDescription>
                Connected view of what Helia remembers for this user and
                conversation context.
              </DialogDescription>
            </DialogHeader>

            <div className="px-6 pb-6">
              <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline">
                  Nodes: {memoryGraph.data?.nodes.length ?? 0}
                </Badge>
                <Badge variant="outline">
                  Edges: {memoryGraph.data?.edges.length ?? 0}
                </Badge>
                {memoryGraph.data?.query && (
                  <Badge variant="outline" className="max-w-full truncate">
                    Query seed: {memoryGraph.data.query}
                  </Badge>
                )}
              </div>

              {memoryGraph.isLoading ? (
                <div className="h-[430px] rounded-xl border border-border/60 bg-muted/15 backdrop-blur-sm flex items-center justify-center gap-2 text-sm text-muted-foreground shadow-inner">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading memory
                  graph...
                </div>
              ) : memoryGraph.isError ? (
                <div className="h-[430px] rounded-xl border border-destructive/25 bg-destructive/[0.06] flex items-center justify-center text-sm text-destructive">
                  Could not load memory graph.
                </div>
              ) : (memoryGraph.data?.nodes.length ?? 0) <= 1 ? (
                <div className="h-[430px] rounded-xl border border-dashed border-border/70 bg-muted/10 backdrop-blur-sm flex flex-col items-center justify-center gap-1 px-6 text-center text-sm text-muted-foreground">
                  <span>No user memory found yet.</span>
                  <span className="text-xs text-muted-foreground/80">
                    Continue chatting to build memory.
                  </span>
                </div>
              ) : (
                <div className="overflow-x-auto rounded-xl border border-border/50 bg-muted/20 shadow-[inset_0_1px_0_0_hsl(var(--border)_/_0.35)] dark:bg-muted/10 dark:shadow-[inset_0_1px_0_0_hsl(var(--border)_/_0.25)]">
                  <div
                    className="relative isolate h-[430px] min-w-[980px] bg-[radial-gradient(ellipse_75%_55%_at_50%_28%,hsl(var(--primary)/0.07)_0%,transparent_60%),radial-gradient(hsl(var(--muted-foreground)/0.09)_1px,transparent_1px)] [background-size:auto,22px_22px] [background-position:center,-1px_-1px]"
                    style={{ width: `${memoryLayout.width}px` }}
                  >
                    <div
                      className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_55%_45%_at_50%_72%,hsl(var(--ring)/0.09)_0%,transparent_65%)]"
                      aria-hidden
                    />
                    <svg
                      className="pointer-events-none absolute inset-0 h-full w-full overflow-visible [&_path]:will-change-[stroke-opacity]"
                      viewBox={`0 0 ${memoryLayout.width} ${memoryLayout.height}`}
                      aria-hidden
                    >
                      <defs>
                        <marker
                          id="memory-graph-arrow"
                          markerUnits="strokeWidth"
                          markerWidth="5.5"
                          markerHeight="5.5"
                          refX="10"
                          refY="5.5"
                          orient="auto"
                          viewBox="0 0 10 11"
                        >
                          <path
                            d="M0 0 L10 5.5 L0 11 Z"
                            fill="hsl(var(--muted-foreground))"
                            fillOpacity={0.55}
                          />
                        </marker>
                        <marker
                          id="memory-graph-arrow-primary"
                          markerUnits="strokeWidth"
                          markerWidth="5.5"
                          markerHeight="5.5"
                          refX="10"
                          refY="5.5"
                          orient="auto"
                          viewBox="0 0 10 11"
                        >
                          <path
                            d="M0 0 L10 5.5 L0 11 Z"
                            fill="hsl(var(--primary))"
                            fillOpacity={0.75}
                          />
                        </marker>
                        <filter
                          id="memory-graph-edge-glow"
                          x="-40%"
                          y="-40%"
                          width="180%"
                          height="180%"
                        >
                          <feGaussianBlur stdDeviation="1.2" result="b" />
                          <feMerge>
                            <feMergeNode in="b" />
                            <feMergeNode in="SourceGraphic" />
                          </feMerge>
                        </filter>
                      </defs>
                      {memoryLayout.positionedEdges.map((edge, idx) => {
                        const isPrimary = edge.type === "remembers";
                        return (
                          <path
                            key={`${edge.source}-${edge.target}-${idx}`}
                            d={memoryGraphEdgePath(edge.from, edge.to, idx)}
                            fill="none"
                            stroke={
                              isPrimary
                                ? "hsl(var(--primary))"
                                : "hsl(var(--muted-foreground))"
                            }
                            strokeOpacity={isPrimary ? 0.42 : 0.22}
                            strokeWidth={isPrimary ? 2.1 : 1.35}
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            markerEnd={`url(#${isPrimary ? "memory-graph-arrow-primary" : "memory-graph-arrow"})`}
                            filter={
                              isPrimary
                                ? "url(#memory-graph-edge-glow)"
                                : undefined
                            }
                            vectorEffect="non-scaling-stroke"
                          />
                        );
                      })}
                    </svg>

                    {memoryLayout.positionedNodes.map((node) => {
                      const Icon =
                        node.type === "user"
                          ? User
                          : node.type === "memory"
                            ? Brain
                            : Lightbulb;
                      return (
                        <div
                          key={node.id}
                          className="absolute z-[1]"
                          style={{
                            left: `${node.position.x}px`,
                            top: `${node.position.y}px`,
                          }}
                          title={node.label}
                        >
                          <div className="relative">
                            <div
                              className={cn(
                                "absolute left-1/2 top-1/2 flex h-[44px] w-[44px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 shadow-[0_10px_30px_-12px_hsl(var(--foreground)/0.45)] ring-2 ring-background/90 transition-transform duration-200 ease-out hover:z-[2] hover:scale-[1.06] hover:shadow-[0_14px_36px_-14px_hsl(var(--foreground)/0.55)]",
                                node.type === "user" &&
                                  "border-primary/40 bg-gradient-to-br from-primary to-primary/85 text-primary-foreground",
                                node.type === "memory" &&
                                  "border-border/80 bg-gradient-to-br from-background to-muted/90 text-foreground dark:from-background dark:to-muted/70",
                                node.type === "concept" &&
                                  "border-secondary/70 bg-gradient-to-br from-secondary to-secondary/80 text-secondary-foreground",
                              )}
                            >
                              <Icon
                                className="h-[18px] w-[18px] shrink-0 opacity-95"
                                strokeWidth={2}
                                aria-hidden
                              />
                            </div>
                            <div className="absolute left-1/2 top-[calc(50%+30px)] w-[min(168px,calc(100vw-6rem))] -translate-x-1/2 text-center">
                              <div className="text-[9px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                                {node.type === "user"
                                  ? "You"
                                  : node.type === "memory"
                                    ? "Memory"
                                    : "Concept"}
                              </div>
                              <div className="mt-0.5 line-clamp-2 text-[11px] font-medium leading-snug text-foreground drop-shadow-sm">
                                {node.label}
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
