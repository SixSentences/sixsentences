"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import {
  EditorialEmptyState,
  EditorialKicker,
  EditorialMetricStrip,
} from "@/components/workspace/editorial-workspace";

import { Button } from "@/components/ui/button";
import { track } from "@/lib/analytics";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import type { Survey } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<Survey["status"], string> = {
  draft: "bg-secondary text-muted-foreground",
  live: "bg-moss-surface/10 text-moss",
  closed: "bg-amber-100 text-amber-800",
};

export default function SurveysPage() {
  const router = useRouter();
  const { me } = useAuth();
  const german = me?.language === "de";
  const queryClient = useQueryClient();
  const { data: surveys, isLoading } = useQuery({
    queryKey: ["surveys"],
    queryFn: api.surveys,
  });
  const create = useMutation({
    mutationFn: () =>
      api.surveyCreate({
        title: "Untitled research survey",
        description: "",
        questions: [
          {
            id: crypto.randomUUID(),
            title: "What would you like us to know?",
            description: "",
            type: "long_text",
            required: false,
            options: [],
            min: null,
            max: null,
          },
        ],
      }),
    onSuccess: (survey) => {
      track("survey_created");
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      router.push(`/surveys/${survey.public_id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Survey creation failed."),
  });
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.surveyDelete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      toast.success("Survey deleted.");
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Survey deletion failed."),
  });

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-background md:rounded-t-2xl">
      <div className="w-full px-4 pb-16 pt-6 sm:px-6 sm:pt-10 lg:px-10 2xl:px-14">
        <header
          data-tour="surveys-page"
          className="grid items-end gap-5 md:grid-cols-[minmax(0,1fr)_auto]"
        >
          <div>
            <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
              <ClipboardList className="size-3.5" /> Research workspace
            </p>
            <h1 className="mt-2 font-display text-[2.4rem] font-normal leading-none text-foreground">
              Surveys
            </h1>
            <p className="mt-3 max-w-2xl text-[0.875rem] leading-relaxed text-muted-foreground">
              Design, host and analyse research surveys in one traceable workspace. Ask the
              response set follow-up questions without losing the underlying counts.
            </p>
          </div>
          <div className="flex items-center gap-2 md:justify-self-end">
            <Button
              className="rounded-full"
              disabled={create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Plus className="size-4" />
              )}
              New survey
            </Button>
          </div>
        </header>

        <EditorialMetricStrip
          className="mt-8"
          items={[
            {
              value: surveys?.length ?? 0,
              label: german ? "Umfragen" : "surveys",
            },
            {
              value: (surveys ?? []).reduce(
                (sum, survey) => sum + survey.response_count,
                0,
              ),
              label: german ? "Antworten" : "responses",
            },
            {
              value: `${(surveys ?? []).filter((survey) => survey.status === "live").length} ${
                german ? "aktiv" : "live"
              }`,
              label: german ? "werden gerade erhoben" : "currently collecting",
              className: "hidden sm:flex",
            },
          ]}
        />

        {isLoading ? (
          <Loader2 className="mx-auto mt-24 size-5 animate-spin text-muted-foreground" />
        ) : (surveys ?? []).length === 0 ? (
          <EditorialEmptyState
            className="mt-8"
            eyebrow={german ? "Erste Umfrage" : "First survey"}
            title={
              german
                ? "Beginne mit den Fragen, nicht mit der Tabelle"
                : "Start with the questions, not the spreadsheet"
            }
            description={
              german
                ? "Erstelle einen gehosteten Fragebogen, sammle Antworten und analysiere das Ergebnis mit einem beleggebundenen Forschungsassistenten."
                : "Build a hosted questionnaire, collect responses and analyse the result with a grounded research assistant."
            }
          >
            <Button
              type="button"
              className="rounded-full"
              disabled={create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Plus className="size-4" />
              )}
              {german ? "Umfrage erstellen" : "Create survey"}
            </Button>
          </EditorialEmptyState>
        ) : (
          <div className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {(surveys ?? []).map((survey) => (
              <article
                key={survey.public_id}
                className="group rounded-2xl border border-border/75 bg-card/75 p-5 transition-colors hover:border-moss/35 hover:bg-card"
              >
                <Link href={`/surveys/${survey.public_id}`} className="block">
                  <div className="flex items-start justify-between gap-3">
                    <EditorialKicker>{german ? "Umfrage" : "Survey"}</EditorialKicker>
                    <span
                      className={cn(
                        "rounded-full px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em]",
                        STATUS_STYLE[survey.status],
                      )}
                    >
                      {survey.status}
                    </span>
                  </div>
                  <h2 className="mt-5 line-clamp-2 text-[0.9375rem] font-medium text-foreground">
                    {survey.title}
                  </h2>
                  <p className="mt-2 line-clamp-2 min-h-9 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {survey.description || "Add a short introduction for participants."}
                  </p>
                  <div className="mt-4 flex items-center gap-4 border-t border-border/70 pt-3 font-mono text-[0.6875rem] text-muted-foreground">
                    <span>{survey.questions.length} questions</span>
                    <span>{survey.response_count} responses</span>
                  </div>
                  <p className="mt-2 text-[0.65625rem] text-muted-foreground">
                    Updated {formatDate(survey.updated_at)}
                  </p>
                </Link>
                <div className="mt-3 flex items-center justify-between">
                  <Button asChild variant="ghost" size="sm" className="h-7 rounded-full px-2">
                    <Link href={`/surveys/${survey.public_id}`}>
                      Open
                    </Link>
                  </Button>
                  <button
                    type="button"
                    aria-label={`Delete ${survey.title}`}
                    className="cursor-pointer p-1 text-muted-foreground opacity-100 transition-opacity hover:text-destructive sm:opacity-0 sm:group-hover:opacity-100"
                    onClick={() => setConfirmDelete(survey.public_id)}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <ConfirmDeleteDialog
        target={
          confirmDelete === null
            ? null
            : {
                title: "Delete this survey?",
                description:
                  "The survey and all collected responses will be removed. This cannot be undone.",
                action: "Delete survey",
                cancel: "Keep survey",
              }
        }
        pending={remove.isPending}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && remove.mutate(confirmDelete)}
      />
    </div>
  );
}
