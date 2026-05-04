import { useGetAdminStats, useGetAdminTrend, useGetAdminActivity, useListDocuments } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { FileText, MessageSquare, Ticket as TicketIcon, Zap, CheckCircle2, AlertCircle, Clock, Search } from "lucide-react";
import { format } from "date-fns";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function AdminDashboard() {
  const { data: stats } = useGetAdminStats();
  const { data: trend } = useGetAdminTrend();
  const { data: activity } = useGetAdminActivity();
  const { data: documents } = useListDocuments();
  
  const pendingDocs = documents?.filter(d => d.status === "pending") || [];

  const kpis = stats ? [
    { title: "Total Queries", value: stats.totalQueries, icon: MessageSquare, color: "text-blue-500" },
    { title: "Helpful Rate", value: `${Math.round(stats.helpfulRate * 100)}%`, icon: CheckCircle2, color: "text-green-500" },
    { title: "Active Documents", value: stats.approvedDocuments, icon: FileText, color: "text-indigo-500" },
    { title: "Knowledge Chunks", value: stats.totalChunks, icon: Zap, color: "text-amber-500" },
    { title: "Open Tickets", value: stats.openTickets, icon: AlertCircle, color: "text-orange-500" },
    { title: "PII Removed", value: stats.piiRemovedTotal, icon: Search, color: "text-purple-500" },
  ] : [];

  const getActivityIcon = (kind: string) => {
    switch(kind) {
      case 'document_uploaded': return <FileText className="h-4 w-4 text-blue-500" />;
      case 'document_approved': return <CheckCircle2 className="h-4 w-4 text-green-500" />;
      case 'query_answered': return <MessageSquare className="h-4 w-4 text-indigo-500" />;
      case 'ticket_opened': return <TicketIcon className="h-4 w-4 text-orange-500" />;
      default: return <Clock className="h-4 w-4 text-gray-500" />;
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Admin Dashboard</h1>
          <p className="text-muted-foreground mt-1">Platform metrics and ingestion queue</p>
        </div>
        <div className="flex gap-2">
          <Link href="/admin/tickets">
            <Button variant="outline"><TicketIcon className="mr-2 h-4 w-4" /> Manage Tickets</Button>
          </Link>
          <Link href="/admin/documents">
            <Button><FileText className="mr-2 h-4 w-4" /> Knowledge Base</Button>
          </Link>
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        {kpis.map((kpi, i) => (
          <Card key={i} className="shadow-sm">
            <CardContent className="p-4">
              <div className="flex justify-between items-start mb-2">
                <p className="text-xs font-medium text-muted-foreground">{kpi.title}</p>
                <kpi.icon className={`h-4 w-4 ${kpi.color}`} />
              </div>
              <h3 className="text-2xl font-bold">{kpi.value}</h3>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Chart */}
        <Card className="col-span-2 shadow-sm">
          <CardHeader>
            <CardTitle className="text-lg">Query Volume (14 Days)</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-[300px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorQueries" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis 
                    dataKey="date" 
                    tickFormatter={(val) => format(new Date(val), 'MMM d')}
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                    dy={10}
                  />
                  <YAxis 
                    fontSize={12}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip 
                    contentStyle={{ borderRadius: '8px', border: '1px solid hsl(var(--border))' }}
                    labelFormatter={(val) => format(new Date(val), 'MMM d, yyyy')}
                  />
                  <Area 
                    type="monotone" 
                    dataKey="queries" 
                    stroke="hsl(var(--primary))" 
                    strokeWidth={2}
                    fillOpacity={1} 
                    fill="url(#colorQueries)" 
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        {/* Pending Queue */}
        <Card className="shadow-sm flex flex-col">
          <CardHeader className="pb-3 border-b border-border flex flex-row items-center justify-between">
            <CardTitle className="text-lg">Pending Review</CardTitle>
            <Badge variant="secondary">{pendingDocs.length}</Badge>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-auto max-h-[300px]">
            {pendingDocs.length === 0 ? (
              <div className="p-8 text-center text-muted-foreground text-sm">
                Queue is empty
              </div>
            ) : (
              <div className="divide-y divide-border">
                {pendingDocs.map(doc => (
                  <div key={doc.id} className="p-4 hover:bg-muted/50 transition-colors flex items-center justify-between">
                    <div className="overflow-hidden mr-4">
                      <p className="text-sm font-medium truncate">{doc.name}</p>
                      <p className="text-xs text-muted-foreground mt-1 truncate">{doc.piiCount} PII flags • {doc.duplicateCount} dupes</p>
                    </div>
                    <Link href={`/admin/documents/${doc.id}`}>
                      <Button size="sm" variant="outline">Review</Button>
                    </Link>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Activity Feed */}
      <Card className="shadow-sm">
        <CardHeader>
          <CardTitle className="text-lg">Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            {activity?.map((item) => (
              <div key={item.id} className="flex gap-4">
                <div className="mt-0.5 p-2 bg-muted rounded-full">
                  {getActivityIcon(item.kind)}
                </div>
                <div>
                  <p className="text-sm font-medium">{item.title}</p>
                  {item.subtitle && <p className="text-sm text-muted-foreground mt-0.5">{item.subtitle}</p>}
                  <p className="text-xs text-muted-foreground mt-1">
                    {format(new Date(item.createdAt), 'MMM d, h:mm a')} {item.actor && `• by ${item.actor}`}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}