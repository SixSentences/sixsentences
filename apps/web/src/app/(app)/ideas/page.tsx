"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronUp, Lightbulb, Loader2, Rocket } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useFeatures } from "@/hooks/queries";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { FeatureRequest } from "@/lib/types";
import { cn } from "@/lib/utils";

function SubmitCard() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const submit = useMutation({
    mutationFn: () => api.createFeature(title.trim(), body.trim()),
    onSuccess: () => {
      toast.success("Thanks! Your idea is waiting for review.");
      setTitle("");
      setBody("");
      void queryClient.invalidateQueries({ queryKey: ["features"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const ready = title.trim().length >= 4 && !submit.isPending;

  return (
    <form
      method="post"
      className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-colors focus-within:border-moss/50"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) submit.mutate();
      }}
    >
      <div className="px-5 pb-2 pt-4">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          Suggest an idea
        </p>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="What should SixSentences_ build next?"
          maxLength={140}
          aria-label="Idea title"
          className="mt-2.5 w-full bg-transparent text-[1.125rem] font-medium text-foreground outline-none placeholder:font-normal placeholder:text-muted-foreground/60"
        />
        <textarea
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder="A sentence or two on why it matters (optional)"
          maxLength={2000}
          rows={2}
          aria-label="Idea description"
          className="mt-1.5 w-full resize-none bg-transparent text-[0.875rem] leading-relaxed outline-none placeholder:text-muted-foreground/50"
        />
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-border/60 bg-secondary/40 px-5 py-3">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          Approved ideas go public. Then everyone votes.
        </p>
        <Button
          type="submit"
          size="sm"
          disabled={!ready}
          className="h-8 rounded-full px-5 text-[0.8125rem]"
        >
          {submit.isPending ? <Loader2 className="size-3.5 animate-spin" /> : "Submit"}
        </Button>
      </div>
    </form>
  );
}

function VoteButton({ idea }: { idea: FeatureRequest }) {
  const queryClient = useQueryClient();
  const vote = useMutation({
    mutationFn: () => api.voteFeature(idea.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["features"] }),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const votable = idea.status === "approved";

  return (
    <button
      type="button"
      disabled={!votable || vote.isPending}
      onClick={() => vote.mutate()}
      aria-label={idea.voted ? "Remove your vote" : "Upvote this idea"}
      aria-pressed={idea.voted}
      className={cn(
        "flex w-14 shrink-0 flex-col items-center justify-center gap-1 self-stretch rounded-xl border py-3 transition-all",
        idea.voted
          ? "border-moss bg-moss-surface text-ivory shadow-sm"
          : "border-border bg-secondary/40 text-muted-foreground",
        votable && !idea.voted && "cursor-pointer hover:border-moss/60 hover:bg-accent hover:text-moss",
        votable && idea.voted && "cursor-pointer hover:bg-moss-surface/90",
        !votable && "opacity-45",
      )}
    >
      <ChevronUp className="size-4" strokeWidth={2.5} />
      <span className="font-mono text-[0.9375rem] font-medium leading-none tabular-nums">
        {idea.votes}
      </span>
    </button>
  );
}

function IdeaCard({
  idea,
  rank,
  tone = "open",
}: {
  idea: FeatureRequest;
  rank?: number;
  tone?: "open" | "review" | "shipped" | "declined";
}) {
  return (
    <li
      className={cn(
        "relative flex items-stretch gap-4 rounded-2xl border p-4 transition-colors",
        tone === "open" && "border-border bg-card hover:border-moss/40",
        tone === "review" && "border-dashed border-border bg-secondary/30",
        tone === "shipped" && "border-moss/30 bg-accent/50",
        tone === "declined" && "border-dashed border-border bg-secondary/20 opacity-70",
      )}
    >
      {rank != null ? (
        <span className="absolute -left-10 top-1/2 hidden w-6 -translate-y-1/2 text-right font-mono text-[0.6875rem] text-muted-foreground/50 lg:block">
          {String(rank).padStart(2, "0")}
        </span>
      ) : null}
      {tone === "shipped" ? (
        <span className="grid w-14 shrink-0 place-items-center self-stretch rounded-xl bg-moss-surface/10 text-moss">
          <Rocket className="size-4" />
        </span>
      ) : (
        <VoteButton idea={idea} />
      )}
      <div className="min-w-0 flex-1 py-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-[0.9375rem] font-medium leading-snug text-foreground">{idea.title}</p>
          {idea.mine ? (
            <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">
              yours
            </span>
          ) : null}
        </div>
        {idea.body ? (
          <p className="mt-1 text-[0.8125rem] leading-relaxed text-muted-foreground">
            {idea.body}
          </p>
        ) : null}
        <p className="mt-2 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground/60">
          {tone === "shipped"
            ? `Shipped · ${idea.votes} votes`
            : tone === "review"
              ? "Waiting for review"
              : tone === "declined"
                ? "Declined"
                : `Suggested ${formatDate(idea.created_at)}`}
        </p>
      </div>
    </li>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </p>
      <ul className="mt-3 space-y-3">{children}</ul>
    </section>
  );
}

export default function IdeasPage() {
  const { data: ideas, isLoading } = useFeatures();
  const list = ideas ?? [];
  const inReview = list.filter((idea) => idea.status === "proposed");
  const open = list.filter((idea) => idea.status === "approved");
  const shipped = list.filter((idea) => idea.status === "shipped");
  const declined = list.filter((idea) => idea.status === "declined");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-4 pb-20 pt-6 sm:px-6 sm:pt-12">
        <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
          <Lightbulb className="size-3.5 text-moss" /> Ideas
        </p>
        <h1 className="font-display mt-2 text-[2.4rem] leading-tight text-foreground">
          What should we build next?
        </h1>
        <p className="mt-1 max-w-xl text-[0.875rem] text-muted-foreground">
          Suggest features and vote on the ones you want. The most wanted ideas
          move to the top of our list.
        </p>

        <div className="mt-8">
          <SubmitCard />
        </div>

        {isLoading ? (
          <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" />
        ) : list.length === 0 ? (
          <div className="mt-10 rounded-2xl border border-dashed border-border px-6 py-12 text-center">
            <span className="mx-auto grid size-11 place-items-center rounded-full bg-accent">
              <Lightbulb className="size-5 text-moss" />
            </span>
            <p className="mt-4 text-[0.9375rem] font-medium text-foreground">
              Nothing on the board yet
            </p>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              Yours could be the first idea here.
            </p>
          </div>
        ) : (
          <>
            {inReview.length > 0 ? (
              <Section label="In review">
                {inReview.map((idea) => (
                  <IdeaCard key={idea.id} idea={idea} tone="review" />
                ))}
              </Section>
            ) : null}
            {open.length > 0 ? (
              <Section label="Open for votes">
                {open.map((idea, index) => (
                  <IdeaCard key={idea.id} idea={idea} rank={index + 1} tone="open" />
                ))}
              </Section>
            ) : null}
            {shipped.length > 0 ? (
              <Section label="Shipped">
                {shipped.map((idea) => (
                  <IdeaCard key={idea.id} idea={idea} tone="shipped" />
                ))}
              </Section>
            ) : null}
            {declined.length > 0 ? (
              <Section label="Declined">
                {declined.map((idea) => (
                  <IdeaCard key={idea.id} idea={idea} tone="declined" />
                ))}
              </Section>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
