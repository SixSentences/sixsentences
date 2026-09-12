"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  Download,
  KeyRound,
  Languages,
  Loader2,
  LockKeyhole,
  Monitor,
  Moon,
  Sun,
  Trash2,
  User,
} from "lucide-react";
import { useTheme } from "next-themes";
import QRCode from "qrcode";
import { useState } from "react";
import { toast } from "sonner";

import { SecretReveal, SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, downloadAccountExport } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { TwoFactorSetup } from "@/lib/types";
import { cn } from "@/lib/utils";

function PasswordSettings({ isGerman }: { isGerman: boolean }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const changePassword = useMutation({
    mutationFn: () => api.changePassword(currentPassword, newPassword),
    onSuccess: () => {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      toast.success(isGerman ? "Passwort aktualisiert." : "Password updated.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Password update failed."),
  });
  const ready =
    currentPassword.length > 0
    && newPassword.length >= 8
    && newPassword === confirmation
    && !changePassword.isPending;

  return (
    <SettingsSection
      title={isGerman ? "Passwort" : "Password"}
      icon={<LockKeyhole className="size-3.5" />}
    >
      <form
        method="post"
        className="grid gap-3 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) changePassword.mutate();
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="settings-current-password">
            {isGerman ? "Aktuell" : "Current"}
          </Label>
          <Input
            id="settings-current-password"
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="settings-new-password">
            {isGerman ? "Neu" : "New"}
          </Label>
          <Input
            id="settings-new-password"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="settings-confirm-password">
            {isGerman ? "Bestätigen" : "Confirm"}
          </Label>
          <Input
            id="settings-confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </div>
        <div className="sm:col-span-3 sm:flex sm:justify-end">
          <Button type="submit" disabled={!ready} className="rounded-full">
            {changePassword.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            {isGerman ? "Passwort speichern" : "Save password"}
          </Button>
        </div>
      </form>
    </SettingsSection>
  );
}

function TwoFactorSettings({ isGerman }: { isGerman: boolean }) {
  const queryClient = useQueryClient();
  const { data: status, isLoading } = useQuery({
    queryKey: ["two-factor-status"],
    queryFn: api.twoFactorStatus,
  });
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);

  const begin = useMutation({
    mutationFn: () => api.setupTwoFactor(password),
    onSuccess: async (result) => {
      setSetup(result);
      setCode("");
      try {
        setQrCode(await QRCode.toDataURL(result.otpauth_uri, { width: 220, margin: 1 }));
      } catch {
        setQrCode(null);
      }
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Two-factor setup failed."),
  });
  const enable = useMutation({
    mutationFn: () => api.enableTwoFactor(code),
    onSuccess: (result) => {
      setRecoveryCodes(result.recovery_codes);
      setSetup(null);
      setQrCode(null);
      setPassword("");
      setCode("");
      void queryClient.invalidateQueries({ queryKey: ["two-factor-status"] });
      toast.success(isGerman ? "Zwei-Faktor-Schutz aktiviert." : "Two-factor protection enabled.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Verification failed."),
  });
  const disable = useMutation({
    mutationFn: () => api.disableTwoFactor(password, code),
    onSuccess: () => {
      setPassword("");
      setCode("");
      setRecoveryCodes([]);
      void queryClient.invalidateQueries({ queryKey: ["two-factor-status"] });
      toast.success(isGerman ? "Zwei-Faktor-Schutz deaktiviert." : "Two-factor protection disabled.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Two-factor update failed."),
  });

  return (
    <SettingsSection
      title={isGerman ? "Zwei-Faktor-Authentifizierung" : "Two-factor authentication"}
      description={
        isGerman
          ? "Schütze dein Konto zusätzlich mit einer Authenticator-App."
          : "Add an authenticator app as a second sign-in factor."
      }
      icon={<KeyRound className="size-3.5" />}
    >
      {isLoading ? (
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
      ) : recoveryCodes.length > 0 ? (
        <SecretReveal
          secret={recoveryCodes.join("  ")}
          note={isGerman ? "Speichere diese Wiederherstellungscodes jetzt sicher." : "Store these recovery codes safely now."}
        />
      ) : setup ? (
        <div className="grid gap-4 sm:grid-cols-[auto_1fr]">
          {qrCode ? (
            // The QR image is generated locally from the server-provided setup URI.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={qrCode} alt="Authenticator setup QR code" className="size-44 rounded-lg bg-white p-2" />
          ) : null}
          <div className="space-y-3">
            <SecretReveal
              secret={setup.secret}
              note={isGerman ? "Alternativer manueller Einrichtungsschlüssel." : "Manual setup key if you cannot scan the code."}
            />
            <div className="flex gap-2">
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
              />
              <Button
                type="button"
                disabled={code.length !== 6 || enable.isPending}
                onClick={() => enable.mutate()}
              >
                {enable.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                {isGerman ? "Aktivieren" : "Enable"}
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            type="password"
            autoComplete="current-password"
            placeholder={isGerman ? "Aktuelles Passwort" : "Current password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {status?.enabled ? (
            <>
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder={isGerman ? "Authenticator-Code" : "Authenticator code"}
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))}
              />
              <Button
                type="button"
                variant="outline"
                disabled={!password || code.length < 6 || disable.isPending}
                onClick={() => disable.mutate()}
              >
                {disable.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                {isGerman ? "Deaktivieren" : "Disable"}
              </Button>
            </>
          ) : (
            <Button
              type="button"
              disabled={!password || begin.isPending}
              onClick={() => begin.mutate()}
            >
              {begin.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              {isGerman ? "Einrichten" : "Set up"}
            </Button>
          )}
        </div>
      )}
    </SettingsSection>
  );
}

export default function AccountSettings() {
  const { me, setLanguage, signOut } = useAuth();
  const { theme, setTheme } = useTheme();
  const [exportPassword, setExportPassword] = useState("");
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const isGerman = me?.language === "de";

  const exportAccount = useMutation({
    mutationFn: () => downloadAccountExport(exportPassword),
    onSuccess: () => {
      setExportPassword("");
      toast.success(isGerman ? "Datenexport heruntergeladen." : "Data export downloaded.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Export failed."),
  });
  const deleteAccount = useMutation({
    mutationFn: () => api.deleteAccount(deletePassword),
    onSuccess: () => signOut(),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Account deletion failed."),
  });

  if (!me) return null;

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SettingsSection title={isGerman ? "Konto" : "Account"} icon={<User className="size-3.5" />}>
        <dl className="grid gap-2 text-[0.8125rem] sm:grid-cols-[8rem_1fr]">
          <dt className="text-muted-foreground">Email</dt>
          <dd className="break-all">{me.email}</dd>
          <dt className="text-muted-foreground">{isGerman ? "Rolle" : "Role"}</dt>
          <dd>{me.role}</dd>
        </dl>
      </SettingsSection>

      <SettingsSection title={isGerman ? "Sprache" : "Language"} icon={<Languages className="size-3.5" />}>
        <div className="flex gap-2">
          {(["en", "de"] as const).map((language) => (
            <Button
              key={language}
              type="button"
              variant={me.language === language ? "default" : "outline"}
              className="rounded-full"
              onClick={() => void setLanguage(language)}
            >
              {language === "de" ? "Deutsch" : "English"}
            </Button>
          ))}
        </div>
      </SettingsSection>

      <SettingsSection title={isGerman ? "Darstellung" : "Appearance"} icon={<Monitor className="size-3.5" />}>
        <div className="grid gap-2 sm:grid-cols-3">
          {([
            { value: "light", label: isGerman ? "Hell" : "Light", icon: Sun },
            { value: "dark", label: isGerman ? "Dunkel" : "Dark", icon: Moon },
            { value: "system", label: "System", icon: Monitor },
          ] as const).map((option) => {
            const Icon = option.icon;
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={theme === option.value}
                onClick={() => setTheme(option.value)}
                className={cn(
                  "flex items-center gap-2 rounded-xl border px-3 py-2 text-[0.8125rem] transition-colors",
                  theme === option.value ? "border-moss/50 bg-accent" : "border-border hover:bg-secondary/60",
                )}
              >
                <Icon className="size-4 text-moss" />
                {option.label}
              </button>
            );
          })}
        </div>
      </SettingsSection>

      <PasswordSettings isGerman={isGerman} />
      <TwoFactorSettings isGerman={isGerman} />

      <SettingsSection
        title={isGerman ? "Datenexport" : "Data export"}
        description={isGerman ? "Lade eine portable Kopie deiner Workspace-Daten herunter." : "Download a portable copy of your workspace data."}
        icon={<Database className="size-3.5" />}
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            type="password"
            autoComplete="current-password"
            placeholder={isGerman ? "Aktuelles Passwort" : "Current password"}
            value={exportPassword}
            onChange={(event) => setExportPassword(event.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            disabled={!exportPassword || exportAccount.isPending}
            onClick={() => exportAccount.mutate()}
          >
            {exportAccount.isPending ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
            {isGerman ? "Export laden" : "Download export"}
          </Button>
        </div>
      </SettingsSection>

      <SettingsSection
        title={isGerman ? "Gefahrenbereich" : "Danger zone"}
        description={isGerman ? "Die Kontolöschung ist endgültig und kann Workspace-Daten entfernen." : "Account deletion is permanent and may remove workspace data."}
        icon={<Trash2 className="size-3.5 text-destructive" />}
        className="border-destructive/35"
      >
        <div className="grid gap-2 sm:grid-cols-2">
          <Input
            type="password"
            autoComplete="current-password"
            placeholder={isGerman ? "Aktuelles Passwort" : "Current password"}
            value={deletePassword}
            onChange={(event) => setDeletePassword(event.target.value)}
          />
          <Input
            aria-label={isGerman ? "Löschbestätigung" : "Deletion confirmation"}
            placeholder={isGerman ? "DELETE eingeben" : "Type DELETE"}
            value={deleteConfirmation}
            onChange={(event) => setDeleteConfirmation(event.target.value)}
          />
        </div>
        <Button
          type="button"
          variant="destructive"
          className="mt-2"
          disabled={!deletePassword || deleteConfirmation !== "DELETE" || deleteAccount.isPending}
          onClick={() => deleteAccount.mutate()}
        >
          {deleteAccount.isPending ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
          {isGerman ? "Konto endgültig löschen" : "Delete account permanently"}
        </Button>
      </SettingsSection>
    </div>
  );
}
