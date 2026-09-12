"use client";

/**
 * The participant's door (/talk/[token], no account, no app shell):
 * consent first, then the currently permitted conversation mode. Public
 * spoken availability is controlled by the server; text remains an alternative.
 */

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  AudioLines,
  Check,
  Keyboard,
  Loader2,
  Mic,
} from "lucide-react";

import LiveSession, {
  liveSessionInterruptionNotice,
  type LiveSessionResult,
} from "@/components/voice/live-session";
import TextSession from "@/components/voice/text-session";
import { ParticipantNoticeContent } from "@/components/participant-information";
import PublicLegalFooter from "@/components/public-legal-footer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { VoiceSessionConfig } from "@/lib/types";
import { userFacingPublicTalkErrorMessage } from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

type Step =
  | "closed"
  | "consent"
  | "miccheck"
  | "live"
  | "text"
  | "saving"
  | "done"
  | "discarded"
  | "failed";

type PublicSession = VoiceSessionConfig & { participant_label: string };

const CLOSED_REASONS: Record<string, { de: string; en: string }> = {
  inactive: {
    de: "Dieser Einladungslink ist nicht mehr aktiv.",
    en: "This invitation link is no longer active.",
  },
  expired: {
    de: "Dieser Einladungslink ist abgelaufen.",
    en: "This invitation link has expired.",
  },
  full: {
    de: "Diese Studie hat bereits genügend Teilnehmende. Vielen Dank für Ihr Interesse.",
    en: "This study already has enough participants. Thank you for your interest.",
  },
  unconfigured: {
    de: "Diese Studie ist gerade nicht erreichbar. Bitte versuchen Sie es später erneut.",
    en: "This study is currently unavailable. Please try again later.",
  },
  unavailable: {
    de: "Diese Studie nimmt derzeit keine neuen Interviews an. Bitte wenden Sie sich an die Studienleitung.",
    en: "This study is not accepting new interviews right now. Please contact the research team.",
  },
};

