"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Database, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, downloadReferenceConnector } from "@/lib/api";

type ZoteroDialogProps = {
  runId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultIncludedOnly: boolean;
};

/** Publish a run through a persistent Zotero or Citavi connector. */
export default function ZoteroDialog({
  runId,
  open,
  onOpenChange,
  defaultIncludedOnly,
}: ZoteroDialogProps) {
  const { data: connectors } = useQuery({
    queryKey: ["reference-connectors"],
    queryFn: api.referenceConnectors,
    enabled: open,
  });
  const [connectorId, setConnectorId] = useState("");
  const [includedOnly, setIncludedOnly] = useState(defaultIncludedOnly);

  useEffect(() => {
    if (!connectorId && connectors?.[0]) setConnectorId(connectors[0].id);
  }, [connectorId, connectors]);

  const publish = useMutation({
    mutationFn: () => api.pushReferenceConnector(runId, connectorId, includedOnly),
    onSuccess: async (result) => {
      toast.success(
        `${result.created} new reference${result.created === 1 ? "" : "s"}` +
          (result.skipped ? ` · ${result.skipped} already present` : "") +
          (result.failed ? ` · ${result.failed} failed` : ""),
      );
      if (result.provider === "citavi") {
        await downloadReferenceConnector(connectorId, "ris");
      }
      onOpenChange(false);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Reference sync failed."),
  });

  const selected = connectors?.find((connector) => connector.id === connectorId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[30rem]">
        <DialogHeader>
          <DialogTitle>Reference manager</DialogTitle>
          <DialogDescription>
            Publish this search to a connected Zotero library or Citavi project.
          </DialogDescription>
        </DialogHeader>

        {(connectors ?? []).length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-4 py-6 text-center">
            <Database className="mx-auto size-5 text-moss" />
            <p className="mt-2 text-[0.8125rem] font-medium">No library connected yet</p>
            <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
              Open Settings → Integrations to connect Zotero or create a Citavi exchange project.
            </p>
          </div>
        ) : (
          <div className="space-y-3 py-1">
            <Select value={connectorId} onValueChange={setConnectorId}>
              <SelectTrigger className="h-11 w-full rounded-xl">
                <SelectValue placeholder="Select a library" />
              </SelectTrigger>
              <SelectContent>
                {(connectors ?? []).map((connector) => (
                  <SelectItem key={connector.id} value={connector.id}>
                    {connector.name} · {connector.provider} · {connector.item_count} items
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex items-center justify-between rounded-xl border border-border px-3.5 py-3">
              <span>
                <span className="block text-[0.8125rem] font-medium">Included works only</span>
                <span className="block text-[0.71875rem] text-muted-foreground">
                  {selected?.provider === "citavi"
                    ? "A Citavi-compatible RIS file downloads after publishing."
                    : "Existing library records are detected and not duplicated."}
                </span>
              </span>
              <Switch checked={includedOnly} onCheckedChange={setIncludedOnly} />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button
            onClick={() => publish.mutate()}
            disabled={publish.isPending || !connectorId}
            className="rounded-full"
          >
            {publish.isPending ? (
              <><Loader2 className="size-4 animate-spin" /> Syncing…</>
            ) : (
              `Publish to ${selected?.provider === "citavi" ? "Citavi" : "Zotero"}`
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
