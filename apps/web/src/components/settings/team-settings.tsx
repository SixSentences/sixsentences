"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useMembers } from "@/hooks/queries";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function TeamSettings() {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const canInvite = me?.role === "owner";
  const queryClient = useQueryClient();
  const { data: members, isLoading } = useMembers();
  const [email, setEmail] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [role, setRole] = useState("member");
  const add = useMutation({
    mutationFn: () => api.addMember(email.trim(), temporaryPassword, role),
    onSuccess: () => {
      setEmail("");
      setTemporaryPassword("");
      void queryClient.invalidateQueries({ queryKey: ["members"] });
      toast.success(isGerman ? "Mitglied hinzugefügt." : "Member added.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Member could not be added."),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      {canInvite ? (
        <SettingsSection
          title={isGerman ? "Mitglied hinzufügen" : "Add member"}
          description={isGerman ? "Teile das temporäre Passwort über einen getrennten sicheren Kanal." : "Share the temporary password through a separate secure channel."}
          icon={<UserPlus className="size-3.5" />}
        >
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_8rem]">
            <div className="space-y-1.5">
              <Label htmlFor="team-email">Email</Label>
              <Input id="team-email" type="email" autoComplete="off" value={email} onChange={(event) => setEmail(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="team-password">{isGerman ? "Temporäres Passwort" : "Temporary password"}</Label>
              <Input id="team-password" type="password" autoComplete="new-password" value={temporaryPassword} onChange={(event) => setTemporaryPassword(event.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>{isGerman ? "Rolle" : "Role"}</Label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">Member</SelectItem>
                  <SelectItem value="reviewer">Reviewer</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <Button
            type="button"
            className="mt-3 rounded-full"
            disabled={!email.includes("@") || temporaryPassword.length < 8 || add.isPending}
            onClick={() => add.mutate()}
          >
            {add.isPending ? <Loader2 className="size-4 animate-spin" /> : <UserPlus className="size-4" />}
            {isGerman ? "Mitglied hinzufügen" : "Add member"}
          </Button>
        </SettingsSection>
      ) : null}

      <SettingsSection title={isGerman ? "Workspace-Mitglieder" : "Workspace members"} icon={<Users className="size-3.5" />}>
        {isLoading ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : members?.length ? (
          <div className="divide-y divide-border">
            {members.map((member) => (
              <div key={member.id} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="truncate text-[0.8125rem] font-medium">{member.email}</p>
                  <p className="text-[0.6875rem] text-muted-foreground">{member.role}</p>
                </div>
                <span className="rounded-full border border-border px-2 py-0.5 text-[0.625rem] text-muted-foreground">
                  {member.is_active ? (isGerman ? "Aktiv" : "Active") : (isGerman ? "Inaktiv" : "Inactive")}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[0.75rem] text-muted-foreground">{isGerman ? "Keine Mitglieder gefunden." : "No members found."}</p>
        )}
      </SettingsSection>
    </div>
  );
}
