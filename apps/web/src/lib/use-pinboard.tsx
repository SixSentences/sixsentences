"use client";

import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

import { api, ApiError, getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PinboardSession } from "@/lib/pinboard";

const PinboardContext = createContext<PinboardSession | null>(null);

/** Keep a private in-memory draft across app routes, never across auth boundaries. */
export function PinboardProvider({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const identity = me ? `${me.org_id}:${me.user_id}` : null;
  const [session, setSession] = useState<PinboardSession | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!identity || !token) return;
    const next = new PinboardSession({
      canAccess: () => getToken() === token,
      read: (signal) => api.pinboard(signal),
      write: (state, signal) => api.updatePinboard(state, signal),
      isConflict: (error) => error instanceof ApiError && error.status === 409,
    });
    setSession(next);
    const clear = () => {
      next.dispose();
      setSession((current) => current === next ? null : current);
    };
    const storage = () => { if (getToken() !== token) clear(); };
    window.addEventListener("six:auth-boundary", clear);
    window.addEventListener("storage", storage);
    return () => {
      next.dispose();
      window.removeEventListener("six:auth-boundary", clear);
      window.removeEventListener("storage", storage);
    };
  }, [identity]);

  return (
    <PinboardContext.Provider value={session}>
      {session && <PinboardPersistence session={session} />}
      {children}
    </PinboardContext.Provider>
  );
}

function PinboardPersistence({ session }: { session: PinboardSession }) {
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  useEffect(() => {
    if (!snapshot.dirty || snapshot.phase !== "ready") return;
    const timer = window.setTimeout(() => { void session.save(); }, 650);
    return () => window.clearTimeout(timer);
  }, [session, snapshot]);
  useEffect(() => {
    if (!snapshot.dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [snapshot.dirty]);
  return null;
}

/** The provider lives above route content; the board itself loads on first visit. */
export function usePinboard(): PinboardSession | null {
  return useContext(PinboardContext);
}
