import { Link, useLocation } from "wouter";
import { useListTickets, getListTicketsQueryKey } from "@workspace/api-client-react";
import { formatDistanceToNow } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Plus, Ticket as TicketIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function Tickets() {
  const { data: tickets, isLoading } = useListTickets();
  const [, setLocation] = useLocation();

  const getStatusColor = (status: string) => {
    switch (status) {
      case "open": return "default";
      case "in_progress": return "secondary";
      case "resolved": return "outline";
      case "closed": return "outline";
      default: return "default";
    }
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "urgent": return "text-destructive border-destructive/20 bg-destructive/10";
      case "high": return "text-orange-600 border-orange-200 bg-orange-50 dark:text-orange-400 dark:border-orange-900/50 dark:bg-orange-900/20";
      default: return "text-muted-foreground border-border";
    }
  };

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Tickets</h1>
          <p className="text-muted-foreground mt-1">Manage your support escalations</p>
        </div>
        <Button onClick={() => setLocation("/app/tickets/new")}>
          <Plus className="mr-2 h-4 w-4" />
          New Ticket
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-24 rounded-lg bg-muted animate-pulse" />
          ))}
        </div>
      ) : tickets?.length === 0 ? (
        <Card className="text-center py-12 border-dashed">
          <CardContent>
            <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center mx-auto mb-4">
              <TicketIcon className="h-6 w-6 text-primary" />
            </div>
            <CardTitle className="mb-2">No tickets yet</CardTitle>
            <CardDescription className="mb-6">When the AI can't help, you can open a ticket here.</CardDescription>
            <Button onClick={() => setLocation("/app/tickets/new")} variant="outline">
              Create your first ticket
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {tickets?.map((ticket) => (
            <Card 
              key={ticket.id} 
              className="cursor-pointer hover:border-primary/50 transition-colors"
              onClick={() => setLocation(`/app/tickets/${ticket.id}`)}
            >
              <CardContent className="p-6 flex items-center justify-between">
                <div className="flex-1 min-w-0 pr-4">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-sm font-medium text-muted-foreground">#{ticket.id}</span>
                    <h3 className="font-semibold text-lg truncate">{ticket.subject}</h3>
                    {ticket.status === 'open' && <div className="w-2 h-2 rounded-full bg-primary" />}
                  </div>
                  <p className="text-sm text-muted-foreground truncate max-w-2xl">
                    {ticket.description}
                  </p>
                </div>
                
                <div className="flex items-center gap-4 flex-shrink-0">
                  <div className="flex flex-col items-end gap-2">
                    <Badge variant={getStatusColor(ticket.status) as any} className="capitalize">
                      {ticket.status.replace('_', ' ')}
                    </Badge>
                    <Badge variant="outline" className={`capitalize ${getPriorityColor(ticket.priority)}`}>
                      {ticket.priority}
                    </Badge>
                  </div>
                  <div className="text-xs text-muted-foreground text-right w-24">
                    {formatDistanceToNow(new Date(ticket.createdAt), { addSuffix: true })}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
