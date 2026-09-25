"use client";

/**
 * The written interview: the mic-free fallback for /talk participants.
 *
 * Same session, same guide, same audit trail — but no realtime needed:
 * each turn is one ordinary server round trip against the session's stored
 * interviewer prompt (the native live model is audio-only by design). A
 * neutral kickoff wakes the interviewer without polluting the transcript.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, PhoneOff, SendHorizontal } from "lucide-react";

import {
  AgentLoadingOrb,
  AgentResponseAvatar,
} from "@/components/agent-work-status";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import type { TextVoiceSessionConfig, VoiceTurn } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { LiveSessionResult } from "@/components/voice/live-session";

const KICKOFF_DE = "[Die Person ist dem schriftlichen Interview beigetreten.]";
const KICKOFF_EN = "[The person joined the written interview.]";

export default function TextSession({
  config,
  sendMessage,
  onFinished,
  onError,
}: {
  config: TextVoiceSessionConfig;
  sendMessage: (message: string, history: VoiceTurn[]) => Promise<string>;
  onFinished: (result: LiveSessionResult) => void;
  onError: (message: string) => void;
}) {
  const german = config.language === "de";
  const [closing, setClosing] = useState(false);
  const [agentTyping, setAgentTyping] = useState(true);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<VoiceTurn[]>([]);
  const [timeLimitReached, setTimeLimitReached] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const finishedRef = useRef(false);
  const mountedRef = useRef(false);
  const requestInFlightRef = useRef(false);
  const onFinishedRef = useRef(onFinished);
  const state = useRef<{ turns: VoiceTurn[]; startedAt: number; kicked: boolean }>({
    turns: [],
    startedAt: 0,
    kicked: false,
  });

  const now = () =>
    state.current.startedAt ? performance.now() - state.current.startedAt : 0;

  useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const finish = useCallback(() => {
    if (!mountedRef.current || finishedRef.current) return;
    finishedRef.current = true;
    const engine = state.current;
    setClosing(true);
    onFinishedRef.current({
      turns: engine.turns.map((turn) => ({ ...turn })),
      duration_ms: Math.round(
        engine.startedAt ? performance.now() - engine.startedAt : 0,
      ),
      aborted:
        engine.turns.filter((turn) => turn.role === "participant").length === 0,
    });
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentTyping]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setTimeLimitReached(true);
      finish();
    }, Math.max(1, config.max_session_minutes) * 60_000);
    return () => window.clearTimeout(timeout);
  }, [config.max_session_minutes, finish]);

  useEffect(() => {
    const engine = state.current;
    if (engine.kicked) return;
    engine.kicked = true;
    requestInFlightRef.current = true;
    engine.startedAt = performance.now();
    sendMessage(german ? KICKOFF_DE : KICKOFF_EN, [])
      .then((reply) => {
        if (!mountedRef.current || finishedRef.current) return;
        const turn: VoiceTurn = {
          role: "interviewer",
          text: reply,
          start_ms: 0,
          end_ms: Math.round(now()),
        };
        engine.turns.push(turn);
        setMessages([turn]);
      })
      .catch(() => {
        if (!mountedRef.current || finishedRef.current) return;
        onError(
          german
            ? "Die Interviewerin ist gerade nicht erreichbar. Bitte versuchen Sie es später erneut."
            : "The interviewer is not available right now. Please try again later.",
        );
      })
      .finally(() => {
        requestInFlightRef.current = false;
        if (mountedRef.current && !finishedRef.current) setAgentTyping(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function send() {
    const engine = state.current;
    const text = input.trim();
    if (
      !text ||
      !mountedRef.current ||
      requestInFlightRef.current ||
      agentTyping ||
      closing ||
      finishedRef.current
    ) return;
    if (now() >= config.max_session_minutes * 60_000) {
      setTimeLimitReached(true);
      finish();
      return;
    }
    // React state updates do not synchronously block a second submit event.
    requestInFlightRef.current = true;
    const priorTurns = [...engine.turns];
    const userTurn: VoiceTurn = {
      role: "participant",
      text,
      start_ms: Math.round(now()),
      end_ms: Math.round(now()),
    };
    engine.turns.push(userTurn);
    setMessages((current) => [...current, userTurn]);
    setInput("");
    setAgentTyping(true);
    try {
      const reply = await sendMessage(text, priorTurns);
      if (!mountedRef.current || finishedRef.current) return;
      const agentTurn: VoiceTurn = {
        role: "interviewer",
        text: reply,
        start_ms: Math.round(now()),
        end_ms: Math.round(now()),
      };
      engine.turns.push(agentTurn);
      setMessages((current) => [...current, agentTurn]);
    } catch (error) {
      if (!mountedRef.current || finishedRef.current) return;
      if (error instanceof ApiError && error.status === 409) {
        setTimeLimitReached(true);
        finish();
        return;
      }
      // the participant's words are kept; they can retry or end
      setMessages((current) => [
        ...current,
        {
          role: "interviewer",
          text: german
            ? "[Kurze Störung. Bitte senden Sie Ihre Antwort noch einmal.]"
            : "[Brief hiccup. Please send your answer again.]",
          start_ms: Math.round(now()),
          end_ms: Math.round(now()),
        },
      ]);
    } finally {
      requestInFlightRef.current = false;
      if (mountedRef.current && !finishedRef.current) setAgentTyping(false);
    }
  }

  return (
    <div lang={german ? "de" : "en"} className="flex min-h-[70vh] flex-col rounded-3xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss-soft">
          {german
            ? `Schriftliches KI-Interview · bis ${config.max_session_minutes} Min.`
            : `Written AI interview · up to ${config.max_session_minutes} min`}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-8 rounded-full px-3 text-[0.71875rem]"
          disabled={closing}
          onClick={finish}
        >
          {closing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <PhoneOff className="size-3.5" />
          )}
          {german ? "Interview beenden" : "End interview"}
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-5">
        {messages.map((message, index) => (
          <div
            key={index}
            className={cn(
              "flex items-start gap-2.5",
              message.role === "participant" && "justify-end",
            )}
          >
            {message.role !== "participant" ? <AgentResponseAvatar /> : null}
            <div
              className={cn(
                "max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-[0.875rem] leading-relaxed",
                message.role === "participant"
                  ? "rounded-br-md bg-moss-surface text-ivory"
                  : "rounded-bl-md border border-border bg-secondary/40 text-foreground",
              )}
            >
              {message.text}
            </div>
          </div>
        ))}
        {agentTyping && (
          <div className="flex items-center gap-2.5">
            <AgentLoadingOrb />
            <p className="shimmer-text text-[0.75rem] font-medium">
              {german
                ? "Der Interview-Agent formuliert die nächste Frage…"
                : "The interview agent is preparing the next question…"}
            </p>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form
        method="post"
        className="flex items-end gap-2 border-t border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        {timeLimitReached && (
          <p className="sr-only" role="status">
            {german
              ? "Die maximale Interviewdauer ist erreicht. Das Interview wird gespeichert."
              : "The maximum interview duration has been reached. The interview is being saved."}
          </p>
        )}
        <Textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={german ? "Deine Antwort…" : "Your answer…"}
          rows={2}
          disabled={closing || timeLimitReached}
          className="min-h-0 flex-1 resize-none rounded-xl text-[0.875rem]"
        />
        <Button
          type="submit"
          size="icon"
          className="size-10 shrink-0 rounded-full"
          disabled={!input.trim() || agentTyping || closing || timeLimitReached}
          aria-label={german ? "Senden" : "Send"}
        >
          <SendHorizontal className="size-4" />
        </Button>
      </form>
    </div>
  );
}
