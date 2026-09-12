"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, SpecialistStreamError } from "@/lib/api";
import type {
  AgentTurnState,
  SpecialistAgentEvent,
} from "@/lib/types";

interface SpecialistTurnRecoveryOptions<TResult> {
  resourceKind: "manuscript" | "dataset" | "interview" | "interview-study" | "survey";
  resourceId: string;
  enabled?: boolean;
  persistedTurnIds?: string[];
  onStarted: (turn: AgentTurnState) => void;
  onEvent: (event: SpecialistAgentEvent) => void;
  onTerminal: (result: TResult | null, turn: AgentTurnState) => Promise<void> | void;
  onError?: (error: unknown) => void;
}

/**
 * Reattaches a remounted specialist workspace to its owner-scoped server turn.
 *
 * Discovery is bodyless and opaque: prompts, selections and source material
 * never enter browser storage. The event ledger is replayed from the server
 * and followed live until its terminal frame arrives.
 */
export function useDurableSpecialistTurn<TResult>({
  resourceKind,
  resourceId,
  enabled = true,
  persistedTurnIds = [],
  onStarted,
  onEvent,
  onTerminal,
  onError,
}: SpecialistTurnRecoveryOptions<TResult>) {
  const discoveryKey = enabled && resourceId
    ? `${resourceKind}:${resourceId}`
    : "";
  const callbacksRef = useRef({ onStarted, onEvent, onTerminal, onError });
  callbacksRef.current = { onStarted, onEvent, onTerminal, onError };
  const [recovering, setRecovering] = useState(false);
  const [settledDiscoveryKey, setSettledDiscoveryKey] = useState("");
  const recoveredTurnRef = useRef("");
  const persistedTurnIdsRef = useRef(new Set(persistedTurnIds));
  persistedTurnIdsRef.current = new Set(persistedTurnIds);

  useEffect(() => {
    if (!enabled || !resourceId) {
      return;
    }
    const controller = new AbortController();
    let mounted = true;

    const recover = async () => {
      try {
        let activeTurn: AgentTurnState | null = null;
        let turn: AgentTurnState | null = null;
        let discoveryAttempt = 0;
        while (mounted && !turn) {
          try {
            activeTurn = await api.agentTurnActive(resourceKind, resourceId);
            turn = activeTurn ?? await api.agentTurnLatest(resourceKind, resourceId);
            break;
          } catch (error) {
            const transient = !(error instanceof ApiError)
              || error.status === 408
              || error.status === 429
              || error.status >= 500;
            if (!transient) throw error;
            discoveryAttempt += 1;
            await new Promise<void>((resolve) => {
              window.setTimeout(
                resolve,
                Math.min(2_000, 250 * 2 ** Math.min(discoveryAttempt - 1, 3)),
              );
            });
          }
        }
        if (!mounted || !turn || recoveredTurnRef.current === turn.turn_id) return;
        if (!activeTurn && persistedTurnIdsRef.current.has(turn.turn_id)) return;
        recoveredTurnRef.current = turn.turn_id;
        setRecovering(true);
        callbacksRef.current.onStarted(turn);
        const terminal = await api.agentTurnEventsStream<TResult>(
          turn.turn_id,
          (event) => callbacksRef.current.onEvent(event),
          controller.signal,
        );
        if (!mounted) return;
        await callbacksRef.current.onTerminal(terminal.result, {
          ...turn,
          status: terminal.status,
          result: terminal.result,
          error_message: terminal.message,
          finished_at: new Date().toISOString(),
        });
        setRecovering(false);
      } catch (error) {
        if (
          !mounted
          || (error instanceof SpecialistStreamError && error.kind === "cancelled")
        ) {
          return;
        }
        recoveredTurnRef.current = "";
        setRecovering(false);
        callbacksRef.current.onError?.(error);
      } finally {
        if (mounted) setSettledDiscoveryKey(discoveryKey);
      }
    };

    void recover();
    return () => {
      mounted = false;
      controller.abort();
    };
  }, [discoveryKey, enabled, resourceId, resourceKind]);

  // Block sends synchronously on the first render for a newly enabled
  // resource. Waiting for the passive effect would leave one paint where a
  // fast submit (or an external Writer task) could race active-turn discovery.
  const checking = Boolean(discoveryKey) && settledDiscoveryKey !== discoveryKey;
  return { checking, recovering };
}

/**
 * Owns the live channel and cooperative Stop state for one specialist UI.
 * The server turn id remains authoritative across both a fresh POST and a
 * bodyless remount recovery; closing the page only closes this browser's SSE.
 */
export function useSpecialistTurnControl() {
  const activeTurnIdRef = useRef<string | null>(null);
  const streamControllerRef = useRef<AbortController | null>(null);
  const stopRequestedTurnIdRef = useRef<string | null>(null);
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [turnAccepted, setTurnAccepted] = useState(false);
  const [stopping, setStopping] = useState(false);

  const acceptTurn = useCallback((turnId: string) => {
    if (activeTurnIdRef.current !== turnId) return;
    setTurnAccepted(true);
  }, []);

  const beginLocalTurn = useCallback((turnId: string) => {
    streamControllerRef.current?.abort();
    const controller = new AbortController();
    streamControllerRef.current = controller;
    activeTurnIdRef.current = turnId;
    setActiveTurnId(turnId);
    setTurnAccepted(false);
    setStopping(false);
    stopRequestedTurnIdRef.current = null;
    return {
      turnId,
      signal: controller.signal,
      onAccepted: acceptTurn,
    };
  }, [acceptTurn]);

  const recoverTurn = useCallback((turnId: string) => {
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
    activeTurnIdRef.current = turnId;
    setActiveTurnId(turnId);
    setTurnAccepted(true);
    setStopping(false);
    stopRequestedTurnIdRef.current = null;
  }, []);

  const finishTurn = useCallback((turnId?: string) => {
    if (turnId && activeTurnIdRef.current !== turnId) return;
    streamControllerRef.current?.abort();
    streamControllerRef.current = null;
    activeTurnIdRef.current = null;
    setActiveTurnId(null);
    setTurnAccepted(false);
    setStopping(false);
    stopRequestedTurnIdRef.current = null;
  }, []);

  const stopTurn = useCallback(async () => {
    const turnId = activeTurnIdRef.current;
    if (
      !turnId
      || !turnAccepted
      || stopping
      || stopRequestedTurnIdRef.current === turnId
    ) return;
    stopRequestedTurnIdRef.current = turnId;
    setStopping(true);
    try {
      await api.agentTurnStop(turnId);
      // Keep the composer locked until the durable stream publishes its
      // terminal event and the workspace has refetched the receipt.
    } catch (error) {
      stopRequestedTurnIdRef.current = null;
      setStopping(false);
      throw error;
    }
  }, [stopping, turnAccepted]);

  useEffect(
    () => () => {
      streamControllerRef.current?.abort();
    },
    [],
  );

  return {
    activeTurnId,
    turnAccepted,
    stopping,
    beginLocalTurn,
    recoverTurn,
    finishTurn,
    stopTurn,
  };
}
