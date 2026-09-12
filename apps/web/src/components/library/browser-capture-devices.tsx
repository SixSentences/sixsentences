"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Globe2, Laptop2, Loader2, Trash2 } from "lucide-react";
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
import { resolveBrowserCaptureDistribution } from "@/lib/browser-capture-distribution.mjs";
import { formatDate } from "@/lib/format";

const browserCaptureDistribution = resolveBrowserCaptureDistribution({
  nodeEnv: process.env.NODE_ENV,
  storeUrl: process.env.NEXT_PUBLIC_BROWSER_CAPTURE_STORE_URL,
});

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
              ? "Widerrufe Browser, die keine Quellen mehr in deiner Library speichern dürfen."
              : "Revoke browsers that should no longer save sources to your Library."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {browserCaptureDistribution ? (
            <div className="rounded-2xl border border-border bg-muted/20 p-4">
              {browserCaptureDistribution.kind === "store" ? (
                <>
                  <p className="text-[0.8125rem] font-medium">
                    {german ? "Browser Capture installieren" : "Install Browser Capture"}
                  </p>
                  <p className="mt-1 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {german
                      ? "Installiere die Chrome-Erweiterung aus dem Chrome Web Store. Updates werden danach automatisch bereitgestellt."
                      : "Install the Chrome extension from the Chrome Web Store. Updates are delivered automatically."}
                  </p>
                  <Button asChild size="sm" className="mt-3 h-8 rounded-full">
                    <a
                      href={browserCaptureDistribution.href}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink className="size-3.5" />
                      {german ? "Chrome Web Store öffnen" : "Open Chrome Web Store"}
                    </a>
                  </Button>
                </>
              ) : (
                <>
                  <p className="text-[0.8125rem] font-medium">
                    {german ? "Chrome- und Edge-Preview installieren oder aktualisieren" : "Install or update the Chrome and Edge preview"}
                  </p>
                  <ol className="mt-2 list-inside list-decimal space-y-1 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    <li>{german ? "ZIP laden und entpacken." : "Download and unzip the package."}</li>
                    <li>{german ? "chrome://extensions oder edge://extensions öffnen." : "Open chrome://extensions or edge://extensions."}</li>
                    <li>{german ? "Entwicklermodus aktivieren und „Entpackte Erweiterung laden“ wählen." : "Enable Developer mode and choose Load unpacked."}</li>
                    <li>{german ? "Bei einer bestehenden Preview alle Dateien im geladenen Ordner ersetzen und auf „Neu laden“ klicken." : "For an existing preview, replace every file in its loaded folder and click Reload."}</li>
                  </ol>
                  <Button asChild size="sm" className="mt-3 h-8 rounded-full">
                    <a href={browserCaptureDistribution.href} download>
                      <Download className="size-3.5" />
                      {german ? "Preview herunterladen" : "Download preview"}
                    </a>
                  </Button>
                  <p className="mt-2 text-[0.625rem] text-muted-foreground">
                    {german ? "Version 0.1.9 · Entpackte Previews aktualisieren sich nicht automatisch · Version im Popup prüfen" : "Version 0.1.9 · unpacked previews do not auto-update · verify the version in the popup"}
                  </p>
                </>
              )}
            </div>
          ) : (
            <div className="rounded-2xl border border-border bg-muted/20 p-4">
              <p className="text-[0.8125rem] font-medium">
                {german ? "Chrome-Erweiterung in Prüfung" : "Chrome extension under review"}
              </p>
              <p className="mt-1 text-[0.71875rem] leading-relaxed text-muted-foreground">
                {german
                  ? "Der Installationslink erscheint hier, sobald die Erweiterung im Chrome Web Store freigegeben ist."
                  : "The install link will appear here as soon as the extension is approved in the Chrome Web Store."}
              </p>
            </div>
          )}
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
