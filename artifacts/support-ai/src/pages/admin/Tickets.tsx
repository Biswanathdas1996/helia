import { useListTickets, getListTicketsQueryKey } from "@workspace/api-client-react";
import { Link, useLocation } from "wouter";
import { formatDistanceToNow } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Ticket as TicketIcon } from "lucide-react";

export default function AdminTickets() {
  const { data: tickets, isLoading } = useListTickets(); // Usually an admin endpoint, but using the specified hook
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
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Support Tickets</h1>
          <p className="text-muted-foreground mt-1">Manage user escalations globally</p>
        </div>
      </div>

      <div className="border rounded-lg bg-background overflow-hidden">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="w-16">ID</TableHead>
              <TableHead>Subject</TableHead>
              <TableHead>User</TableHead>
              <TableHead>Priority</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow><TableCell colSpan={6} className="h-24 text-center">Loading...</TableCell></TableRow>
            ) : tickets?.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-48 text-center">
                  <TicketIcon className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
                  <p className="text-muted-foreground">No tickets found</p>
                </TableCell>
              </TableRow>
            ) : (
              tickets?.map((ticket) => (
                <TableRow 
                  key={ticket.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setLocation(`/app/tickets/${ticket.id}`)}
                >
                  <TableCell className="font-mono text-xs text-muted-foreground">#{ticket.id}</TableCell>
                  <TableCell className="font-medium max-w-xs truncate">{ticket.subject}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{ticket.createdByName ?? ticket.createdBy}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={`capitalize ${getPriorityColor(ticket.priority)}`}>
                      {ticket.priority}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={getStatusColor(ticket.status) as any} className="capitalize">
                      {ticket.status.replace('_', ' ')}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatDistanceToNow(new Date(ticket.createdAt), { addSuffix: true })}
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