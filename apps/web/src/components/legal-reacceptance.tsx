"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { publicLegalUrl } from "@/lib/public-links";
import type { Me } from "@/lib/types";
import { AccountRightsControls } from "@/components/settings/settings-dialog";

const legalLinkClass = "font-medium underline underline-offset-4 hover:text-foreground";

function LegalInformationLinks({ german }: { german: boolean }) {
  const links = [
    { href: publicLegalUrl("privacy"), label: german ? "Datenschutzrechte" : "Privacy rights" },
    { href: publicLegalUrl("imprint"), label: german ? "Betreiber und Kontakt" : "Operator and contact" },
  ].filter((link): link is { href: string; label: string } => link.href !== null);
  if (links.length === 0) return null;

  return (
    <nav aria-label={german ? "Rechtliche Informationen" : "Legal information"} className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
      {links.map((link) => <a key={link.href} className={legalLinkClass} href={link.href}>{link.label}</a>)}
    </nav>
  );
}

export function LegalReacceptance({
  me, onAccepted, onSignOut,
}: {
  me: Me;
  onAccepted: () => Promise<void>;
  onSignOut: () => void;
}) {
  const german = me.language === "de";
  const required = me.legal_requirements;
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [dpaAccepted, setDpaAccepted] = useState(false);
  const [controllerName, setControllerName] = useState(() => me.legal_controller_name ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const [error, setError] = useState("");
  const needDpa = Boolean(required?.dpa && required.dpa_can_accept);
  const waitingForOwner = Boolean(required?.dpa && !required.dpa_can_accept);
  const hasOwnDecision = Boolean(required?.age || required?.terms || needDpa);
  const termsUrl = publicLegalUrl("terms");
  const dpaUrl = publicLegalUrl("dpa");
  const privacyUrl = publicLegalUrl("privacy");
  const requiredDocumentMissing = Boolean(
    (required?.terms && !termsUrl) || (needDpa && !dpaUrl),
  );
  const ready = Boolean(required && hasOwnDecision)
    && !requiredDocumentMissing
    && (!required?.age || ageConfirmed)
    && (!required?.terms || termsAccepted)
    && (!needDpa || (dpaAccepted && controllerName.trim().length >= 2));

  async function submit() {
    if (!ready || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await api.acceptCurrentLegalTerms({
        age_requirement_confirmed: Boolean(required?.age && ageConfirmed),
        terms_accepted: Boolean(required?.terms && termsAccepted),
        terms_version: me.current_terms_version,
        privacy_acknowledged: false,
        privacy_version: me.current_privacy_version,
        dpa_accepted: needDpa && dpaAccepted,
        dpa_version: me.current_dpa_version,
        controller_name: controllerName.trim(),
        controller_authority_confirmed: needDpa && dpaAccepted,
      });
      await onAccepted();
    } catch {
      setError(german
        ? "Die Bestätigung konnte nicht gespeichert werden. Bitte lade die Seite neu und versuche es erneut. Deine Kontorechte bleiben erreichbar."
        : "We could not save your confirmation. Please reload and try again. Your account rights remain available.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main lang={german ? "de" : "en"} className="min-h-dvh bg-background px-4 py-8 text-foreground sm:py-14">
      <section className="mx-auto w-full max-w-2xl rounded-[32px] border border-border bg-card p-6 sm:p-9">
        <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">{german ? "Dein Workspace" : "Your workspace"}</p>
        <h1 className="mt-3 font-serif text-3xl leading-tight">{german ? "Ein kurzer Blick auf die Bedingungen." : "A quick review of the terms."}</h1>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          {german
            ? "Hier stehen nur die Erklärungen, die noch fehlen. Datenexport und Kontosicherheit bleiben unabhängig davon verfügbar."
            : "Only outstanding declarations appear here. Data export and account security remain available independently."}
        </p>
        {!required && <p role="alert" className="mt-4 text-sm">{german ? "Bitte lade die Seite neu, um den aktuellen Stand zu erhalten." : "Please reload to get the current requirements."}</p>}
        <div className="mt-7 space-y-5">
          {required?.age && (
            <label className="flex cursor-pointer gap-3 text-sm leading-6">
              <input type="checkbox" checked={ageConfirmed} onChange={(event) => setAgeConfirmed(event.target.checked)} className="mt-1 size-4 shrink-0 accent-foreground" />
              <span>{german ? "Ich bestätige, dass ich mindestens 18 Jahre alt bin." : "I confirm that I am at least 18 years old."}</span>
            </label>
          )}
          {required?.terms && (
            <div className="space-y-3">
              <p className="text-xs leading-5 text-muted-foreground">
                {german
                  ? "Die verbundene API verlangt die Bestätigung aktualisierter Nutzungsbedingungen. Prüfe das vom Betreiber veröffentlichte Dokument, bevor du bestätigst."
                  : "The connected API requires acceptance of updated terms. Review the document published by the deployment operator before confirming."}
              </p>
              {termsUrl ? (
                <label className="flex cursor-pointer gap-3 text-sm leading-6">
                  <input type="checkbox" checked={termsAccepted} onChange={(event) => setTermsAccepted(event.target.checked)} className="mt-1 size-4 shrink-0 accent-foreground" />
                  <span>{german ? "Ich akzeptiere die " : "I accept the "}<a className={legalLinkClass} href={termsUrl} target="_blank" rel="noreferrer">{german ? "Nutzungsbedingungen" : "Terms"}</a>{german ? " in der Fassung " : " version "}{me.current_terms_version}.</span>
                </label>
              ) : null}
            </div>
          )}
          {needDpa && dpaUrl && (
            <div className="space-y-3 rounded-2xl border border-border p-4">
              <p className="text-sm font-medium">{german ? "Wer ist für die Daten im Workspace verantwortlich?" : "Who controls the data in this workspace?"}</p>
              <p className="text-xs leading-5 text-muted-foreground">{german ? "Nenne dich bei eigener Verantwortlichkeit vollständig, sonst die Hochschule, Organisation oder das Unternehmen, für das du befugt handelst. Die Vereinbarung gilt für den Workspace, nicht für jedes Teammitglied einzeln." : "Use your full name if acting on your own behalf, otherwise the university, organization or company you are authorized to represent. This agreement covers the workspace, not each team member separately."}</p>
              <label className="block text-sm">
                <span>{german ? "Vollständiger Name des Verantwortlichen" : "Full legal name of the controller"}</span>
                <input value={controllerName} onChange={(event) => { setControllerName(event.target.value); setDpaAccepted(false); }} maxLength={240} autoComplete="organization" className="mt-2 min-h-11 w-full rounded-xl border border-border bg-background px-3" />
              </label>
              {me.legal_controller_name && <p className="text-xs leading-5 text-muted-foreground">{german ? "Deine zuletzt ausdrücklich bestätigte Angabe ist vorausgefüllt. Prüfe sie und ändere sie bei Bedarf; die neue Fassung bestätigst du weiterhin selbst." : "Your last explicitly confirmed identity is prefilled. Check it and edit it if needed; you still confirm the new version yourself."}</p>}
              <label className="flex cursor-pointer gap-3 text-sm leading-6">
                <input type="checkbox" checked={dpaAccepted} onChange={(event) => setDpaAccepted(event.target.checked)} className="mt-1 size-4 shrink-0 accent-foreground" />
                <span>{german ? "Ich bin zur Vertretung des genannten Verantwortlichen befugt und akzeptiere die " : "I am authorized to represent the named controller and accept "}<a className={legalLinkClass} href={dpaUrl} target="_blank" rel="noreferrer">{german ? "Auftragsverarbeitungsvereinbarung" : "Data Processing Agreement"} {me.current_dpa_version}</a>{german ? " für diesen Workspace." : " for this workspace."}</span>
              </label>
            </div>
          )}
          {requiredDocumentMissing && (
            <p role="alert" className="rounded-2xl border border-amber-500/35 bg-amber-500/8 p-4 text-sm leading-6">
              {german
                ? "Der Betreiber muss die erforderlichen rechtlichen Dokumente konfigurieren, bevor diese Bestätigung abgeschlossen werden kann."
                : "The deployment operator must configure the required legal documents before this confirmation can be completed."}
            </p>
          )}
          {waitingForOwner && <p className="rounded-2xl bg-muted/40 p-4 text-sm leading-6">{german ? "Die AVV für diesen Workspace muss noch von einer befugten Person mit Owner-Zugang abgeschlossen werden. Du musst sie nicht im Namen deiner Organisation selbst akzeptieren." : "An authorized workspace owner still needs to conclude its DPA. You do not need to accept it on your organization's behalf."}</p>}
        </div>
        {privacyUrl ? (
          <p className="mt-5 text-xs leading-5 text-muted-foreground">
            {german ? "Wie diese Instanz Daten verarbeitet, erklärt die " : "Read how this deployment handles data in its "}
            <a href={privacyUrl} target="_blank" rel="noreferrer" className={legalLinkClass}>{german ? "Datenschutzerklärung" : "Privacy Notice"}</a>
            {german ? ". Das ist eine Information, keine zusätzliche Einwilligung." : ". This is information, not an additional consent."}
          </p>
        ) : null}
        {error && <p role="alert" className="mt-4 text-sm text-amber-700 dark:text-amber-300">{error}</p>}
        <div className="mt-7 flex flex-wrap gap-3">
          {hasOwnDecision && <button type="button" disabled={!ready || submitting} onClick={() => void submit()} className="min-h-11 rounded-full bg-foreground px-6 text-sm font-medium text-background disabled:opacity-40">{submitting ? (german ? "Wird gespeichert …" : "Saving…") : (german ? "Bestätigen" : "Confirm")}</button>}
          <button type="button" onClick={() => setShowControls((value) => !value)} aria-expanded={showControls} className="min-h-11 rounded-full border border-border px-5 text-sm">{german ? "Konto und Sicherheit" : "Account and security"}</button>
          <button type="button" onClick={onSignOut} className="min-h-11 px-3 text-sm text-muted-foreground">{german ? "Abmelden" : "Sign out"}</button>
        </div>
        <div className="mt-7 border-t border-border pt-5"><LegalInformationLinks german={german} /></div>
        {showControls && <div className="mt-7 border-t border-border pt-6"><AccountRightsControls /></div>}
      </section>
    </main>
  );
}

export function PrivacyUpdateNotice({ me, onPresented }: { me: Me; onPresented: () => Promise<void> }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const german = me.language === "de";
  const privacyUrl = publicLegalUrl("privacy");
  if (!me.privacy_notice_update_available) return null;
  async function close() {
    setPending(true);
    try {
      await api.recordPrivacyNotice(me.current_privacy_version);
      await onPresented();
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }
  return (
    <aside lang={german ? "de" : "en"} aria-label={german ? "Datenschutzhinweis" : "Privacy update"} className="fixed bottom-5 right-5 z-50 max-w-sm rounded-2xl border border-border bg-card p-5 shadow-lg max-sm:left-5">
      <p className="text-sm font-medium">{german ? "Aktualisierte Datenschutzhinweise" : "Updated privacy information"}</p>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">{german ? "Der Betreiber dieser Instanz hat seine Datenschutzhinweise aktualisiert. Deine Nutzung bleibt verfügbar." : "The operator of this deployment updated its privacy information. Your workspace remains available."}</p>
      {privacyUrl ? <a href={privacyUrl} target="_blank" rel="noreferrer" className="mt-3 inline-block text-xs underline underline-offset-4">{german ? "Datenschutzhinweise ansehen" : "View privacy information"}</a> : null}
      <button type="button" disabled={pending} onClick={() => void close()} className="ml-5 min-h-10 text-xs font-medium">{german ? "Schließen" : "Close"}</button>
      {error && <p role="status" className="text-xs">{german ? "Bitte versuche es noch einmal." : "Please try again."}</p>}
    </aside>
  );
}
