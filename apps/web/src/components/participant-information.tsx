"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { ParticipantInformation, ParticipantNotice } from "@/lib/types";
import { participantInformationGapLabels } from "@/lib/participant-information-feedback";
import { publicLegalUrl } from "@/lib/public-links";

/** Display server-owned wording verbatim; the same surface is saved on participation. */
export function ParticipantNoticeContent({ notice }: { notice: ParticipantNotice }) {
  const de = notice.language === "de";
  return (
    <section lang={de ? "de" : "en"} className="space-y-4 rounded-2xl border border-border bg-secondary/30 p-4 text-sm leading-relaxed">
      <h2 className="font-medium">{de ? "Ihre Teilnahme im Überblick" : "Your participation at a glance"}</h2>
      {notice.core.map((line, index) => <p key={index} className="whitespace-pre-wrap break-words">{line}</p>)}
      <details className="border-t border-border pt-3">
        <summary className="cursor-pointer font-medium text-moss">{de ? "Alle Informationen zu Daten und Rechten" : "Full information about data and your rights"}</summary>
        <div className="mt-4 space-y-4">
          {notice.sections.map((section) => (
            <section key={section.title}>
              <h3 className="font-medium">{section.title}</h3>
              <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{section.body}</p>
            </section>
          ))}
          {notice.privacy_notice_url && <a className="block underline underline-offset-2" href={notice.privacy_notice_url} target="_blank" rel="noreferrer">{de ? "Datenschutzhinweis der Studie" : "Study privacy notice"}</a>}
          <a className="block underline underline-offset-2" href={notice.platform_privacy_url} target="_blank" rel="noreferrer">{de ? "Datenschutzhinweis der Instanz" : "Deployment privacy notice"}</a>
          <p className="text-xs text-muted-foreground">{de ? "Fassung" : "Version"} {notice.version}</p>
        </div>
      </details>
    </section>
  );
}

const fields: Array<{ key: keyof ParticipantInformation; label: string; hint?: string; multiline?: boolean; max: number }> = [
  { key: "controller_name", label: "Responsible institution / controller", max: 240 },
  { key: "controller_address", label: "Controller postal address", max: 500 },
  { key: "contact_email", label: "Study contact email", max: 320 },
  { key: "data_protection_contact", label: "Data protection officer / contact (where applicable)", max: 500 },
  { key: "purpose", label: "Purpose of this study", multiline: true, max: 2000 },
  { key: "data_categories", label: "What data and answers are collected?", hint: "Include respondent labels, transcript and audio if applicable. Do not request sensitive or special-category data.", multiline: true, max: 1500 },
  { key: "legal_basis_details", label: "Explain the legal basis", hint: "Consent: describe the specific purpose. Public task: identify the statutory basis. Legitimate interests: identify the concrete interests and assess participants' rights.", multiline: true, max: 1500 },
  { key: "retention_period", label: "Deletion deadline or concrete retention criteria", hint: "You must implement this period. This field does not schedule automatic deletion; the deployment's backup periods must be disclosed separately.", multiline: true, max: 1500 },
  { key: "additional_recipients", label: "Who in your institution or outside it receives the data?", hint: "State explicitly if there are no additional recipients. The deployment operator and its service providers must be disclosed separately.", multiline: true, max: 1500 },
  { key: "additional_transfers", label: "Additional international transfers and safeguards", hint: "State explicitly if none. For your own transfers, name countries, safeguards and where participants can obtain a copy.", multiline: true, max: 1500 },
  { key: "supervisory_authority", label: "Competent supervisory authority and complaint contact", max: 1000 },
  { key: "privacy_notice_url", label: "Institution / study privacy notice (optional HTTPS link)", max: 2000 },
];

