import { useListDocuments } from "@workspace/api-client-react";
import { Link, useLocation } from "wouter";
import { format } from "date-fns";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Plus, Database, FileText, Search, AlertCircle } from "lucide-react";
import { Input } from "@/components/ui/input";
import { useState } from "react";

export default function AdminDocuments() {
  const { data: documents, isLoading } = useListDocuments();
  const [, setLocation] = useLocation();
  const [search, setSearch] = useState("");

  const filtered = documents?.filter(d => 
    d.name.toLowerCase().includes(search.toLowerCase()) || 
    d.tags.some(t => t.toLowerCase().includes(search.toLowerCase()))
  );

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "approved": return <Badge className="bg-green-100 text-green-800 border-green-200 hover:bg-green-100">Approved</Badge>;
      case "pending": return <Badge className="bg-yellow-100 text-yellow-800 border-yellow-200 hover:bg-yellow-100">Pending</Badge>;
      case "rejected": return <Badge variant="destructive">Rejected</Badge>;
      default: return <Badge>{status}</Badge>;
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Knowledge Base</h1>
          <p className="text-muted-foreground mt-1">Manage documents powering the support assistant</p>
        </div>
        <Link href="/admin/documents/new">
          <Button><Plus className="mr-2 h-4 w-4" /> Upload Document</Button>
        </Link>
      </div>

      <div className="flex items-center gap-4 bg-background border rounded-lg p-2">
        <Search className="h-5 w-5 text-muted-foreground ml-2" />
        <Input 
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search documents by name or tag..." 
          className="border-0 shadow-none focus-visible:ring-0 px-0"
        />
      </div>

      <div className="border rounded-lg bg-background overflow-hidden">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Chunks</TableHead>
              <TableHead className="text-right">Data Cleaned</TableHead>
              <TableHead>Uploaded</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow><TableCell colSpan={5} className="h-24 text-center">Loading...</TableCell></TableRow>
            ) : filtered?.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="h-48 text-center">
                  <Database className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                  <p className="text-muted-foreground">No documents found</p>
                </TableCell>
              </TableRow>
            ) : (
              filtered?.map((doc) => (
                <TableRow 
                  key={doc.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setLocation(`/admin/documents/${doc.id}`)}
                >
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-primary/10 rounded">
                        <FileText className="h-4 w-4 text-primary" />
                      </div>
                      <div>
                        <p className="font-medium text-sm">{doc.name}</p>
                        <div className="flex gap-1 mt-1">
                          {doc.tags.slice(0, 3).map(tag => (
                            <Badge key={tag} variant="secondary" className="text-[10px] px-1 py-0">{tag}</Badge>
                          ))}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>{getStatusBadge(doc.status)}</TableCell>
                  <TableCell className="text-right font-mono text-sm">{doc.chunkCount}</TableCell>
                  <TableCell className="text-right">
                    <div className="text-sm">
                      {doc.piiCount > 0 && <span className="text-purple-600 font-medium mr-2">{doc.piiCount} PII</span>}
                      {doc.duplicateCount > 0 && <span className="text-orange-600 font-medium">{doc.duplicateCount} Dupes</span>}
                      {doc.piiCount === 0 && doc.duplicateCount === 0 && <span className="text-muted-foreground">-</span>}
                    </div>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {format(new Date(doc.createdAt), 'MMM d, yyyy')}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}