"use client";

import { useId } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PUBLIC_WEB_SEARCH_NOTICE_VERSION } from "@/lib/public-web-search-query";
import { publicLegalUrl } from "@/lib/public-links";
import { cn } from "@/lib/utils";

interface PublicWebSearchApprovalProps {
  language: "de" | "en";
  query: string;
  onQueryChange?: (value: string) => void;
  exactQuery?: boolean;
  confirmed: boolean;
  onConfirmedChange: (checked: boolean) => void;
  className?: string;
}

/** A scoped public-search acknowledgement, not permission to forward workspace content. */
export default function PublicWebSearchApproval({
  language,
  query,
  onQueryChange,
  exactQuery,
  confirmed,
  onConfirmedChange,
  className,
}: PublicWebSearchApprovalProps) {
  const id = useId();
  const isGerman = language === "de";
  const editable = Boolean(onQueryChange);
  const exact = exactQuery ?? editable;
  const privacyUrl = publicLegalUrl("privacy");

  return (
    <div
      data-notice-version={exact ? PUBLIC_WEB_SEARCH_NOTICE_VERSION : undefined}
      className={cn("min-w-0 space-y-2 rounded-xl border border-moss/25 bg-accent/35 p-2.5", className)}
    >
      <div className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
        <p id={`${id}-query-label`} className="shrink-0 font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted-foreground sm:max-w-40">
          {exact
            ? isGerman ? "Websuche" : "Web search"
            : isGerman ? "Öffentlicher Suchauftrag" : "Public search request"}
        </p>
        {onQueryChange ? (
          <Input
            aria-labelledby={`${id}-query-label`}
            value={query}
            maxLength={400}
            placeholder={isGerman ? "Öffentliches Suchthema eingeben" : "Enter a public search topic"}
            onChange={(event) => {
              onQueryChange(event.target.value);
              onConfirmedChange(false);
            }}
            className="h-8 min-w-0 flex-1 rounded-lg bg-background/60 text-[0.75rem]"
          />
        ) : (
          <div
            role="region"
            aria-labelledby={`${id}-query-label`}
            tabIndex={0}
            className="max-h-24 min-w-0 flex-1 overflow-y-auto overscroll-contain whitespace-pre-wrap break-words rounded-lg bg-background/45 px-2.5 py-1.5 text-[0.75rem] leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {query}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <Checkbox
            id={`${id}-confirmed`}
            checked={confirmed}
            onCheckedChange={(checked) => onConfirmedChange(checked === true)}
            aria-describedby={`${id}-exclusion`}
            aria-required="true"
            className="mt-0.5"
          />
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <Label htmlFor={`${id}-confirmed`} className="cursor-pointer text-[0.75rem] leading-relaxed">
              {isGerman ? "Nur öffentliche Suchbegriffe" : "Public search terms only"}
            </Label>
            <p id={`${id}-exclusion`} className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              {isGerman ? "Keine persönlichen oder vertraulichen Inhalte." : "No personal or confidential content."}
            </p>
          </div>
        </div>
        <details className="ml-auto min-w-0 text-[0.6875rem] leading-relaxed text-muted-foreground open:basis-full">
          <summary
            aria-label={isGerman ? "Details zur Websuche" : "Web search details"}
            className="w-fit cursor-pointer rounded-sm text-moss outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Details
          </summary>
          <div className="mt-2 space-y-2">
            <p>
              {exact
                ? isGerman
                  ? "Die Freigabe gilt nur für die oben sichtbaren Suchbegriffe."
                  : "This approval applies only to the search terms shown above."
                : isGerman
                  ? "Aus dem bestätigten öffentlichen Suchauftrag werden Suchbegriffe formuliert. Auch eine zugehörige Protokollabfrage und Kriterien dürfen nur öffentliche Angaben enthalten."
                  : "Search terms are formulated from the approved public search request. Any associated protocol query and criteria must also contain public information only."}
            </p>
            <p>
              {isGerman
                ? "Keine personenbezogenen Daten, vertraulichen oder sensiblen Angaben verwenden. Der Websuche werden Gesprächsverlauf, Transkripte, Manuskripte und Uploads nicht automatisch beigefügt. Öffentliche Suchbegriffe daraus benötigen deine ausdrückliche Freigabe."
                : "Do not use personal data, confidential or sensitive information. The web search does not automatically include conversation history, transcripts, manuscripts or uploads. Public search terms from that context require your explicit approval."}
            </p>
            <p>
              {isGerman
                ? "Die minimierten Suchbegriffe gehen an den von dieser Instanz konfigurierten öffentlichen Suchdienst. Angaben zu Empfängern, Verarbeitungsorten und Aufbewahrung muss der Betreiber in seinen Datenschutzhinweisen veröffentlichen."
                : "The minimized search terms are sent to the public-search service configured by this deployment. Its operator must disclose recipients, processing locations and retention in the privacy notice."}
            </p>
            <p>
              {isGerman
                ? "Suchfilter garantieren keine vollständige Anonymisierung. "
                : "Search filters do not guarantee full anonymization. "}
              {privacyUrl ? (
                <a href={privacyUrl} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
                  {isGerman ? "Datenschutzhinweise" : "Privacy notice"}
                </a>
              ) : (isGerman ? "Bitte den Betreiber nach den Datenschutzhinweisen fragen." : "Ask the deployment operator for its privacy notice.")}
            </p>
          </div>
        </details>
      </div>
    </div>
  );
}
