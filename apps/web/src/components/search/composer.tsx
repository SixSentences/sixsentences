"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  BadgeCheck,
  BookOpenCheck,
  CalendarRange,
  Check,
  ChevronDown,
  Database,
  Dices,
  FileText,
  FileUp,
  FolderClosed,
  Inbox,
  Globe,
  ListChecks,
  Loader2,
  Paperclip,
  Radar,
  Plus,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Telescope,
  Brain,
  Waypoints,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { dropOverlayClass, usePdfDrop } from "@/hooks/use-pdf-drop";

import ModelPicker from "@/components/search/model-picker";
import { usePrivateModelPreference } from "@/hooks/use-private-model-preference";
import PublicWebSearchApproval from "@/components/search/public-web-search-approval";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjects } from "@/hooks/queries";
import { track } from "@/lib/analytics";
import { api, fileToBase64 } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useActiveProject } from "@/lib/project-context";
import type {
  RunConfig,
  RunCreateRequest,
  UploadedDocument,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { explicitWebResearchRequested } from "@/lib/web-search-consent";

const EXAMPLE_QUESTIONS = [
  "How do LLM ensembles perform at title/abstract screening?",
  "Which methods detect hallucinations in large language models?",
  "Does retrieval-augmented generation reduce factual errors?",
  "How is attention used in vision transformers?",
  "What benchmarks exist for automated systematic reviews?",
  "How well do language models summarize scientific abstracts?",
];

type Options = {
  screen: boolean;
  reviewMethod: NonNullable<RunConfig["review_method"]>;
  live: boolean;
  acquire: boolean;
  fullText: boolean;
  webSearch: boolean;
  snowball: boolean;
  semantic: boolean;
  importBatches: { id: number; label: string; count: number }[];
  gateProtocol: boolean;
  exhaustive: boolean;
  peerReviewedOnly: boolean;
  yearFrom: string;
  yearTo: string;
  query: string;
  paperLimit: string;
  retrievalLimit: string;
  canaryIds: string;
};

// nothing selected by default: a bare question gets a quick, cited answer;
// picking the systematic mode (or any option) runs the full pipeline
const DEFAULTS: Options = {
  screen: false,
  reviewMethod: "prisma",
  live: false,
  acquire: false,
  fullText: false,
  webSearch: false,
  snowball: false,
  semantic: false,
  importBatches: [],
  gateProtocol: false,
  exhaustive: true,
  peerReviewedOnly: false,
  yearFrom: "",
  yearTo: "",
  query: "",
  paperLimit: "",
  retrievalLimit: "",
  canaryIds: "",
};

function WorkflowToggle({
  label,
  description,
  active,
  onToggle,
  icon,
}: {
  label: string;
  description: string;
  active: boolean;
  onToggle: () => void;
  icon: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={cn(
        "group flex h-full min-h-[4.25rem] min-w-0 w-full cursor-pointer items-start gap-2 overflow-hidden rounded-xl border p-2 text-left transition-all duration-200",
        active
          ? "border-moss/45 bg-accent/70 shadow-[inset_0_0_0_1px_rgba(62,99,89,0.18)]"
          : "border-border/80 bg-background/70 hover:border-moss/30 hover:bg-secondary/45",
      )}
    >
      <span
        className={cn(
          "grid size-6 shrink-0 place-items-center rounded-lg transition-colors",
          active
            ? "bg-moss-surface text-ivory"
            : "bg-secondary text-muted-foreground group-hover:text-moss",
        )}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-start gap-1.5 text-[0.75rem] font-semibold text-foreground">
          <span className="min-w-0 break-words">{label}</span>
        </span>
        <span className="mt-0.5 block text-[0.625rem] leading-[1.05rem] text-muted-foreground">
          {description}
        </span>
      </span>
      <span
        className={cn(
          "mt-1 grid size-4 shrink-0 place-items-center rounded-full border transition-colors",
          active
            ? "border-moss bg-moss-surface text-ivory"
            : "border-border bg-card text-transparent",
        )}
      >
        <Check className="size-3" />
      </span>
    </button>
  );
}

/** Rebuild the composer's option state from a finished run's config, so a
 * refinement starts exactly where the parent version left off. */
function optionsFromConfig(config: Partial<RunConfig>): Options {
  return {
    ...DEFAULTS,
    screen: Boolean(config.screen),
    reviewMethod: config.review_method ?? "prisma",
    live: Boolean(config.live),
    acquire: Boolean(config.acquire),
    fullText: Boolean(config.full_text),
    webSearch: Boolean(config.web_search),
    snowball: Boolean(config.snowball),
    semantic: Boolean(config.semantic),
    gateProtocol: Boolean(config.gate_protocol),
    exhaustive: config.exhaustive !== false,
    peerReviewedOnly: Boolean(config.peer_reviewed_only),
    yearFrom: config.year_from ? String(config.year_from) : "",
    yearTo: config.year_to ? String(config.year_to) : "",
    query: config.query ?? "",
    paperLimit:
      config.paper_limit || config.screen_limit
        ? String(config.paper_limit || config.screen_limit)
        : "",
    retrievalLimit: config.retrieval_limit ? String(config.retrieval_limit) : "",
    canaryIds: (config.canary_ids ?? []).join(", "),
    importBatches: (config.import_batch_ids ?? []).map((id) => ({
      id,
      label: `attached export #${id}`,
      count: 0,
    })),
  };
}

type ComposerProps = {
  autoFocus?: boolean;
  /** Externally injected question (example pills); nonce re-triggers. */
  seed?: { text: string; nonce: number } | null;
};

export default function Composer({ autoFocus = true, seed = null }: ComposerProps) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const router = useRouter();
  const queryClient = useQueryClient();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");
  const [webSearchPublicDataConfirmed, setWebSearchPublicDataConfirmed] =
    useState(false);

  useEffect(() => {
    if (seed) {
      setQuestion(seed.text);
      setWebSearchPublicDataConfirmed(false);
      textareaRef.current?.focus();
    }
  }, [seed]);
  const [options, setOptions] = useState<Options>(DEFAULTS);
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false);
  const [reviewDialogTourMode, setReviewDialogTourMode] = useState(false);

  // The product tour opens the real review setup instead of describing an
  // invisible dialog. Keeping this as an event avoids coupling the composer
  // to the tour component and also works after client-side navigation.
  useEffect(() => {
    const setTourDialog = (event: Event) => {
      const detail = (event as CustomEvent<{ open?: boolean }>).detail;
      const open = detail?.open !== false;
      setReviewDialogTourMode(open);
      setReviewDialogOpen(open);
    };
    window.addEventListener("six:tour-review-settings", setTourDialog);
    return () =>
      window.removeEventListener("six:tour-review-settings", setTourDialog);
  }, []);

  // refining an existing run: the run page stashes question + config in
  // sessionStorage; the new run records parent_run_id for the version diff
  const [refineOf, setRefineOf] = useState<{
    publicId: string;
    label: string;
  } | null>(null);
  useEffect(() => {
    const consume = () => {
      const raw = sessionStorage.getItem("six:refine");
      if (!raw) return;
      sessionStorage.removeItem("six:refine");
      try {
        const stash = JSON.parse(raw) as {
          public_id: string;
          label: string;
          question: string;
          config: Partial<RunConfig>;
        };
        setRefineOf({ publicId: stash.public_id, label: stash.label });
        setQuestion(stash.question);
        // Consent is purpose- and run-bound. A prior run's confirmation is
        // never inherited by a refinement, even when Web sources is restored.
        setWebSearchPublicDataConfirmed(false);
        setOptions(optionsFromConfig(stash.config));
        textareaRef.current?.focus();
      } catch {
        // a malformed stash just starts a plain search
      }
    };
    consume(); // navigation into a fresh mount
    window.addEventListener("six:refine-search", consume); // already-mounted home
    return () => window.removeEventListener("six:refine-search", consume);
  }, []);
  // PDFs attached before the run exists; they ride along via document_ids
  const [pendingDocs, setPendingDocs] = useState<UploadedDocument[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  // Workspace questions and attachments use the private-content catalog.
  const [model, setModel] = usePrivateModelPreference();
  // explicit save-target; falls back to the sidebar's active project.
  // "none" = explicitly unfiled — a chat does not need a project.
  const [projectChoice, setProjectChoice] = useState<number | "none" | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const { activeProjectId } = useActiveProject();
  const { data: projects } = useProjects();
  const { data: screeningMethods } = useQuery({
    queryKey: ["screening-methods"],
    queryFn: api.screeningMethods,
    staleTime: 60 * 60 * 1000,
  });

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  const webSearchAvailable = true;
  const effectiveWebSearch = webSearchAvailable && options.webSearch;

  // no options, no filters: the agent answers directly instead of running
  // the systematic pipeline
  const activeFilterCount = [
    Boolean(options.yearFrom || options.yearTo),
    options.peerReviewedOnly,
    !options.exhaustive,
    Boolean(options.query.trim()),
    Boolean(options.paperLimit),
    Boolean(options.retrievalLimit),
    Boolean(options.canaryIds.trim()),
    options.importBatches.length > 0,
  ].filter(Boolean).length;
  const filtersActive = activeFilterCount > 0;
  const activeWorkflowCount = [
    options.screen,
    options.live,
    options.snowball,
    options.semantic,
    options.acquire,
    options.fullText,
    effectiveWebSearch,
    options.gateProtocol,
  ].filter(Boolean).length;
  const activeReviewCount = activeWorkflowCount + activeFilterCount;
  const askMode =
    !options.screen &&
    !options.live &&
    !options.acquire &&
    !options.fullText &&
    !effectiveWebSearch &&
    !options.snowball &&
    !options.semantic &&
    !options.gateProtocol &&
    !filtersActive;
  const quickAnswerWebSearch =
    askMode && webSearchAvailable && explicitWebResearchRequested(question);
  const webSearchConfirmationRequired =
    effectiveWebSearch || quickAnswerWebSearch;
  const uploadDoc = useMutation({
    mutationFn: async (input: { file?: File; url?: string }) => {
      if (input.file) {
        const content_base64 = await fileToBase64(input.file);
        return api.uploadDocument({ filename: input.file.name, content_base64 });
      }
      return api.uploadDocument({ url: input.url });
    },
    onSuccess: (doc) => {
      setPendingDocs((docs) =>
        docs.some((d) => d.id === doc.id) ? docs : [...docs, doc],
      );
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "The upload failed."),
  });

  // dropping a PDF on the composer attaches it — no paperclip click needed
  const { dragging, dropProps } = usePdfDrop((files) => {
    for (const file of files) uploadDoc.mutate({ file });
  });

  // RIS/BibTeX exports from other databases join identification as batches
  const importFileRef = useRef<HTMLInputElement>(null);
  const uploadImport = useMutation({
    mutationFn: async (file: File) => api.createImport(file.name, "", await file.text()),
    onSuccess: (batch) => {
      setOptions((o) => ({ ...o, importBatches: [...o.importBatches, batch] }));
      toast.success(`${batch.count} records attached (${batch.label}).`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "The import failed."),
  });

  const { data: referenceConnectors } = useQuery({
    queryKey: ["reference-connectors"],
    queryFn: api.referenceConnectors,
  });
  const attachConnector = useMutation({
    mutationFn: async (id: string) => {
      const current = referenceConnectors?.find((connector) => connector.id === id);
      return current?.provider === "zotero"
        ? api.syncReferenceConnector(id)
        : current;
    },
    onSuccess: (connector) => {
      if (!connector?.import_batch_id || connector.item_count === 0) {
        toast.error("This connected library does not contain any references yet.");
        return;
      }
      setOptions((current) => ({
        ...current,
        importBatches: current.importBatches.some(
          (batch) => batch.id === connector.import_batch_id,
        )
          ? current.importBatches
          : [
              ...current.importBatches,
              {
                id: connector.import_batch_id as number,
                label: `${connector.name} (${connector.provider})`,
                count: connector.item_count,
              },
            ],
      }));
      toast.success(`${connector.item_count} references attached from ${connector.name}.`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Library sync failed."),
  });

  const createProject = useMutation({
    mutationFn: (name: string) => api.createProject(name),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      setProjectChoice(project.id);
      setCreatingProject(false);
      setNewProjectName("");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not create the project."),
  });

  const createRun = useMutation({
    mutationFn: async () => {
      // file the run into the picked project (or the sidebar's active one);
      // with no pick it stays unfiled — a chat does not need a project
      const targetProject =
        projectChoice === "none" ? null : (projectChoice ?? activeProjectId ?? null);
      const documentIds = pendingDocs.map((doc) => doc.id);
      if (askMode) {
        const created = await api.createRun(targetProject, {
          question: question.trim(),
          mode: "ask",
          ...(quickAnswerWebSearch && {
            web_search: true,
            web_search_public_data_confirmed: true,
          }),
          ...(model !== "auto" && { model }),
          ...(documentIds.length > 0 && { document_ids: documentIds }),
        });
        return { created, projectId: targetProject, ask: true, text: question.trim() };
      }
      const body: RunCreateRequest = {
        question: question.trim(),
        ...(model !== "auto" && { model }),
        ...(documentIds.length > 0 && { document_ids: documentIds }),
        screen: options.screen,
        review_method: options.reviewMethod,
        live: options.live,
        acquire: options.acquire || options.fullText,
        full_text: options.fullText,
        web_search: effectiveWebSearch,
        ...(effectiveWebSearch && {
          web_search_public_data_confirmed: true,
        }),
        snowball: options.snowball,
        semantic: options.semantic,
        gate_protocol: options.gateProtocol,
        exhaustive: options.exhaustive,
        peer_reviewed_only: options.peerReviewedOnly,
      };
      if (options.importBatches.length > 0) {
        body.import_batch_ids = options.importBatches.map((batch) => batch.id);
      }
      if (options.query.trim()) body.query = options.query.trim();
      if (options.yearFrom) body.year_from = Number(options.yearFrom);
      if (options.yearTo) body.year_to = Number(options.yearTo);
      if (options.paperLimit) body.paper_limit = Number(options.paperLimit);
      if (options.retrievalLimit) body.retrieval_limit = Number(options.retrievalLimit);
      if (options.canaryIds.trim()) {
        body.canary_ids = options.canaryIds
          .split(/[\s,]+/)
          .map((id) => id.trim())
          .filter(Boolean);
      }
      if (refineOf) body.parent_run_id = refineOf.publicId;
      const created = await api.createRun(targetProject, body);
      return { created, projectId: targetProject, ask: false, text: question.trim() };
    },
    onSuccess: ({ created, projectId, ask, text }) => {
      track("run_started", {
        mode: ask ? "ask" : "search",
        filed: projectId != null,
        refine: refineOf != null,
        documents: pendingDocs.length,
        live: options.live,
        full_text: options.fullText,
      });
      // seed the run detail so the question shows the moment we navigate,
      // instead of a skeleton while the first fetch is in flight
      queryClient.setQueryData(["run", created.public_id], {
        id: created.id,
        public_id: created.public_id,
        project_id: projectId,
        status: created.status,
        question: text,
        title: null,
        config: ask ? { mode: "ask" } : { mode: "search" },
        prisma: null,
        error: null,
        created_at: new Date().toISOString(),
        finished_at: null,
        last_event: null,
      });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      setPendingDocs([]);
      setRefineOf(null);
      router.push(`/r/${created.public_id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not start the search."),
  });

  const webSearchConfirmationMissing =
    webSearchConfirmationRequired && !webSearchPublicDataConfirmed;
  const canSubmit =
    question.trim().length > 2 &&
    !webSearchConfirmationMissing &&
    !createRun.isPending;

  function submit() {
    if (!canSubmit) return;
    createRun.mutate();
    // Consent is bound to the submitted request. Editing or sending another
    // question always requires a fresh affirmative action.
    setWebSearchPublicDataConfirmed(false);
  }

  function toggle(key: keyof Options) {
    if (key === "webSearch" && options.webSearch) {
      setWebSearchPublicDataConfirmed(false);
    }
    setOptions((o) => {
      const next = { ...o, [key]: !o[key] };
      // full-text screening implies acquisition
      if (key === "fullText" && next.fullText) next.acquire = true;
      if (key === "acquire" && !next.acquire) next.fullText = false;
      // snowballing seeds from the includes, so it needs screening
      if (key === "snowball" && next.snowball) next.screen = true;
      // Live discovery is independent: only its own toggle changes this choice.
      return next;
    });
  }

  function resetSystematicReview() {
    setOptions(DEFAULTS);
    setWebSearchPublicDataConfirmed(false);
  }

  const resolvedProjectId =
    projectChoice === "none" ? null : (projectChoice ?? activeProjectId ?? null);
  const targetProject =
    projects?.find((project) => project.id === resolvedProjectId) ?? null;

  return (
    <div className="w-full" data-tour="home-composer">
      {/* the mode lives in the frame: color + a small tag, no switch */}
      <div
        className={cn(
          "mb-1.5 flex items-center pr-4",
          refineOf ? "justify-between pl-4" : "justify-end",
        )}
      >
        {refineOf && (
          <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-moss/40 bg-accent/60 py-1 pl-2.5 pr-1.5 text-[0.71875rem] text-foreground">
            <Waypoints className="size-3 shrink-0 text-moss" />
            <span className="truncate">
              Refines: {refineOf.label.slice(0, 60)}
            </span>
            <button
              type="button"
              onClick={() => setRefineOf(null)}
              aria-label="Detach from the previous search"
              className="grid size-4.5 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          </span>
        )}
        <span
          className={cn(
            "inline-flex items-center gap-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.18em] transition-colors duration-300",
            askMode ? "text-moss" : "text-foreground",
          )}
        >
          {askMode ? <Zap className="size-3.5" /> : <Telescope className="size-3.5" />}
          {askMode
            ? isGerman
              ? "Direkte Antwort"
              : "Quick answer"
            : isGerman
              ? "Systematische Suche"
              : "Systematic search"}
        </span>
      </div>
      <div
        {...dropProps}
        className={cn(
          "relative rounded-[2rem] border bg-card transition-all duration-300",
          askMode
            ? "border-moss-soft/60 shadow-[0_2px_16px_rgba(90,137,125,0.14)] focus-within:border-moss focus-within:shadow-[0_2px_24px_rgba(51,84,76,0.16)]"
            : "border-ring/55 shadow-[0_2px_16px_rgba(51,84,76,0.12)] focus-within:border-ring focus-within:shadow-[0_2px_24px_rgba(90,137,125,0.16)]",
        )}
      >
        {dragging && (
          <div className={dropOverlayClass()}>
            <div className="flex items-center gap-2.5 text-moss">
              <FileUp className="size-5" />
              <p className="text-[0.875rem] font-medium">
                Drop your PDF here to ask about it
              </p>
            </div>
          </div>
        )}
        <Dialog
          modal={!reviewDialogTourMode}
          open={reviewDialogOpen}
          onOpenChange={(open) => {
            setReviewDialogOpen(open);
            if (!open) setReviewDialogTourMode(false);
          }}
        >
          <div
            data-tour="home-question-input"
            className="flex min-w-0 items-end gap-1 px-2.5 pt-2.5 sm:gap-1.5 sm:px-3.5 sm:pt-3.5"
          >
          {/* attach a PDF: it becomes a first-class source of the answer */}
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,.pdf,image/png,image/jpeg,image/webp,image/gif"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) uploadDoc.mutate({ file });
              event.target.value = "";
            }}
          />
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="mb-1 size-10 shrink-0 rounded-full text-muted-foreground hover:text-foreground sm:size-11"
                disabled={uploadDoc.isPending}
                aria-label={isGerman ? "PDF oder Foto anhängen" : "Attach a PDF or a photo"}
                onClick={() => fileRef.current?.click()}
              >
                {uploadDoc.isPending ? (
                  <Loader2 className="size-5 animate-spin" />
                ) : (
                  <Paperclip className="size-5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">
              {isGerman
                ? "PDF oder Foto einer Quelle anhängen. Reinziehen geht auch; Links einfach in die Frage schreiben."
                : "Attach a PDF or a photo of a source. Dropping works too; links can go straight into the question."}
            </TooltipContent>
          </Tooltip>

          <Textarea
            ref={textareaRef}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              setWebSearchPublicDataConfirmed(false);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={isGerman ? "Stelle eine Forschungsfrage…" : "Ask a research question…"}
            aria-label={isGerman ? "Forschungsfrage" : "Research question"}
            rows={2}
            className="max-h-52 min-h-[3.75rem] min-w-0 flex-1 resize-none border-0 bg-transparent px-2 py-2.5 text-[1rem] leading-relaxed shadow-none focus-visible:ring-0 dark:bg-transparent sm:min-h-[4.25rem] sm:px-3 sm:pt-3 sm:text-[1.125rem]"
          />

          {/* submitting the prompt is the suffix of the input line */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="icon"
                onClick={submit}
                disabled={!canSubmit}
                className="mb-1 size-10 shrink-0 rounded-full transition-transform active:scale-95 sm:size-11"
                aria-label={
                  askMode
                    ? isGerman
                      ? "Frage stellen"
                      : "Ask the question"
                    : isGerman
                      ? "Suche starten"
                      : "Start the search"
                }
              >
                {createRun.isPending ? (
                  <Loader2 className="size-5 animate-spin" />
                ) : (
                  <ArrowUp className="size-5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">
              {askMode
                ? isGerman
                  ? "Fragen · Enter"
                  : "Ask · Enter"
                : isGerman
                  ? "Suche starten · Enter"
                  : "Start the search · Enter"}
            </TooltipContent>
          </Tooltip>
        </div>

        {/* attached PDFs ride along with the question */}
        {pendingDocs.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 px-5 pt-2">
            {pendingDocs.map((doc) => (
              <span
                key={doc.id}
                className="inline-flex max-w-[20rem] items-center gap-2 rounded-full border border-moss/25 bg-accent/60 py-1.5 pl-3 pr-1.5 text-[0.78125rem] font-medium text-foreground/85"
              >
                <FileText className="size-3 shrink-0 text-moss" />
                <span className="truncate">{doc.title}</span>
                {doc.verified && (
                  <BadgeCheck className="size-3 shrink-0 text-moss" aria-label="Metadata verified" />
                )}
                <button
                  type="button"
                  onClick={() =>
                    setPendingDocs((docs) => docs.filter((d) => d.id !== doc.id))
                  }
                  className="grid size-4.5 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-moss-surface hover:text-ivory"
                  aria-label={`Remove ${doc.title}`}
                >
                  <X className="size-2.5" />
                </button>
              </span>
            ))}
          </div>
        )}

        {quickAnswerWebSearch && (
          <div
            data-testid="quick-answer-web-search-confirmation"
            className="mx-4 mt-2"
          >
            <PublicWebSearchApproval
              language={isGerman ? "de" : "en"}
              query={question}
              confirmed={webSearchPublicDataConfirmed}
              onConfirmedChange={setWebSearchPublicDataConfirmed}
            />
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 px-3 pb-3 pt-2 sm:px-4 sm:pb-4">
          {/* All review features belong to one workflow, not eight competing modes. */}
          <DialogTrigger asChild>
            <button
              data-tour="review-setup-trigger"
              type="button"
              className={cn(
                "inline-flex h-10 min-w-0 cursor-pointer items-center gap-2 rounded-full border px-3 text-[0.8125rem] font-medium transition-all duration-200 sm:gap-2.5 sm:px-4 sm:text-[0.875rem]",
                askMode
                  ? "border-border bg-background text-muted-foreground hover:border-moss/35 hover:text-foreground"
                  : "border-moss/45 bg-accent text-foreground shadow-[inset_0_0_0_1px_rgba(62,99,89,0.16)]",
              )}
              aria-label={isGerman ? "Systematische Recherche konfigurieren" : "Configure the systematic review"}
            >
              <Telescope className="size-4 text-moss" />
              <span className="truncate">
                {isGerman ? "Systematische Recherche" : "Systematic review"}
              </span>
              {activeReviewCount > 0 && (
                <span className="rounded-full bg-moss-surface px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-ivory">
                  {activeReviewCount} {isGerman ? "aktiv" : "active"}
                </span>
              )}
              <SlidersHorizontal className="size-3.5 text-muted-foreground/60" />
            </button>
          </DialogTrigger>
          {!askMode && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={resetSystematicReview}
                  className="size-10 rounded-full text-muted-foreground hover:text-foreground"
                  aria-label={isGerman ? "Zurück zur direkten Antwort" : "Return to quick answer"}
                >
                  <X className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">
                {isGerman ? "Zurück zur direkten Antwort" : "Return to quick answer"}
              </TooltipContent>
            </Tooltip>
          )}

          {/* One setup for workflow, scope, retrieval and known works. */}
          <DialogContent
            data-tour="review-setup-dialog"
            className="max-h-[calc(100dvh-1rem)] w-full max-w-[calc(100vw-1rem)] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-2xl bg-card p-0 sm:max-w-[calc(100vw-1rem)] xl:max-w-[76rem]"
          >
            <DialogHeader className="shrink-0 border-b border-border/80 px-3 py-3 pr-12 text-left sm:px-5 sm:pr-14">
              <div className="flex min-w-0 items-start gap-2 sm:gap-4">
                <div className="min-w-0">
                  <DialogTitle className="font-serif text-2xl leading-tight text-foreground">
                    {isGerman ? "Systematische Recherche einrichten" : "Systematic review setup"}
                  </DialogTitle>
                  <DialogDescription className="mt-0.5 text-[0.75rem]">
                    {isGerman
                      ? "Wähle nur, was diese Recherche braucht. Ohne Optionen erhältst du wieder eine direkte Antwort."
                      : "Choose only what this review needs. Clear every option to return to Quick Answer."}
                  </DialogDescription>
                </div>
                <div className="ml-auto flex shrink-0 items-center gap-2">
                  {activeReviewCount > 0 && (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={resetSystematicReview}
                      className="h-8 rounded-full px-2.5 text-[0.71875rem]"
                    >
                      <RotateCcw className="size-3.5" />
                      {isGerman ? "Zurücksetzen" : "Reset"}
                    </Button>
                  )}
                </div>
              </div>
            </DialogHeader>

            <div className="grid min-h-0 min-w-0 items-stretch gap-3 overflow-y-auto overflow-x-hidden bg-secondary/20 p-2.5 sm:p-4 lg:grid-cols-6">
              <div className="contents">
                <div className="contents">
                  <section
                    data-tour="review-discovery"
                    className="h-full min-w-0 rounded-2xl border border-border/80 bg-card p-3 lg:col-span-3 lg:col-start-1 lg:row-start-1"
                  >
                    <div className="mb-2 flex items-center gap-2 px-1">
                      <span className="grid size-7 place-items-center rounded-lg bg-accent text-moss">
                        <Radar className="size-3.5" />
                      </span>
                      <div>
                        <p className="text-[0.78125rem] font-semibold">
                          {isGerman ? "Entdeckung" : "Discovery"}
                        </p>
                        <p className="text-[0.65625rem] text-muted-foreground">
                          {isGerman ? "Weitere Evidenzquellen erschließen" : "Expand where evidence comes from"}
                        </p>
                      </div>
                    </div>
                    <div className="grid auto-rows-fr gap-2 sm:grid-cols-2">
                      <WorkflowToggle
                        label={isGerman ? "Live-Index" : "Live index"}
                        description={isGerman ? "Bezieht aktuelle Paper aus zusätzlichen Quellen ein." : "Includes current papers from additional sources."}
                        active={options.live}
                        onToggle={() => toggle("live")}
                        icon={<Radar className="size-3.5" />}
                      />
                      <WorkflowToggle
                        label={isGerman ? "Semantische Suche" : "Semantic sweep"}
                        description={isGerman ? "Bedeutungsähnliche Treffer und Synonyme." : "Meaning-level matches and synonyms."}
                        active={options.semantic}
                        onToggle={() => toggle("semantic")}
                        icon={<Brain className="size-3.5" />}
                      />
                      <WorkflowToggle
                        label="Snowballing"
                        description={isGerman ? "Zitierte und zitierende Paper verfolgen." : "Follow cited and citing papers."}
                        active={options.snowball}
                        onToggle={() => toggle("snowball")}
                        icon={<Waypoints className="size-3.5" />}
                      />
                      {webSearchAvailable && (
                        <WorkflowToggle
                          label={isGerman ? "Webquellen" : "Web sources"}
                          description={isGerman ? "Berichte, Standards und graue Literatur ergänzen." : "Add reports, standards and grey literature."}
                          active={options.webSearch}
                          onToggle={() => toggle("webSearch")}
                          icon={<Globe className="size-3.5" />}
                        />
                      )}
                    </div>
                    {effectiveWebSearch && (
                      <div
                        data-testid="web-search-public-data-confirmation"
                        className="mt-2"
                      >
                        <PublicWebSearchApproval
                          language={isGerman ? "de" : "en"}
                          query={question}
                          confirmed={webSearchPublicDataConfirmed}
                          onConfirmedChange={setWebSearchPublicDataConfirmed}
                        />
                      </div>
                    )}
                  </section>

                  <section
                    data-tour="review-screening"
                    className="h-full min-w-0 rounded-2xl border border-border/80 bg-card p-3 lg:col-span-3 lg:col-start-4 lg:row-start-1"
                  >
                    <div className="mb-2 flex items-center gap-2 px-1">
                      <span className="grid size-7 place-items-center rounded-lg bg-accent text-moss">
                        <ListChecks className="size-3.5" />
                      </span>
                      <div className="min-w-0">
                        <p className="text-[0.78125rem] font-semibold">
                          {isGerman ? "Screening & Kontrolle" : "Screening & control"}
                        </p>
                        <p className="text-[0.65625rem] text-muted-foreground">
                          {isGerman ? "Festlegen, wie tief Quellen geprüft werden" : "Decide how deeply records are checked"}
                        </p>
                      </div>
                    </div>
                    <div className="grid auto-rows-fr gap-2 sm:grid-cols-2">
                      <WorkflowToggle
                        label={isGerman ? "Titel- & Abstract-Screening" : "Title & abstract screen"}
                        description={isGerman ? "Eignungsprüfung mit Zitaten und Review-Signalen." : "Eligibility pass with quotes and reviewer signals."}
                        active={options.screen}
                        onToggle={() => toggle("screen")}
                        icon={<ListChecks className="size-3.5" />}
                      />
                      <WorkflowToggle
                        label={isGerman ? "Volltexte beschaffen" : "Acquire full texts"}
                        description={isGerman ? "Legale Open-Access-PDFs abrufen." : "Retrieve legal open-access PDFs."}
                        active={options.acquire}
                        onToggle={() => toggle("acquire")}
                        icon={<FileText className="size-3.5" />}
                      />
                      <WorkflowToggle
                        label={isGerman ? "Volltext-Screening" : "Deep screen"}
                        description={isGerman ? "Zweite Eignungsprüfung im Volltext." : "Second eligibility pass on full text."}
                        active={options.fullText}
                        onToggle={() => toggle("fullText")}
                        icon={<BookOpenCheck className="size-3.5" />}
                      />
                    <WorkflowToggle
                      label={isGerman ? "Protokollfreigabe" : "Protocol gate"}
                      description={isGerman ? "Vor der Suche zur Protokollfreigabe pausieren." : "Pause for protocol approval before retrieval."}
                      active={options.gateProtocol}
                      onToggle={() => toggle("gateProtocol")}
                      icon={<ShieldCheck className="size-3.5" />}
                    />
                    </div>
                    <div className="mt-2 rounded-xl border border-border/80 bg-secondary/30 p-2.5">
                      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                        <div className="min-w-0">
                          <p className="text-[0.71875rem] font-semibold text-foreground">
                            {isGerman ? "Review-Methodik" : "Review framework"}
                          </p>
                          <p className="truncate text-[0.625rem] text-muted-foreground">
                            {screeningMethods?.find((method) => method.id === options.reviewMethod)?.best_for ?? "Reporting and screening guidance"}
                          </p>
                        </div>
                        <Select
                          value={options.reviewMethod}
                          onValueChange={(reviewMethod) =>
                            setOptions((current) => ({
                              ...current,
                              reviewMethod: reviewMethod as Options["reviewMethod"],
                              screen: true,
                            }))
                          }
                        >
                          <SelectTrigger className="h-9 w-full min-w-0 shrink-0 rounded-full bg-background text-[0.71875rem] sm:h-8 sm:w-[9.5rem]">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {(screeningMethods ?? []).map((method) => (
                              <SelectItem key={method.id} value={method.id}>
                                {method.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  </section>
                </div>
              </div>

              <section className="overflow-hidden rounded-2xl border border-border/80 bg-card lg:col-span-6 lg:row-start-2">
                <div className="grid lg:grid-cols-3 lg:divide-x lg:divide-border/80">
                  <div data-tour="review-scope" className="relative p-4">
                    <div className="mb-3 flex items-center gap-2">
                      <span className="grid size-7 place-items-center rounded-lg bg-accent text-moss">
                        <CalendarRange className="size-3.5" />
                      </span>
                      <div>
                        <p className="text-[0.8125rem] font-semibold text-foreground">Scope</p>
                        <p className="text-[0.6875rem] text-muted-foreground">Time and publication type</p>
                      </div>
                    </div>

                    <Label className="text-[0.65625rem] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      Publication window
                    </Label>
                    <div className="mt-1.5 grid grid-cols-2 gap-2">
                      <Input
                        inputMode="numeric"
                        placeholder="From · 2015"
                        value={options.yearFrom}
                        onChange={(e) =>
                          setOptions((o) => ({
                            ...o,
                            yearFrom: e.target.value.replace(/\D/g, ""),
                          }))
                        }
                        className="h-9 rounded-xl bg-background/70"
                      />
                      <Input
                        inputMode="numeric"
                        placeholder="To · 2026"
                        value={options.yearTo}
                        onChange={(e) =>
                          setOptions((o) => ({
                            ...o,
                            yearTo: e.target.value.replace(/\D/g, ""),
                          }))
                        }
                        className="h-9 rounded-xl bg-background/70"
                      />
                    </div>

                    <div className="mt-3 grid gap-2">
                      <div className="flex items-center justify-between gap-3 rounded-xl bg-secondary/45 px-3 py-2">
                        <div className="min-w-0">
                          <p className="text-[0.75rem] font-medium">Peer-reviewed only</p>
                          <p className="text-[0.625rem] text-muted-foreground">Exclude preprints</p>
                        </div>
                        <Switch
                          checked={options.peerReviewedOnly}
                          onCheckedChange={(checked) =>
                            setOptions((o) => ({ ...o, peerReviewedOnly: checked }))
                          }
                        />
                      </div>
                      <div className="flex items-center justify-between gap-3 rounded-xl bg-secondary/45 px-3 py-2">
                        <div className="min-w-0">
                          <p className="text-[0.75rem] font-medium">{isGerman ? "Suchanfragen erweitern" : "Expand search queries"}</p>
                          <p className="text-[0.625rem] text-muted-foreground">{isGerman ? "Weitere Suchvarianten und einen breiteren Nachlauf verwenden. Aus: keine Erweiterung, aber weiterhin vollständiges Screening der gefundenen Kandidaten." : "Use additional query variants and a broader follow-up pass. Off: no expansion, but retrieved candidates are still screened."}</p>
                        </div>
                        <Switch
                          checked={options.exhaustive}
                          onCheckedChange={(checked) =>
                            setOptions((o) => ({ ...o, exhaustive: checked }))
                          }
                        />
                      </div>
                    </div>
                  </div>

                  <div
                    data-tour="review-retrieval"
                    className="relative border-t border-border/80 p-4 lg:border-t-0"
                  >
                    <div className="mb-3 flex items-center gap-2">
                      <span className="grid size-7 place-items-center rounded-lg bg-accent text-moss">
                        <Telescope className="size-3.5" />
                      </span>
                      <div>
                        <p className="text-[0.8125rem] font-semibold text-foreground">Retrieval</p>
                        <p className="text-[0.6875rem] text-muted-foreground">Query and workload bounds</p>
                      </div>
                    </div>

                    <Label className="text-[0.65625rem] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      Boolean query
                      <span className="ml-1 normal-case tracking-normal">· optional</span>
                    </Label>
                    <Textarea
                      value={options.query}
                      onChange={(e) => setOptions((o) => ({ ...o, query: e.target.value }))}
                      placeholder={'title:"large language model*" AND screening'}
                      rows={2}
                      className="mt-1.5 min-h-14 resize-none rounded-xl bg-background/70 font-mono text-[0.75rem]"
                    />
                    <p className="mt-1 text-[0.65625rem] text-muted-foreground">
                      A manual query skips automatic synthesis.
                    </p>

                    <div className="mt-3">
                      <Label className="text-[0.65625rem] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                        {isGerman ? "Maximaler Ergebnisbestand" : "Maximum result set"}
                        <span className="ml-1 normal-case tracking-normal">
                          · {isGerman ? "nach Relevanz" : "ranked by relevance"}
                        </span>
                      </Label>
                      <div className="mt-1.5 grid grid-cols-5 gap-1.5">
                        {[
                          { value: "", label: isGerman ? "Alle" : "All" },
                          { value: "50", label: "50" },
                          { value: "100", label: "100" },
                          { value: "300", label: "300" },
                        ].map((preset) => (
                          <button
                            key={preset.label}
                            type="button"
                            onClick={() =>
                              setOptions((current) => ({
                                ...current,
                                paperLimit: preset.value,
                              }))
                            }
                            className={cn(
                              "h-8 rounded-xl border text-[0.71875rem] transition-colors",
                              options.paperLimit === preset.value
                                ? "border-moss/45 bg-accent font-medium text-foreground"
                                : "border-border bg-background/70 text-muted-foreground hover:border-moss/30 hover:text-foreground",
                            )}
                          >
                            {preset.label}
                          </button>
                        ))}
                        <Input
                          inputMode="numeric"
                          aria-label={isGerman ? "Eigene maximale Paperzahl" : "Custom paper limit"}
                          placeholder="#"
                          value={
                            ["", "50", "100", "300"].includes(options.paperLimit)
                              ? ""
                              : options.paperLimit
                          }
                          onChange={(event) =>
                            setOptions((current) => ({
                              ...current,
                              paperLimit: event.target.value.replace(/\D/g, ""),
                            }))
                          }
                          className="h-8 rounded-xl bg-background/70 px-2 text-center text-[0.71875rem]"
                        />
                      </div>
                      <p className="mt-2 text-[0.65625rem] leading-relaxed text-muted-foreground">
                        {isGerman ? "Diese Grenze gilt erst nach Screening und Auswahl. Die Suche kann wesentlich mehr Treffer prüfen; sie ist kein Zeit- oder Kostenlimit." : "This limit applies after screening and selection. Search may inspect many more records; it is not a time or cost limit."}
                      </p>
                    </div>
                  </div>

                  <div
                    data-tour="review-known-sources"
                    className="relative border-t border-border/80 p-4 lg:border-t-0"
                  >
                    <div className="mb-3 flex items-center gap-2">
                      <span className="grid size-7 place-items-center rounded-lg bg-accent text-moss">
                        <Database className="size-3.5" />
                      </span>
                      <div>
                        <p className="text-[0.8125rem] font-semibold text-foreground">Known sources</p>
                        <p className="text-[0.6875rem] text-muted-foreground">Must-hits and external records</p>
                      </div>
                    </div>

                    <Label className="text-[0.65625rem] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      OpenAlex IDs
                    </Label>
                    <Input
                      value={options.canaryIds}
                      onChange={(e) => setOptions((o) => ({ ...o, canaryIds: e.target.value }))}
                      placeholder="W2741809807, W1234…"
                      className="mt-1.5 h-9 rounded-xl bg-background/70 font-mono text-[0.75rem]"
                    />

                    <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-[minmax(0,1fr)_8rem]">
                      <div className="rounded-xl border border-border/80 p-2">
                        <p className="text-[0.71875rem] font-medium">Connected libraries</p>
                        <p className="mt-0.5 text-[0.625rem] leading-snug text-muted-foreground">
                          Zotero and Citavi references join retrieval as a documented source.
                        </p>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={attachConnector.isPending || !(referenceConnectors ?? []).length}
                              className="mt-2 h-7.5 w-full rounded-full text-[0.71875rem]"
                            >
                              {attachConnector.isPending ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <><Database className="size-3" /> Add library</>
                              )}
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start" className="w-64">
                            {(referenceConnectors ?? []).map((connector) => (
                              <DropdownMenuItem
                                key={connector.id}
                                onSelect={() => attachConnector.mutate(connector.id)}
                              >
                                <Database className="size-3.5" />
                                <span className="min-w-0 flex-1 truncate">{connector.name}</span>
                                <span className="font-mono text-[0.59375rem] uppercase text-muted-foreground">
                                  {connector.provider} · {connector.item_count}
                                </span>
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>

                      <div className="flex flex-col justify-between rounded-xl bg-secondary/45 p-2">
                        <div>
                          <p className="text-[0.71875rem] font-medium">Database export</p>
                          <p className="mt-0.5 text-[0.625rem] leading-snug text-muted-foreground">
                            RIS, BibTeX or ENW
                          </p>
                        </div>
                        <input
                          ref={importFileRef}
                          type="file"
                          accept=".ris,.bib,.bibtex,.enw,.txt"
                          className="hidden"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) uploadImport.mutate(file);
                            e.target.value = "";
                          }}
                        />
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={uploadImport.isPending}
                          onClick={() => importFileRef.current?.click()}
                          className="mt-2 h-7.5 w-full rounded-full px-2 text-[0.6875rem]"
                        >
                          {uploadImport.isPending ? (
                            <Loader2 className="size-3 animate-spin" />
                          ) : (
                            <>
                              <FileUp className="size-3" />
                              Attach
                            </>
                          )}
                        </Button>
                      </div>
                    </div>

                    {options.importBatches.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {options.importBatches.map((batch) => (
                          <span
                            key={batch.id}
                            className="inline-flex items-center gap-1 rounded-full bg-accent py-1 pl-2.5 pr-1.5 font-mono text-[0.65625rem] text-moss"
                          >
                            <span className="max-w-36 truncate">{batch.label}</span> · {batch.count}
                            <button
                              type="button"
                              className="grid size-4 place-items-center rounded-full hover:bg-moss-surface hover:text-ivory"
                              aria-label={`Remove ${batch.label}`}
                              onClick={() =>
                                setOptions((o) => ({
                                  ...o,
                                  importBatches: o.importBatches.filter(
                                    (b) => b.id !== batch.id,
                                  ),
                                }))
                              }
                            >
                              <X className="size-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </section>
            </div>
          </DialogContent>

          {/* Model + save target + generate (bottom right) */}
          <div
            data-tour="home-context-controls"
            className="ml-auto flex min-w-0 items-center gap-1.5 max-sm:w-full"
          >
            <ModelPicker value={model} onChange={setModel} large />
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className="group/proj inline-flex h-10 min-w-0 cursor-pointer items-center gap-2 rounded-full px-3 text-[0.875rem] font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground data-[state=open]:bg-accent data-[state=open]:text-moss max-sm:px-1.5"
                      aria-label={
                        isGerman
                          ? "Projekt auswählen, in dem diese Suche gespeichert wird"
                          : "Choose the project this search is saved to"
                      }
                    >
                      <span className="grid size-6 place-items-center rounded-md bg-accent text-moss transition-colors group-hover/proj:bg-moss-surface group-hover/proj:text-ivory">
                        <FolderClosed className="size-3.5" />
                      </span>
                      <span className="max-w-[10rem] truncate max-sm:hidden">
                        {targetProject?.name ?? (isGerman ? "Kein Projekt" : "No project")}
                      </span>
                      <ChevronDown className="size-3.5 text-muted-foreground/50 transition-transform group-data-[state=open]/proj:rotate-180 max-sm:hidden" />
                    </button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent side="top">
                  {isGerman ? "Speicherort dieser Suche" : "Where this search is saved"}
                </TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="end" className="w-[14.375rem] p-1.5">
                <DropdownMenuLabel className="px-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  {isGerman ? "In Projekt speichern" : "Save to project"}
                </DropdownMenuLabel>
                <DropdownMenuItem
                  onSelect={() => setProjectChoice("none")}
                  className="rounded-lg"
                >
                  <span
                    className={cn(
                      "grid size-6 shrink-0 place-items-center rounded-md",
                      targetProject === null
                        ? "bg-moss-surface text-ivory"
                        : "bg-secondary text-muted-foreground",
                    )}
                  >
                    <Inbox className="size-3" />
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {isGerman ? "Kein Projekt" : "No project"}
                  </span>
                  {targetProject === null && <Check className="size-4 text-moss" />}
                </DropdownMenuItem>
                <div className="max-h-[13.75rem] overflow-y-auto">
                  {(projects ?? []).map((project) => (
                    <DropdownMenuItem
                      key={project.id}
                      onSelect={() => setProjectChoice(project.id)}
                      className="rounded-lg"
                    >
                      <span
                        className={cn(
                          "grid size-6 shrink-0 place-items-center rounded-md",
                          project.id === targetProject?.id
                            ? "bg-moss-surface text-ivory"
                            : "bg-secondary text-muted-foreground",
                        )}
                      >
                        <FolderClosed className="size-3" />
                      </span>
                      <span className="min-w-0 flex-1 truncate">{project.name}</span>
                      {project.id === targetProject?.id && (
                        <Check className="size-4 text-moss" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </div>
                <DropdownMenuSeparator />
                {creatingProject ? (
                  <div className="flex items-center gap-1 px-1 pb-0.5">
                    <Input
                      autoFocus
                      value={newProjectName}
                      onChange={(event) => setNewProjectName(event.target.value)}
                      onKeyDown={(event) => {
                        event.stopPropagation();
                        if (event.key === "Enter" && newProjectName.trim()) {
                          createProject.mutate(newProjectName.trim());
                        }
                        if (event.key === "Escape") setCreatingProject(false);
                      }}
                      placeholder={isGerman ? "Projektname…" : "Project name…"}
                      className="h-8 rounded-lg text-[0.8125rem]"
                    />
                    <Button
                      size="icon"
                      variant="ghost"
                      disabled={!newProjectName.trim() || createProject.isPending}
                      onClick={() => createProject.mutate(newProjectName.trim())}
                      className="size-8 shrink-0 rounded-lg text-moss"
                      aria-label="Create project"
                    >
                      {createProject.isPending ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Check className="size-3.5" />
                      )}
                    </Button>
                  </div>
                ) : (
                  <DropdownMenuItem
                    onSelect={(event) => {
                      event.preventDefault();
                      setCreatingProject(true);
                    }}
                    className="rounded-lg text-muted-foreground"
                  >
                    <Plus className="size-4" /> {isGerman ? "Neues Projekt…" : "New project…"}
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    const pool = EXAMPLE_QUESTIONS.filter((q) => q !== question);
                    setQuestion(pool[Math.floor(Math.random() * pool.length)]);
                    setWebSearchPublicDataConfirmed(false);
                    textareaRef.current?.focus();
                  }}
                  className="size-10 rounded-full text-muted-foreground hover:text-foreground"
                  aria-label={isGerman ? "Beispielfrage einfügen" : "Generate an example question"}
                >
                  <Dices className="size-4.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">
                {isGerman ? "Beispielfrage einfügen" : "Insert an example question"}
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
        </Dialog>
      </div>

      <p className="mt-4 text-center text-[0.8125rem] leading-relaxed text-muted-foreground">
        <span className="font-medium">{isGerman ? "KI-Assistent · " : "AI assistant · "}</span>
        {askMode ? (
          <>
            {isGerman
              ? "Du erhältst eine direkte, zitierte Antwort im Chat. Öffne die systematische Recherche für eine vollständig dokumentierte Literatursuche."
              : "You'll get a direct, cited answer in the chat. Open Systematic review for a documented literature search."}
          </>
        ) : (
          <>
            {isGerman
              ? "Systematische Recherche aktiv: Abruf, Deduplizierung, Ranking und dokumentierte Synthese laufen als ein Workflow."
              : "Systematic review active: retrieval, deduplication, ranking and a documented synthesis run as one workflow."}
          </>
        )}
      </p>
    </div>
  );
}