export default function TalkPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;

  const { data: info, isLoading, isError } = useQuery({
    queryKey: ["talk", token],
    queryFn: () => api.publicTalkInfo(token),
    retry: 1,
  });
  const german = info?.language !== "en";

  const [step, setStep] = useState<Step | null>(null);
  const [consented, setConsented] = useState(false);
  const [audioConsented, setAudioConsented] = useState(false);
  const [selectedMode, setSelectedMode] = useState<"live" | "text">("live");
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [passcode, setPasscode] = useState("");
  const [starting, setStarting] = useState(false);
  const [session, setSession] = useState<PublicSession | null>(null);
  const [pendingFinalize, setPendingFinalize] = useState<{
    session: PublicSession;
    result: LiveSessionResult;
  } | null>(null);
  const [error, setError] = useState("");
  const [endNotice, setEndNotice] = useState<string | null>(null);
  const [micLevel, setMicLevel] = useState(0);
  const [micOk, setMicOk] = useState<boolean | null>(null);
  // latches once: the instantaneous level hovers around the threshold and
  // would otherwise flip the status text every animation frame
  const [micHeard, setMicHeard] = useState(false);
  const micStreamRef = useRef<MediaStream | null>(null);
  const abandoningSessionIdsRef = useRef(new Set<string>());
  const participationMode = info?.live_available ? selectedMode : "text";
  const participantNotice = info?.participant_notices?.[participationMode];

  useEffect(() => {
    setConsented(false);
    setAudioConsented(false);
  }, [info?.consent_fingerprint]);

  useEffect(() => {
    if (info && step === null) {
      setStep(info.state === "open" ? "consent" : "closed");
    }
  }, [info, step]);

  // the mic check: permission + a visible level so people trust their setup
  useEffect(() => {
    if (step !== "miccheck") return;
    setMicHeard(false);
    setMicLevel(0);
    let cancelled = false;
    let context: AudioContext | null = null;
    let raf = 0;
    let smoothed = 0;
    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        if (cancelled) {
          for (const track of stream.getTracks()) track.stop();
          return;
        }
        micStreamRef.current = stream;
        setMicOk(true);
        context = new AudioContext();
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteTimeDomainData(data);
          let sum = 0;
          for (const value of data) {
            const centered = (value - 128) / 128;
            sum += centered * centered;
          }
          const rms = Math.sqrt(sum / data.length);
          smoothed = smoothed * 0.7 + rms * 0.3;
          setMicLevel(smoothed);
          if (rms > 0.025) setMicHeard(true);
          raf = requestAnimationFrame(tick);
        };
        tick();
      })
      .catch(() => setMicOk(false));
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      if (context) void context.close();
      if (micStreamRef.current) {
        for (const track of micStreamRef.current.getTracks()) track.stop();
        micStreamRef.current = null;
      }
    };
  }, [step]);

  async function begin(mode: "live" | "text") {
    if (!info) return;
    if (mode === "live" && !info.live_available) {
      setError(
        german
          ? "Das gesprochene Interview ist derzeit nicht verfügbar. Bitte nehmen Sie schriftlich teil."
          : "The spoken interview is currently unavailable. Please participate in writing.",
      );
      setStep("consent");
      return;
    }
    setStarting(true);
    setError("");
    setEndNotice(null);
    try {
      const config = await api.publicTalkStart(
        token,
        true,
        ageConfirmed,
        info.consent_fingerprint,
        passcode.trim(),
        mode,
        mode === "live" && audioConsented,
      );
      if (config.mode !== mode) {
        throw new Error(
          german
            ? "Der Interviewmodus konnte nicht gestartet werden."
            : "The selected interview mode could not be started.",
        );
      }
      setSession(config);
      setStep(mode);
    } catch (requestError) {
      setError(userFacingPublicTalkErrorMessage(requestError, german ? "de" : "en"));
    } finally {
      setStarting(false);
    }
  }

  async function finalizeSession(active: PublicSession, result: LiveSessionResult) {
    setStep("saving");
    setError("");
    try {
      const settled = await api.publicTalkFinalize(token, active.id, {
        turns: result.turns,
        duration_ms: result.duration_ms,
        ...(result.audio_base64 ? { audio_base64: result.audio_base64 } : {}),
        aborted: result.aborted,
      });
      setPendingFinalize(null);
      setEndNotice(liveSessionInterruptionNotice(
        result.endReason, settled.status === "completed", german,
      ));
      setStep(settled.status === "completed" ? "done" : "discarded");
    } catch (requestError) {
      setPendingFinalize({ session: active, result });
      setError(userFacingPublicTalkErrorMessage(requestError, german ? "de" : "en"));
      setStep("failed");
    }
  }

  async function handleFinished(result: LiveSessionResult) {
    const active = session;
    setSession(null);
    if (!active) return;
    await finalizeSession(active, result);
  }

  function handleSessionError(message: string) {
    const active = session;
    setSession(null);
    setError(message);
    setStep("failed");
    if (!active || abandoningSessionIdsRef.current.has(active.id)) return;
    abandoningSessionIdsRef.current.add(active.id);
    void api
      .publicTalkFinalize(token, active.id, {
        turns: [],
        duration_ms: 0,
        aborted: true,
      })
      .catch(() => {
        // Session cleanup is an operator concern; the participant should see
        // only the actionable connection error and can try again.
      });
  }

  if (!isLoading && (isError || !info)) {
    return (
      <Frame>
        <Card>
          <p className="text-[0.9375rem] font-medium text-pine">
            Diese Einladung ist gerade nicht erreichbar.
          </p>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
            Bitte prüfen Sie den Link oder versuchen Sie es später erneut. /
            Please check the invitation link or try again later.
          </p>
        </Card>
      </Frame>
    );
  }

  if (isLoading || !info || step === null) {
    return (
      <Frame>
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </Frame>
    );
  }

  if (step === "live" && session?.mode === "live") {
    return (
      <LiveSession
        config={session}
        title={info.title}
        onFinished={handleFinished}
        onError={handleSessionError}
        showPublicLegalFooter
      />
    );
  }

  return (
    <Frame>
      {step === "closed" && (
        <Card>
          <p className="text-[0.9375rem] font-medium text-pine">{info.title}</p>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {(CLOSED_REASONS[info.state] ?? CLOSED_REASONS.unconfigured)[
              german ? "de" : "en"
            ]}
          </p>
          {info.contact_line && (
            <p className="mt-4 text-[0.75rem] text-muted-foreground">{info.contact_line}</p>
          )}
        </Card>
      )}

      {step === "consent" && (
        <Card>
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
            {german ? "Einladung zu einem Forschungsinterview" : "Research interview invitation"}
          </p>
          <h1 className="mt-2 font-serif text-3xl text-pine">{info.title}</h1>
          <p className="mt-2 text-[0.8125rem] text-muted-foreground">
            {german
              ? `Dauer: bis etwa ${info.expected_minutes} Minuten · Sprache: Deutsch`
              : `Duration: up to about ${info.expected_minutes} minutes · Language: English`}
          </p>

          <div className="mt-5 space-y-4">
            {info.live_available && (
              <fieldset className="flex flex-wrap gap-3 text-sm">
                <legend className="mb-2 font-medium">{german ? "Wie möchten Sie teilnehmen?" : "How would you like to participate?"}</legend>
                {(["live", "text"] as const).map((mode) => (
                  <label key={mode} className="flex cursor-pointer items-center gap-2">
                    <input type="radio" name="participation-mode" checked={selectedMode === mode} onChange={() => { setSelectedMode(mode); setConsented(false); setAudioConsented(false); }} />
                    {mode === "live" ? german ? "Per Sprache" : "Spoken interview" : german ? "Schriftlich" : "Written interview"}
                  </label>
                ))}
              </fieldset>
            )}
            {participantNotice && <ParticipantNoticeContent notice={participantNotice} />}
          </div>

          {participantNotice?.requires_consent ? (
            <label className="mt-4 flex cursor-pointer items-start gap-2.5 text-sm leading-relaxed">
              <input type="checkbox" checked={consented} onChange={(event) => setConsented(event.target.checked)} className="mt-1 size-4 accent-[#33544c]" />
              {participantNotice.declaration}
            </label>
          ) : <p className="mt-4 text-sm text-muted-foreground">{participantNotice?.declaration}</p>}
          {selectedMode === "live" && info.live_available && (
            <label className="mt-3 flex cursor-pointer items-start gap-2.5 text-sm leading-relaxed">
              <input type="checkbox" checked={audioConsented} onChange={(event) => setAudioConsented(event.target.checked)} className="mt-1 size-4 accent-[#33544c]" />
              {participantNotice?.audio_declaration}
            </label>
          )}
          <label className="mt-3 flex cursor-pointer items-start gap-2.5 text-sm leading-relaxed">
            <input type="checkbox" checked={ageConfirmed} onChange={(event) => setAgeConfirmed(event.target.checked)} className="mt-1 size-4 accent-[#33544c]" />
            {info.age_declaration}
          </label>

          {info.passcode_required && (
            <Input
              value={passcode}
              onChange={(event) => setPasscode(event.target.value)}
              placeholder={german ? "Zugangscode" : "Access code"}
              className="mt-4"
            />
          )}
          {error && <p className="mt-3 text-[0.8125rem] text-destructive">{error}</p>}
          <Button
            className="mt-5 w-full rounded-full"
            disabled={
              starting ||
              !participantNotice ||
              (participantNotice.requires_consent && !consented) ||
              (participationMode === "live" && !audioConsented) ||
              !ageConfirmed ||
              (info.passcode_required && !passcode.trim())
            }
            onClick={() => {
              if (participationMode === "live") {
                setStep("miccheck");
                return;
              }
              void begin("text");
            }}
          >
            {starting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : participationMode === "text" ? (
              <Keyboard className="size-4" />
            ) : null}
            {participationMode === "live"
              ? german
                ? "Weiter"
                : "Continue"
              : german
                ? "Schriftliches Interview starten"
                : "Start written interview"}
          </Button>
          {!info.live_available && (
            <p className="mt-3 text-center text-[0.71875rem] leading-relaxed text-muted-foreground">
              {german
                ? "Das Interview findet derzeit schriftlich statt; Mikrofon und Audioaufnahme werden nicht verwendet."
                : "This interview currently takes place in writing; the microphone and audio recording are not used."}
            </p>
          )}
          {info.contact_line && (
            <p className="mt-4 text-center text-[0.71875rem] text-muted-foreground">
              {info.contact_line}
            </p>
          )}
        </Card>
      )}

      {step === "miccheck" && info.live_available && (
        <Card>
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
            {german ? "Mikrofon-Check" : "Microphone check"}
          </p>
          <h2 className="mt-2 font-serif text-2xl text-pine">
            {german ? "Sagen Sie kurz etwas." : "Say something briefly."}
          </h2>
          {micOk === null && (
            <p className="mt-3 flex items-center gap-2 text-[0.8125rem] text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {german ? "Warte auf Mikrofonfreigabe…" : "Waiting for microphone permission…"}
            </p>
          )}
          {micOk === true && (
            <>
              <div className="mt-5 flex h-12 items-center gap-1">
                {Array.from({ length: 28 }).map((_, index) => (
                  <span
                    key={index}
                    className="w-1.5 rounded-full bg-moss/70 transition-all duration-75"
                    style={{
                      height: `${Math.max(
                        8,
                        Math.min(48, micLevel * 560 * (0.5 + ((index * 37) % 17) / 17)),
                      )}px`,
                    }}
                  />
                ))}
              </div>
              <p
                className={cn(
                  "mt-2 flex items-center gap-1.5 text-[0.8125rem]",
                  micHeard ? "text-moss" : "text-muted-foreground",
                )}
              >
                {micHeard && <Check className="size-4" />}
                {micHeard
                  ? german
                    ? "Ihr Mikrofon funktioniert."
                    : "Your microphone works."
                  : german
                    ? "Sprechen Sie, um den Pegel zu sehen."
                    : "Speak to see the level."}
              </p>
              <Button
                className="mt-5 w-full rounded-full"
                disabled={starting}
                onClick={() => void begin("live")}
              >
                {starting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Mic className="size-4" />
                )}
                {german ? "Gespräch starten" : "Start the conversation"}
              </Button>
            </>
          )}
          {micOk === false && (
            <p className="mt-3 text-[0.8125rem] leading-relaxed text-muted-foreground">
              {german
                ? "Ohne Mikrofonfreigabe ist das gesprochene Interview nicht möglich."
                : "The spoken interview is not possible without microphone access."}
            </p>
          )}
          {error && <p className="mt-3 text-[0.8125rem] text-destructive">{error}</p>}
          <button
            type="button"
            disabled={starting}
            onClick={() => { setSelectedMode("text"); setConsented(false); setAudioConsented(false); setStep("consent"); }}
            className="mt-4 flex w-full cursor-pointer items-center justify-center gap-1.5 text-[0.78125rem] text-moss hover:underline"
          >
            <Keyboard className="size-3.5" />
            {german ? "Lieber schriftlich teilnehmen" : "Prefer to participate in writing"}
          </button>
        </Card>
      )}

      {step === "text" && session?.mode === "text" && (
        <div className="w-full max-w-2xl">
          <TextSession
            config={session}
            sendMessage={(message, history) =>
              api
                .publicTalkMessage(token, session.id, message, history)
                .then((result) => result.reply)
            }
            onFinished={handleFinished}
            onError={handleSessionError}
          />
        </div>
      )}

      {step === "saving" && (
        <Card>
          <p className="flex items-center gap-2 text-[0.9375rem] font-medium text-pine">
            <Loader2 className="size-4 animate-spin" />
            {german ? "Interview wird gespeichert…" : "Saving the interview…"}
          </p>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {german
              ? "Bitte lassen Sie dieses Fenster noch einen Moment geöffnet."
              : "Please keep this window open for another moment."}
          </p>
        </Card>
      )}

      {step === "done" && (
        <Card>
          <div className="grid size-12 place-items-center rounded-2xl bg-accent">
            {endNotice ? <AudioLines className="size-6 text-moss" /> : <Check className="size-6 text-moss" />}
          </div>
          <h2 className="mt-4 font-serif text-2xl text-pine">
            {endNotice
              ? german ? "Interview unterbrochen" : "Interview interrupted"
              : german ? "Vielen Dank!" : "Thank you!"}
          </h2>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {endNotice ?? (german
              ? "Ihr Interview wurde gespeichert und wird vom Forschungsteam ausgewertet. Sie können dieses Fenster jetzt schließen."
              : "Your interview was saved and will be reviewed by the research team. You can close this window now.")}
          </p>
          {info.contact_line && (
            <p className="mt-4 text-[0.75rem] text-muted-foreground">{info.contact_line}</p>
          )}
        </Card>
      )}

      {step === "discarded" && (
        <Card>
          <div className="grid size-12 place-items-center rounded-2xl bg-accent">
            {endNotice ? <AudioLines className="size-6 text-moss" /> : <Check className="size-6 text-moss" />}
          </div>
          <h2 className="mt-4 font-serif text-2xl text-pine">
            {endNotice
              ? german ? "Interview unterbrochen" : "Interview interrupted"
              : german ? "Interview beendet" : "Interview ended"}
          </h2>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {endNotice ?? (german
              ? "Es wurde kein Interview-Transkript gespeichert."
              : "No interview transcript was saved.")}
          </p>
          {endNotice && info.contact_line && (
            <p className="mt-4 text-[0.75rem] text-muted-foreground">{info.contact_line}</p>
          )}
        </Card>
      )}

      {step === "failed" && (
        <Card>
          <p className="text-[0.9375rem] font-medium text-pine">
            {german ? "Das hat leider nicht geklappt." : "That did not work."}
          </p>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-muted-foreground">{error}</p>
          <Button
            variant="outline"
            className="mt-4 rounded-full"
            onClick={() => {
              if (pendingFinalize) {
                void finalizeSession(pendingFinalize.session, pendingFinalize.result);
                return;
              }
              setError("");
              setStep("consent");
            }}
          >
            {pendingFinalize
              ? german
                ? "Speichern erneut versuchen"
                : "Try saving again"
              : german
                ? "Nochmal versuchen"
                : "Try again"}
          </Button>
        </Card>
      )}
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center bg-ivory px-4 py-6 sm:px-5 sm:py-10">
      <div className="mb-6 flex w-full max-w-xl flex-wrap items-center justify-between gap-2 sm:mb-8">
        <span className="font-mono text-[0.6875rem] uppercase tracking-[0.2em] text-pine sm:text-[0.75rem] sm:tracking-[0.24em]">
          SIXSENTENCES_
        </span>
        <span className="flex items-center gap-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-moss-soft sm:text-[0.59375rem] sm:tracking-[0.18em]">
          <AudioLines className="size-3.5" /> AI interview · disclosed
        </span>
      </div>
      <div className="flex w-full flex-1 flex-col items-center justify-center">
        {children}
      </div>
      <PublicLegalFooter className="mt-6 w-full max-w-xl shrink-0" />
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="w-full max-w-xl rounded-3xl border border-border bg-card p-5 shadow-sm sm:p-7">
      {children}
    </div>
  );
}
