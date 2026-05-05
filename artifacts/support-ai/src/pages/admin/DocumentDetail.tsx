import { useLocation, useParams } from "wouter";
import { useGetDocument, useApproveDocument, useRejectDocument, useDeleteDocument, useExcludeDocumentDuplicateChunk, useListDocuments, getListDocumentsQueryKey, getGetDocumentQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { ArrowLeft, Check, X, Trash2, Search, AlertTriangle, FileText, File, Loader2, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";

type DedupDebugInfo = {
  mode: string;
  dedupMethod: string | null;
  embeddingsAvailable: boolean;
  vectorSearchEnabled: boolean;
  thresholds: {
    jaccard: number | null;
    cosine: number | null;
  };
};

function parseDedupDebug(value: unknown): DedupDebugInfo | null {
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  const thresholdsObj =
    obj.thresholds && typeof obj.thresholds === "object"
      ? (obj.thresholds as Record<string, unknown>)
      : null;

  return {
    mode: typeof obj.mode === "string" ? obj.mode : "unknown",
    dedupMethod: typeof obj.dedupMethod === "string" ? obj.dedupMethod : null,
    embeddingsAvailable: Boolean(obj.embeddingsAvailable),
    vectorSearchEnabled: Boolean(obj.vectorSearchEnabled),
    thresholds: {
      jaccard: typeof thresholdsObj?.jaccard === "number" ? thresholdsObj.jaccard : null,
      cosine: typeof thresholdsObj?.cosine === "number" ? thresholdsObj.cosine : null,
    },
  };
}

function parseIngestionDedupMethod(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  return typeof obj.dedupMethod === "string" ? obj.dedupMethod : null;
}

export default function AdminDocumentDetail() {
  const params = useParams<{ id: string }>();
  const id = parseInt(params.id!, 10);
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  
  const { data: doc, isLoading } = useGetDocument(id, {
    query: { enabled: !!id, queryKey: getGetDocumentQueryKey(id) }
  });
  const { data: allDocs } = useListDocuments({
    query: { enabled: !!id, queryKey: getListDocumentsQueryKey() }
  });

  const approve = useApproveDocument();
  const reject = useRejectDocument();
  const remove = useDeleteDocument();
  const excludeDuplicate = useExcludeDocumentDuplicateChunk();

  const [rejectReason, setRejectReason] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [lineageQuery, setLineageQuery] = useState("");
  const [activeSourcePosition, setActiveSourcePosition] = useState<number | null>(null);

  const rootId = doc?.rootDocumentId ?? doc?.id ?? id;
  const lineageDocs = !allDocs?.length
    ? []
    : allDocs
        .filter(d => (d.rootDocumentId ?? d.id) === rootId)
        .sort((a, b) => {
          const av = a.documentVersion ?? 0;
          const bv = b.documentVersion ?? 0;
          if (av !== bv) return bv - av;
          return b.id - a.id;
        });
  const lineageStats = {
    total: lineageDocs.length,
    approved: lineageDocs.filter(d => d.status === "approved").length,
    pending: lineageDocs.filter(d => d.status === "pending").length,
    totalChunks: lineageDocs.reduce((sum, d) => sum + (d.chunkCount ?? 0), 0),
  };
  const lineageOriginId = !lineageDocs.length
    ? null
    : [...lineageDocs]
        .sort((a, b) => {
          const av = a.documentVersion ?? 0;
          const bv = b.documentVersion ?? 0;
          if (av !== bv) return av - bv;
          return a.id - b.id;
        })[0]?.id ?? null;
  const q = lineageQuery.trim().toLowerCase();
  const filteredLineageDocs = !q
    ? lineageDocs
    : lineageDocs.filter(item => {
        const haystack = [
          item.name,
          String(item.id),
          String(item.documentVersion ?? 0),
          item.status,
          item.lastIngestionRunId ?? "",
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q);
      });

  if (isLoading) return <div className="p-8 flex justify-center"><Loader2 className="animate-spin h-8 w-8 text-primary" /></div>;
  if (!doc) return <div className="p-8">Document not found</div>;

  const debugSource = (doc as unknown as Record<string, unknown>).dedupDebug;
  const dedupDebug = parseDedupDebug(debugSource);
  const dedupMethodLabel = dedupDebug?.dedupMethod ?? parseIngestionDedupMethod((doc as unknown as Record<string, unknown>).ingestionReport);

  const handleApprove = async () => {
    try {
      await approve.mutateAsync({ id });
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document approved and indexed" });
    } catch(err) {
      toast({ title: "Failed to approve", variant: "destructive" });
    }
  };

  const handleReject = async () => {
    try {
      await reject.mutateAsync({ id, data: { reason: rejectReason } });
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document rejected" });
      setRejectOpen(false);
    } catch(err) {
      toast({ title: "Failed to reject", variant: "destructive" });
    }
  };

  const handleDelete = async () => {
    try {
      await remove.mutateAsync({ id });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: "Document deleted" });
      setLocation("/admin/documents");
    } catch(err) {
      toast({ title: "Failed to delete", variant: "destructive" });
    }
  };

  const handleExcludeDuplicateChunk = async (sourceChunkPosition: number | undefined) => {
    if (typeof sourceChunkPosition !== "number") {
      toast({ title: "Missing source chunk position", variant: "destructive" });
      return;
    }

    try {
      setActiveSourcePosition(sourceChunkPosition);
      await excludeDuplicate.mutateAsync({ id, sourcePosition: sourceChunkPosition });
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey() });
      toast({ title: doc.status === "pending" ? "Duplicate chunk excluded" : "Duplicate chunk removed" });
    } catch {
      toast({ title: "Failed to update duplicate chunk", variant: "destructive" });
    } finally {
      setActiveSourcePosition(null);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const formatConfidence = (confidence?: number) => {
    if (typeof confidence !== "number") return null;
    return `${Math.round(confidence * 100)}% confidence`;
  };

  const isPending = doc.status === "pending";

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-6">
      <Button variant="ghost" onClick={() => setLocation("/admin/documents")} className="-ml-4 mb-2">
        <ArrowLeft className="mr-2 h-4 w-4" />
        Back to Knowledge Base
      </Button>

      <div className="flex justify-between items-start">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-3xl font-bold">{doc.name}</h1>
            <Badge variant={doc.status === 'approved' ? 'default' : doc.status === 'rejected' ? 'destructive' : 'secondary'} className="capitalize">
              {doc.status}
            </Badge>
          </div>
          <div className="flex gap-2 mb-4">
            {doc.tags.map(tag => <Badge key={tag} variant="outline">{tag}</Badge>)}
          </div>
          <p className="text-sm text-muted-foreground">
            Uploaded {format(new Date(doc.createdAt), 'PPP')} • Source: {doc.sourceType.toUpperCase()}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Version {doc.documentVersion ?? 0}
            {doc.rootDocumentId ? ` • Root #${doc.rootDocumentId}` : ""}
            {doc.lastIngestionRunId ? ` • Run ${doc.lastIngestionRunId}` : ""}
          </p>
        </div>

        <div className="flex gap-2">
          {isPending && (
            <>
              <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
                <DialogTrigger asChild>
                  <Button variant="outline" className="border-destructive text-destructive hover:bg-destructive/10"><X className="mr-2 h-4 w-4" /> Reject</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Reject Document</DialogTitle>
                    <DialogDescription>
                      Provide a reason for rejecting this document. It will not be indexed.
                    </DialogDescription>
                  </DialogHeader>
                  <Textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)} placeholder="Reason (optional)" />
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setRejectOpen(false)}>Cancel</Button>
                    <Button variant="destructive" onClick={handleReject} disabled={reject.isPending}>Reject Document</Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
              <Button onClick={handleApprove} disabled={approve.isPending} className="bg-green-600 hover:bg-green-700 text-white">
                <Check className="mr-2 h-4 w-4" /> Approve & Index
              </Button>
            </>
          )}
          {!isPending && (
            <Button variant="outline" className="border-destructive text-destructive hover:bg-destructive/10" onClick={handleDelete} disabled={remove.isPending}>
              <Trash2 className="mr-2 h-4 w-4" /> Delete
            </Button>
          )}
        </div>
      </div>

      {doc.rejectionReason && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 mt-0.5" />
          <div>
            <h4 className="font-semibold">Rejection Reason</h4>
            <p className="text-sm mt-1">{doc.rejectionReason}</p>
          </div>
        </div>
      )}

      {/* Ingestion Report */}
      <Card className="border-primary/20 shadow-sm bg-primary/5">
        <CardHeader className="pb-3">
          <CardTitle className="text-lg flex items-center gap-2 text-primary">
            <CheckCircle2 className="h-5 w-5" /> Ingestion Report
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Size Reduction</p>
              <div className="flex items-baseline gap-2">
                <span className="text-2xl font-bold">{formatBytes(doc.finalSize)}</span>
                <span className="text-sm text-muted-foreground line-through">{formatBytes(doc.originalSize)}</span>
              </div>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Knowledge Chunks</p>
              <span className="text-2xl font-bold font-mono">{doc.chunkCount}</span>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">PII Removed</p>
              <span className="text-2xl font-bold text-purple-600">{doc.piiCount}</span>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Duplicates Skipped</p>
              <span className="text-2xl font-bold text-orange-600">{doc.duplicateCount}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Lineage Trace</CardTitle>
          <CardDescription>
            Documents connected by root lineage #{rootId}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-xs uppercase text-muted-foreground tracking-wider">Family Docs</p>
              <p className="text-xl font-semibold">{lineageStats.total}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-muted-foreground tracking-wider">Approved</p>
              <p className="text-xl font-semibold text-green-600">{lineageStats.approved}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-muted-foreground tracking-wider">Pending</p>
              <p className="text-xl font-semibold text-amber-600">{lineageStats.pending}</p>
            </div>
            <div>
              <p className="text-xs uppercase text-muted-foreground tracking-wider">Indexed Chunks</p>
              <p className="text-xl font-semibold font-mono">{lineageStats.totalChunks}</p>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <Input
              value={lineageQuery}
              onChange={e => setLineageQuery(e.target.value)}
              placeholder="Filter by name, id, version, status, run id"
              className="max-w-md"
            />
            <p className="text-xs text-muted-foreground">
              Showing {filteredLineageDocs.length} of {lineageDocs.length}
            </p>
          </div>

          {filteredLineageDocs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No related lineage documents found.</p>
          ) : (
            <div className="space-y-2">
              {filteredLineageDocs.map(item => {
                const current = item.id === doc.id;
                const isRoot = item.id === rootId;
                const isOrigin = lineageOriginId !== null && item.id === lineageOriginId;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => !current && setLocation(`/admin/documents/${item.id}`)}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition ${current ? "border-primary bg-primary/5" : "hover:bg-muted/40"}`}
                    disabled={current}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium truncate">{item.name}</p>
                        <p className="text-xs text-muted-foreground">
                          Doc #{item.id} • v{item.documentVersion ?? 0}
                          {item.lastIngestionRunId ? ` • ${item.lastIngestionRunId}` : ""}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {current && <Badge variant="secondary">Current</Badge>}
                        {isRoot && <Badge variant="outline">Root</Badge>}
                        {isOrigin && <Badge variant="outline">Origin</Badge>}
                        <Badge variant={item.status === 'approved' ? 'default' : item.status === 'rejected' ? 'destructive' : 'secondary'} className="capitalize">
                          {item.status}
                        </Badge>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Tabs defaultValue="pii" className="w-full">
        <TabsList className="mb-4">
          <TabsTrigger value="pii">PII Findings</TabsTrigger>
          <TabsTrigger value="duplicates">Potential Duplicate Content</TabsTrigger>
        </TabsList>
        
        <TabsContent value="pii">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2 text-purple-700 dark:text-purple-400">
                <Search className="h-4 w-4" /> PII Findings ({doc.piiFindings.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="p-6">
              {doc.piiFindings.length === 0 ? (
                <p className="text-sm text-muted-foreground">No PII detected in this document.</p>
              ) : (
                <div className="space-y-3">
                  {doc.piiFindings.map((finding, i) => (
                    <div key={i} className="text-sm border-b pb-3 last:border-0 last:pb-0">
                      <div className="flex justify-between items-center mb-1">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className="text-[10px] bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-900/20 dark:text-purple-400">{finding.type}</Badge>
                          {finding.detector && (
                            <Badge variant="secondary" className="text-[10px] font-normal">{finding.detector}</Badge>
                          )}
                        </div>
                        <span className="text-muted-foreground">→ replaced with <code className="bg-muted px-1 rounded">{finding.replacement}</code></span>
                      </div>
                      {formatConfidence(finding.confidence) && (
                        <p className="text-[11px] text-muted-foreground">{formatConfidence(finding.confidence)}</p>
                      )}
                      <p className="font-mono text-xs bg-muted p-2 rounded mt-2">{finding.value}</p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="duplicates">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2 text-orange-700 dark:text-orange-400">
                <File className="h-4 w-4" /> Potential Duplicate Content ({doc.duplicateFindings.length})
              </CardTitle>
              <CardDescription>
                {doc.status === "pending"
                  ? "Potential duplicates are auto-removed on approval; you can manually exclude them now."
                  : "Manually remove duplicate chunks when needed."}
              </CardDescription>
              <div className="flex flex-wrap gap-2 pt-1">
                {dedupMethodLabel && (
                  <Badge variant="outline" className="text-[10px]">Method: {dedupMethodLabel}</Badge>
                )}
                {typeof dedupDebug?.thresholds.jaccard === "number" && (
                  <Badge variant="outline" className="text-[10px]">Jaccard ≥ {(dedupDebug.thresholds.jaccard * 100).toFixed(0)}%</Badge>
                )}
                {typeof dedupDebug?.thresholds.cosine === "number" && (
                  <Badge variant="outline" className="text-[10px]">Cosine ≥ {(dedupDebug.thresholds.cosine * 100).toFixed(0)}%</Badge>
                )}
                {dedupDebug && (
                  <Badge variant="outline" className="text-[10px]">
                    {dedupDebug.vectorSearchEnabled ? "Vector search on" : "Vector search off"}
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="p-6">
              {doc.duplicateFindings.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {doc.status === "pending"
                    ? "No overlap detected above preview threshold."
                    : "No duplicate content detected."}
                </p>
              ) : (
                <div className="space-y-4">
                  {doc.duplicateFindings.map((finding, i) => {
                    const findingExtra = finding as unknown as Record<string, unknown>;
                    const matchedChunkPosition = typeof findingExtra.matchedChunkPosition === "number"
                      ? findingExtra.matchedChunkPosition
                      : null;
                    const matchedSnippet = typeof findingExtra.matchedSnippet === "string"
                      ? findingExtra.matchedSnippet
                      : null;

                    const dupReason = finding.method === "hash"
                      ? "Exact duplicate — these chunks share identical content (same hash)."
                      : finding.method === "embedding"
                        ? `Semantically near-identical — vector similarity is ${Math.round(finding.similarity * 100)}%, meaning the chunks convey the same meaning even if worded differently.`
                        : finding.method === "jaccard"
                          ? `High term overlap — ${Math.round(finding.similarity * 100)}% of words are shared between these chunks.`
                          : `Content similarity of ${Math.round(finding.similarity * 100)}% exceeds the deduplication threshold.`;

                    const matchedLabel = typeof finding.matchedDocumentId === "number"
                      ? `Matched chunk in "${finding.matchedDocumentName ?? `Document #${finding.matchedDocumentId}`}"${typeof matchedChunkPosition === "number" ? ` · position #${matchedChunkPosition}` : ""}`
                      : `Same document · position #${matchedChunkPosition ?? "?"}`;

                    return (
                    <div key={i} className="text-sm border rounded-md p-3 last:mb-0">
                      <div className="flex justify-between items-center mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-xs bg-orange-100 text-orange-800 px-2 py-0.5 rounded dark:bg-orange-900/30 dark:text-orange-400">
                            {Math.round(finding.similarity * 100)}% match
                          </span>
                          {finding.method && (
                            <Badge variant="secondary" className="text-[10px] font-normal">{finding.method}</Badge>
                          )}
                          <span className="text-muted-foreground text-xs">
                            {typeof finding.matchedDocumentId === "number"
                              ? `vs Document #${finding.matchedDocumentId}${finding.matchedDocumentName ? ` — ${finding.matchedDocumentName}` : ""}`
                              : "within this document"}
                          </span>
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          disabled={
                            excludeDuplicate.isPending ||
                            activeSourcePosition === finding.sourceChunkPosition ||
                            typeof finding.sourceChunkPosition !== "number"
                          }
                          onClick={() => handleExcludeDuplicateChunk(finding.sourceChunkPosition)}
                        >
                          {excludeDuplicate.isPending && activeSourcePosition === finding.sourceChunkPosition
                            ? "Removing..."
                            : doc.status === "pending"
                              ? "Exclude Chunk"
                              : "Delete Chunk"}
                        </Button>
                      </div>
                      <p className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1 mb-3 border-l-2 border-orange-300 dark:border-orange-700">
                        {dupReason}
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                            Incoming chunk{typeof finding.sourceChunkPosition === "number" ? ` · position #${finding.sourceChunkPosition}` : ""}
                          </p>
                          <p className="italic text-muted-foreground leading-relaxed bg-orange-50 dark:bg-orange-950/20 border border-orange-200 dark:border-orange-900/40 p-2 rounded whitespace-pre-wrap break-words">"{finding.snippet}"</p>
                        </div>
                        <div>
                          <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                            {matchedLabel}
                          </p>
                          {matchedSnippet
                            ? <p className="italic text-muted-foreground leading-relaxed bg-muted border p-2 rounded whitespace-pre-wrap break-words">"{matchedSnippet}"</p>
                            : (
                              <p className="text-xs text-muted-foreground italic bg-muted/50 border border-dashed p-2 rounded">
                                {typeof matchedChunkPosition === "number"
                                  ? `Matched chunk at position #${matchedChunkPosition}${typeof finding.matchedDocumentId === "number" ? ` in Document #${finding.matchedDocumentId}` : " within this document"} — re-upload or re-approve to load preview.`
                                  : "Matched chunk content unavailable — re-upload or re-approve to load preview."}
                              </p>
                            )
                          }
                        </div>
                      </div>
                    </div>
                  );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}