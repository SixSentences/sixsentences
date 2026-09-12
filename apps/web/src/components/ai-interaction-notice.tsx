"use client";

import { useAuth } from "@/lib/auth";

/** One quiet disclosure at the interaction boundary, not on every paragraph. */
export function AiInteractionNotice() {
  const { me } = useAuth();
  return (
    <p data-ai-interaction-notice className="mb-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
      {me?.language === "de"
        ? "KI-Assistent · Prüfe Aussagen und Quellen vor der Übernahme."
        : "AI assistant · Review claims and sources before using the result."}
    </p>
  );
}
