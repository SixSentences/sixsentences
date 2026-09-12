"use client";

import {
  Blocks,
  ChartNoAxesColumn,
  KeyRound,
  Scale,
  SlidersHorizontal,
  User,
  Users,
  Webhook,
} from "lucide-react";
import { useEffect, useRef } from "react";

import AccountSettings from "@/components/settings/account-settings";
import ApiKeySettings from "@/components/settings/api-key-settings";
import AssistantSettings from "@/components/settings/assistant-settings";
import IntegrationSettings from "@/components/settings/integration-settings";
import { SettingsSection } from "@/components/settings/settings-section";
import TeamSettings from "@/components/settings/team-settings";
import WebhookSettings from "@/components/settings/webhook-settings";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";
import { publicLegalUrl } from "@/lib/public-links";
import { cn } from "@/lib/utils";

export type SettingsTab =
  | "account"
  | "assistant"
  | "api-keys"
  | "integrations"
  | "team"
  | "webhooks"
  | "legal";

/** Account and security controls stay reachable during a legal reacceptance. */
export function AccountRightsControls() {
  return <AccountSettings />;
}

function LegalSettings() {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const documents = [
    {
      href: publicLegalUrl("privacy"),
      title: isGerman ? "Datenschutzerklärung" : "Privacy policy",
      description: isGerman
        ? "Datenverarbeitung, Empfänger und Aufbewahrung."
        : "Data processing, recipients and retention.",
    },
    {
      href: publicLegalUrl("terms"),
      title: isGerman ? "Nutzungsbedingungen" : "Terms of service",
      description: isGerman
        ? "Bedingungen des betriebenen Dienstes."
        : "Terms for the operated service.",
    },
    {
      href: publicLegalUrl("imprint"),
      title: isGerman ? "Impressum" : "Legal notice",
      description: isGerman ? "Betreiber und Kontakt." : "Operator and contact details.",
    },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SettingsSection
        title={isGerman ? "Rechtliche Dokumente" : "Legal documents"}
        description={isGerman ? "Diese Links stammen vom konfigurierten Betreiber der Instanz." : "These links are provided by the configured instance operator."}
        icon={<Scale className="size-3.5" />}
      >
        <div className="divide-y divide-border">
          {documents.map((document) => (
            <a
              key={document.href}
              href={document.href}
              target="_blank"
              rel="noreferrer"
              className="block py-2.5 first:pt-0 last:pb-0"
            >
              <span className="block text-[0.8125rem] font-medium">{document.title}</span>
              <span className="mt-0.5 block text-[0.6875rem] text-muted-foreground">{document.description}</span>
            </a>
          ))}
        </div>
      </SettingsSection>
      <SettingsSection title={isGerman ? "Produktanalyse" : "Product analytics"} icon={<ChartNoAxesColumn className="size-3.5" />}>
        <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
          {isGerman
            ? "Optionale Produktanalyse im Browser ist in dieser Open-Source-Anwendung deaktiviert. Betreiber können notwendige Sicherheits- und Betriebsprotokolle serverseitig verarbeiten."
            : "Optional in-browser product analytics is disabled in this open-source application. Operators may process necessary security and operational logs on the server."}
        </p>
      </SettingsSection>
    </div>
  );
}

type SettingsDialogProps = {
  open: boolean;
  tab: SettingsTab;
  onOpenChange: (open: boolean) => void;
  onTabChange: (tab: SettingsTab) => void;
};

export default function SettingsDialog({
  open,
  tab,
  onOpenChange,
  onTabChange,
}: SettingsDialogProps) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const mobileNavRef = useRef<HTMLDivElement>(null);
  const tabs: Array<{ value: SettingsTab; label: string; icon: React.ReactNode }> = [
    { value: "account", label: isGerman ? "Konto" : "Account", icon: <User className="size-4" /> },
    { value: "assistant", label: isGerman ? "KI-Verhalten" : "AI behavior", icon: <SlidersHorizontal className="size-4" /> },
    { value: "api-keys", label: isGerman ? "API-Schlüssel" : "API keys", icon: <KeyRound className="size-4" /> },
    { value: "integrations", label: isGerman ? "Integrationen" : "Integrations", icon: <Blocks className="size-4" /> },
    { value: "team", label: "Team", icon: <Users className="size-4" /> },
    { value: "webhooks", label: "Webhooks", icon: <Webhook className="size-4" /> },
    { value: "legal", label: isGerman ? "Rechtliches" : "Legal", icon: <Scale className="size-4" /> },
  ];
  const active = tabs.find((item) => item.value === tab) ?? tabs[0];

  useEffect(() => {
    if (!open) return;
    mobileNavRef.current
      ?.querySelector<HTMLElement>(`[data-settings-tab="${tab}"]`)
      ?.scrollIntoView({ behavior: "auto", block: "nearest", inline: "center" });
  }, [open, tab]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[calc(100dvh-0.5rem)] flex-col gap-0 overflow-hidden rounded-xl p-0 sm:h-[min(42rem,calc(100dvh-3rem))] sm:max-w-[min(60rem,calc(100vw-3rem))] sm:rounded-2xl">
        <DialogHeader className="sr-only">
          <DialogTitle>{isGerman ? "Einstellungen" : "Settings"}</DialogTitle>
          <DialogDescription>
            {isGerman
              ? "Konto-, KI-, API-, Integrations-, Team-, Webhook- und Rechtseinstellungen."
              : "Account, AI, API, integration, team, webhook and legal settings."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1">
          <nav className="hidden w-60 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-border bg-secondary/35 p-3 md:flex">
            <p className="px-2.5 pb-3 pt-1.5 font-display text-[1.125rem]">
              {isGerman ? "Einstellungen" : "Settings"}
            </p>
            {tabs.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => onTabChange(item.value)}
                aria-current={tab === item.value ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[0.8125rem] transition-colors",
                  tab === item.value
                    ? "bg-card font-medium text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-card/60 hover:text-foreground",
                )}
              >
                <span className={tab === item.value ? "text-moss" : "text-muted-foreground/70"}>{item.icon}</span>
                {item.label}
              </button>
            ))}
          </nav>

          <div className="flex min-w-0 flex-1 flex-col">
            <div ref={mobileNavRef} className="mr-10 flex shrink-0 gap-1 overflow-x-auto border-b border-border p-2 md:hidden">
              {tabs.map((item) => (
                <button
                  key={item.value}
                  data-settings-tab={item.value}
                  type="button"
                  onClick={() => onTabChange(item.value)}
                  aria-current={tab === item.value ? "page" : undefined}
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[0.75rem]",
                    tab === item.value ? "bg-secondary font-medium" : "text-muted-foreground",
                  )}
                >
                  {item.icon}
                  {item.label}
                </button>
              ))}
            </div>
            <div className="shrink-0 border-b border-border px-4 py-3 sm:px-6 sm:py-4">
              <p className="font-display text-[1.25rem]">{active.label}</p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-6 sm:py-5">
              {tab === "account" ? <AccountSettings /> : null}
              {tab === "assistant" ? <AssistantSettings /> : null}
              {tab === "api-keys" ? <ApiKeySettings /> : null}
              {tab === "integrations" ? <IntegrationSettings /> : null}
              {tab === "team" ? <TeamSettings /> : null}
              {tab === "webhooks" ? <WebhookSettings /> : null}
              {tab === "legal" ? <LegalSettings /> : null}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
