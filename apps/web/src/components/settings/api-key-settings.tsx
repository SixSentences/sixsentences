"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { SecretReveal, SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useApiKeyScopes, useApiKeys } from "@/hooks/queries";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ApiKeyScope, ApiKeyScopeDefinition } from "@/lib/types";

const FALLBACK_SCOPES: ApiKeyScopeDefinition[] = [
  { id: "research:read", label: "Read research", description: "Projects, runs, results and review records." },
  { id: "research:write", label: "Run research", description: "Create and control research workflows." },
  { id: "library:read", label: "Read library", description: "List sources and download workspace files." },
  { id: "library:write", label: "Write library", description: "Upload and attach sources." },
];

export default function ApiKeySettings() {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const queryClient = useQueryClient();
  const { data: keys, isLoading } = useApiKeys();
  const { data: definitions } = useApiKeyScopes();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ApiKeyScope[]>(["research:read"]);
  const [expiresInDays, setExpiresInDays] = useState("90");
  const [revealed, setRevealed] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<{ id: number; name: string } | null>(null);

  const create = useMutation({
    mutationFn: () => api.createApiKey(name.trim(), scopes, Number(expiresInDays)),
    onSuccess: (result) => {
      setRevealed(result.api_key);
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "API key creation failed."),
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeApiKey(id),
    onSuccess: (_result, keyId) => {
      setRevokeTarget((current) => current?.id === keyId ? null : current);
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success(isGerman ? "API-Schlüssel widerrufen." : "API key revoked.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "API key revocation failed."),
  });

  const scopeOptions = definitions?.length ? definitions : FALLBACK_SCOPES;

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SettingsSection
        title={isGerman ? "Neuer API-Schlüssel" : "New API key"}
        description={isGerman ? "Schlüssel werden nur einmal angezeigt. Vergib nur notwendige Rechte." : "Keys are shown once. Grant only the scopes the client needs."}
        icon={<Plus className="size-3.5" />}
      >
        <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
          <div className="space-y-1.5">
            <Label htmlFor="api-key-name">{isGerman ? "Name" : "Name"}</Label>
            <Input
              id="api-key-name"
              value={name}
              maxLength={80}
              placeholder="Research notebook"
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>{isGerman ? "Gültigkeit" : "Expires"}</Label>
            <Select value={expiresInDays} onValueChange={setExpiresInDays}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="30">30 days</SelectItem>
                <SelectItem value="90">90 days</SelectItem>
                <SelectItem value="365">365 days</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {scopeOptions.map((scope) => (
            <label key={scope.id} className="flex cursor-pointer gap-2 rounded-xl border border-border p-3">
              <Checkbox
                checked={scopes.includes(scope.id)}
                onCheckedChange={(checked) =>
                  setScopes((current) =>
                    checked
                      ? [...new Set([...current, scope.id])]
                      : current.filter((item) => item !== scope.id),
                  )
                }
              />
              <span>
                <span className="block text-[0.78125rem] font-medium">{scope.label}</span>
                <span className="mt-0.5 block text-[0.6875rem] leading-relaxed text-muted-foreground">{scope.description}</span>
              </span>
            </label>
          ))}
        </div>
        <Button
          type="button"
          className="mt-3 rounded-full"
          disabled={!name.trim() || scopes.length === 0 || create.isPending}
          onClick={() => create.mutate()}
        >
          {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
          {isGerman ? "Schlüssel erstellen" : "Create key"}
        </Button>
      </SettingsSection>

      {revealed ? (
        <SecretReveal
          secret={revealed}
          note={isGerman ? "Kopiere den Schlüssel jetzt; er wird nicht erneut angezeigt." : "Copy this key now; it will not be shown again."}
        />
      ) : null}

      <SettingsSection title={isGerman ? "Aktive Schlüssel" : "Active keys"} icon={<KeyRound className="size-3.5" />}>
        {isLoading ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : keys?.length ? (
          <div className="divide-y divide-border">
            {keys.map((key) => (
              <div key={key.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.8125rem] font-medium">{key.name}</p>
                  <p className="truncate font-mono text-[0.65625rem] text-muted-foreground">
                    {key.prefix}… · {key.scopes.join(", ")}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Revoke ${key.name}`}
                  disabled={revoke.isPending}
                  onClick={() => setRevokeTarget({ id: key.id, name: key.name })}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[0.75rem] text-muted-foreground">
            {isGerman ? "Keine aktiven API-Schlüssel." : "No active API keys."}
          </p>
        )}
      </SettingsSection>
      <ConfirmDeleteDialog
        target={revokeTarget ? {
          title: isGerman ? "API-Schlüssel widerrufen?" : "Revoke this API key?",
          description: isGerman
            ? `„${revokeTarget.name}“ funktioniert danach sofort nicht mehr.`
            : `“${revokeTarget.name}” will stop working immediately.`,
          action: isGerman ? "Widerrufen" : "Revoke key",
          cancel: isGerman ? "Behalten" : "Keep key",
        } : null}
        pending={revoke.isPending}
        onCancel={() => { if (!revoke.isPending) setRevokeTarget(null); }}
        onConfirm={() => { if (revokeTarget && !revoke.isPending) revoke.mutate(revokeTarget.id); }}
      />
    </div>
  );
}
