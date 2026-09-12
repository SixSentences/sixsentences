"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";

import AuthShell from "@/components/auth/auth-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  // accept ?token=… from a handed-over reset link
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("token");
    if (fromUrl) setToken(fromUrl);
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = String(new FormData(event.currentTarget).get("password") ?? "");
    if (password.length < 8) {
      setError("The password needs at least 8 characters.");
      return;
    }
    setPending(true);
    setError("");
    try {
      await api.resetPassword(token.trim(), password);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthShell>
      {done ? (
        <div className="rise rise-1">
          <span className="grid size-11 place-items-center rounded-full bg-accent">
            <KeyRound className="size-5 text-moss" />
          </span>
          <h1 className="mt-4 font-display text-4xl text-foreground">Password updated</h1>
          <p className="mt-3 text-[0.9375rem] leading-relaxed text-muted-foreground">
            Every previous session was signed out. Sign in with your new
            password.
          </p>
          <Button
            onClick={() => router.push("/login")}
            className="mt-6 h-11 w-full rounded-full text-[0.9375rem]"
          >
            Sign in
          </Button>
        </div>
      ) : (
        <>
          <div className="rise rise-1">
            <h1 className="font-display text-4xl text-foreground">Set a new password</h1>
            <p className="mt-2 text-[0.9375rem] text-muted-foreground">
              Paste the reset token you received. It is valid for one hour.
            </p>
          </div>

          <form
            method="post"
            onSubmit={handleSubmit}
            className="rise rise-2 mt-8 space-y-4"
            noValidate
          >
            <div className="space-y-2">
              <Label htmlFor="token">Reset token</Label>
              <Input
                id="token"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="six_ss_…"
                required
                className="h-11 rounded-xl font-mono text-[0.8125rem]"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">New password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="new-password"
                placeholder="At least 8 characters"
                required
                minLength={8}
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
              disabled={pending || !token.trim()}
              className="h-11 w-full rounded-full text-[0.9375rem]"
            >
              {pending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Updating…
                </>
              ) : (
                "Update password"
              )}
            </Button>
          </form>

          <p className="rise rise-3 mt-8 text-center text-[0.875rem] text-muted-foreground">
            No token yet?{" "}
            <Link
              href="/forgot"
              className="font-medium text-moss underline decoration-moss/35 underline-offset-4 transition hover:text-foreground hover:decoration-moss"
            >
              Request one
            </Link>
          </p>
        </>
      )}
    </AuthShell>
  );
}
