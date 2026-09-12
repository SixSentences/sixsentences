"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Blocks, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function IntegrationSettings() {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const queryClient = useQueryClient();
  const { data: connectors, isLoading } = useQuery({
    queryKey: ["reference-connectors"],
    queryFn: api.referenceConnectors,
  });
  const [provider, setProvider] = useState<"zotero" | "citavi">("zotero");
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [libraryType, setLibraryType] = useState<"user" | "group">("user");
  const [libraryId, setLibraryId] = useState("");
  const [collectionKey, setCollectionKey] = useState("");
  const [removeConnectorTarget, setRemoveConnectorTarget] = useState<{ id: string; name: string } | null>(null);

  const create = useMutation({
    mutationFn: () =>
      provider === "zotero"
        ? api.createZoteroConnector({
            name: name.trim() || "Zotero",
            api_key: apiKey,
            library_type: libraryType,
            library_id: libraryId.trim(),
            ...(collectionKey.trim() ? { collection_key: collectionKey.trim() } : {}),
          })
        : api.createCitaviConnector(name.trim() || "Citavi"),
    onSuccess: (_result, connectorId) => {
      setRemoveConnectorTarget((current) => current?.id === connectorId ? null : current);
      setName("");
      setApiKey("");
      setLibraryId("");
      setCollectionKey("");
      void queryClient.invalidateQueries({ queryKey: ["reference-connectors"] });
      toast.success(isGerman ? "Integration verbunden." : "Integration connected.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Integration could not be connected."),
  });
  const sync = useMutation({
    mutationFn: (id: string) => api.syncReferenceConnector(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reference-connectors"] });
      toast.success(isGerman ? "Synchronisierung abgeschlossen." : "Synchronization complete.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Synchronization failed."),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteReferenceConnector(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reference-connectors"] });
      toast.success(isGerman ? "Integration entfernt." : "Integration removed.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Integration could not be removed."),
  });

  const canCreate = provider === "citavi"
    ? Boolean(name.trim())
    : Boolean(apiKey && libraryId.trim());

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SettingsSection
        title={isGerman ? "Referenzmanager verbinden" : "Connect reference manager"}
        description={isGerman ? "Zugangsdaten werden direkt an deinen konfigurierten API-Server übertragen." : "Credentials are sent directly to your configured API server."}
        icon={<Plus className="size-3.5" />}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>{isGerman ? "Anbieter" : "Provider"}</Label>
            <Select value={provider} onValueChange={(value) => setProvider(value as "zotero" | "citavi")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="zotero">Zotero</SelectItem>
                <SelectItem value="citavi">Citavi</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="connector-name">{isGerman ? "Anzeigename" : "Display name"}</Label>
            <Input id="connector-name" value={name} onChange={(event) => setName(event.target.value)} placeholder={provider === "zotero" ? "My Zotero" : "Research project"} />
          </div>
          {provider === "zotero" ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="zotero-key">Zotero API key</Label>
                <Input id="zotero-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>{isGerman ? "Bibliothekstyp" : "Library type"}</Label>
                <Select value={libraryType} onValueChange={(value) => setLibraryType(value as "user" | "group")}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="user">User</SelectItem>
                    <SelectItem value="group">Group</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="zotero-library">Library ID</Label>
                <Input id="zotero-library" value={libraryId} onChange={(event) => setLibraryId(event.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="zotero-collection">{isGerman ? "Collection Key (optional)" : "Collection key (optional)"}</Label>
                <Input id="zotero-collection" value={collectionKey} onChange={(event) => setCollectionKey(event.target.value)} />
              </div>
            </>
          ) : null}
        </div>
        <Button type="button" className="mt-3 rounded-full" disabled={!canCreate || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Blocks className="size-4" />}
          {isGerman ? "Verbinden" : "Connect"}
        </Button>
      </SettingsSection>

      <SettingsSection title={isGerman ? "Verbundene Integrationen" : "Connected integrations"} icon={<Blocks className="size-3.5" />}>
        {isLoading ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : connectors?.length ? (
          <div className="divide-y divide-border">
            {connectors.map((connector) => (
              <div key={connector.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.8125rem] font-medium">{connector.name}</p>
                  <p className="text-[0.65625rem] text-muted-foreground">
                    {connector.provider} · {connector.item_count} {isGerman ? "Einträge" : "items"}
                  </p>
                </div>
                <Button type="button" variant="outline" size="sm" disabled={sync.isPending} onClick={() => sync.mutate(connector.id)}>
                  {sync.isPending && sync.variables === connector.id ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                  Sync
                </Button>
                <Button type="button" variant="ghost" size="icon" aria-label={`Delete ${connector.name}`} disabled={remove.isPending} onClick={() => setRemoveConnectorTarget({ id: connector.id, name: connector.name })}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[0.75rem] text-muted-foreground">{isGerman ? "Keine Integrationen verbunden." : "No integrations connected."}</p>
        )}
      </SettingsSection>
      <ConfirmDeleteDialog
        target={removeConnectorTarget ? {
          title: isGerman ? "Integration entfernen?" : "Remove this integration?",
          description: isGerman
            ? `„${removeConnectorTarget.name}“ wird vom Workspace getrennt.`
            : `“${removeConnectorTarget.name}” will be disconnected from the workspace.`,
          action: isGerman ? "Entfernen" : "Remove integration",
          cancel: isGerman ? "Behalten" : "Keep integration",
        } : null}
        pending={remove.isPending}
        onCancel={() => { if (!remove.isPending) setRemoveConnectorTarget(null); }}
        onConfirm={() => { if (removeConnectorTarget && !remove.isPending) remove.mutate(removeConnectorTarget.id); }}
      />
    </div>
  );
}