/** Explicit human-reviewed facts, never generated or silently approved by an agent. */
export function ParticipantInformationEditor({ value, onSave, gaps = [], requireDpia = false }: {
  value: ParticipantInformation;
  onSave: (value: ParticipantInformation) => Promise<unknown>;
  gaps?: readonly string[];
  requireDpia?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState("");
  function change(key: keyof ParticipantInformation, next: string | boolean) {
    setDraft((current) => ({ ...current, [key]: next, ...(key !== "researcher_reviewed" ? { researcher_reviewed: false } : {}) }));
    setResult("");
  }
  return (
    <section className="@container/participant-information min-w-0 space-y-4 rounded-[1.5rem] border border-border bg-card p-5">
      <div>
        <h2 className="font-medium">Participant information</h2>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">Set the responsible institution and study-specific data handling. Participants see a short overview with expandable details. New participation requires complete, reviewed information; existing responses remain accessible.</p>
      </div>
      {gaps.length > 0 && (
        <div role="status" className="rounded-xl border border-border bg-secondary/35 p-3 text-sm">
          <p className="font-medium">Still needed before publishing</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
            {participantInformationGapLabels(gaps).map((label) => <li key={label}>{label}</li>)}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">This list updates when you save the participant information.</p>
        </div>
      )}
      <div className="grid min-w-0 gap-4 @min-[32rem]/participant-information:grid-cols-2 [&>*]:min-w-0">
        <label className="space-y-1 text-sm">Participant information language
          <span className="relative block">
            <select className="block w-full appearance-none rounded-xl border border-border bg-background p-2 pr-9 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto" value={draft.language ?? ""} onChange={(event) => change("language", event.target.value)}>
              <option value="">Choose the participants&apos; language</option>
              <option value="en">English</option><option value="de">Deutsch</option>
            </select>
            <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground forced-colors:hidden" />
          </span>
        </label>
        <label className="space-y-1 text-sm">Research legal basis
          <span className="relative block">
            <select className="block w-full appearance-none rounded-xl border border-border bg-background p-2 pr-9 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto" value={draft.legal_basis ?? ""} onChange={(event) => change("legal_basis", event.target.value)}>
              <option value="">Choose after assessment</option>
              <option value="consent">Consent · Art. 6(1)(a)</option>
              <option value="public_task">Public task · Art. 6(1)(e)</option>
              <option value="legitimate_interests">Legitimate interests · Art. 6(1)(f)</option>
            </select>
            <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground forced-colors:hidden" />
          </span>
        </label>
        {requireDpia && (
          <>
            <label className="space-y-1 text-sm">Data protection impact assessment
              <span className="block text-xs leading-relaxed text-muted-foreground">The accountable controller must complete and approve a DPIA for the concrete study before an AI-led interview link is published. For German controllers, DSK mandatory-list item 11 applies to AI-controlled interaction.</span>
              <span className="relative block">
                <select className="block w-full appearance-none rounded-xl border border-border bg-background p-2 pr-9 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto" value={draft.dpia_status ?? ""} onChange={(event) => change("dpia_status", event.target.value)}>
                  <option value="">Choose after assessment</option>
                  <option value="completed">DPIA completed and approved</option>
                </select>
                <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground forced-colors:hidden" />
              </span>
            </label>
            <label className="space-y-1 text-sm @min-[32rem]/participant-information:col-span-2">
              <span>DPIA decision reference and reasoning</span>
              <span className="block text-xs leading-relaxed text-muted-foreground">Identify the dated assessment, accountable owner, approval and applicable safeguards. Any material guide or setting change clears this approval and requires a fresh review. This platform record supports accountability but does not replace the controller&apos;s signed assessment.</span>
              <Textarea rows={3} maxLength={1500} value={draft.dpia_reference ?? ""} onChange={(event) => change("dpia_reference", event.target.value)} />
            </label>
            <label className="flex items-start gap-2 text-sm leading-relaxed @min-[32rem]/participant-information:col-span-2">
              <input type="checkbox" className="mt-1" checked={draft.ai_interview_scope_attested ?? false} onChange={(event) => change("ai_interview_scope_attested", event.target.checked)} />
              I confirm that this study does not intentionally solicit special-category, criminal-offence or confidential third-party data; use voice or answers to infer or score personality, emotion, health, credibility, suitability, performance, protected traits or identity; perform biometric identification; or make or recommend a legal or similarly significant decision about a participant.
            </label>
            <label className="flex items-start gap-2 text-sm leading-relaxed @min-[32rem]/participant-information:col-span-2">
              <input type="checkbox" className="mt-1" checked={draft.spoken_processing_approved ?? false} onChange={(event) => change("spoken_processing_approved", event.target.checked)} />
              I confirm that the concrete DPIA also covers spoken interviews: participant audio is processed through this deployment&apos;s authenticated relay and its configured live speech provider. Leave this unchecked to approve written interviews only.
            </label>
          </>
        )}
        {fields.map((field) => <label key={field.key} className={`space-y-1 text-sm ${field.multiline ? "@min-[32rem]/participant-information:col-span-2" : ""}`}>
          <span>{field.label}</span>
          {field.hint && <span className="block text-xs leading-relaxed text-muted-foreground">{field.hint}</span>}
          {field.multiline
            ? <Textarea rows={3} maxLength={field.max} value={String(draft[field.key] ?? "")} onChange={(event) => change(field.key, event.target.value)} />
            : <Input maxLength={field.max} value={String(draft[field.key] ?? "")} onChange={(event) => change(field.key, event.target.value)} />}
        </label>)}
      </div>
      <label className="flex items-start gap-2 text-sm leading-relaxed">
        <input type="checkbox" className="mt-1" checked={draft.researcher_reviewed ?? false} onChange={(event) => change("researcher_reviewed", event.target.checked)} />
        {requireDpia
          ? "I am authorized by the controller, have assessed the legal basis, completed and approved the DPIA for this concrete study, and reviewed these facts and the deployment's processing disclosures. The documented safeguards are in force before publication."
          : "I am authorized by the controller, have assessed the stated legal basis, and have reviewed these facts and the deployment's processing disclosures. No additional publication or optional use is included in this participation."}
      </label>
      <p className="text-xs text-muted-foreground">A completeness check is not legal approval. Confirm your institution's processing agreement and any international-transfer safeguards before inviting participants.</p>
      {publicLegalUrl("privacy") || publicLegalUrl("dpa") ? (
        <p className="flex gap-4 text-xs">
          {publicLegalUrl("privacy") ? <a href={publicLegalUrl("privacy")!} target="_blank" rel="noreferrer" className="underline">Deployment privacy notice</a> : null}
          {publicLegalUrl("dpa") ? <a href={publicLegalUrl("dpa")!} target="_blank" rel="noreferrer" className="underline">Processing agreement</a> : null}
        </p>
      ) : null}
      <Button disabled={saving} onClick={async () => {
        setSaving(true); setResult("");
        try { await onSave(draft); setResult("Participant information saved."); }
        catch { setResult("Could not save the participant information. Please review the fields and try again."); }
        finally { setSaving(false); }
      }}>{saving ? "Saving…" : "Save participant information"}</Button>
      {result && <p role="status" className="text-sm text-muted-foreground">{result}</p>}
    </section>
  );
}
