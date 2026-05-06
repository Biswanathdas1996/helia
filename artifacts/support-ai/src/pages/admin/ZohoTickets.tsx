import { useListZohoSyncedTickets } from "@workspace/api-client-react";
import { useLocation } from "wouter";
import { formatDistanceToNow } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ExternalLink, Globe } from "lucide-react";

function zohoDeskId(externalId: string | null | undefined): string | null {
  if (!externalId?.startsWith("zoho:")) return null;
  return externalId.slice("zoho:".length).trim() || null;
}

/** Agent UI deep link; requires org id (same value as Zoho Desk ``ZOHO_ORG_ID`` / API header). */
function zohoDeskTicketUrl(ticketId: string): string | null {
  const template = import.meta.env.VITE_ZOHO_DESK_TICKET_URL_TEMPLATE?.trim();
  if (template) {
    const orgId = import.meta.env.VITE_ZOHO_DESK_ORG_ID?.trim() ?? "";
    return template.replaceAll("{ticketId}", ticketId).replaceAll("{orgId}", orgId);
  }
  const orgId = import.meta.env.VITE_ZOHO_DESK_ORG_ID?.trim();
  if (!orgId) return null;
  const origin = (
    import.meta.env.VITE_ZOHO_DESK_WEB_ORIGIN?.trim() || "https://desk.zoho.com"
  ).replace(/\/$/, "");
  return `${origin}/agent/${orgId}/tickets/detail/${encodeURIComponent(ticketId)}`;
}

export default function AdminZohoTickets() {
  const { data: tickets, isLoading } = useListZohoSyncedTickets();
  const [, setLocation] = useLocation();

  const getStatusColor = (status: string) => {
    switch (status) {
      case "open":
        return "default";
      case "in_progress":
        return "secondary";
      case "resolved":
        return "outline";
      case "closed":
        return "outline";
      default:
        return "default";
    }
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "urgent":
        return "text-destructive border-destructive/20 bg-destructive/10";
      case "high":
        return "text-orange-600 border-orange-200 bg-orange-50 dark:text-orange-400 dark:border-orange-900/50 dark:bg-orange-900/20";
      default:
        return "text-muted-foreground border-border";
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex justify-between items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Globe className="h-8 w-8 text-muted-foreground" aria-hidden />
            Zoho Desk tickets
          </h1>
          <p className="text-muted-foreground mt-1">
            Support tickets that were created or linked in Zoho Desk (synced from Helia).
          </p>
        </div>
      </div>

      <div className="border rounded-lg bg-background overflow-hidden">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="w-16">Helia #</TableHead>
              <TableHead className="w-44">Zoho ID</TableHead>
              <TableHead>Subject</TableHead>
              <TableHead>User</TableHead>
              <TableHead>Priority</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center">
                  Loading...
                </TableCell>
              </TableRow>
            ) : tickets?.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-48 text-center">
                  <Globe className="h-8 w-8 text-muted-foreground mx-auto mb-2" aria-hidden />
                  <p className="text-muted-foreground">No Zoho-linked tickets yet</p>
                  <p className="text-sm text-muted-foreground/80 mt-1">
                    Tickets appear here once they are created with a Zoho Desk external id.
                  </p>
                </TableCell>
              </TableRow>
            ) : (
              tickets?.map((ticket) => {
                const zid = zohoDeskId(ticket.externalId);
                const deskUrl = zid ? zohoDeskTicketUrl(zid) : null;
                return (
                  <TableRow
                    key={ticket.id}
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => setLocation(`/app/tickets/${ticket.id}`)}
                  >
                    <TableCell className="font-mono text-xs text-muted-foreground">#{ticket.id}</TableCell>
                    <TableCell className="font-mono text-xs">
                      <div className="flex items-center gap-2">
                        <span>{zid ?? "—"}</span>
                        {zid &&
                          (deskUrl ? (
                            <a
                              href={deskUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-primary hover:opacity-90 shrink-0"
                              aria-label={`Open ticket ${zid} in Zoho Desk`}
                              title="Open in Zoho Desk"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          ) : (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span
                                  className="inline-flex text-muted-foreground cursor-help shrink-0"
                                  onClick={(e) => e.stopPropagation()}
                                  aria-label="Desk link not configured"
                                >
                                  <ExternalLink className="h-3.5 w-3.5 opacity-40" />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent className="max-w-xs text-xs">
                                Set{" "}
                                <span className="font-mono">VITE_ZOHO_DESK_ORG_ID</span> (same as Desk{" "}
                                <span className="font-mono">ZOHO_ORG_ID</span>) when building the web app.
                                Use{" "}
                                <span className="font-mono">VITE_ZOHO_DESK_WEB_ORIGIN</span> for EU (
                                <span className="font-mono">https://desk.zoho.eu</span>) or other regions.
                              </TooltipContent>
                            </Tooltip>
                          ))}
                      </div>
                    </TableCell>
                    <TableCell className="font-medium max-w-xs truncate">{ticket.subject}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{ticket.createdBy}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`capitalize ${getPriorityColor(ticket.priority)}`}>
                        {ticket.priority}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={getStatusColor(ticket.status) as "default" | "secondary" | "outline"} className="capitalize">
                        {ticket.status.replace("_", " ")}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDistanceToNow(new Date(ticket.createdAt), { addSuffix: true })}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
