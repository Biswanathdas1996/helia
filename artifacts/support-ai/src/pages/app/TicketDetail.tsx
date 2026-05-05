import { useParams, useLocation } from "wouter";
import {
  useGetTicket,
  useUpdateTicket,
  useGetConversation,
  getGetTicketQueryKey,
  getListTicketsQueryKey,
  getGetConversationQueryKey,
} from "@workspace/api-client-react";
import { useGetMe } from "@workspace/api-client-react";
import { format } from "date-fns";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ArrowLeft, Clock, AlertCircle, MessageSquare } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/hooks/use-toast";

export default function TicketDetail() {
  const params = useParams<{ id: string }>();
  const id = parseInt(params.id!, 10);
  const [, setLocation] = useLocation();
  const { data: me } = useGetMe();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: ticket, isLoading } = useGetTicket(id, {
    query: { enabled: !!id, queryKey: getGetTicketQueryKey(id) }
  });

  const updateTicket = useUpdateTicket();

  const handleStatusChange = async (status: string) => {
    try {
      await updateTicket.mutateAsync({ 
        id, 
        data: { status: status as any, lastUpdate: `Status changed to ${status.replace('_', ' ')}` } 
      });
      queryClient.invalidateQueries({ queryKey: getGetTicketQueryKey(id) });
      queryClient.invalidateQueries({ queryKey: getListTicketsQueryKey() });
      toast({ title: "Status updated" });
    } catch (err) {
      toast({ title: "Failed to update status", variant: "destructive" });
    }
  };

  if (isLoading) return <div className="p-8">Loading...</div>;
  if (!ticket) return <div className="p-8">Ticket not found</div>;

  const isAdmin = me?.role === "admin";
  const relatedConversationMatch = ticket.description.match(/\bConversation ID:\s*(\d+)\b/i);
  const relatedConversationId = relatedConversationMatch
    ? Number.parseInt(relatedConversationMatch[1], 10)
    : null;

  const { data: relatedConversation, isLoading: loadingRelatedConversation } = useGetConversation(
    relatedConversationId as number,
    {
      query: {
        enabled: Boolean(relatedConversationId),
        queryKey: getGetConversationQueryKey(relatedConversationId as number),
      },
    },
  );

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <Button variant="ghost" onClick={() => setLocation(isAdmin ? "/admin/tickets" : "/app/tickets")} className="mb-6 -ml-4">
        <ArrowLeft className="mr-2 h-4 w-4" />
        Back to tickets
      </Button>

      <div className="flex justify-between items-start mb-8">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <span className="text-muted-foreground font-mono">#{ticket.id}</span>
            <Badge variant={ticket.status === 'open' ? 'default' : 'secondary'} className="capitalize">
              {ticket.status.replace('_', ' ')}
            </Badge>
            <Badge variant="outline" className="capitalize border-orange-200 text-orange-700 bg-orange-50 dark:border-orange-900/50 dark:text-orange-400 dark:bg-orange-900/20">
              {ticket.priority} priority
            </Badge>
          </div>
          <h1 className="text-3xl font-bold">{ticket.subject}</h1>
          <div className="text-sm text-muted-foreground mt-2 flex items-center gap-4">
            <span className="flex items-center"><Clock className="mr-1 h-3 w-3" /> {format(new Date(ticket.createdAt), "MMM d, yyyy h:mm a")}</span>
            <span className="flex items-center"><AlertCircle className="mr-1 h-3 w-3" /> {ticket.createdBy}</span>
          </div>
        </div>

        {isAdmin && (
          <div className="w-48">
            <Select value={ticket.status} onValueChange={handleStatusChange} disabled={updateTicket.isPending}>
              <SelectTrigger>
                <SelectValue placeholder="Update status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">Open</SelectItem>
                <SelectItem value="in_progress">In Progress</SelectItem>
                <SelectItem value="resolved">Resolved</SelectItem>
                <SelectItem value="closed">Closed</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        <div className="md:col-span-2 space-y-6">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-medium">Description</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="whitespace-pre-wrap text-sm leading-relaxed">
                {ticket.description}
              </div>
            </CardContent>
          </Card>

          {ticket.relatedMessageId && (
            <Card className="bg-muted/30">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-medium flex items-center">
                  <MessageSquare className="mr-2 h-4 w-4" />
                  Related Context
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground mb-4">This ticket was escalated from a conversation.</p>
                {relatedConversationId ? (
                  <div className="space-y-3">
                    <div className="text-xs text-muted-foreground">
                      Conversation #{relatedConversationId}
                    </div>
                    {loadingRelatedConversation ? (
                      <div className="text-sm text-muted-foreground">Loading conversation...</div>
                    ) : relatedConversation?.messages?.length ? (
                      <div className="max-h-80 overflow-y-auto rounded-md border border-border/70 bg-background p-3 space-y-2">
                        {relatedConversation.messages.map((message) => (
                          <div key={message.id} className="rounded-md border border-border/60 bg-muted/20 p-2.5">
                            <div className="flex items-center justify-between gap-2 mb-1.5">
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                                {message.role}
                              </span>
                              <span className="text-[11px] text-muted-foreground">
                                {format(new Date(message.createdAt), "MMM d, h:mm a")}
                              </span>
                            </div>
                            <div className="text-sm whitespace-pre-wrap leading-relaxed">
                              {message.content}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">
                        Conversation details are unavailable right now.
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">
                    Conversation details are unavailable for this ticket.
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-medium">Status Timeline</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="relative border-l border-border ml-3 pl-4 pb-4">
                <div className="absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full bg-primary ring-4 ring-background" />
                <p className="text-sm font-medium">Current Status</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {ticket.lastUpdate || `Ticket created`}
                </p>
                <p className="text-[10px] text-muted-foreground mt-2">
                  {format(new Date(ticket.updatedAt), "MMM d, h:mm a")}
                </p>
              </div>
              <div className="relative border-l border-transparent ml-3 pl-4">
                <div className="absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full bg-muted border border-border ring-4 ring-background" />
                <p className="text-sm font-medium text-muted-foreground">Ticket Created</p>
                <p className="text-[10px] text-muted-foreground mt-2">
                  {format(new Date(ticket.createdAt), "MMM d, h:mm a")}
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}