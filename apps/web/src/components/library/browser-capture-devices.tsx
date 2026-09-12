"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe2, Laptop2, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";

export function BrowserCaptureDevices() {
  const { me } = useAuth();
  const german = me?.language === "de";
  const [open, setOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<{
    id: number;
    name: string;
  } | null>(null);
  const queryClient = useQueryClient();
  const devices = useQuery({
    queryKey: ["browser-capture-devices"],
    queryFn: api.browserCaptureDevices,
    enabled: open,
  });
  const revoke = useMutation({
    mutationFn: api.revokeBrowserCaptureDevice,
    onSuccess: (_, deviceId) => {
      setRevokeTarget((current) =>
        current?.id === deviceId ? null : current,
      );
      void queryClient.invalidateQueries({ queryKey: ["browser-capture-devices"] });
      toast.success(german ? "Browser-Zugriff widerrufen." : "Browser access revoked.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not revoke this browser."),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-9 rounded-full">
          <Globe2 className="size-3.5" />
          {german ? "Browser Capture" : "Browser Capture"}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{german ? "Verbundene Browser" : "Connected browsers"}</DialogTitle>
          <DialogDescription>
            {german
              ? "Verwalte die widerrufbaren Zugänge kompatibler Capture-Clients. Diese Open-Source-Distribution enthält keine Browser-Erweiterung."
              : "Manage revocable access for compatible capture clients. This open-source distribution does not include a browser extension."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {devices.isLoading ? (
            <div className="grid min-h-24 place-items-center"><Loader2 className="size-4 animate-spin text-moss" /></div>
          ) : (devices.data?.devices.length ?? 0) === 0 ? (
            <div className="rounded-2xl border border-dashed border-border p-5 text-center text-[0.8125rem] text-muted-foreground">
              {german ? "Noch kein Browser verbunden." : "No browser connected yet."}
            </div>
          ) : devices.data?.devices.map((device) => (
            <div key={device.id} className="flex items-center gap-3 rounded-2xl border border-border p-3">
              <span className="grid size-9 place-items-center rounded-xl bg-accent text-moss"><Laptop2 className="size-4" /></span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[0.8125rem] font-medium">{device.name || "Browser Capture"}</p>
                <p className="text-[0.6875rem] text-muted-foreground">
                  {german ? "Verbunden" : "Connected"} {formatDate(device.created_at)} · {device.prefix}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 rounded-full text-muted-foreground hover:text-destructive"
                aria-label={`${german ? "Zugriff widerrufen" : "Revoke access"}: ${device.name || "Browser Capture"}`}
                disabled={revoke.isPending}
                onClick={() =>
                  setRevokeTarget({
                    id: device.id,
                    name: device.name || "Browser Capture",
                  })
                }
              >
                {revoke.isPending && revoke.variables === device.id ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
              </Button>
            </div>
          ))}
        </div>
      </DialogContent>
      <ConfirmDeleteDialog
        target={
          revokeTarget
            ? {
                title: german ? "Browser-Zugriff widerrufen?" : "Revoke browser access?",
                description: german
                  ? `„${revokeTarget.name}“ kann danach keine Quellen mehr in deiner Library speichern.`
                  : `“${revokeTarget.name}” will no longer be able to save sources to your Library.`,
                action: german ? "Zugriff widerrufen" : "Revoke access",
                cancel: german ? "Browser behalten" : "Keep browser",
              }
            : null
        }
        pending={revoke.isPending}
        onCancel={() => {
          if (!revoke.isPending) setRevokeTarget(null);
        }}
        onConfirm={() => {
          const target = revokeTarget;
          if (!target || revoke.isPending) return;
          revoke.mutate(target.id);
        }}
      />
    </Dialog>
  );
}
