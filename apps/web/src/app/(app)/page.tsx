"use client";

import { useEffect, useState } from "react";

import NewChatCursorWave from "@/components/brand/new-chat-cursor-wave";
import Composer from "@/components/search/composer";
import { useAuth } from "@/lib/auth";
import { pickGermanGreeting, pickGreeting } from "@/lib/greetings";
import { consumeResearchQuestionHandoff } from "@/lib/research-question-handoff";

export default function NewSearchPage() {
  const { me } = useAuth();
  // picked after mount: greetings are random and time-of-day dependent,
  // which server-rendered HTML cannot agree with (hydration)
  const [greeting, setGreeting] = useState<string | null>(null);
  useEffect(() => {
    if (me?.first_name) {
      setGreeting(
        me.language === "de"
          ? pickGermanGreeting(me.first_name)
          : pickGreeting(me.first_name),
      );
    }
  }, [me?.first_name, me?.language]);
  const isGerman = me?.language === "de";

  // a question picked in the onboarding's last step is stashed in
  // sessionStorage before navigating here; consume it into the composer
  const [seed, setSeed] = useState<{ text: string; nonce: number } | null>(null);
  useEffect(() => {
    const consume = () => {
      const params = new URLSearchParams(window.location.search);
      const handoff = params.get("q");
      const text =
        handoff ??
        sessionStorage.getItem("six:onboarding-seed") ??
        consumeResearchQuestionHandoff();
      if (text) {
        sessionStorage.removeItem("six:onboarding-seed");
        setSeed({ text, nonce: Date.now() });
        if (handoff) {
          params.delete("q");
          const query = params.toString();
          window.history.replaceState(
            null,
            "",
            `${window.location.pathname}${query ? `?${query}` : ""}`,
          );
        }
      }
    };
    consume(); // covers navigation into a fresh mount
    window.addEventListener("six:seed-search", consume); // covers already-mounted home
    return () => window.removeEventListener("six:seed-search", consume);
  }, []);

  return (
    <div className="relative isolate flex min-h-0 flex-1 flex-col items-center justify-start overflow-x-hidden overflow-y-auto px-3 py-6 sm:px-5 sm:py-10 lg:justify-center lg:rounded-[calc(var(--radius)*1.8-1px)]">
      <NewChatCursorWave />

      <div className="relative z-10 w-full max-w-[52.5rem] lg:-translate-y-[4%]">
        <div className="rise rise-1 mb-5 flex flex-col items-center text-center sm:mb-8">
          <h1 className="font-display text-[clamp(2rem,9vw,3.2rem)] leading-tight text-foreground">
            {greeting ?? (isGerman ? "Frag die Literatur." : "Ask the literature.")}
          </h1>
          <p className="mt-2 max-w-md text-pretty text-[0.875rem] leading-relaxed text-muted-foreground sm:text-[0.9375rem]">
            {isGerman
              ? "Stell deine Forschungsfrage, entdecke relevante Literatur und prüfe Antworten anhand ihrer Quellen."
              : "Ask a research question, discover relevant literature and check answers against their sources."}
          </p>
        </div>

        <div className="rise rise-2">
          <Composer seed={seed} />
        </div>
      </div>
    </div>
  );
}
