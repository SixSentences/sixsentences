"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Check, Loader2, MailCheck, MailWarning } from "lucide-react";

import AuthShell from "@/components/auth/auth-shell";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type State = "ready" | "working" | "complete" | "invalid";

export default function VerifyEmailPage() {
  const { signIn } = useAuth();
  const [token, setToken] = useState("");
  const [state, setState] = useState<State>("ready");
  const [error, setError] = useState("");

  useEffect(() => {
    setToken(new URLSearchParams(window.location.search).get("token") ?? "");
  }, []);

  async function confirm() {
    if (!token) {
      setState("invalid");
      setError("This confirmation link is incomplete.");
      return;
    }
    setState("working");
    setError("");
    try {
      const result = await api.verifyEmail(token);
      await signIn(result.token);
      setState("complete");
    } catch (err) {
      setState("invalid");
      setError(
        err instanceof Error
          ? err.message
          : "This confirmation link is invalid or has expired.",
      );
    }
  }

  return (
    <AuthShell>
      <div className="rise rise-1">
        <span className="grid size-11 place-items-center rounded-full bg-accent">
          {state === "complete" ? (
            <Check className="size-5 text-moss" />
          ) : state === "invalid" ? (
            <MailWarning className="size-5 text-moss" />
          ) : state === "ready" ? (
            <MailCheck className="size-5 text-moss" />
          ) : (
            <Loader2 className="size-5 animate-spin text-moss" />
          )}
        </span>
        <h1 className="mt-4 font-display text-4xl text-foreground">
          {state === "complete"
            ? "Email confirmed"
            : state === "invalid"
              ? "Link unavailable"
              : "Confirm your email"}
        </h1>
        <p className="mt-3 text-[0.9375rem] leading-relaxed text-muted-foreground">
          {state === "complete"
            ? "Your workspace is active and ready for your first research question."
            : state === "invalid"
              ? error
              : "Confirm this address to activate your research workspace."}
        </p>

        {state === "complete" ? (
          <Button asChild className="mt-6 h-11 w-full rounded-full text-[0.9375rem]">
            <Link href="/">Open my workspace</Link>
          </Button>
        ) : state === "invalid" ? (
          <Button asChild variant="outline" className="mt-6 h-11 w-full rounded-full">
            <Link href="/login">Back to sign in</Link>
          </Button>
        ) : (
          <Button
            type="button"
            disabled={state === "working" || !token}
            onClick={() => void confirm()}
            className="mt-6 h-11 w-full rounded-full text-[0.9375rem]"
          >
            {state === "working" ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Confirming…
              </>
            ) : (
              "Confirm email and open workspace"
            )}
          </Button>
        )}
      </div>
    </AuthShell>
  );
}
