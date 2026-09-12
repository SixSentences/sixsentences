"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { Check, Loader2, MailCheck } from "lucide-react";

import AuthShell from "@/components/auth/auth-shell";
import {
  AuthMethodDivider,
  GoogleAuthButton,
} from "@/components/auth/google-auth-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { publicLegalUrl } from "@/lib/public-links";
import { useSignupStatus } from "@/lib/signup-control";
import {
  SIGNUP_LEGAL_VERSIONS,
  type SignupLegalAcceptance,
} from "@/lib/legal";
import { evaluatePasswordStrength } from "@/lib/password-strength";
import { captureResearchQuestionFromHash } from "@/lib/research-question-handoff";
import { cn } from "@/lib/utils";

export default function RegisterPage() {
  const { status, signIn } = useAuth();
  const signup = useSignupStatus();
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [ageRequirementConfirmed, setAgeRequirementConfirmed] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [verification, setVerification] = useState<{
    email: string;
    sent: boolean;
  } | null>(null);
  const [resending, setResending] = useState(false);
  const passwordStrength = evaluatePasswordStrength(password, { email, name });
  const passwordsMatch =
    passwordConfirmation.length > 0 && password === passwordConfirmation;
  const termsUrl = publicLegalUrl("terms");
  const privacyUrl = publicLegalUrl("privacy");
  const dpaUrl = publicLegalUrl("dpa");
  const legalDocumentsConfigured = Boolean(termsUrl && privacyUrl && dpaUrl);

  function legalAcceptance(): SignupLegalAcceptance | null {
    if (!SIGNUP_LEGAL_VERSIONS || !legalDocumentsConfigured) return null;
    return {
      age_requirement_confirmed: ageRequirementConfirmed,
      terms_accepted: termsAccepted,
      terms_version: SIGNUP_LEGAL_VERSIONS.terms,
      privacy_acknowledged: false,
      privacy_version: SIGNUP_LEGAL_VERSIONS.privacy,
      dpa_accepted: false,
      dpa_version: SIGNUP_LEGAL_VERSIONS.dpa,
      // Compatibility default for APIs that still accept the former hosted field.
      marketing_consent: false,
    };
  }

  useEffect(() => {
    captureResearchQuestionFromHash();
    if (status === "signed-in") router.replace("/");
  }, [status, router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!signup.enabled) return;
    const acceptance = legalAcceptance();
    if (!acceptance) {
      setError("Registration is unavailable until the deployment operator configures its legal documents.");
      return;
    }
    const data = new FormData(event.currentTarget);
    const orgName = String(data.get("org") ?? "").trim();

    const normalizedName = name.trim();
    const normalizedEmail = email.trim();
    if (!normalizedName) {
      setError("Please tell us your first name.");
      return;
    }
    if (!passwordStrength.strong) {
      setError("Choose a strong password that meets every requirement below.");
      return;
    }
    if (!passwordsMatch) {
      setError("The passwords do not match.");
      return;
    }
    if (!ageRequirementConfirmed || !termsAccepted) {
      setError("Confirm that you are at least 18 and accept the Terms to continue.");
      return;
    }

    setPending(true);
    setError("");
    try {
      const result = await api.register(
        normalizedName,
        normalizedEmail,
        password,
        orgName,
        acceptance,
      );
      if (result.verification_required) {
        setVerification({
          email: result.email,
          sent: result.email_sent,
        });
        setPending(false);
      } else {
        await signIn(result.token);
        router.replace("/");
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Something went wrong. Please try again.",
      );
      setPending(false);
    }
  }

  async function handleGoogleCredential(credential: string) {
    if (!signup.enabled) return;
    const acceptance = legalAcceptance();
    if (!acceptance) {
      setError("Registration is unavailable until the deployment operator configures its legal documents.");
      return;
    }
    setPending(true);
    setError("");
    try {
      if (!ageRequirementConfirmed || !termsAccepted) {
        setError("Confirm that you are at least 18 and accept the Terms to continue.");
        setPending(false);
        return;
      }
      const result = await api.googleAuth(
        credential,
        "signup",
        "",
        acceptance,
      );
      if ("verification_required" in result && result.verification_required) {
        setVerification({
          email: result.email,
          sent: result.email_sent,
        });
        setPending(false);
        return;
      }
      if ("token" in result) {
        await signIn(result.token);
        router.replace("/");
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Google signup did not complete. Please try again.",
      );
      setPending(false);
    }
  }

  const legalConfigurationMissing = signup.enabled
    && (!SIGNUP_LEGAL_VERSIONS || !legalDocumentsConfigured);

  if (!verification && (!signup.enabled || legalConfigurationMissing)) return (
    <AuthShell>
      <h1 data-launch-signup-control="runtime" className="font-display text-4xl text-foreground">
        {signup.isPending
          ? "Checking availability…"
          : legalConfigurationMissing
            ? "Registration is not configured"
            : "Registration is currently closed"}
      </h1>
      <p className="mt-3 text-[0.9375rem] text-muted-foreground">
        {signup.isError
          ? "We could not check registration right now. Please try again shortly."
          : legalConfigurationMissing
            ? "The deployment operator must publish its legal documents and versions before accepting registrations."
            : "Already have an account? You can still sign in as usual."}
      </p>
      <Link href="/login" className="mt-6 inline-block text-moss underline underline-offset-4">Sign in</Link>
    </AuthShell>
  );

  if (verification) {
    const verificationEmail = verification.email;

    async function resend() {
      setResending(true);
      setError("");
      try {
        await api.resendVerification(verificationEmail);
        setVerification((current) => (current ? { ...current, sent: true } : current));
      } catch (err) {
        setError(err instanceof Error ? err.message : "The email could not be sent.");
      } finally {
        setResending(false);
      }
    }

    return (
      <AuthShell>
        <div className="rise rise-1">
          <span className="grid size-11 place-items-center rounded-full bg-accent">
            <MailCheck className="size-5 text-moss" />
          </span>
          <h1 className="mt-4 font-display text-4xl text-foreground">Confirm your email</h1>
          <p className="mt-3 text-[0.9375rem] leading-relaxed text-muted-foreground">
            {verification.sent
              ? `We sent a confirmation link to ${verification.email}.`
              : `Your account is ready, but the message to ${verification.email} has not left yet.`}
          </p>
          <p className="mt-3 text-[0.8125rem] leading-relaxed text-muted-foreground">
            Open the link within twenty four hours. You can close this page safely.
          </p>
          <Button
            type="button"
            variant="outline"
            disabled={resending}
            onClick={() => void resend()}
            className="mt-6 h-11 w-full rounded-full text-[0.9375rem]"
          >
            {resending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Sending…
              </>
            ) : (
              "Send a new link"
            )}
          </Button>
          {error ? (
            <p className="mt-4 font-mono text-[0.75rem] tracking-wide text-destructive">
              {error}
            </p>
          ) : null}
          <p className="mt-6 text-center text-[0.875rem] text-muted-foreground">
            <Link
              href="/login"
              className="font-medium text-moss underline decoration-moss/35 underline-offset-4 transition hover:text-foreground hover:decoration-moss"
            >
              Back to sign in
            </Link>
          </p>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <div className="rise rise-1" data-launch-signup-surface="open">
        <h1 className="font-display text-4xl text-foreground">Create your account</h1>
        <p className="mt-2 text-[0.9375rem] text-muted-foreground">
          Create a research workspace on this deployment.
        </p>
      </div>

      <div className="rise rise-2 mt-8 space-y-3 rounded-2xl border border-border/80 bg-card/45 p-4">
        <label className="flex cursor-pointer items-start gap-3 text-[0.8125rem] leading-relaxed text-foreground">
          <input
            type="checkbox"
            checked={ageRequirementConfirmed}
            onChange={(event) => {
              setAgeRequirementConfirmed(event.target.checked);
              setError("");
            }}
            className="mt-0.5 size-4 shrink-0 accent-moss"
          />
          <span>I confirm that I am at least 18 years old.</span>
        </label>
        <label className="flex cursor-pointer items-start gap-3 text-[0.8125rem] leading-relaxed text-foreground">
          <input
            type="checkbox"
            checked={termsAccepted}
            onChange={(event) => {
              setTermsAccepted(event.target.checked);
              setError("");
            }}
            className="mt-0.5 size-4 shrink-0 accent-moss"
          />
          <span>
            I accept the{" "}
            <a
              href={termsUrl!}
              target="_blank"
              rel="noreferrer"
              className="text-moss underline underline-offset-4"
            >
              Terms
            </a>
            .
          </span>
        </label>
        <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">
          Our{" "}
          <a href={privacyUrl!} target="_blank" rel="noreferrer" className="text-moss underline underline-offset-4">Privacy Notice</a>
          {" "}explains how we process your data. It is information, not an additional consent.
        </p>
        <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
          Before using your workspace, its authorised owner completes the{" "}
          <a href={dpaUrl!} target="_blank" rel="noreferrer" className="text-moss underline underline-offset-4">Data Processing Agreement</a>
          {" "}for the responsible person or organisation.
        </p>
      </div>

      <div className="mt-5 space-y-4">
        <GoogleAuthButton
          intent="signup"
          disabled={pending || !ageRequirementConfirmed || !termsAccepted}
          onCredential={(credential) => void handleGoogleCredential(credential)}
          onLoadError={() =>
            setError("Google signup could not be loaded. You can still use email.")
          }
        />
        <AuthMethodDivider />
      </div>

      <form
        method="post"
        onSubmit={handleSubmit}
        className="mt-4 space-y-4"
        noValidate
      >
        <div className="space-y-2">
          <Label htmlFor="name">First name</Label>
          <Input
            id="name"
            name="name"
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setError("");
            }}
            autoComplete="given-name"
            placeholder="Ada"
            required
            className="h-11 rounded-xl"
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            name="email"
            type="email"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setError("");
            }}
            autoComplete="email"
            placeholder="you@university.edu"
            required
            className="h-11 rounded-xl"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setError("");
            }}
            autoComplete="new-password"
            placeholder="Create a strong password"
            required
            minLength={12}
            maxLength={200}
            aria-invalid={password.length > 0 && !passwordStrength.strong}
            aria-describedby="password-strength"
            className="h-11 rounded-xl"
          />
          <div id="password-strength" className="space-y-2 pt-0.5">
            <div className="flex items-center gap-1.5">
              {[1, 2, 3, 4].map((segment) => (
                <span
                  key={segment}
                  className={cn(
                    "h-1 flex-1 rounded-full bg-border transition-colors",
                    segment <= passwordStrength.score &&
                      (passwordStrength.score <= 1
                        ? "bg-destructive"
                        : passwordStrength.score === 2
                          ? "bg-amber-500"
                          : passwordStrength.score === 3
                            ? "bg-moss/55"
                            : "bg-moss"),
                  )}
                />
              ))}
              <span
                className={cn(
                  "ml-1 min-w-11 text-right font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted-foreground",
                  password.length > 0 &&
                    (passwordStrength.strong
                      ? "text-moss"
                      : passwordStrength.score <= 1
                        ? "text-destructive"
                        : "text-amber-700"),
                )}
              >
                {password.length > 0 ? passwordStrength.label : "Strength"}
              </span>
            </div>
            <div className="grid gap-x-3 gap-y-1 sm:grid-cols-2">
              {[
                {
                  met: passwordStrength.criteria.length,
                  label: "12 or more characters",
                },
                {
                  met: passwordStrength.criteria.variety,
                  label: "3 character types or 16+ character passphrase",
                },
                {
                  met: passwordStrength.criteria.notCommon,
                  label: "No common password or sequence",
                },
                {
                  met: passwordStrength.criteria.notPersonal,
                  label: "Does not contain your name or email",
                },
              ].map((criterion) => (
                <span
                  key={criterion.label}
                  className={cn(
                    "flex items-start gap-1.5 text-[0.6875rem] leading-relaxed text-muted-foreground",
                    criterion.met && "text-moss",
                  )}
                >
                  <span
                    className={cn(
                      "mt-[0.2rem] grid size-3.5 shrink-0 place-items-center rounded-full border border-border",
                      criterion.met && "border-moss bg-moss text-ivory",
                    )}
                  >
                    {criterion.met ? <Check className="size-2.5" /> : null}
                  </span>
                  {criterion.label}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="password-confirmation">Repeat password</Label>
          <Input
            id="password-confirmation"
            name="password-confirmation"
            type="password"
            value={passwordConfirmation}
            onChange={(event) => {
              setPasswordConfirmation(event.target.value);
              setError("");
            }}
            autoComplete="new-password"
            placeholder="Repeat your password"
            required
            minLength={12}
            maxLength={200}
            aria-invalid={passwordConfirmation.length > 0 && !passwordsMatch}
            aria-describedby="password-match"
            className="h-11 rounded-xl"
          />
          <p
            id="password-match"
            aria-live="polite"
            className={cn(
              "min-h-4 text-[0.6875rem] text-muted-foreground",
              passwordConfirmation.length > 0 &&
                (passwordsMatch ? "text-moss" : "text-destructive"),
            )}
          >
            {passwordConfirmation.length > 0
              ? passwordsMatch
                ? "Passwords match."
                : "Passwords do not match yet."
              : ""}
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="org">
            Lab or organization{" "}
            <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="org"
            name="org"
            type="text"
            autoComplete="organization"
            placeholder="e.g. NLP Lab, Reutlingen University"
            className="h-11 rounded-xl"
          />
        </div>

        <div aria-live="polite" role="status">
          {error ? (
            <p className="font-mono text-[0.75rem] tracking-wide text-destructive">{error}</p>
          ) : null}
        </div>

        <Button
          type="submit"
          disabled={
            pending ||
              !passwordStrength.strong ||
              !passwordsMatch ||
              !ageRequirementConfirmed ||
              !termsAccepted
          }
          className="h-11 w-full rounded-full text-[0.9375rem]"
        >
          {pending ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Creating account…
            </>
          ) : (
            "Create account"
          )}
        </Button>
      </form>

      <p className="rise rise-3 mt-8 text-center text-[0.875rem] text-muted-foreground">
        Already have an account?{" "}
        <Link
          href="/login"
          className="font-medium text-moss underline decoration-moss/35 underline-offset-4 transition hover:text-foreground hover:decoration-moss"
        >
          Sign in
        </Link>
      </p>
    </AuthShell>
  );
}
