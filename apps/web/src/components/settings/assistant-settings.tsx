"use client";

import { Check, Loader2, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import type { AssistantPreferences } from "@/lib/types";
import { cn } from "@/lib/utils";

const DEFAULT_PREFERENCES: AssistantPreferences = {
  detail: "balanced",
  tone: "academic",
  format: "adaptive",
  custom_instructions: "",
};

function ChoiceGrid({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ value: string; label: string; description: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-xl border p-3 text-left transition-colors",
            value === option.value
              ? "border-moss/50 bg-accent/70"
              : "border-border hover:bg-secondary/45",
          )}
        >
          <span className="flex items-center justify-between gap-2 text-[0.8125rem] font-medium">
            {option.label}
            {value === option.value ? <Check className="size-3.5 text-moss" /> : null}
          </span>
          <span className="mt-1 block text-[0.6875rem] leading-relaxed text-muted-foreground">
            {option.description}
          </span>
        </button>
      ))}
    </div>
  );
}

export default function AssistantSettings() {
  const { me, setAssistantPreferences } = useAuth();
  const isGerman = me?.language === "de";
  const saved = me?.assistant_preferences ?? DEFAULT_PREFERENCES;
  const [draft, setDraft] = useState<AssistantPreferences>(saved);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(saved), [saved]);
  if (!me) return null;

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const detailOptions = [
    { value: "concise", label: isGerman ? "Kompakt" : "Concise", description: isGerman ? "Ergebnis zuerst, knapp belegt." : "Answer first, with concise evidence." },
    { value: "balanced", label: isGerman ? "Ausgewogen" : "Balanced", description: isGerman ? "Kontext und Lesbarkeit im Gleichgewicht." : "A balance of context and readability." },
    { value: "thorough", label: isGerman ? "Tiefgehend" : "Thorough", description: isGerman ? "Mehr Herleitung und methodische Grenzen." : "More reasoning and methodological limits." },
  ];
  const toneOptions = [
    { value: "direct", label: isGerman ? "Direkt" : "Direct", description: isGerman ? "Klar und ohne Vorrede." : "Clear and without unnecessary framing." },
    { value: "academic", label: isGerman ? "Akademisch" : "Academic", description: isGerman ? "Präzise und wissenschaftlich." : "Precise and research-oriented." },
    { value: "explanatory", label: isGerman ? "Erklärend" : "Explanatory", description: isGerman ? "Begriffe verständlich aufbauen." : "Build up concepts clearly." },
    { value: "critical", label: isGerman ? "Kritisch" : "Critical", description: isGerman ? "Annahmen und Schwächen sichtbar machen." : "Surface assumptions and weaknesses." },
  ];
  const formatOptions = [
    { value: "adaptive", label: isGerman ? "Adaptiv" : "Adaptive", description: isGerman ? "Passende Darstellung automatisch wählen." : "Choose the clearest presentation." },
    { value: "prose", label: isGerman ? "Fließtext" : "Prose", description: isGerman ? "Zusammenhängende Absätze bevorzugen." : "Prefer connected paragraphs." },
    { value: "structured", label: isGerman ? "Strukturiert" : "Structured", description: isGerman ? "Listen und Tabellen bevorzugen." : "Prefer lists and tables." },
  ];

  async function save() {
    setSaving(true);
    try {
      await setAssistantPreferences(draft);
      toast.success(isGerman ? "KI-Verhalten gespeichert." : "AI behavior saved.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Preferences could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <div className="flex gap-3 rounded-xl border border-moss/25 bg-accent/45 p-3">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-moss" />
        <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
          {isGerman
            ? "Diese Auswahl verändert Darstellung und Erklärstil. Quellen-, Werkzeug-, Datenschutz- und Sicherheitsregeln bleiben unverändert."
            : "These choices change presentation and explanation style. Citation, tool, privacy and safety rules remain unchanged."}
        </p>
      </div>

      <SettingsSection title={isGerman ? "Antworttiefe" : "Answer depth"}>
        <ChoiceGrid
          value={draft.detail}
          options={detailOptions}
          onChange={(detail) => setDraft((current) => ({ ...current, detail: detail as AssistantPreferences["detail"] }))}
        />
      </SettingsSection>
      <SettingsSection title={isGerman ? "Arbeitsweise" : "Approach"}>
        <ChoiceGrid
          value={draft.tone}
          options={toneOptions}
          onChange={(tone) => setDraft((current) => ({ ...current, tone: tone as AssistantPreferences["tone"] }))}
        />
      </SettingsSection>
      <SettingsSection title={isGerman ? "Darstellung" : "Presentation"}>
        <ChoiceGrid
          value={draft.format}
          options={formatOptions}
          onChange={(format) => setDraft((current) => ({ ...current, format: format as AssistantPreferences["format"] }))}
        />
      </SettingsSection>
      <SettingsSection title={isGerman ? "Persönlicher Hinweis" : "Personal note"}>
        <Textarea
          value={draft.custom_instructions}
          maxLength={800}
          className="min-h-28 resize-y"
          placeholder={isGerman ? "Fachgebiet, Zielgruppe oder bevorzugte Terminologie …" : "Domain, audience or preferred terminology …"}
          onChange={(event) => setDraft((current) => ({ ...current, custom_instructions: event.target.value }))}
        />
        <p className="mt-1 text-right font-mono text-[0.625rem] text-muted-foreground">
          {draft.custom_instructions.length}/800
        </p>
      </SettingsSection>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={!dirty || saving} onClick={() => setDraft(saved)}>
          {isGerman ? "Zurücksetzen" : "Reset"}
        </Button>
        <Button type="button" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
          {isGerman ? "Speichern" : "Save"}
        </Button>
      </div>
    </div>
  );
}
