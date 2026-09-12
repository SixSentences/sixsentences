"use client";

import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  BarChart3,
  BookMarked,
  Boxes,
  Check,
  ChevronDown,
  Cpu,
  Database,
  Download,
  FileWarning,
  FileText,
  GitBranch,
  GitFork,
  ImagePlus,
  Lightbulb,
  Loader2,
  PenLine,
  Pencil,
  RefreshCcw,
  Shapes,
  Trash2,
  Wand2,
  Waypoints,
  Workflow,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import {
  immutableRepositoryEvidenceUrl,
  RepositorySourceDialog,
} from "@/components/figures/repository-source-dialog";
import { RepositoryManuscriptDialog } from "@/components/figures/repository-manuscript-panel";

import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useRuns } from "@/hooks/queries";
import { track } from "@/lib/analytics";
import { api, downloadFigureSource, fetchFigureUrl } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { FIGURE_PROMPT_MAX_CHARACTERS } from "@/lib/figure-limits";
import { REPOSITORY_VISUAL_SOURCE_ENABLED } from "@/lib/launch-features";
import { useAuth } from "@/lib/auth";
import { useActiveProject } from "@/lib/project-context";
import { publicLegalUrl } from "@/lib/public-links";
import type {
  Figure,
  FigureRepositoryProvenance,
  RepositoryAnalysis,
  RepositoryDiagramKind,
} from "@/lib/types";
import {
  userFacingErrorMessage,
  userFacingStoredErrorMessage,
} from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

const MossField = dynamic(() => import("@/components/brand/moss-field"), {
  ssr: false,
});

type FireworkBurst = {
  id: number;
  x: number;
  y: number;
};

const FIREWORK_PARTICLES = Array.from({ length: 14 }, (_, index) => ({
  angle: `${(360 / 14) * index}deg`,
  delay: `${(index % 3) * 18}ms`,
  distance: `${34 + (index % 4) * 5}px`,
}));

/* Style presets in the spirit of PaperBanana's prompt mode: one click sets
   the genre, the author's words stay the content. */
const PRESETS = [
  {
    id: "method",
    label: "Method",
    icon: Workflow,
    prompt:
      "A left-to-right method pipeline diagram with clearly labeled stages "
      + "connected by arrows.",
  },
  {
    id: "architecture",
    label: "Architecture",
    icon: Boxes,
    prompt:
      "A system architecture diagram with components, layers and labeled "
      + "data-flow arrows.",
  },
  {
    id: "flow",
    label: "Flow",
    icon: GitBranch,
    prompt:
      "A top-down flowchart with process boxes, decision diamonds and "
      + "labeled branches.",
  },
  {
    id: "concept",
    label: "Concept",
    icon: Lightbulb,
    prompt:
      "A conceptual illustration that makes the core idea instantly "
      + "graspable for a reader skimming the paper.",
  },
  {
    id: "plot",
    label: "Data plot",
    icon: BarChart3,
    prompt:
      "A precise statistical plot with a readable legend, clearly labeled "
      + "axes and no values beyond those supplied in the brief.",
  },
] as const;

const RESOLUTIONS = [
  { id: "1k", label: "1K", hint: "Compact, 4–6 cm" },
  { id: "2k", label: "2K", hint: "Single column, 7–9 cm" },
  { id: "4k", label: "4K", hint: "Full width, 14–17 cm" },
] as const;

const ASPECT_RATIOS = ["1:1", "4:3", "3:2", "16:9", "2:3"] as const;
const DEFAULT_REDRAW_PROMPT =
  "Redraw this exactly as a clean, professional publication figure. "
  + "Keep every element and label.";

/* Provider selection remains server-owned in the open-source client. */
const FIGURE_MODELS = [
  { id: "auto", label: "Auto", hint: "Model selected by the connected API" },
] as const;

/* The blank page teaches by example: one click drops a real prompt in. */
const STARTERS = [
  {
    title: "Screening funnel",
    prompt:
      "A vertical PRISMA-style screening funnel from records identified "
      + "through duplicates removed and screening down to the included "
      + "studies, with the count in each stage.",
    grounded: true,
  },
  {
    title: "Method overview",
    prompt:
      "A clean overview diagram of a retrieval, screening and synthesis "
      + "pipeline: database icons on the left, an evaluation loop in the "
      + "middle, a report artifact on the right.",
    grounded: false,
  },
  {
    title: "Concept for the intro",
    prompt:
      "A conceptual illustration contrasting manual literature screening "
      + "(a person before a huge paper stack) with assisted screening (the "
      + "same person reviewing a short, ranked list).",
    grounded: false,
  },
] as const;

/** The image route needs the auth header, so figures render from object
 * URLs. Those URLs must outlive the component: the query cache keeps the
 * string across tab switches, so revoking on unmount would hand the next
 * mount a dead URL (the gallery then degrades to alt text). One URL per
 * figure lives in this module map until the figure is deleted. */
const figureObjectUrls = new Map<string, string>();

async function figureObjectUrl(id: string): Promise<string> {
  const cached = figureObjectUrls.get(id);
  if (cached) return cached;
  const url = await fetchFigureUrl(id);
  const existing = figureObjectUrls.get(id); // a parallel fetch may have won
  if (existing) {
    URL.revokeObjectURL(url);
    return existing;
  }
  figureObjectUrls.set(id, url);
  return url;
}

function releaseFigureUrl(id: string): void {
  const url = figureObjectUrls.get(id);
  if (url) {
    URL.revokeObjectURL(url);
    figureObjectUrls.delete(id);
  }
}

function FigureImage({
  figure,
  className,
  onClick,
}: {
  figure: Figure;
  className?: string;
  onClick?: () => void;
}) {
  const { data: url } = useQuery({
    queryKey: ["figure-image", figure.public_id],
    queryFn: () => figureObjectUrl(figure.public_id),
    enabled: figure.status === "ok",
    staleTime: Infinity,
    gcTime: 5 * 60_000,
  });
  if (!url) {
    return <Loader2 className="size-5 animate-spin text-muted-foreground" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt={figure.prompt.slice(0, 120)}
      onClick={onClick}
      className={cn("h-full w-full object-contain", className)}
    />
  );
}

