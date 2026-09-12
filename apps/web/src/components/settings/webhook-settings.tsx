"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2, Webhook } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { SecretReveal, SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useWebhooks } from "@/hooks/queries";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function WebhookSettings() {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const queryClient = useQueryClient();
  const { data: hooks, isLoading } = useWebhooks();
  const [url, setUrl] = useState("");
  const [revealed, setRevealed] = useState<string | null>(null);
  const [removeWebhookTarget, setRemoveWebhookTarget] = useState<{ id: number; url: string } | null>(null);
  const create = useMutation({
    mutationFn: () => api.createWebhook(url.trim(), []),
    onSuccess: (result) => {
      setUrl("");
      setRevealed(result.secret);
      void queryClient.invalidateQueries({ queryKey: ["webhooks"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Webhook creation failed."),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteWebhook(id),
    onSuccess: (_result, webhookId) => {
      setRemoveWebhookTarget((current) => current?.id === webhookId ? null : current);
      void queryClient.invalidateQueries({ queryKey: ["webhooks"] });
      toast.success(isGerman ? "Webhook gelöscht." : "Webhook deleted.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Webhook deletion failed."),
  });

  let validUrl = false;
  try {
    const parsed = new URL(url);
    validUrl = parsed.protocol === "https:" || parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
  } catch {
    validUrl = false;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SettingsSection
        title={isGerman ? "Webhook erstellen" : "Create webhook"}
        description={isGerman ? "Produktionsziele müssen HTTPS verwenden; lokale Entwicklungsziele dürfen HTTP nutzen." : "Production targets must use HTTPS; local development targets may use HTTP."}
        icon={<Plus className="size-3.5" />}
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="min-w-0 flex-1 space-y-1.5">
            <Label htmlFor="webhook-url">Endpoint URL</Label>
            <Input id="webhook-url" type="url" inputMode="url" placeholder="https://example.org/hooks/sixsentences" value={url} onChange={(event) => setUrl(event.target.value)} />
          </div>
          <Button type="button" className="self-end" disabled={!validUrl || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Webhook className="size-4" />}
            {isGerman ? "Erstellen" : "Create"}
          </Button>
        </div>
      </SettingsSection>

      {revealed ? (
        <SecretReveal
          secret={revealed}
          note={isGerman ? "Signatur-Schlüssel jetzt sicher speichern; er wird nicht erneut angezeigt." : "Store this signing secret now; it will not be shown again."}
        />
      ) : null}

      <SettingsSection title="Webhooks" icon={<Webhook className="size-3.5" />}>
        {isLoading ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : hooks?.length ? (
          <div className="divide-y divide-border">
            {hooks.map((hook) => (
              <div key={hook.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-[0.75rem]">{hook.url}</p>
                  <p className="text-[0.65625rem] text-muted-foreground">
                    {hook.events.length ? hook.events.join(", ") : (isGerman ? "Alle Ereignisse" : "All events")}
                  </p>
                </div>
                <Button type="button" variant="ghost" size="icon" aria-label={`Delete ${hook.url}`} disabled={remove.isPending} onClick={() => setRemoveWebhookTarget({ id: hook.id, url: hook.url })}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[0.75rem] text-muted-foreground">{isGerman ? "Keine Webhooks konfiguriert." : "No webhooks configured."}</p>
        )}
      </SettingsSection>
      <ConfirmDeleteDialog
        target={removeWebhookTarget ? {
          title: isGerman ? "Webhook löschen?" : "Delete this webhook?",
          description: isGerman
            ? `„${removeWebhookTarget.url}“ erhält danach keine Ereignisse mehr.`
            : `“${removeWebhookTarget.url}” will stop receiving events.`,
          action: isGerman ? "Löschen" : "Delete webhook",
          cancel: isGerman ? "Behalten" : "Keep webhook",
        } : null}
        pending={remove.isPending}
        onCancel={() => { if (!remove.isPending) setRemoveWebhookTarget(null); }}
        onConfirm={() => { if (removeWebhookTarget && !remove.isPending) remove.mutate(removeWebhookTarget.id); }}
      />
    </div>
  );
}
