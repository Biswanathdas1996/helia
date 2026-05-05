import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "wouter";
import { 
  useListConversations, 
  useGetConversation, 
  useSendMessage, 
  useCreateConversation,
  useListTickets,
  useRateMessage,
  getListConversationsQueryKey,
  getGetConversationQueryKey
} from "@workspace/api-client-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Bot, FileText, Send, ThumbsDown, ThumbsUp, Ticket, AlertCircle, Loader2, MessageSquarePlus, Share2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useToast } from "@/hooks/use-toast";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const BULLET_RE = /^[-*]\s+/;
const ORDERED_RE = /^\d+\.\s+/;
const CITATION_RE = /\[(\d+)\]/g;

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
  positionedEdges: Array<MemoryGraphEdge & { from: MemoryGraphPoint; to: MemoryGraphPoint }>;
};

function buildMemoryGraphLayout(
  nodes: MemoryGraphNode[] | undefined,
  edges: MemoryGraphEdge[] | undefined,
): MemoryGraphLayout {
  const safeNodes = nodes ?? [];
  const safeEdges = edges ?? [];
  const width = 980;
  const height = 430;

  const userNodes = safeNodes.filter((n) => n.type === "user");
  const memoryNodes = safeNodes.filter((n) => n.type === "memory");
  const conceptNodes = safeNodes.filter((n) => n.type === "concept");

  const positions = new Map<string, MemoryGraphPoint>();

  if (userNodes.length > 0) {
    positions.set(userNodes[0].id, { x: width / 2, y: 68 });
  }

  memoryNodes.forEach((node, idx) => {
    const x = ((idx + 1) * width) / (memoryNodes.length + 1);
    positions.set(node.id, { x, y: 200 });
  });

  conceptNodes.forEach((node, idx) => {
    const x = ((idx + 1) * width) / (conceptNodes.length + 1);
    positions.set(node.id, { x, y: 340 });
  });

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
    .filter(Boolean) as Array<MemoryGraphEdge & { from: MemoryGraphPoint; to: MemoryGraphPoint }>;

  return {
    width,
    height,
    positionedNodes,
    positionedEdges,
  };
}

function renderInlineCitations(text: string, keyPrefix: string): React.ReactNode[] {
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

function renderAssistantContent(content: string): React.ReactNode {
  const blocks = content
    .split(/\n{2,}/)
    .map((b) => b.trim())
    .filter(Boolean);

  return (
    <div className="space-y-3 text-[15px] leading-7 text-foreground/95">
      {blocks.map((block, blockIdx) => {
        const lines = block
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean);

        if (lines.length > 0 && lines.every((line) => BULLET_RE.test(line))) {
          return (
            <ul key={`ul-${blockIdx}`} className="list-disc pl-5 space-y-1.5 marker:text-primary">
              {lines.map((line, liIdx) => (
                <li key={`li-${blockIdx}-${liIdx}`}>{renderInlineCitations(line.replace(BULLET_RE, ""), `ul-${blockIdx}-${liIdx}`)}</li>
              ))}
            </ul>
          );
        }

        if (lines.length > 0 && lines.every((line) => ORDERED_RE.test(line))) {
          return (
            <ol key={`ol-${blockIdx}`} className="list-decimal pl-5 space-y-1.5 marker:text-primary">
              {lines.map((line, liIdx) => (
                <li key={`oli-${blockIdx}-${liIdx}`}>{renderInlineCitations(line.replace(ORDERED_RE, ""), `ol-${blockIdx}-${liIdx}`)}</li>
              ))}
            </ol>
          );
        }

        return (
          <p key={`p-${blockIdx}`} className="whitespace-pre-wrap">
            {renderInlineCitations(block, `p-${blockIdx}`)}
          </p>
        );
      })}
    </div>
  );
}

