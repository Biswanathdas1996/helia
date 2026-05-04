import { useGetMe } from "@workspace/api-client-react";
import { useLocation } from "wouter";
import { useEffect } from "react";
import { useToast } from "@/hooks/use-toast";
import { Shell } from "@/components/layout/Shell";
import { Loader2 } from "lucide-react";

export function AdminGuard({ children }: { children: React.ReactNode }) {
  const { data: me, isLoading } = useGetMe();
  const [, setLocation] = useLocation();
  const { toast } = useToast();

  useEffect(() => {
    if (!isLoading && me && me.role !== "admin") {
      toast({ title: "Unauthorized", description: "Admin access required", variant: "destructive" });
      setLocation("/app");
    }
  }, [me, isLoading, setLocation, toast]);

  if (isLoading || !me || me.role !== "admin") {
    return (
      <Shell>
        <div className="flex h-full items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      </Shell>
    );
  }

  return <Shell>{children}</Shell>;
}