import { useLocation, useParams } from "wouter";
import { useGetDocument, useApproveDocument, useRejectDocument, useDeleteDocument, getListDocumentsQueryKey, getGetDocumentQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { ArrowLeft, Check, X, Trash2, Search, AlertTriangle, FileText, File, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";

export default function AdminDocumentDetail() {
  const params = useParams<{ id: string }>();
  const id = parseInt(params.id!, 10);
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  
  const { data: doc, isLoading } = useGetDocument(id, {
    query: { enabled: !!id, queryKey: getGetDocumentQueryKey(id) }
  });

  const approve = useApproveDocument();
  const reject = useRejectDocument();
  const remove = useDeleteDocument();

  const [rejectReason, setRejectReason] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);

  if (isLoading) return <div className="p-8 flex justify-center"><Loader2 className="animate-spin h-8 w-8 text-primary" /></div>;
  if (!doc) return <div className="p-8">Document not found</div>;

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

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
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

      <Tabs defaultValue="findings" className="w-full">
        <TabsList className="mb-4">
          <TabsTrigger value="findings">Review Findings</TabsTrigger>
          <TabsTrigger value="content">Cleaned Content</TabsTrigger>
          <TabsTrigger value="original">Original Content</TabsTrigger>
        </TabsList>
        
        <TabsContent value="findings" className="space-y-6">
          <div className="grid md:grid-cols-2 gap-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2 text-purple-700 dark:text-purple-400">
                  <Search className="h-4 w-4" /> PII Findings ({doc.piiFindings.length})
                </CardTitle>
              </CardHeader>
              <CardContent>
                {doc.piiFindings.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No PII detected in this document.</p>
                ) : (
                  <div className="space-y-3">
                    {doc.piiFindings.map((finding, i) => (
                      <div key={i} className="text-sm border-b pb-3 last:border-0 last:pb-0">
                        <div className="flex justify-between items-center mb-1">
                          <Badge variant="outline" className="text-[10px] bg-purple-50 text-purple-700 border-purple-200 dark:bg-purple-900/20 dark:text-purple-400">{finding.type}</Badge>
                          <span className="text-muted-foreground">→ replaced with <code className="bg-muted px-1 rounded">{finding.replacement}</code></span>
                        </div>
                        <p className="font-mono text-xs bg-muted p-2 rounded mt-2">{finding.value}</p>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2 text-orange-700 dark:text-orange-400">
                  <File className="h-4 w-4" /> Duplicate Chunks ({doc.duplicateFindings.length})
                </CardTitle>
              </CardHeader>
              <CardContent>
                {doc.duplicateFindings.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No duplicate content detected.</p>
                ) : (
                  <div className="space-y-4">
                    {doc.duplicateFindings.map((finding, i) => (
                      <div key={i} className="text-sm border-b pb-4 last:border-0 last:pb-0">
                        <div className="flex justify-between items-center mb-2">
                          <span className="font-medium text-xs bg-orange-100 text-orange-800 px-2 py-0.5 rounded dark:bg-orange-900/30 dark:text-orange-400">
                            {Math.round(finding.similarity * 100)}% match
                          </span>
                          <span className="text-muted-foreground text-xs">with Document #{finding.matchedDocumentId}</span>
                        </div>
                        <p className="italic text-muted-foreground leading-relaxed line-clamp-3 bg-muted p-2 rounded">"{finding.snippet}"</p>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="content">
          <Card>
            <CardContent className="p-6">
              <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-foreground bg-muted/30 p-6 rounded-lg overflow-x-auto">
                {doc.cleanedText}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="original">
          <Card>
            <CardContent className="p-6">
              <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-muted-foreground bg-muted/10 p-6 rounded-lg overflow-x-auto opacity-70">
                {doc.originalText}
              </pre>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// Needed import for CheckCircle2
import { CheckCircle2 } from "lucide-react";