function FigureRenderPreview({ figure }: { figure: Figure }) {
  const rawStages = figure.config.pipeline?.length
    ? figure.config.pipeline
    : [
        {
          id: "render",
          label: "Rendering the composition",
          status: "running" as const,
        },
      ];
  const configuredActiveIndex = figure.config.active_stage
    ? rawStages.findIndex((stage) => stage.id === figure.config.active_stage)
    : -1;
  const lastRunningIndex = rawStages.reduce(
    (latest, stage, index) => (stage.status === "running" ? index : latest),
    -1,
  );
  const activeIndex =
    configuredActiveIndex >= 0 ? configuredActiveIndex : lastRunningIndex;
  const stages = rawStages.map((stage, index) => {
    if (activeIndex < 0) return stage;
    if (index < activeIndex && (stage.status === "pending" || stage.status === "running")) {
      return { ...stage, status: "completed" as const, detail: undefined };
    }
    if (index === activeIndex && stage.status !== "failed" && stage.status !== "skipped") {
      return { ...stage, status: "running" as const };
    }
    if (index !== activeIndex && stage.status === "running") {
      return { ...stage, status: "pending" as const, detail: undefined };
    }
    return stage;
  });
  const active = (activeIndex >= 0 ? stages[activeIndex] : undefined)
    ?? [...stages].reverse().find((stage) => stage.status === "running")
    ?? stages.find((stage) => stage.status === "pending")
    ?? stages.at(-1);
  const activeLabel = active?.id.startsWith("refine_")
    ? "Refining the final composition"
    : active?.label;
  return (
    <div
      className="w-full max-w-[20rem] px-3"
      role="status"
      aria-live="polite"
    >
      <div className="figure-render-preview mx-auto scale-90" aria-hidden="true">
        <span className="figure-render-grid" />
        <span className="figure-render-connection figure-render-connection-a" />
        <span className="figure-render-connection figure-render-connection-b" />
        <span className="figure-render-node figure-render-node-a" />
        <span className="figure-render-node figure-render-node-b" />
        <span className="figure-render-node figure-render-node-c" />
        <span className="figure-render-scan" />
      </div>
      <p className="mt-1 text-center text-[0.75rem] font-medium leading-relaxed text-foreground">
        {activeLabel ?? "Preparing the figure"}
      </p>
      <div className="mx-auto mt-3 w-fit max-w-full space-y-1.5 text-left">
        {stages.map((stage) => (
          <div key={stage.id} className="flex items-center gap-2">
            {/* identical geometry per stage; only the inner mark differs, so
                the pulse animation can never skew the row */}
            <span
              className={cn(
                "grid size-4 shrink-0 place-items-center rounded-full border",
                stage.status === "completed"
                  ? "border-moss bg-moss-surface text-ivory"
                  : stage.status === "running"
                    ? "border-moss bg-accent"
                    : stage.status === "failed"
                      ? "border-destructive/60 bg-destructive/10"
                      : "border-border bg-card",
              )}
            >
              {stage.status === "completed" ? <Check className="size-2.5" /> : null}
              {stage.status === "running" ? (
                <span className="size-1.5 animate-pulse rounded-full bg-moss-surface" />
              ) : null}
            </span>
            <span className="min-w-0">
              <span
                className={cn(
                  "block truncate text-[0.65625rem] leading-tight",
                  stage.status === "running"
                    ? "font-medium text-foreground"
                    : stage.status === "completed"
                      ? "text-foreground/70"
                      : "text-muted-foreground/65",
                )}
              >
                {stage.label}
              </span>
              {stage.detail && (stage.status === "running" || stage.status === "failed") ? (
                <span className="mt-0.5 block truncate text-[0.59375rem] text-muted-foreground">
                  {stage.status === "failed"
                    ? userFacingStoredErrorMessage(
                        stage.detail,
                        "This step could not be completed. Try the figure again.",
                      )
                    : stage.detail}
                </span>
              ) : null}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function storedRepositoryProvenance(
  figure: Figure,
): FigureRepositoryProvenance | null {
  const raw = figure.config.repository as unknown;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const repositoryAccess = value.repository_access === "private"
    ? "private"
    : "public";
  const owner = typeof value.owner === "string" ? value.owner.trim() : "";
  const name = typeof value.name === "string" ? value.name.trim() : "";
  if (repositoryAccess === "public" && (!owner || !name)) return null;
  const diagramKind = ["architecture", "flow", "deployment", "module"].includes(
    String(value.diagram_kind),
  ) ? value.diagram_kind as RepositoryDiagramKind : "architecture";
  const numericCount = (candidate: unknown) =>
    typeof candidate === "number" && Number.isFinite(candidate)
      ? Math.max(0, Math.trunc(candidate))
      : 0;
  return {
    analysis_id: typeof value.analysis_id === "string"
      ? value.analysis_id
      : figure.repository_analysis_id ?? "",
    ...(typeof value.repository_id === "string"
      ? { repository_id: value.repository_id }
      : {}),
    repository_access: repositoryAccess,
    ...(owner ? { owner } : {}),
    ...(name ? { name } : {}),
    ...(typeof value.repository_url === "string"
      ? { repository_url: value.repository_url }
      : repositoryAccess === "public"
        ? { repository_url: `https://github.com/${owner}/${name}` }
        : {}),
    commit_sha: typeof value.commit_sha === "string" ? value.commit_sha : "",
    ref: typeof value.ref === "string" ? value.ref : null,
    subpath: typeof value.subpath === "string" ? value.subpath : null,
    diagram_kind: diagramKind,
    verification_status: "styled_variant",
    spec_node_count: numericCount(value.spec_node_count),
    spec_edge_count: numericCount(value.spec_edge_count),
  };
}

function RepositoryFigureBadge({ figure }: { figure: Figure }) {
  const repository = storedRepositoryProvenance(figure);
  if (!repository) return null;
  const privateSource = repository.repository_access === "private";
  return (
    <p
      className="mb-2 flex items-center gap-1.5 truncate text-[0.65625rem] text-moss"
      title={privateSource
        ? "Styled from verified private repository spec"
        : `Styled from verified repository spec · ${repository.owner}/${repository.name}@${repository.commit_sha}`}
    >
      <GitFork className="size-3 shrink-0" />
      <span className="truncate">
        {privateSource ? "Private GitHub repository" : "Styled from verified repository spec"}
      </span>
      {!privateSource && repository.commit_sha ? <span className="shrink-0 font-mono">· {repository.commit_sha.slice(0, 8)}</span> : null}
    </p>
  );
}

function RepositoryFigureProvenanceDetail({
  figure,
  userId,
}: {
  figure: Figure;
  userId: number;
}) {
  const repository = storedRepositoryProvenance(figure);
  const analysisId = figure.repository_analysis_id ?? repository?.analysis_id;
  const privateSource = repository?.repository_access === "private";
  const analysis = useQuery({
    queryKey: ["repository-analysis", userId, analysisId],
    queryFn: () => api.repositoryAnalysis(analysisId as string),
    enabled: Boolean(repository && analysisId && !privateSource),
    retry: false,
  });
  if (!repository) return null;
  const commitSha = repository.commit_sha ?? "";

  const commitUrl = !privateSource
    && repository.owner
    && repository.name
    && /^[0-9a-f]{40}$/i.test(commitSha)
    ? `https://github.com/${encodeURIComponent(repository.owner)}/${encodeURIComponent(repository.name)}/tree/${commitSha}`
    : null;

  return (
    <section className="rounded-2xl border border-moss/25 bg-accent/25 p-3.5" aria-label="Repository provenance">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="flex items-center gap-2 text-[0.75rem] font-medium text-foreground">
            <GitFork className="size-3.5 text-moss" />
            {privateSource ? "Private GitHub repository" : "Styled from verified repository spec"}
          </p>
          <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
            {privateSource
              ? "This figure shows screened, derived architecture. Repository identity, commit, raw evidence records, source links, and raw code stay hidden; screened component or directory labels may be path-like."
              : "The reviewed specification and SHA-pinned evidence are canonical. This PNG is a styled variant, not an exact representation of the code."}
          </p>
        </div>
        {commitUrl ? (
          <a
            href={commitUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[0.65625rem] text-foreground hover:border-moss/45"
          >
            {repository.owner}/{repository.name}@{commitSha.slice(0, 10)}
            <ArrowUpRight className="size-3" />
          </a>
        ) : !privateSource ? (
          <span className="font-mono text-[0.59375rem] text-muted-foreground">
            {repository.owner}/{repository.name}
          </span>
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.1em] text-muted-foreground">
        <span>{repository.diagram_kind}</span>
        <span>·</span>
        <span>{repository.spec_node_count} nodes</span>
        <span>·</span>
        <span>{repository.spec_edge_count} edges</span>
        {!privateSource && repository.subpath ? <><span>·</span><span>{repository.subpath}</span></> : null}
      </div>
      {analysis.data?.evidence.length ? (
        <div className="mt-3 max-h-36 space-y-1 overflow-y-auto border-t border-border/70 pt-2">
          {analysis.data.evidence.map((evidence, index) => {
            const url = immutableRepositoryEvidenceUrl(analysis.data as RepositoryAnalysis, evidence);
            const path = evidence.path ?? evidence.source_path;
            const label = evidence.label ?? evidence.fact ?? evidence.summary ?? path ?? `Evidence ${index + 1}`;
            return url ? (
              <a
                key={evidence.id ?? evidence.evidence_id ?? `${path}-${index}`}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[0.65625rem] text-foreground hover:bg-card"
              >
                <span className="min-w-0 flex-1 truncate">{label}</span>
                <span className="shrink-0 font-mono text-[0.5625rem] text-muted-foreground">SHA evidence</span>
                <ArrowUpRight className="size-3 shrink-0" />
              </a>
            ) : null;
          })}
        </div>
      ) : null}
    </section>
  );
}

export default function FiguresPage() {
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const [prompt, setPrompt] = useState("");
  const [preset, setPreset] = useState<string | null>(null);
  const [resolution, setResolution] = useState<"1k" | "2k" | "4k">("2k");
  const [aspectRatio, setAspectRatio] = useState<(typeof ASPECT_RATIOS)[number]>("4:3");
  const [reviewPasses, setReviewPasses] = useState(1);
  const [groundRun, setGroundRun] = useState<{
    id: number;
    label: string;
  } | null>(null);
  const [groundDataset, setGroundDataset] = useState<{
    id: string;
    label: string;
  } | null>(null);
  const [repositoryGrounding, setRepositoryGrounding] =
    useState<RepositoryAnalysis | null>(null);
  const [galleryTab, setGalleryTab] = useState<"generated" | "sources">(
    "generated",
  );
  const [selected, setSelected] = useState<Figure | null>(null);
  const [manuscriptAnalysisId, setManuscriptAnalysisId] = useState<string | null>(null);
  const [requestedAnalysisId, setRequestedAnalysisId] = useState<string | null>(null);
  const selectedRepository = selected
    ? storedRepositoryProvenance(selected)
    : null;
  const [fireworkBursts, setFireworkBursts] = useState<FireworkBurst[]>([]);
  const openedFigureRef = useRef<string | null>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const fireworkIdRef = useRef(0);
  const fireworkTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(
    () => () => {
      fireworkTimersRef.current.forEach((timer) => clearTimeout(timer));
    },
    [],
  );

  useEffect(() => {
    const url = new URL(window.location.href);
    const requested = url.searchParams.get("analysis");
    if (!requested) return;
    if (!REPOSITORY_VISUAL_SOURCE_ENABLED) {
      url.searchParams.delete("analysis");
      window.history.replaceState(
        window.history.state,
        "",
        `${url.pathname}${url.search}${url.hash}`,
      );
      return;
    }
    if (requested.length > 128) return;
    setRequestedAnalysisId(requested);
  }, []);

  const launchBackgroundFirework = (event: ReactMouseEvent<HTMLElement>) => {
    const target = event.target as HTMLElement;
    if (
      target.closest(
        'a, button, input, textarea, select, [role="button"], [data-tour="figures-composer"]',
      )
    ) {
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const bounds = event.currentTarget.getBoundingClientRect();
    const id = ++fireworkIdRef.current;
    const burst = {
      id,
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    };
    setFireworkBursts((current) => [...current.slice(-3), burst]);
    const timer = setTimeout(() => {
      setFireworkBursts((current) => current.filter((item) => item.id !== id));
      fireworkTimersRef.current = fireworkTimersRef.current.filter(
        (candidate) => candidate !== timer,
      );
    }, 900);
    fireworkTimersRef.current.push(timer);
  };

  /** A draft to redraw professionally: an uploaded image or an existing
   * figure. The model treats it as the brief and improves only the craft. */
  const [sourceDraft, setSourceDraft] = useState<
    | { kind: "upload"; base64: string; preview: string | null; label: string }
    | { kind: "figure"; id: string; preview: string | null; label: string }
    | null
  >(null);
  const selectedPreset = PRESETS.find((entry) => entry.id === preset);
  const composedPrompt = useMemo(() => {
    const typed = prompt.trim();
    if (repositoryGrounding) return typed;
    if (sourceDraft) return typed || DEFAULT_REDRAW_PROMPT;
    if (selectedPreset && typed) return `${selectedPreset.prompt}\n\n${typed}`;
    return selectedPreset?.prompt ?? typed;
  }, [prompt, repositoryGrounding, selectedPreset, sourceDraft]);
  const composedPromptLength = composedPrompt.length;
  const repositoryGoalMismatch = Boolean(
    repositoryGrounding
    && prompt.trim() !== repositoryGrounding.goal.trim(),
  );
  const promptTooLong = composedPromptLength > FIGURE_PROMPT_MAX_CHARACTERS;
  const visiblePromptLimit = Math.max(
    0,
    FIGURE_PROMPT_MAX_CHARACTERS
      - (!repositoryGrounding && !sourceDraft && selectedPreset
        ? selectedPreset.prompt.length + 2
        : 0),
  );
  const sourceFileRef = useRef<HTMLInputElement>(null);
  function stageSourceFile(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      toast.error("Attach an image file (PNG, JPG or WebP).");
      return;
    }
    if (file.size > 12 * 1024 * 1024) {
      toast.error("Source images are limited to 12 MB.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result ?? "");
      const base64 = dataUrl.split(",", 2)[1] ?? "";
      if (!base64) {
        toast.error("Could not read that image.");
        return;
      }
      setGroundRun(null);
      setGroundDataset(null);
      setRepositoryGrounding(null);
      setSourceDraft({ kind: "upload", base64, preview: dataUrl, label: file.name });
      promptRef.current?.focus();
    };
    reader.readAsDataURL(file);
  }

  // which model renders; persisted like the writer's composer choice
  const [model, setModel] = useState("auto");
  useEffect(() => {
    const stored = localStorage.getItem("six:figure-model");
    if (stored && FIGURE_MODELS.some((entry) => entry.id === stored)) {
      setModel(stored);
    }
  }, []);
  const pickModel = (id: string) => {
    setModel(id);
    localStorage.setItem("six:figure-model", id);
  };
  const activeModel =
    FIGURE_MODELS.find((entry) => entry.id === model) ?? FIGURE_MODELS[0];

  const { data: figures, isLoading: figuresLoading } = useQuery({
    queryKey: ["figures"],
    queryFn: api.figures,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((figure) => figure.status === "pending")
        ? 2500
        : false,
  });
  const generatedFigures = useMemo(
    () => (figures ?? []).filter((figure) => figure.config.kind !== "source"),
    [figures],
  );
  const sourceFigures = useMemo(
    () => (figures ?? []).filter((figure) => figure.config.kind === "source"),
    [figures],
  );
  const visibleFigures =
    galleryTab === "generated" ? generatedFigures : sourceFigures;
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("figure");
    if (!requested || !figures || openedFigureRef.current === requested) return;
    const figure = figures.find((item) => item.public_id === requested);
    if (!figure) return;
    openedFigureRef.current = requested;
    setGalleryTab(figure.config.kind === "source" ? "sources" : "generated");
    setSelected(figure);
    if (figure.project_id) setActiveProjectId(figure.project_id);
  }, [figures, setActiveProjectId]);
  const { data: runs } = useRuns();
  const { data: datasets } = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("dataset");
    if (!requested || !datasets) return;
    const dataset = datasets.find((item) => item.public_id === requested);
    if (dataset) {
      setGroundDataset({ id: dataset.public_id, label: dataset.name });
      setGroundRun(null);
      setRepositoryGrounding(null);
      setSourceDraft(null);
    }
  }, [datasets]);
  const completedRuns = useMemo(
    () =>
      (runs ?? []).filter((run) => run.status === "completed" && run.prisma),
    [runs],
  );
  const { data: docs } = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
  });

  const create = useMutation({
    mutationFn: () =>
      api.figureCreate(
        composedPrompt,
        repositoryGrounding ? null : (groundRun?.id ?? null),
        model,
        {
        kind: repositoryGrounding
          ? (repositoryGrounding.diagram_kind === "flow" ? "flow" : "architecture")
          : sourceDraft ? "refine" : (preset ?? "method"),
        resolution,
        aspect_ratio: aspectRatio,
        review_passes: reviewPasses,
        dataset_id: repositoryGrounding ? null : (groundDataset?.id ?? null),
        ...(repositoryGrounding
          ? { repository_analysis_id: repositoryGrounding.public_id }
          : {}),
        ...(activeProjectId ? { project_id: activeProjectId } : {}),
        ...(sourceDraft?.kind === "upload"
          ? { source_image_base64: sourceDraft.base64 }
          : {}),
        ...(sourceDraft?.kind === "figure"
          ? { source_figure_id: sourceDraft.id }
          : {}),
        },
      ),
    onSuccess: () => {
      track("figure_generated", {
        kind: repositoryGrounding
          ? (repositoryGrounding.diagram_kind === "flow" ? "flow" : "architecture")
          : sourceDraft ? "refine" : (preset ?? "method"),
        resolution,
        review_passes: reviewPasses,
      });
      setPrompt("");
      setSourceDraft(null);
      setRepositoryGrounding(null);
      setGalleryTab("generated");
      toast.success("Rendering started. Most figures finish in 30 to 90 seconds.");
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  function renderFigure() {
    if (repositoryGoalMismatch) {
      setRepositoryGrounding(null);
      toast.error(
        me?.language === "de"
          ? "Der Visual-Brief wurde geändert. Analysiere das Repository erneut."
          : "The visual brief changed. Analyze the repository again.",
      );
      return;
    }
    if (promptTooLong) {
      toast.error("Shorten the complete brief to 16,000 characters.");
      return;
    }
    create.mutate();
  }

  const attach = useMutation({
    mutationFn: (input: { figureId: string; documentId: string }) =>
      api.figureAttach(input.figureId, input.documentId),
    onSuccess: (result) => {
      toast.success(
        `${result.filename} attached. Embed it via the editor's Visuals menu.`,
      );
      void queryClient.invalidateQueries({
        queryKey: ["writer-assets", result.document_public_id],
      });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: (figureId: string) => api.figureDelete(figureId),
    onSuccess: () => {
      setSelected(null);
      setConfirmDelete(null);
      toast.success("Figure deleted.");
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  // inline rename: which card is being edited, and the working title
  const [renaming, setRenaming] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  // the dialog title is edited in place, manuscript-style
  const [dialogTitleDraft, setDialogTitleDraft] = useState("");
  useEffect(() => {
    setDialogTitleDraft(selected?.config.title ?? "");
  }, [selected?.public_id, selected?.config.title]); // eslint-disable-line react-hooks/exhaustive-deps
  const rename = useMutation({
    mutationFn: (input: { id: string; title: string }) =>
      api.figureRename(input.id, input.title),
    onSuccess: (updated) => {
      setRenaming(null);
      setSelected((current) =>
        current && current.public_id === updated.public_id ? updated : current,
      );
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Rename failed."),
  });
  const commitRename = (figure: Figure) => {
    const title = nameDraft.trim();
    if (title !== (figure.config.title ?? "")) {
      rename.mutate({ id: figure.public_id, title });
    } else {
      setRenaming(null);
    }
  };

  const download = async (figure: Figure) => {
    try {
      const url = await fetchFigureUrl(figure.public_id);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `figure-${figure.public_id}.png`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(userFacingErrorMessage(error, "Download failed."));
    }
  };

  const reuse = (figure: Figure) => {
    setPrompt(figure.prompt);
    setGroundRun(null);
    setGroundDataset(null);
    setRepositoryGrounding(null);
    setSourceDraft(null);
    setSelected(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
    promptRef.current?.focus();
  };

  const startFrom = (starter: (typeof STARTERS)[number]) => {
    setPrompt(starter.prompt);
    setGroundRun(null);
    setGroundDataset(null);
    setRepositoryGrounding(null);
    setSourceDraft(null);
    if (starter.grounded && completedRuns.length > 0) {
      const run = completedRuns[0];
      setGroundRun({ id: run.id, label: run.title ?? run.question });
    }
    promptRef.current?.focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const useRepositoryAnalysis = (analysis: RepositoryAnalysis) => {
    setPrompt(analysis.goal);
    setPreset(null);
    setGroundRun(null);
    setGroundDataset(null);
    setSourceDraft(null);
    setRepositoryGrounding(analysis);
    setActiveProjectId(analysis.project_id);
    window.scrollTo({ top: 0, behavior: "smooth" });
    window.setTimeout(() => promptRef.current?.focus(), 0);
  };

  const attachMenu = (figure: Figure, align: "start" | "end" = "end") => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={figure.status !== "ok"}
          className="h-7 rounded-full px-2.5 text-[0.71875rem]"
        >
          <PenLine className="size-3" />
          To Writer
          <ChevronDown className="size-3" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align={align} className="w-[17rem]">
        <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          Attach to a document
        </DropdownMenuLabel>
        {(docs ?? []).length === 0 ? (
          <p className="px-2 py-3 text-[0.75rem] text-muted-foreground">
            No Writer documents yet. Create one and the figure attaches as a
            compile-ready asset.
          </p>
        ) : (
          (docs ?? []).slice(0, 10).map((doc) => (
            <DropdownMenuItem
              key={doc.id}
              onSelect={() =>
                attach.mutate({
                  figureId: figure.public_id,
                  documentId: doc.public_id ?? String(doc.id),
                })
              }
            >
              <span className="truncate">{doc.title}</span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    // the scroll box clips itself to the shell's rounded frame, so the dark
    // hero cannot poke square corners into it while scrolling
    <div className="h-full overflow-y-auto md:rounded-t-2xl">
      <div>
        <section
          className="relative overflow-hidden bg-pine dark:bg-background"
          onDoubleClick={launchBackgroundFirework}
        >
          <div data-visual-lab-background className="absolute inset-0">
            <MossField className="absolute inset-0 dark:opacity-35 dark:grayscale" />
          </div>
          <div className="pointer-events-none absolute inset-0 hidden bg-background/45 dark:block" />
          {/* the shader breathes behind the composer and fades into the
              page toward every edge, so the hero sits IN the frame */}
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-52 bg-gradient-to-b from-transparent via-background/45 to-background" />
          <div className="pointer-events-none absolute inset-y-0 left-0 w-64 bg-gradient-to-r from-background/60 via-background/20 to-transparent" />
          <div className="pointer-events-none absolute inset-y-0 right-0 w-64 bg-gradient-to-l from-background/60 via-background/20 to-transparent" />
          <div className="pointer-events-none absolute inset-0 z-[1] overflow-hidden" aria-hidden="true">
            {fireworkBursts.map((burst) => (
              <span
                key={burst.id}
                className="visual-lab-firework"
                style={{ left: burst.x, top: burst.y }}
              >
                <span className="visual-lab-firework-ring" />
                {FIREWORK_PARTICLES.map((particle, index) => (
                  <span
                    key={index}
                    className="visual-lab-firework-ray"
                    style={
                      {
                        "--firework-angle": particle.angle,
                        "--firework-delay": particle.delay,
                        "--firework-distance": particle.distance,
                      } as CSSProperties
                    }
                  >
                    <span className="visual-lab-firework-particle" />
                  </span>
                ))}
              </span>
            ))}
          </div>
          <div className="relative z-[2] mx-auto w-full max-w-4xl px-4 pb-12 pt-10 text-center sm:px-6 sm:pb-16 sm:pt-14">
          <p className="flex items-center justify-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.28em] text-ivory/70">
            <Shapes className="size-3.5" /> Visual Lab
          </p>
          <h1 className="font-display mt-3 text-balance text-[clamp(2.4rem,4vw,3.4rem)] leading-[1.05] text-ivory">
            Build the visual your paper needs.
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-balance text-[0.9375rem] leading-relaxed text-ivory/75">
            Turn a research brief into a paper-ready diagram or plot. Choose
            the exact canvas, ground it in a search, then review it before it
            reaches your manuscript.
          </p>
          <div className="mx-auto mt-6 flex max-w-lg items-center justify-center gap-2 text-[0.6875rem] text-ivory/65">
            {[
              ["01", "Brief"],
              ["02", "Render"],
              ["03", "Review"],
            ].map(([step, label], index) => (
              <Fragment key={step}>
                {index > 0 ? <span className="h-px w-8 bg-ivory/20" /> : null}
                <span className="rounded-full border border-ivory/15 px-2.5 py-1">
                  <span className="mr-1.5 font-mono text-ivory/40">{step}</span>{label}
                </span>
              </Fragment>
            ))}
          </div>

          <div
            data-tour="figures-composer"
            className="mt-9 overflow-hidden rounded-[1.75rem] border border-ivory/10 bg-card text-left shadow-[0_30px_80px_-30px_rgba(0,0,0,0.55)] transition-colors focus-within:border-moss/60"
          >
          <div data-tour="figures-brief" className="relative">
            <textarea
              id="visual-lab-brief"
              ref={promptRef}
              value={prompt}
              aria-label={
                me?.language === "de"
                  ? "Was soll die Grafik erklären?"
                  : "What should the visual explain?"
              }
              maxLength={visiblePromptLimit}
              onChange={(event) => {
                const nextPrompt = event.target.value;
                if (
                  repositoryGrounding
                  && nextPrompt.trim() !== repositoryGrounding.goal.trim()
                ) {
                  setRepositoryGrounding(null);
                }
                setPrompt(nextPrompt);
              }}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter"
                  && (event.metaKey || event.ctrlKey)
                  && composedPromptLength > 2
                  && !promptTooLong
                  && !repositoryGoalMismatch
                  && !create.isPending
                ) {
                  renderFigure();
                }
              }}
              rows={3}
              placeholder={
                sourceDraft
                  ? "Optional: what should change beyond a clean redraw? Colors, emphasis, layout…"
                  : "A pipeline from records identified through screening to the included studies, with the counts at every stage…"
              }
              className="block w-full resize-none border-0 bg-transparent px-6 pb-7 pt-5 text-[1rem] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/60"
            />
            <span
              title={
                selectedPreset && !sourceDraft
                  ? "Includes the selected preset instructions"
                  : "Complete figure brief"
              }
              className={cn(
                "pointer-events-none absolute bottom-2 right-5 font-mono text-[0.5625rem] tracking-[0.08em]",
                promptTooLong ? "text-destructive" : "text-muted-foreground/70",
              )}
            >
              {composedPromptLength.toLocaleString()} /{" "}
              {FIGURE_PROMPT_MAX_CHARACTERS.toLocaleString()}
            </span>
          </div>
          {promptTooLong ? (
            <p className="px-6 pb-3 text-[0.6875rem] text-destructive">
              The complete brief includes the selected preset and exceeds the
              16,000-character limit.
            </p>
          ) : null}
          {sourceDraft && (
            <div className="flex items-center px-6 pb-3">
              <span className="flex min-w-0 items-center gap-2.5 rounded-2xl border border-moss/30 bg-accent/40 py-1.5 pl-1.5 pr-3">
                {sourceDraft.preview ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={sourceDraft.preview}
                    alt=""
                    className="size-9 shrink-0 rounded-lg border border-border bg-card object-cover"
                  />
                ) : (
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-secondary">
                    <ImagePlus className="size-4 text-moss" />
                  </span>
                )}
                <span className="min-w-0">
                  <span className="block text-[0.71875rem] font-medium text-foreground">
                    {sourceDraft.kind === "upload"
                      ? "Redraw your image professionally"
                      : "Redraw this figure professionally"}
                  </span>
                  <span className="block max-w-64 truncate text-[0.65625rem] text-muted-foreground">
                    {sourceDraft.label}
                  </span>
                </span>
                <button
                  type="button"
                  aria-label="Remove the source image"
                  onClick={() => setSourceDraft(null)}
                  className="cursor-pointer p-0.5 text-muted-foreground hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              </span>
            </div>
          )}
          <div
            className={cn(
              "flex flex-wrap items-center gap-1.5 px-6 pb-4",
              (sourceDraft || repositoryGrounding) && "hidden",
            )}
          >
            {PRESETS.map((entry) => {
              const Icon = entry.icon;
              const active = preset === entry.id;
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() =>
                    setPreset((current) =>
                      current === entry.id ? null : entry.id,
                    )
                  }
                  className={cn(
                    "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-[0.71875rem] font-medium transition-all",
                    active
                      ? "border-primary bg-primary text-primary-foreground shadow-sm"
                      : "border-border text-muted-foreground hover:border-moss/50 hover:text-foreground",
                  )}
                >
                  <Icon className="size-3" />
                  {entry.label}
                </button>
              );
            })}
          </div>
          <div
            data-tour="figures-controls"
            className="grid gap-3 border-t border-border/60 bg-secondary/15 px-5 py-3 sm:grid-cols-[1.2fr_1fr_1fr]"
          >
            <div>
              <p className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">Output size</p>
              <div className="mt-1.5 flex gap-1">
                {RESOLUTIONS.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    title={entry.hint}
                    onClick={() => setResolution(entry.id)}
                    className={cn(
                      "flex-1 cursor-pointer rounded-lg border px-2 py-1.5 text-[0.6875rem] font-medium transition-colors",
                      resolution === entry.id
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-card text-muted-foreground hover:border-moss/50",
                    )}
                  >
                    <span className="inline-flex items-center gap-1">{entry.label}</span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">Canvas</p>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {ASPECT_RATIOS.map((ratio) => (
                  <button
                    key={ratio}
                    type="button"
                    onClick={() => setAspectRatio(ratio)}
                    className={cn(
                      "cursor-pointer rounded-lg border px-2 py-1.5 font-mono text-[0.625rem] transition-colors",
                      aspectRatio === ratio
                        ? "border-moss bg-accent text-moss"
                        : "border-border bg-card text-muted-foreground hover:border-moss/50",
                    )}
                  >
                    {ratio}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">Review passes</p>
              <div className="mt-1.5 flex gap-1">
                {[0, 1, 2].map((passes) => (
                  <button
                    key={passes}
                    type="button"
                    onClick={() => setReviewPasses(passes)}
                    className={cn(
                      "flex-1 cursor-pointer rounded-lg border px-2 py-1.5 text-[0.6875rem] transition-colors",
                      reviewPasses === passes
                        ? "border-moss bg-accent font-medium text-moss"
                        : "border-border bg-card text-muted-foreground hover:border-moss/50",
                    )}
                  >
                    <span className="inline-flex items-center gap-1">
                      {passes === 0 ? "Fast" : passes === 1 ? "Checked" : "Strict"}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 bg-secondary/30 px-4 py-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 shrink-0 rounded-full text-[0.75rem]"
                  >
                    <Cpu className="size-3.5" />
                    {activeModel.label}
                    <ChevronDown className="size-3" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-[17rem]">
                  <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Rendered by
                  </DropdownMenuLabel>
                  <p className="px-2 pb-1.5 text-[0.625rem] leading-relaxed text-muted-foreground">
                    Rendered through the model provider configured by this deployment.
                    {publicLegalUrl("privacy") ? <>{" "}<a href={publicLegalUrl("privacy")!} target="_blank" rel="noreferrer" className="underline underline-offset-2">Data processing and retention</a></> : null}
                  </p>
                  {FIGURE_MODELS.map((entry) => (
                    <DropdownMenuItem
                      key={entry.id}
                      onSelect={() => pickModel(entry.id)}
                      className="items-start"
                    >
                      <div className="min-w-0">
                        <p className="text-[0.8125rem]">{entry.label}</p>
                        <p className="text-[0.6875rem] text-muted-foreground">
                          {entry.hint}
                        </p>
                      </div>
                      {model === entry.id && (
                        <Check className="ml-auto size-3.5 shrink-0 text-moss" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <input
                ref={sourceFileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(event) => {
                  stageSourceFile(event.target.files?.[0] ?? undefined);
                  event.target.value = "";
                }}
              />
              <Button
                variant="outline"
                size="sm"
                className="h-8 shrink-0 rounded-full text-[0.75rem]"
                onClick={() => sourceFileRef.current?.click()}
              >
                <ImagePlus className="size-3.5" />
                {sourceDraft?.kind === "upload" ? "Replace image" : "From your image"}
              </Button>
              {me ? REPOSITORY_VISUAL_SOURCE_ENABLED ? (
                <RepositorySourceDialog
                  key={me.user_id}
                  userId={me.user_id}
                  userLanguage={me.language}
                  projectId={activeProjectId}
                  activeAnalysis={repositoryGrounding}
                  requestedAnalysisId={requestedAnalysisId}
                  analysisGoal={prompt}
                  onEditGoal={() => {
                    window.setTimeout(() => promptRef.current?.focus(), 0);
                  }}
                  onUseAnalysis={useRepositoryAnalysis}
                  onAnalysisDeleted={(analysisId) => {
                    if (repositoryGrounding?.public_id === analysisId) {
                      setRepositoryGrounding(null);
                    }
                  }}
                />
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled
                  aria-disabled="true"
                  aria-label={me.language === "de"
                    ? "Aus GitHub-Repository, derzeit in Erprobung und nicht verfügbar"
                    : "From GitHub repository, currently in testing and unavailable"}
                  title={me.language === "de"
                    ? "Dieses Feature wird noch getestet."
                    : "This feature is still being tested."}
                  data-launch-feature="repository-visual-source"
                  className="h-8 shrink-0 cursor-not-allowed rounded-full border-dashed border-border/70 bg-muted/30 text-[0.75rem] text-muted-foreground opacity-60"
                >
                  <GitFork className="size-3.5" />
                  {me.language === "de" ? "Aus GitHub-Repository" : "From GitHub repository"}
                  <span
                    aria-hidden="true"
                    className="rounded-full border border-border/70 bg-background/70 px-1.5 py-0.5 font-mono text-[0.5rem] uppercase tracking-[0.12em]"
                  >
                    {me.language === "de" ? "In Erprobung" : "In testing"}
                  </span>
                </Button>
              ) : null}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm" className="h-8 shrink-0 rounded-full text-[0.75rem]">
                    <Database className="size-3.5" />
                    {groundDataset ? "Data linked" : "Ground in data"}
                    <ChevronDown className="size-3" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-[19rem]">
                  <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">Primary research data</DropdownMenuLabel>
                  {(datasets ?? []).length === 0 ? (
                    <p className="px-2 py-3 text-[0.75rem] leading-relaxed text-muted-foreground">Import CSV, JSON or Excel in Data Hub first.</p>
                  ) : (
                    (datasets ?? []).map((dataset) => (
                      <DropdownMenuItem key={dataset.public_id} onSelect={() => {
                        setGroundDataset({ id: dataset.public_id, label: dataset.name });
                        setGroundRun(null);
                        setRepositoryGrounding(null);
                        setSourceDraft(null);
                      }}>
                        <span className="min-w-0 flex-1 truncate">{dataset.name}</span>
                        <span className="font-mono text-[0.59375rem] text-muted-foreground">{dataset.row_count.toLocaleString()} rows</span>
                        {groundDataset?.id === dataset.public_id && <Check className="size-3.5 shrink-0 text-moss" />}
                      </DropdownMenuItem>
                    ))
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 shrink-0 rounded-full text-[0.75rem]"
                  >
                    <BookMarked className="size-3.5" />
                    {groundRun ? "Grounded" : "Ground in a search"}
                    <ChevronDown className="size-3" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-[19rem]">
                  <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Real numbers from
                  </DropdownMenuLabel>
                  {completedRuns.length === 0 ? (
                    <p className="px-2 py-3 text-[0.75rem] text-muted-foreground">
                      No completed searches yet. Figures work without one;
                      grounding adds your audited counts and study titles.
                    </p>
                  ) : (
                    completedRuns.slice(0, 12).map((run) => (
                      <DropdownMenuItem
                        key={run.id}
                        onSelect={() => {
                          setGroundRun({
                            id: run.id,
                            label: run.title ?? run.question,
                          });
                          setGroundDataset(null);
                          setRepositoryGrounding(null);
                          setSourceDraft(null);
                        }}
                      >
                        <span className="truncate">
                          {run.title ?? run.question}
                        </span>
                        {groundRun?.id === run.id && (
                          <Check className="ml-auto size-3.5 shrink-0 text-moss" />
                        )}
                      </DropdownMenuItem>
                    ))
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              {groundRun ? (
                <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-moss/40 bg-accent/70 px-2.5 py-1 text-[0.71875rem] text-foreground">
                  <Waypoints className="size-3 shrink-0 text-moss" />
                  <span className="max-w-[13rem] truncate">
                    {groundRun.label}
                  </span>
                  <button
                    type="button"
                    onClick={() => setGroundRun(null)}
                    aria-label="Remove grounding"
                    className="cursor-pointer text-muted-foreground hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ) : null}
              {groundDataset ? (
                <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-moss/40 bg-accent/70 px-2.5 py-1 text-[0.71875rem] text-foreground">
                  <Database className="size-3 shrink-0 text-moss" />
                  <span className="max-w-[13rem] truncate">{groundDataset.label}</span>
                  <button type="button" onClick={() => setGroundDataset(null)} aria-label="Remove data grounding" className="cursor-pointer text-muted-foreground hover:text-foreground"><X className="size-3" /></button>
                </span>
              ) : null}
              {repositoryGrounding ? (
                <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-moss/40 bg-accent/70 px-2.5 py-1 text-[0.71875rem] text-foreground">
                  <GitFork className="size-3 shrink-0 text-moss" />
                  <span className="max-w-[16rem] truncate">
                    {repositoryGrounding.repository_access === "private"
                      ? me?.language === "de"
                        ? "Privates GitHub-Repository"
                        : "Private GitHub repository"
                      : `${repositoryGrounding.owner}/${repositoryGrounding.name}${repositoryGrounding.commit_sha ? `@${repositoryGrounding.commit_sha.slice(0, 10)}` : ""}`}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setRepositoryGrounding(null);
                    }}
                    aria-label="Remove repository grounding"
                    className="cursor-pointer text-muted-foreground hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                Configured renderer
              </span>
              <Button
                disabled={composedPromptLength < 3 || promptTooLong || repositoryGoalMismatch || create.isPending}
                onClick={renderFigure}
                className="h-10 rounded-full px-6 text-[0.84375rem] font-medium shadow-sm"
              >
                {create.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <>
                    Render
                    <ArrowUpRight className="size-4" />
                  </>
                )}
              </Button>
            </div>
          </div>
          </div>
            <p className="mx-auto mt-3 max-w-2xl text-[0.71875rem] leading-relaxed text-ivory/60">
              The review pass checks hierarchy, labels and connections. You
              still approve every number and word before manuscript use.
            </p>
          </div>
        </section>
      </div>

      <div className="w-full px-4 pb-20 pt-6 sm:px-6 sm:pt-10 lg:px-10 2xl:px-14">
        {figuresLoading ? (
          <div className="grid place-items-center py-16">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : (figures ?? []).length === 0 ? (
          <div className="mx-auto mt-2 w-full">
            <p className="text-center font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Start with one of these
            </p>
            <div className="mx-auto mt-3 grid max-w-4xl gap-3 sm:grid-cols-3">
              {STARTERS.map((starter) => (
                <button
                  key={starter.title}
                  type="button"
                  onClick={() => startFrom(starter)}
                  className="group cursor-pointer rounded-3xl border border-border bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-moss/50 hover:shadow-sm"
                >
                  <p className="flex items-center justify-between text-[0.84375rem] font-medium text-foreground">
                    {starter.title}
                    <ArrowUpRight className="size-3.5 text-muted-foreground transition-colors group-hover:text-moss" />
                  </p>
                  <p className="mt-1.5 line-clamp-3 text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {starter.prompt}
                  </p>
                  {starter.grounded ? (
                    <p className="mt-2 inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-moss">
                      <Waypoints className="size-2.5" /> uses your search
                    </p>
                  ) : null}
                </button>
              ))}
            </div>
            <div className="mt-12 grid grid-cols-2 gap-5 lg:grid-cols-4">
              {[Workflow, Boxes, GitBranch, Lightbulb].map((Ghost, index) => (
                <div
                  key={index}
                  className="grid aspect-[4/3] place-items-center rounded-2xl border border-dashed border-border bg-card/40"
                >
                  <Ghost className="size-7 text-foreground/[0.12]" strokeWidth={1.4} />
                </div>
              ))}
            </div>
            <p className="mt-4 text-center font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground/60">
              Your visuals appear here
            </p>
          </div>
        ) : (
          <div data-tour="figures-gallery" className="mt-10">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Your visuals
                </p>
                <p className="mt-1 text-[0.75rem] text-muted-foreground">
                  Generated work stays separate from figures collected as source material.
                </p>
              </div>
              <div
                role="tablist"
                aria-label="Visual collection"
                className="inline-flex w-fit rounded-full border border-border bg-secondary/60 p-1"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={galleryTab === "generated"}
                  onClick={() => setGalleryTab("generated")}
                  className={cn(
                    "flex h-8 items-center gap-2 rounded-full px-3 text-[0.75rem] transition-colors",
                    galleryTab === "generated"
                      ? "bg-card font-medium text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Wand2 className="size-3.5" />
                  Generated
                  <span className="font-mono text-[0.625rem] text-muted-foreground">
                    {generatedFigures.length}
                  </span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={galleryTab === "sources"}
                  onClick={() => setGalleryTab("sources")}
                  className={cn(
                    "flex h-8 items-center gap-2 rounded-full px-3 text-[0.75rem] transition-colors",
                    galleryTab === "sources"
                      ? "bg-card font-medium text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <BookMarked className="size-3.5" />
                  Saved sources
                  <span className="font-mono text-[0.625rem] text-muted-foreground">
                    {sourceFigures.length}
                  </span>
                </button>
              </div>
            </div>
            {visibleFigures.length === 0 ? (
              <div className="mt-4 grid min-h-48 place-items-center rounded-3xl border border-dashed border-border bg-card/35 px-6 text-center">
                <div className="max-w-md">
                  {galleryTab === "generated" ? (
                    <Wand2 className="mx-auto size-6 text-moss" />
                  ) : (
                    <BookMarked className="mx-auto size-6 text-moss" />
                  )}
                  <p className="mt-3 text-[0.84375rem] font-medium text-foreground">
                    {galleryTab === "generated"
                      ? "No generated visuals yet"
                      : "No saved source figures yet"}
                  </p>
                  <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
                    {galleryTab === "generated"
                      ? "Describe a visual above or redraw a saved source to create a publication-ready figure."
                      : "Save a figure from a paper in the Library. It will appear here with its page and source attached."}
                  </p>
                </div>
              </div>
            ) : (
            <div className="mt-3 grid gap-5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {visibleFigures.map((figure) => (
                <div
                  key={figure.public_id}
                  className="group overflow-hidden rounded-3xl border border-border bg-card transition-all hover:-translate-y-0.5 hover:border-moss/40 hover:shadow-sm"
                >
                  <div
                    data-figure-card-media
                    className="relative grid aspect-[4/3] place-items-center overflow-hidden bg-secondary/25 p-3 sm:p-4 dark:bg-[#151817]"
                  >
                    {figure.config.is_example ? (
                      <span className="absolute left-3 top-3 z-10 rounded-full border border-pine/10 bg-pine px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-ivory shadow-sm">
                        Example
                      </span>
                    ) : null}
                    {figure.status === "pending" ? (
                      <FigureRenderPreview figure={figure} />
                    ) : figure.status === "error" ? (
                      <div className="max-w-[16rem] text-center">
                        <FileWarning className="mx-auto size-5 text-destructive" />
                        <p className="mt-2 line-clamp-3 text-[0.71875rem] leading-relaxed text-muted-foreground">
                          {userFacingStoredErrorMessage(
                            figure.error,
                            "The visual could not be generated. Please edit the brief and try again.",
                          )}
                        </p>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => reuse(figure)}
                          className="mt-2 h-7 rounded-full px-3 text-[0.71875rem]"
                        >
                          <RefreshCcw className="size-3" />
                          Edit and retry
                        </Button>
                      </div>
                    ) : (
                      <div
                        data-figure-card-frame
                        className="grid h-full w-full min-h-0 min-w-0 place-items-center overflow-hidden rounded-xl border border-border/60 bg-white p-1.5 shadow-sm"
                      >
                        <FigureImage
                          figure={figure}
                          onClick={() => setSelected(figure)}
                          className="min-h-0 min-w-0 max-h-full max-w-full cursor-zoom-in"
                        />
                      </div>
                    )}
                  </div>
                  <div className="border-t border-border/60 p-3.5">
                    <RepositoryFigureBadge figure={figure} />
                    {figure.config.source && (
                      <p className="mb-2 flex items-center gap-1.5 truncate text-[0.65625rem] text-moss">
                        <BookMarked className="size-3 shrink-0" />
                        <span className="truncate">{figure.config.source.paper_title}</span>
                        <span className="shrink-0">· p. {figure.config.source.page}</span>
                      </p>
                    )}
                    {renaming === figure.public_id ? (
                      <input
                        autoFocus
                        value={nameDraft}
                        onChange={(event) => setNameDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") event.currentTarget.blur();
                          if (event.key === "Escape") {
                            setNameDraft(figure.config.title ?? figure.prompt);
                            event.currentTarget.blur();
                          }
                        }}
                        onBlur={() => commitRename(figure)}
                        aria-label="Figure name"
                        className="w-full rounded-md border border-moss/40 bg-background px-1.5 py-0.5 text-[0.78125rem] font-medium text-foreground outline-none"
                      />
                    ) : (
                      <p
                        className="truncate text-[0.78125rem] font-medium text-foreground"
                        title={figure.config.title ?? figure.prompt}
                      >
                        {figure.config.title ?? figure.prompt}
                      </p>
                    )}
                    <div className="mt-1.5 flex h-7 items-center justify-between gap-2">
                      <p className="min-w-0 truncate font-mono text-[0.625rem] uppercase tracking-[0.12em] text-muted-foreground">
                        {figure.config?.resolution?.toUpperCase() ?? "2K"} · {figure.config?.aspect_ratio ?? "4:3"} · {figure.run ? figure.run.label : formatDate(figure.created_at)}
                      </p>
                      <div className="flex shrink-0 items-center gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => {
                            setNameDraft(figure.config.title ?? figure.prompt);
                            setRenaming(figure.public_id);
                          }}
                          aria-label="Rename figure"
                          className="size-7 rounded-lg text-muted-foreground hover:text-foreground"
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                        {attachMenu(figure)}
                        {figure.config.source && (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => void downloadFigureSource(figure.public_id)}
                            aria-label="Download source citation"
                            className="size-7 rounded-lg text-muted-foreground hover:text-foreground"
                          >
                            <BookMarked className="size-3.5" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          disabled={figure.status !== "ok"}
                          onClick={() => void download(figure)}
                          aria-label="Download PNG"
                          className="size-7 rounded-lg text-muted-foreground hover:text-foreground"
                        >
                          <Download className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setConfirmDelete(figure.public_id)}
                          aria-label="Delete figure"
                          className="size-7 rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDeleteDialog
        target={
          confirmDelete === null
            ? null
            : {
                title: "Delete this figure?",
                description:
                  "The figure and its rendered files will be removed from the workspace. This cannot be undone.",
                action: "Delete figure",
                cancel: "Keep figure",
              }
        }
        pending={remove.isPending}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && remove.mutate(confirmDelete)}
      />

      <Dialog
        open={selected !== null}
        onOpenChange={(open) => !open && setSelected(null)}
      >
        <DialogContent className="sm:max-w-4xl">
          {selected ? (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  <Shapes className="size-4 shrink-0 text-moss" />
                  <input
                    value={dialogTitleDraft}
                    maxLength={160}
                    aria-label="Figure name"
                    placeholder={`Name this figure… (Visual ${selected.public_id})`}
                    onChange={(event) => setDialogTitleDraft(event.target.value)}
                    onBlur={() => {
                      const title = dialogTitleDraft.trim();
                      if (title !== (selected.config.title ?? "")) {
                        rename.mutate({ id: selected.public_id, title });
                      }
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") event.currentTarget.blur();
                      if (event.key === "Escape") {
                        setDialogTitleDraft(selected.config.title ?? "");
                        event.currentTarget.blur();
                      }
                    }}
                    className="w-full min-w-0 bg-transparent outline-none placeholder:font-normal placeholder:text-muted-foreground/60"
                  />
                  {selected.config.is_example ? (
                    <span className="shrink-0 rounded-full bg-accent px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-moss">
                      Example
                    </span>
                  ) : null}
                </DialogTitle>
                <DialogDescription className="line-clamp-3">
                  {selected.prompt}
                </DialogDescription>
              </DialogHeader>
              <div className="grid max-h-[62vh] place-items-center overflow-hidden rounded-xl border border-border bg-white p-3 dark:bg-[#151817]">
                <FigureImage figure={selected} className="max-h-[58vh]" />
              </div>
              {selectedRepository && me ? (
                <RepositoryFigureProvenanceDetail figure={selected} userId={me.user_id} />
              ) : null}
              {selected.config.pipeline?.length ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  {selected.config.pipeline.map((stage) => {
                    const stageStatus =
                      selected.status === "ok"
                      && stage.status !== "failed"
                      && stage.status !== "skipped"
                        ? "completed"
                        : stage.status;
                    return (
                      <span
                        key={stage.id}
                        title={
                          stageStatus === "failed"
                            ? userFacingStoredErrorMessage(
                                stage.detail,
                                "This step could not be completed. Try the figure again.",
                              )
                            : stage.detail
                        }
                        className={cn(
                          "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.6875rem]",
                          stageStatus === "completed"
                            ? "border-moss/25 bg-accent/55 text-foreground"
                            : stageStatus === "failed"
                              ? "border-destructive/25 bg-destructive/5 text-destructive"
                              : "border-border bg-secondary/45 text-muted-foreground",
                        )}
                      >
                        <span
                          className={cn(
                            "size-1.5 rounded-full",
                            stageStatus === "completed"
                              ? "bg-moss-surface"
                              : stageStatus === "failed"
                                ? "bg-destructive"
                                : "bg-muted-foreground/35",
                          )}
                        />
                        {stage.label}
                      </span>
                    );
                  })}
                </div>
              ) : null}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p
                  title={selected.run?.label}
                  className="min-w-0 max-w-[22rem] truncate font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground"
                >
                  {selected.run
                    ? `grounded in "${selected.run.label}"`
                    : selectedRepository
                      ? selectedRepository.repository_access === "private"
                        ? "private GitHub repository · verified derived spec"
                        : `styled from verified repository spec · ${selectedRepository.owner}/${selectedRepository.name}${selectedRepository.commit_sha ? `@${selectedRepository.commit_sha.slice(0, 10)}` : ""}`
                    : selected.config.source
                      ? `source: ${selected.config.source.paper_title}, p. ${selected.config.source.page}`
                    : selected.config.dataset_name
                      ? `grounded in "${selected.config.dataset_name}"`
                      : "no source linked"}
                </p>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  {selectedRepository?.analysis_id && selectedRepository.repository_access !== "private" ? (
                    REPOSITORY_VISUAL_SOURCE_ENABLED ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setManuscriptAnalysisId(selectedRepository.analysis_id!);
                          setSelected(null);
                        }}
                        className="h-7 rounded-full px-3 text-[0.71875rem]"
                      >
                        <FileText className="size-3" />
                        {me?.language === "de" ? "Manuskript-Text" : "Manuscript copy"}
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled
                        aria-disabled="true"
                        title={me?.language === "de"
                          ? "Dieses Feature wird noch getestet."
                          : "This feature is still being tested."}
                        className="h-7 cursor-not-allowed rounded-full border-dashed px-3 text-[0.71875rem] text-muted-foreground opacity-60"
                      >
                        <FileText className="size-3" />
                        {me?.language === "de" ? "Manuskript-Text · In Erprobung" : "Manuscript copy · In testing"}
                      </Button>
                    )
                  ) : null}
                  {attachMenu(selected, "end")}
                  {selected.config.source && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void downloadFigureSource(selected.public_id)}
                      className="h-7 rounded-full px-3 text-[0.71875rem]"
                    >
                      <BookMarked className="size-3" />
                      Source
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void download(selected)}
                    className="h-7 rounded-full px-3 text-[0.71875rem]"
                  >
                    <Download className="size-3" />
                    PNG
                  </Button>
                  {selected.config.kind !== "source" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => reuse(selected)}
                      className="h-7 rounded-full px-3 text-[0.71875rem]"
                    >
                      <RefreshCcw className="size-3" />
                      Reuse prompt
                    </Button>
                  ) : null}
                  {selected.status === "ok" && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setGroundRun(null);
                        setGroundDataset(null);
                        setRepositoryGrounding(null);
                        setSourceDraft({
                          kind: "figure",
                          id: selected.public_id,
                          preview: null,
                          label: selected.config.title || selected.prompt.slice(0, 80),
                        });
                        setSelected(null);
                        window.scrollTo({ top: 0, behavior: "smooth" });
                        promptRef.current?.focus();
                      }}
                      className="h-7 rounded-full px-3 text-[0.71875rem]"
                    >
                      <Wand2 className="size-3" />
                      Redraw
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setConfirmDelete(selected.public_id)}
                    className="h-7 rounded-full border-destructive/30 px-3 text-[0.71875rem] text-destructive hover:bg-destructive/10 hover:text-destructive"
                  >
                    <Trash2 className="size-3" />
                    Delete
                  </Button>
                </div>
              </div>
            </>
          ) : null}
        </DialogContent>
      </Dialog>

      {me && REPOSITORY_VISUAL_SOURCE_ENABLED ? (
        <RepositoryManuscriptDialog
          analysisId={manuscriptAnalysisId}
          userId={me.user_id}
          german={me.language === "de"}
          open={manuscriptAnalysisId !== null}
          onOpenChange={(open) => {
            if (!open) setManuscriptAnalysisId(null);
          }}
        />
      ) : null}
    </div>
  );
}
