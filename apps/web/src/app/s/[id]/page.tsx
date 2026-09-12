"use client";

import { type ReactNode, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, ClipboardList, LockKeyhole, Loader2, Send } from "lucide-react";

import SixMark from "@/components/brand/six-mark";
import { ParticipantNoticeContent } from "@/components/participant-information";
import PublicLegalFooter from "@/components/public-legal-footer";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { publicLegalUrl } from "@/lib/public-links";
import type { PublicSurvey, SurveyQuestion } from "@/lib/types";
import { cn } from "@/lib/utils";

function CenteredSurveyState({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-dvh flex-col bg-background px-5">
      <div className="grid flex-1 place-items-center py-10">{children}</div>
      <PublicLegalFooter className="shrink-0 pb-5" />
    </main>
  );
}

function PublicQuestion({
  question,
  value,
  onChange,
}: {
  question: SurveyQuestion;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  return (
    <section className="rounded-2xl border border-border bg-card p-5 sm:p-6">
      <h2 className="text-[0.9375rem] font-medium text-foreground">
        {question.title}
        {question.required && <span className="ml-1 text-moss">*</span>}
      </h2>
      {question.description && (
        <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
          {question.description}
        </p>
      )}
      <div className="mt-4">
        {question.type === "short_text" && (
          <Input value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />
        )}
        {question.type === "long_text" && (
          <Textarea value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className="min-h-32" />
        )}
        {question.type === "single_choice" && (
          <div className="space-y-2">
            {question.options.map((option) => (
              <button
                type="button"
                key={option}
                onClick={() => onChange(option)}
                className={cn(
                  "flex w-full cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-[0.78125rem] transition-colors",
                  value === option ? "border-moss bg-moss/7 text-foreground" : "border-border hover:border-moss/45",
                )}
              >
                <span className={cn("grid size-4 place-items-center rounded-full border", value === option ? "border-moss bg-moss" : "border-border")}>
                  {value === option && <span className="size-1.5 rounded-full bg-white" />}
                </span>
                {option}
              </button>
            ))}
          </div>
        )}
        {question.type === "multiple_choice" && (
          <div className="space-y-2">
            {question.options.map((option) => {
              const selected = Array.isArray(value) && value.includes(option);
              return (
                <label key={option} className="flex cursor-pointer items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-[0.78125rem] hover:border-moss/45">
                  <Checkbox
                    checked={selected}
                    onCheckedChange={(checked) => {
                      const current = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
                      onChange(checked ? [...current, option] : current.filter((item) => item !== option));
                    }}
                  />
                  {option}
                </label>
              );
            })}
          </div>
        )}
        {(question.type === "rating" || question.type === "scale") && (
          <div className="flex flex-wrap gap-2">
            {Array.from(
              { length: Math.min(20, (question.max ?? 5) - (question.min ?? 1) + 1) },
              (_, index) => (question.min ?? 1) + index,
            ).map((number) => (
              <button
                type="button"
                key={number}
                onClick={() => onChange(number)}
                className={cn(
                  "grid size-10 cursor-pointer place-items-center rounded-xl border font-mono text-[0.75rem] transition-colors",
                  value === number ? "border-moss bg-moss text-ivory" : "border-border hover:border-moss/45",
                )}
              >
                {number}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export default function PublicSurveyPage() {
  const params = useParams<{ id: string }>();
  const surveyId = params.id;
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [respondentLabel, setRespondentLabel] = useState("");
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [unlockedSurvey, setUnlockedSurvey] = useState<PublicSurvey | null>(null);
  const [consented, setConsented] = useState(false);
  const { data: survey, isLoading, error } = useQuery({
    queryKey: ["public-survey", surveyId],
    queryFn: () => api.publicSurvey(surveyId),
    retry: false,
  });
  const access = useMutation({
    mutationFn: () => api.accessPublicSurvey(surveyId, password),
    onSuccess: (result) => setUnlockedSurvey(result),
  });
  const submit = useMutation({
    mutationFn: () => api.submitSurveyResponse(surveyId, answers, respondentLabel, password, (unlockedSurvey ?? survey)?.participant_information_fingerprint, consented),
    onSuccess: (result) => setConfirmation(result.confirmation),
  });

  const noticeFingerprint = (unlockedSurvey ?? survey)?.participant_information_fingerprint;
  useEffect(() => { setConsented(false); }, [noticeFingerprint]);

  if (isLoading) {
    return (
      <CenteredSurveyState>
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </CenteredSurveyState>
    );
  }
  if (error || !survey) {
    return (
      <CenteredSurveyState>
        <div className="max-w-md text-center"><ClipboardList className="mx-auto size-7 text-muted-foreground" /><h1 className="mt-4 font-display text-3xl text-foreground">This survey is not collecting responses</h1><p className="mt-2 text-sm text-muted-foreground">The link may be invalid, or the research team has closed the survey.</p></div>
      </CenteredSurveyState>
    );
  }
  if (survey.password_protected && !unlockedSurvey) {
    return (
      <CenteredSurveyState>
        <div className="w-full max-w-md rounded-3xl border border-border bg-card p-7 shadow-sm sm:p-8">
          <div className="flex items-center gap-2.5">
            <SixMark className="size-6 text-foreground" />
            <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-foreground">SixSentences_ Survey</span>
          </div>
          <span className="mt-8 grid size-11 place-items-center rounded-2xl bg-secondary text-moss"><LockKeyhole className="size-5" /></span>
          <p className="mt-5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">Protected survey</p>
          <h1 className="mt-2 font-display text-3xl leading-tight text-foreground">{survey.title}</h1>
          {survey.description && <p className="mt-3 text-[0.8125rem] leading-relaxed text-muted-foreground">{survey.description}</p>}
          <form
            method="post"
            className="mt-6"
            onSubmit={(event) => {
              event.preventDefault();
              if (password) access.mutate();
            }}
          >
            <label htmlFor="survey-password" className="text-[0.75rem] font-medium text-foreground">Survey password</label>
            <Input
              id="survey-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-2"
              autoFocus
            />
            {access.error && <p className="mt-2 text-[0.71875rem] text-destructive">{access.error instanceof Error ? access.error.message : "The password is incorrect."}</p>}
            <Button type="submit" className="mt-4 w-full rounded-full" disabled={!password || access.isPending}>
              {access.isPending ? <Loader2 className="size-4 animate-spin" /> : <LockKeyhole className="size-4" />} Open survey
            </Button>
          </form>
          <p className="mt-5 text-[0.6875rem] leading-relaxed text-muted-foreground">The research team restricted this questionnaire. Your password is only used to unlock and submit this form.</p>
        </div>
      </CenteredSurveyState>
    );
  }
  if (confirmation) {
    return (
      <CenteredSurveyState>
        <div className="w-full max-w-lg rounded-3xl border border-border bg-card p-8 text-center shadow-sm">
          <span className="mx-auto grid size-12 place-items-center rounded-full bg-moss text-ivory"><Check className="size-5" /></span>
          <h1 className="mt-5 font-display text-3xl text-foreground">Response received</h1>
          <p className="mt-3 text-[0.875rem] leading-relaxed text-muted-foreground">{confirmation}</p>
        </div>
      </CenteredSurveyState>
    );
  }
  const activeSurvey = unlockedSurvey ?? survey;

  return (
    <main className="flex min-h-dvh flex-col bg-background px-4 py-8 sm:px-6 sm:py-12">
      <div className="mx-auto w-full max-w-2xl flex-1">
        <div className="mb-8 flex items-center gap-2.5"><SixMark className="size-6 text-foreground" /><span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-foreground">SixSentences_ Survey</span></div>
        <header className="rounded-3xl border border-border bg-card p-6 sm:p-8">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">Research survey</p>
          <h1 className="mt-3 font-display text-4xl leading-tight text-foreground">{activeSurvey.title}</h1>
          {activeSurvey.description && <p className="mt-4 text-[0.875rem] leading-relaxed text-muted-foreground">{activeSurvey.description}</p>}
          <p className="mt-5 border-t border-border pt-4 text-[0.6875rem] text-muted-foreground">Required questions are marked with an asterisk. Your response is submitted directly to the research workspace.</p>
        </header>
        {activeSurvey.participant_notice && <div className="mt-4"><ParticipantNoticeContent notice={activeSurvey.participant_notice} /></div>}
        <form
          method="post"
          className="mt-4 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit.mutate();
          }}
        >
          {activeSurvey.settings.collect_identity && (
            <section className="rounded-2xl border border-border bg-card p-5 sm:p-6">
              <h2 className="text-[0.9375rem] font-medium text-foreground">Participant label</h2>
              <p className="mt-1 text-[0.75rem] text-muted-foreground">Optional. Use the identifier provided by the research team.</p>
              <Input value={respondentLabel} onChange={(event) => setRespondentLabel(event.target.value)} className="mt-4" />
            </section>
          )}
          {activeSurvey.questions.map((question) => (
            <PublicQuestion key={question.id} question={question} value={answers[question.id]} onChange={(value) => setAnswers((current) => ({ ...current, [question.id]: value }))} />
          ))}
          {activeSurvey.participant_notice?.requires_consent ? (
            <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-border bg-card p-5 text-sm leading-relaxed">
              <input type="checkbox" className="mt-1" checked={consented} onChange={(event) => setConsented(event.target.checked)} />
              {activeSurvey.participant_notice.declaration}
            </label>
          ) : <p className="text-sm text-muted-foreground">{activeSurvey.participant_notice?.declaration}</p>}
          {submit.error && <p className="rounded-xl bg-destructive/8 px-4 py-3 text-[0.75rem] text-destructive">{submit.error instanceof Error ? submit.error.message : "The response could not be submitted."}</p>}
          <Button type="submit" size="lg" className="w-full rounded-full" disabled={submit.isPending || !activeSurvey.participant_information_fingerprint || (activeSurvey.participant_notice?.requires_consent && !consented)}>
            {submit.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />} Submit response
          </Button>
        </form>
        <p className="mt-8 text-center text-[0.6875rem] leading-relaxed text-muted-foreground">
          The research team that shared this link is responsible for this survey and for how responses are processed. Responses are submitted to its configured research workspace.
          {publicLegalUrl("privacy") ? <>{" "}<a href={publicLegalUrl("privacy")!} className="underline underline-offset-2 hover:text-foreground">Privacy</a></> : null}
        </p>
        <p className="mt-3 text-center font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">SixSentences_ research workspace</p>
      </div>
      <PublicLegalFooter className="mx-auto mt-8 w-full max-w-2xl shrink-0" />
    </main>
  );
}
