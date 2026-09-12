"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";
import { Loader2, MailCheck } from "lucide-react";

import AuthShell from "@/components/auth/auth-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") ?? "").trim();
    if (!email) return;
    setPending(true);
    setError("");
    try {
      await api.forgotPassword(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthShell>
      {sent ? (
        <div className="rise rise-1">
          <span className="grid size-11 place-items-center rounded-full bg-accent">
            <MailCheck className="size-5 text-moss" />
          </span>
          <h1 className="mt-4 font-display text-4xl text-foreground">Check your inbox</h1>
          <p className="mt-3 text-[0.9375rem] leading-relaxed text-muted-foreground">
            If that account exists, we sent a secure reset link. It is valid
            for one hour.
          </p>
          <p className="mt-6 text-center text-[0.875rem] text-muted-foreground">
            <Link
              href="/login"
              className="font-medium text-moss underline decoration-moss/35 underline-offset-4 transition hover:text-foreground hover:decoration-moss"
            >
              Back to sign in
            </Link>
          </p>
        </div>
      ) : (
        <>
          <div className="rise rise-1">
            <h1 className="font-display text-4xl text-foreground">Forgot your password?</h1>
            <p className="mt-2 text-[0.9375rem] text-muted-foreground">
              We&apos;ll send a secure link for your account.
            </p>
          </div>

          <form
            method="post"
            onSubmit={handleSubmit}
            className="rise rise-2 mt-8 space-y-4"
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
                  Issuing…
                </>
              ) : (
                  "Send reset link"
              )}
            </Button>
          </form>

          <p className="rise rise-3 mt-8 text-center text-[0.875rem] text-muted-foreground">
            Remembered it?{" "}
            <Link
              href="/login"
              className="font-medium text-moss underline decoration-moss/35 underline-offset-4 transition hover:text-foreground hover:decoration-moss"
            >
              Sign in
            </Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
