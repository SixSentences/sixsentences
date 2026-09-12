"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { ArrowLeft, KeyRound, Loader2 } from "lucide-react";

import AuthShell from "@/components/auth/auth-shell";
import {
  AuthMethodDivider,
  GoogleAuthButton,
} from "@/components/auth/google-auth-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useSignupStatus } from "@/lib/signup-control";
import { captureResearchQuestionFromHash } from "@/lib/research-question-handoff";
import { safeAgentArtifactHref } from "@/lib/safe-agent-display";

function postLoginDestination(): string {
  if (typeof window === "undefined") return "/";
  const requested = new URLSearchParams(window.location.search).get("next");
  const safe = safeAgentArtifactHref(requested);
  return safe.startsWith("/") ? safe : "/";
}

export default function LoginPage() {
  const { status, signIn } = useAuth();
  const signup = useSignupStatus();
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");

  useEffect(() => {
    captureResearchQuestionFromHash();
    if (status === "signed-in") router.replace(postLoginDestination());
  }, [status, router]);

  async function handlePasswordSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const email = String(data.get("email") ?? "").trim();
    const password = String(data.get("password") ?? "");
    if (!email || !password) return;

    setPending(true);
    setError("");
    try {
      const result = await api.login(email, password);
      if ("mfa_required" in result && result.mfa_required) {
        setChallenge(result.challenge);
        setCode("");
        setPending(false);
        return;
      }
      if ("token" in result) {
        await signIn(result.token);
        router.replace(postLoginDestination());
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "That email and password don't match."
          : err instanceof Error
            ? err.message
            : "Something went wrong. Please try again.",
      );
      setPending(false);
    }
  }

  async function handleGoogleCredential(credential: string) {
    setPending(true);
    setError("");
    try {
      const result = await api.googleAuth(credential, "login");
      if ("mfa_required" in result && result.mfa_required) {
        setChallenge(result.challenge);
        setCode("");
        setPending(false);
        return;
      }
      if ("token" in result) {
        await signIn(result.token);
        router.replace(postLoginDestination());
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Google sign-in did not complete. Please try again.",
      );
      setPending(false);
    }
  }

  async function handleSecondFactor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challenge || !code.trim()) return;
    setPending(true);
    setError("");
    try {
      const result = await api.verifyTwoFactorLogin(challenge, code.trim());
      await signIn(result.token);
      router.replace(postLoginDestination());
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "That code is invalid, expired, or has already been used."
          : err instanceof Error
            ? err.message
            : "Something went wrong. Please try again.",
      );
      setPending(false);
    }
  }

  return (
    <AuthShell>
      <div className="rise rise-1">
        <h1 className="font-display text-4xl text-foreground">
          {challenge ? "Confirm it’s you" : "Welcome back"}
        </h1>
        <p className="mt-2 text-[0.9375rem] text-muted-foreground">
          {challenge
            ? "Enter the code from your authenticator app or use a recovery code."
            : "Sign in to continue your searches."}
        </p>
      </div>

      {challenge ? (
        <form
          method="post"
          onSubmit={handleSecondFactor}
          className="rise rise-2 mt-8 space-y-4"
          noValidate
        >
          <div className="space-y-2">
            <Label htmlFor="code">Authentication code</Label>
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="code"
                name="code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                type="text"
                inputMode="text"
                autoComplete="one-time-code"
                autoFocus
                placeholder="000000 or recovery code"
                required
                className="h-12 rounded-xl pl-10 font-mono tracking-[0.12em]"
              />
            </div>
          </div>

          <div aria-live="polite" role="status">
            {error ? (
              <p className="font-mono text-[0.75rem] tracking-wide text-destructive">
                {error}
              </p>
            ) : null}
          </div>

          <Button
            type="submit"
            disabled={pending || !code.trim()}
            className="h-11 w-full rounded-full text-[0.9375rem]"
          >
            {pending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Verifying…
              </>
            ) : (
              "Verify and sign in"
            )}
          </Button>
          <button
            type="button"
            className="mx-auto flex items-center gap-1.5 text-[0.75rem] text-muted-foreground transition hover:text-foreground"
            onClick={() => {
              setChallenge("");
              setCode("");
              setError("");
            }}
          >
            <ArrowLeft className="size-3.5" />
            Use email and password instead
          </button>
        </form>
      ) : (
        <div className="rise rise-2 mt-8 space-y-4">
          <GoogleAuthButton
            intent="login"
            disabled={pending}
            onCredential={(credential) => void handleGoogleCredential(credential)}
            onLoadError={() =>
              setError("Google sign-in could not be loaded. You can still use email.")
            }
          />
          <AuthMethodDivider />
          <form
            method="post"
            onSubmit={handlePasswordSubmit}
            className="space-y-4"
            noValidate
          >
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                placeholder="you@university.edu"
                required
                className="h-11 rounded-xl"
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-baseline justify-between">
                <Label htmlFor="password">Password</Label>
                  <Link
                    href="/forgot"
                    className="text-[0.75rem] text-muted-foreground underline decoration-border underline-offset-4 transition hover:text-foreground"
                  >
                    Forgot password?
                  </Link>
              </div>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                required
                className="h-11 rounded-xl"
              />
            </div>

            <div aria-live="polite" role="status">
              {error ? (
                <p className="font-mono text-[0.75rem] tracking-wide text-destructive">
                  {error}
                </p>
              ) : null}
            </div>

            <Button
              type="submit"
              disabled={pending}
              className="h-11 w-full rounded-full text-[0.9375rem]"
            >
              {pending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Signing in…
                </>
              ) : (
                "Sign in"
              )}
            </Button>
          </form>
        </div>
      )}

      {!challenge && signup.enabled ? (
        <p className="rise rise-3 mt-8 text-center text-[0.875rem] text-muted-foreground">
          New to SixSentences_?{" "}
          <Link
            href="/register"
            className="font-medium text-moss underline decoration-moss/35 underline-offset-4 transition hover:text-foreground hover:decoration-moss"
          >
            Create an account
          </Link>
        </p>
      ) : !challenge ? (
        <p className="rise rise-3 mt-8 text-center text-[0.875rem] text-muted-foreground">
          New registrations are currently closed. Ask your workspace operator for
          access.
        </p>
      ) : null}
    </AuthShell>
  );
}
