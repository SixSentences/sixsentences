"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Globe2, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, API_URL } from "@/lib/api";
import { useAuth } from "@/lib/auth";

interface BrowserCaptureRequest {
  codeChallenge: string;
  state: string;
  redirectUri: string;
  deviceName: string;
}

function requestFromUrl(): BrowserCaptureRequest | null {
  const params = new URLSearchParams(window.location.search);
  if (params.get("connect") !== "browser-capture") return null;
  const codeChallenge = params.get("code_challenge") ?? "";
  const state = params.get("state") ?? "";
  const redirectUri = params.get("redirect_uri") ?? "";
  if (!/^[A-Za-z0-9_-]{43}$/.test(codeChallenge)) return null;
  if (!/^[A-Za-z0-9_.~-]{16,128}$/.test(state)) return null;
  if (!/^https:\/\/[a-p]{32}\.chromiumapp\.org\/$/.test(redirectUri)) return null;
  return {
    codeChallenge,
    state,
    redirectUri,
    deviceName: (params.get("device_name") || "Chrome").slice(0, 120),
  };
}

export function BrowserCaptureConnect() {
  const { me } = useAuth();
  const german = me?.language === "de";
  const [request, setRequest] = useState<BrowserCaptureRequest | null>(null);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    const parsed = requestFromUrl();
    setRequest(parsed);
    setOpen(Boolean(parsed));
    if (new URLSearchParams(window.location.search).has("connect")) {
      window.history.replaceState({}, "", "/library");
    }
  }, []);
  const permissions = useMemo(
    () => german
      ? ["Geprüfte Metadaten und ausgewählten Text speichern", "Eine PDF nur nach deiner ausdrücklichen Bestätigung hochladen"]
      : ["Save reviewed metadata and selected text", "Upload a PDF only when you explicitly confirm it"],
    [german],
  );
  if (!request) return null;

  const connect = async () => {
    setPending(true);
    try {
      const result = await api.browserCapturePair({
        code_challenge: request.codeChallenge,
        state: request.state,
        redirect_uri: request.redirectUri,
        device_name: request.deviceName,
      });
      window.location.assign(result.callback_url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not connect Browser Capture.");
      setPending(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(next) => {
      setOpen(next);
      if (!next) window.history.replaceState({}, "", "/library");
    }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <div className="mb-2 grid size-11 place-items-center rounded-2xl bg-accent text-moss">
            <Globe2 className="size-5" />
          </div>
          <DialogTitle>{german ? "Browser Capture verbinden" : "Connect Browser Capture"}</DialogTitle>
          <DialogDescription>
            {german
              ? `Gib ${request.deviceName} einen eingeschränkten, widerrufbaren Schlüssel für deine Library. Passwort und Web-Sitzung bleiben in SixSentences.`
              : `Give ${request.deviceName} a limited, revocable key for your Library. Your password and browser session stay in SixSentences.`}
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-2xl border border-border bg-muted/25 p-4">
          <p className="flex items-center gap-2 text-[0.8125rem] font-medium">
            <ShieldCheck className="size-4 text-moss" /> {german ? "Nur nach deiner Bestätigung" : "Explicit capture only"}
          </p>
          <ul className="mt-3 space-y-2">
            {permissions.map((permission) => (
              <li key={permission} className="flex gap-2 text-[0.75rem] text-muted-foreground">
                <Check className="mt-0.5 size-3.5 shrink-0 text-moss" /> {permission}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[0.6875rem] text-muted-foreground">
            {german ? "Kein vollständiger Seiteninhalt, Browserverlauf, Cookies oder Request-Header. Ein markierter Textausschnitt wird nur nach Bestätigung gespeichert." : "No full page body, browsing history, cookies or request headers. A selected passage is saved only after confirmation."} API: {new URL(API_URL).hostname}
          </p>
        </div>
        <Button className="rounded-full" onClick={connect} disabled={pending}>
          {pending ? <Loader2 className="size-4 animate-spin" /> : <Globe2 className="size-4" />}
          {german ? "Diesen Browser verbinden" : "Connect this browser"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