export default function Chat() {
  const [location, setLocation] = useLocation();
  const params = useParams<{ id?: string }>();
  const currentId = params.id ? parseInt(params.id, 10) : undefined;
  
  const { data: conversations, isLoading: loadingConvos } = useListConversations();
  const { data: tickets } = useListTickets();
  const activeTickets = tickets?.filter(t => t.status !== "closed" && t.status !== "resolved") || [];

  const { data: activeConvo, isLoading: loadingActive } = useGetConversation(currentId as number, { 
    query: { enabled: !!currentId, queryKey: getGetConversationQueryKey(currentId as number) } 
  });

  const createConvo = useCreateConversation();
  const sendMessage = useSendMessage();
  const rateMessage = useRateMessage();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [input, setInput] = useState("");
  const [memoryGraphOpen, setMemoryGraphOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const memoryGraph = useQuery<MemoryGraphResponse>({
    queryKey: ["chat-memory-graph", currentId],
    enabled: Boolean(currentId) && memoryGraphOpen,
    queryFn: async () => {
      const res = await fetch(`/api/chat/conversations/${currentId}/memory-graph`, {
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error("Failed to load memory graph");
      }
      return (await res.json()) as MemoryGraphResponse;
    },
    staleTime: 60_000,
  });

  const memoryLayout = useMemo(
    () => buildMemoryGraphLayout(memoryGraph.data?.nodes, memoryGraph.data?.edges),
    [memoryGraph.data?.nodes, memoryGraph.data?.edges],
  );

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [activeConvo?.messages, sendMessage.isPending]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || sendMessage.isPending) return;

    const content = input;
    setInput("");

    let convoId = currentId;
    if (!convoId) {
      try {
        const newConvo = await createConvo.mutateAsync({ data: { title: content.substring(0, 40) } });
        convoId = newConvo.id;
        setLocation(`/app/conversations/${convoId}`);
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
      } catch (err) {
        toast({ title: "Failed to create conversation", variant: "destructive" });
        return;
      }
    }

    try {
      // Optimistic update could go here
      await sendMessage.mutateAsync({ id: convoId, data: { content } });
      queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(convoId) });
      queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
    } catch (err) {
      toast({ title: "Failed to send message", variant: "destructive" });
    }
  };

  const handleRate = async (messageId: number, rating: "up" | "down") => {
    if (!currentId) return;
    try {
      await rateMessage.mutateAsync({ id: messageId, data: { rating } });
      queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(currentId) });
    } catch (err) {
      toast({ title: "Failed to rate message", variant: "destructive" });
    }
  };

  return (
    <div className="flex h-full bg-background">
      {/* Secondary Sidebar */}
      <div className="w-80 border-r border-border bg-muted/20 flex flex-col h-full overflow-hidden">
        <div className="p-4 border-b border-border">
          <Button 
            className="w-full justify-start text-left font-medium" 
            variant={!currentId ? "secondary" : "outline"} 
            onClick={() => setLocation("/app")}
          >
            <MessageSquarePlus className="mr-2 h-4 w-4" />
            New Conversation
          </Button>
        </div>

        {activeTickets.length > 0 && (
          <div className="px-4 py-3 border-b border-border bg-primary/5">
            <h3 className="text-xs font-semibold text-primary uppercase tracking-wider mb-2 flex items-center">
              <AlertCircle className="h-3 w-3 mr-1" /> Open Tickets ({activeTickets.length})
            </h3>
            <div className="space-y-1">
              {activeTickets.slice(0, 3).map(ticket => (
                <Button 
                  key={ticket.id} 
                  variant="link" 
                  className="w-full justify-start px-2 py-1 h-auto text-xs text-foreground/80 hover:text-foreground"
                  onClick={() => setLocation(`/app/tickets/${ticket.id}`)}
                >
                  <Ticket className="h-3 w-3 mr-2 text-muted-foreground" />
                  <span className="truncate">{ticket.subject}</span>
                </Button>
              ))}
            </div>
          </div>
        )}

        <ScrollArea className="flex-1">
          <div className="p-2 space-y-1">
            {loadingConvos ? (
              <div className="p-4 text-center text-sm text-muted-foreground">Loading...</div>
            ) : conversations?.length === 0 ? (
              <div className="p-4 text-center text-sm text-muted-foreground">No conversations yet</div>
            ) : (
              conversations?.map(convo => (
                <Button
                  key={convo.id}
                  variant={currentId === convo.id ? "secondary" : "ghost"}
                  className="w-full justify-start text-left h-auto py-3 px-3"
                  onClick={() => setLocation(`/app/conversations/${convo.id}`)}
                >
                  <div className="flex flex-col items-start gap-1 overflow-hidden w-full">
                    <span className="font-medium text-sm truncate w-full">{convo.title || "New Conversation"}</span>
                    {convo.lastMessagePreview && (
                      <span className="text-xs text-muted-foreground truncate w-full">{convo.lastMessagePreview}</span>
                    )}
                  </div>
                </Button>
              ))
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col h-full bg-background relative">
        <div className="border-b border-border/70 bg-background/90 backdrop-blur-sm">
          <div className="max-w-4xl mx-auto px-4 md:px-8 py-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold truncate">
                {activeConvo?.conversation?.title || "Conversation"}
              </h2>
              <p className="text-xs text-muted-foreground">
                Visualize user memory and related concepts for this thread.
              </p>
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
            <h2 className="text-2xl font-semibold mb-2">How can I help you today?</h2>
            <p className="text-muted-foreground max-w-md">
              Ask any question about our products, services, or internal documentation.
            </p>
            <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4 max-w-2xl w-full pointer-events-auto">
              {[
                "How do I reset my password?",
                "What are the API rate limits?",
                "How to integrate with Salesforce?",
                "Where is the billing dashboard?"
              ].map(q => (
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
              className={`flex gap-4 max-w-4xl mx-auto ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              {msg.role === 'assistant' && (
                <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-1">
                  <Bot className="h-4 w-4 text-primary" />
                </div>
              )}
              
              <div className={`flex flex-col gap-2 max-w-[80%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                <div className={`p-4 rounded-2xl text-sm ${
                  msg.role === 'user' 
                    ? 'bg-primary text-primary-foreground rounded-tr-sm' 
                    : 'bg-card border border-border/80 shadow-sm text-foreground rounded-tl-sm'
                }`}>
                  {msg.role === 'assistant' ? (
                    renderAssistantContent(msg.content)
                  ) : (
                    <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                  )}
                </div>

                {msg.role === 'assistant' && (
                  <div className="flex flex-wrap items-center gap-2 mt-1">
                    {msg.citations && msg.citations.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {msg.citations.map((cite, idx) => (
                          <Popover key={idx}>
                            <PopoverTrigger asChild>
                              <Badge variant="outline" className="cursor-pointer hover:bg-accent text-xs py-0 h-6 font-normal text-muted-foreground border-border/70 bg-background/80">
                                [{idx + 1}] {cite.documentName}
                              </Badge>
                            </PopoverTrigger>
                            <PopoverContent className="w-80 p-4 text-sm" align="start">
                              <div className="font-medium flex items-center gap-2 mb-2 pb-2 border-b border-border">
                                <FileText className="h-4 w-4 text-primary" />
                                {cite.documentName}
                              </div>
                              <p className="text-muted-foreground leading-relaxed">"...{cite.snippet}..."</p>
                              <div className="mt-3">
                                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                                  Chunk Metadata
                                </p>
                                <pre className="text-[11px] leading-relaxed bg-muted/60 border border-border rounded p-2 overflow-x-auto">
{JSON.stringify(
  {
    fileName: cite.metadata?.fileName ?? cite.documentName,
    pageNumber: cite.metadata?.pageNumber ?? null,
    keyPhrases: cite.metadata?.keyPhrases ?? [],
    chunkPosition: cite.metadata?.chunkPosition ?? null,
    tokenCount: cite.metadata?.tokenCount ?? null,
    sourceType: cite.metadata?.sourceType ?? null,
    score: Number(cite.score.toFixed(3)),
  },
  null,
  2,
)}
                                </pre>
                              </div>
                            </PopoverContent>
                          </Popover>
                        ))}
                      </div>
                    )}
                    
                    <div className="flex items-center gap-1 ml-auto">
                      <Button 
                        variant="ghost" 
                        size="icon" 
                        className={`h-6 w-6 rounded-full ${msg.rating === 'up' ? 'text-green-600 bg-green-50 dark:bg-green-900/20' : 'text-muted-foreground'}`}
                        onClick={() => handleRate(msg.id, "up")}
                      >
                        <ThumbsUp className="h-3 w-3" />
                      </Button>
                      <Button 
                        variant="ghost" 
                        size="icon" 
                        className={`h-6 w-6 rounded-full ${msg.rating === 'down' ? 'text-destructive bg-destructive/10' : 'text-muted-foreground'}`}
                        onClick={() => handleRate(msg.id, "down")}
                      >
                        <ThumbsDown className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                )}
                
                {msg.role === 'assistant' && msg.canAnswer === false && (
                  <div className="mt-2 bg-secondary/50 border border-secondary p-3 rounded-lg flex items-center justify-between w-full">
                    <span className="text-sm text-foreground/80">I couldn't find a confident answer. Would you like to escalate this?</span>
                    <Button 
                      size="sm" 
                      onClick={() => setLocation(`/app/tickets/new?messageId=${msg.id}`)}
                    >
                      Raise Ticket
                    </Button>
                  </div>
                )}
              </div>
            </div>
          ))}

          {sendMessage.isPending && (
            <div className="flex gap-4 max-w-4xl mx-auto justify-start">
              <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0 mt-1">
                <Bot className="h-4 w-4 text-primary" />
              </div>
              <div className="p-4 rounded-2xl bg-muted/50 border border-border rounded-tl-sm flex items-center gap-1">
                <div className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "0ms" }} />
                <div className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "150ms" }} />
                <div className="w-2 h-2 rounded-full bg-muted-foreground/40 animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          )}
        </div>

        <div className="p-4 bg-background border-t border-border">
          <form onSubmit={handleSubmit} className="max-w-4xl mx-auto relative flex items-center">
            <Input
              id="chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything..."
              className="pr-12 py-6 text-base rounded-xl shadow-sm bg-background border-input"
              autoComplete="off"
            />
            <Button 
              type="submit" 
              size="icon" 
              disabled={!input.trim() || sendMessage.isPending}
              className="absolute right-2 h-10 w-10 rounded-lg"
            >
              <Send className="h-4 w-4" />
            </Button>
          </form>
          <div className="text-center mt-2 text-[11px] text-muted-foreground">
            Helia AI can make mistakes. Consider verifying important information.
          </div>
        </div>

        <Dialog open={memoryGraphOpen} onOpenChange={setMemoryGraphOpen}>
          <DialogContent className="max-w-5xl w-[95vw] p-0 overflow-hidden">
            <DialogHeader className="px-6 pt-6 pb-3">
              <DialogTitle>Memory Graph</DialogTitle>
              <DialogDescription>
                Connected view of what Helia remembers for this user and conversation context.
              </DialogDescription>
            </DialogHeader>

            <div className="px-6 pb-6">
              <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline">Nodes: {memoryGraph.data?.nodes.length ?? 0}</Badge>
                <Badge variant="outline">Edges: {memoryGraph.data?.edges.length ?? 0}</Badge>
                {memoryGraph.data?.query && (
                  <Badge variant="outline" className="max-w-full truncate">
                    Query seed: {memoryGraph.data.query}
                  </Badge>
                )}
              </div>

              {memoryGraph.isLoading ? (
                <div className="h-[430px] border rounded-lg flex items-center justify-center gap-2 text-sm text-muted-foreground bg-muted/30">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading memory graph...
                </div>
              ) : memoryGraph.isError ? (
                <div className="h-[430px] border rounded-lg flex items-center justify-center text-sm text-destructive bg-destructive/5">
                  Could not load memory graph.
                </div>
              ) : (memoryGraph.data?.nodes.length ?? 0) <= 1 ? (
                <div className="h-[430px] border rounded-lg flex items-center justify-center text-sm text-muted-foreground bg-muted/20">
                  No user memory found yet. Continue chatting to build memory.
                </div>
              ) : (
                <div className="overflow-x-auto rounded-lg border bg-gradient-to-b from-slate-50/80 to-slate-100/80">
                  <div
                    className="relative h-[430px] min-w-[980px]"
                    style={{ width: `${memoryLayout.width}px` }}
                  >
                    <svg
                      className="absolute inset-0 h-full w-full"
                      viewBox={`0 0 ${memoryLayout.width} ${memoryLayout.height}`}
                    >
                      {memoryLayout.positionedEdges.map((edge, idx) => (
                        <line
                          key={`${edge.source}-${edge.target}-${idx}`}
                          x1={edge.from.x}
                          y1={edge.from.y}
                          x2={edge.to.x}
                          y2={edge.to.y}
                          stroke={edge.type === "remembers" ? "#1d4ed8" : "#64748b"}
                          strokeOpacity={edge.type === "remembers" ? 0.5 : 0.35}
                          strokeWidth={edge.type === "remembers" ? 2 : 1.5}
                        />
                      ))}
                    </svg>

                    {memoryLayout.positionedNodes.map((node) => (
                      <div
                        key={node.id}
                        className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-xl border px-3 py-2 text-xs shadow-sm max-w-[220px] text-center ${
                          node.type === "user"
                            ? "bg-blue-600 text-white border-blue-500"
                            : node.type === "memory"
                              ? "bg-white text-slate-900 border-slate-300"
                              : "bg-amber-50 text-amber-900 border-amber-200"
                        }`}
                        style={{ left: `${node.position.x}px`, top: `${node.position.y}px` }}
                        title={node.label}
                      >
                        <div className="font-medium mb-0.5">
                          {node.type === "user" ? "User" : node.type === "memory" ? "Memory" : "Concept"}
                        </div>
                        <div className="line-clamp-2">{node.label}</div>
                      </div>
                    ))}
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
