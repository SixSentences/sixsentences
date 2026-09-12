"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

export const GOOGLE_AUTH_ENABLED = Boolean(
  process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
);

type GoogleCredentialResponse = {
  credential: string;
  select_by: string;
};

type GoogleButtonConfiguration = {
  type: "standard";
  theme: "outline";
  size: "large";
  text: "signin_with" | "signup_with";
  shape: "pill";
  logo_alignment: "left";
  width: number;
};

type GoogleAccounts = {
  id: {
    initialize: (configuration: {
      client_id: string;
      callback: (response: GoogleCredentialResponse) => void;
      context: "signin" | "signup";
      cancel_on_tap_outside: boolean;
      itp_support: boolean;
    }) => void;
    renderButton: (
      parent: HTMLElement,
      configuration: GoogleButtonConfiguration,
    ) => void;
  };
};

declare global {
  interface Window {
    google?: { accounts: GoogleAccounts };
  }
}

type GoogleAuthButtonProps = {
  intent: "login" | "signup";
  disabled?: boolean;
  onCredential: (credential: string) => void;
  onLoadError: () => void;
};

export function GoogleAuthButton({
  intent,
  disabled = false,
  onCredential,
  onLoadError,
}: GoogleAuthButtonProps) {
  const clientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";
  const containerRef = useRef<HTMLDivElement>(null);
  const credentialCallback = useRef(onCredential);
  const initialized = useRef(false);
  const [scriptReady, setScriptReady] = useState(false);

  credentialCallback.current = onCredential;

  const renderButton = useCallback(() => {
    const google = window.google;
    const container = containerRef.current;
    if (!clientId || !google || !container) return;
    if (!initialized.current) {
      google.accounts.id.initialize({
        client_id: clientId,
        callback: (response) => credentialCallback.current(response.credential),
        context: intent === "signup" ? "signup" : "signin",
        cancel_on_tap_outside: true,
        itp_support: true,
      });
      initialized.current = true;
    }
    const width = Math.min(400, Math.max(240, Math.floor(container.clientWidth)));
    container.replaceChildren();
    google.accounts.id.renderButton(container, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: intent === "signup" ? "signup_with" : "signin_with",
      shape: "pill",
      logo_alignment: "left",
      width,
    });
  }, [clientId, intent]);

  useEffect(() => {
    if (window.google) setScriptReady(true);
  }, []);

  useEffect(() => {
    if (!scriptReady) return;
    renderButton();
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(renderButton);
    observer.observe(container);
    return () => observer.disconnect();
  }, [renderButton, scriptReady]);

  if (!clientId) return null;

  return (
    <>
      <Script
        id="google-identity-services"
        src="https://accounts.google.com/gsi/client"
        strategy="afterInteractive"
        onLoad={() => setScriptReady(true)}
        onError={onLoadError}
      />
      <div
        className={disabled ? "pointer-events-none opacity-60" : undefined}
        aria-busy={disabled}
      >
        <div
          ref={containerRef}
          className="flex h-11 w-full items-center justify-center overflow-hidden rounded-full"
        />
      </div>
    </>
  );
}

export function AuthMethodDivider() {
  if (!GOOGLE_AUTH_ENABLED) return null;
  return (
    <div className="flex items-center gap-3 py-1" aria-hidden="true">
      <span className="h-px flex-1 bg-border" />
      <span className="font-mono text-[0.625rem] tracking-[0.18em] text-muted-foreground">
        OR CONTINUE WITH EMAIL
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
