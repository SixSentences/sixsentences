"use client";
import { AiInteractionNotice } from "@/components/ai-interaction-notice";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  BarChart3,
  Check,
  ChevronDown,
  Clipboard,
  FileSpreadsheet,
  FileUp,
  History,
  Image as ImageIcon,
  Loader2,
  PenLine,
  Pencil,
  SendHorizontal,
  Shapes,
  Square,
} from "lucide-react";
import { toast } from "sonner";

import {
  AgentActivityTimeline,
  AgentTimelineHandoffs,
  AgentTurnElapsed,
  SpecialistCompletedTurn,
  agentTurnIdsFromTimelines,
  useAgentTimelineLedger,
} from "@/components/agent-work-status";
import {
  AgentTurnQueue,
  useAgentTurnQueue,
} from "@/components/agent/agent-turn-queue";
import {
  isClearChatCommand,
  SpecialistChatResetButton,
  SpecialistChatResetDialog,
  useSpecialistChatReset,
} from "@/components/agent/specialist-chat-reset";
import { WorkspaceActionList } from "@/components/agent/workspace-action-card";
import { DetailError } from "@/components/detail-error";
import { InlineTitle } from "@/components/inline-title";
import {
  MobileWorkspaceSwitch,
  type MobileWorkspacePane,
} from "@/components/mobile-workspace-switch";
import { ResizableWorkspaceSplit } from "@/components/workspace/resizable-workspace-split";

import ModelPicker from "@/components/search/model-picker";
import { usePrivateModelPreference } from "@/hooks/use-private-model-preference";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  SpecialistStreamError,
  api,
  createSpecialistTurnId,
  fileToBase64,
} from "@/lib/api";
import { useActiveProject } from "@/lib/project-context";
import {
  useDurableSpecialistTurn,
  useSpecialistTurnControl,
} from "@/hooks/use-durable-specialist-turn";
import { useAuth } from "@/lib/auth";
import type {
  DatasetChatAction,
  DatasetChatReply,
  DatasetMessage,
  ResearchDataset,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPTED_EXTENSIONS = [".csv", ".tsv", ".tab", ".json", ".xlsx"];
const MAX_FILE_BYTES = 100 * 1024 * 1024;

const STARTERS = [
  {
    label: ["Daten zusammenfassen", "Summarise the data"],
    prompt: [
      "Gib mir eine belegte Zusammenfassung dieses Datensatzes: Struktur, Datenqualität und die drei interessantesten Muster.",
      "Give me a grounded summary of this dataset: structure, data quality and the three most interesting patterns.",
    ],
  },
  {
    label: ["Datenqualität prüfen", "Check data quality"],
    prompt: [
      "Analysiere fehlende Werte und Verteilungen und markiere alles, was ein sorgfältiger Reviewer hinterfragen würde.",
      "Analyse missingness and distributions, and flag anything a careful reviewer would question.",
    ],
  },
  {
    label: ["Kernergebnis visualisieren", "Chart the key result"],
    prompt: [
      "Erstelle einen exakten Chart für den entscheidungsrelevantesten numerischen Zusammenhang in diesem Datensatz.",
      "Create an exact chart of the most decision-relevant numeric relationship in this dataset.",
    ],
  },
  {
    label: ["Für Methodik beschreiben", "Describe for methods"],
    prompt: [
      "Schreibe einen kurzen, methodentauglichen Absatz, der diesen Datensatz mit exakten Anzahlen beschreibt.",
      "Write a short methods-ready paragraph describing this dataset with exact counts.",
    ],
  },
] as const;

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function fmtNumber(value: unknown): string {
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return String(value ?? "—");
  return String(Number(num.toFixed(4)));
}

/** Deterministic recipe output, rendered per recipe shape. */
function AnalysisResultView({
  result,
  isGerman,
}: {
  result: Record<string, unknown>;
  isGerman: boolean;
}) {
  const kind = String(result.kind ?? "");
  if (kind === "descriptive") {
    return (
      <div className="grid grid-cols-3 gap-1.5">
        {(
          [
            ["n", result.n],
            ["mean", result.mean],
            ["median", result.median],
            ["sd", result.sd],
            ["min", result.min],
            ["max", result.max],
          ] as [string, unknown][]
        ).map(([label, value]) => (
          <div key={label} className="rounded-lg bg-secondary/55 px-2 py-1.5 text-center">
            <p className="font-mono text-[0.75rem] text-foreground">{fmtNumber(value)}</p>
            <p className="text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">{label}</p>
          </div>
        ))}
      </div>
    );
  }
  if (kind === "missingness") {
    const columns = (result.columns as { name: string; missing: number; percent: number }[]) ?? [];
    return (
      <div className="space-y-1">
        {columns.filter((column) => column.missing > 0).slice(0, 8).map((column) => (
          <div key={column.name} className="flex items-baseline justify-between gap-3 text-[0.6875rem]">
            <span className="min-w-0 truncate font-mono text-foreground">{column.name}</span>
            <span className="shrink-0 text-muted-foreground">{column.missing} · {column.percent}%</span>
          </div>
        ))}
        {columns.every((column) => column.missing === 0) && (
          <p className="text-[0.6875rem] text-moss">
            {isGerman ? "Keine fehlenden Werte in den profilierten Datensätzen." : "No missing values in the profiled records."}
          </p>
        )}
      </div>
    );
  }
  if (kind === "group_summary") {
    const groups = (result.groups as { group: string; n: number; value: number }[]) ?? [];
    return (
      <div>
        <p className="mb-1.5 text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          {String(result.value_column)} by {String(result.group_by)} · {String(result.metric)}
        </p>
        <div className="space-y-1">
          {groups.slice(0, 10).map((group) => (
            <div key={group.group} className="flex items-baseline justify-between gap-3 text-[0.6875rem]">
              <span className="min-w-0 truncate text-foreground">{group.group}</span>
              <span className="shrink-0 font-mono text-muted-foreground">{fmtNumber(group.value)} · n={group.n}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (kind === "correlation") {
    return (
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="font-display text-2xl text-foreground">r = {fmtNumber(result.pearson_r)}</p>
          <p className="mt-0.5 text-[0.6875rem] text-muted-foreground">
            {String(result.x_column)} vs {String(result.y_column)} · n={fmtNumber(result.n)}
          </p>
        </div>
      </div>
    );
  }
  if (kind === "meta_analysis") {
    const ci = (result.ci_95 as number[]) ?? [];
    return (
      <div className="space-y-1 text-[0.6875rem]">
        <p className="font-display text-xl text-foreground">{fmtNumber(result.pooled_effect)}</p>
        <p className="text-muted-foreground">
          95% CI [{ci.map(fmtNumber).join(", ")}] · I² {fmtNumber(result.i_squared)}% · k={fmtNumber(result.k)}
        </p>
        <p className="text-[0.625rem] text-muted-foreground/80">{String(result.model)}</p>
      </div>
    );
  }
  return (
    <pre className="max-h-48 overflow-auto rounded-lg bg-secondary/55 p-2 font-mono text-[0.625rem]">
      {JSON.stringify(result, null, 2)}
    </pre>
  );
}

export default function DatasetWorkspacePage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const datasetId = params.id;
  const focusedAnalysisId = searchParams.get("analysis") ?? "";
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useActiveProject();
  const chatEndRef = useRef<HTMLDivElement>(null);
  const versionInputRef = useRef<HTMLInputElement>(null);
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const t = (de: string, en: string) => (isGerman ? de : en);

  const [chatInput, setChatInput] = useState("");
  const [mobilePane, setMobilePane] = useState<MobileWorkspacePane>("workspace");
  const [agentWorking, setAgentWorking] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const {
    events: agentEvents,
    handoffs: agentTimelineHandoffs,
    recordAgentEvent,
    startAgentTurn,
    handoffAgentTurn,
    resetAgentTimeline,
  } = useAgentTimelineLedger();
  const {
    activeTurnId,
    turnAccepted,
    stopping,
    beginLocalTurn,
    recoverTurn,
    finishTurn,
    stopTurn,
  } = useSpecialistTurnControl();
  const [renderedVisuals, setRenderedVisuals] = useState<Record<string, string>>({});
  const [profileEditing, setProfileEditing] = useState(false);
  const [editDescription, setEditDescription] = useState("");
  const [editProvenance, setEditProvenance] = useState("");
  const [editLicense, setEditLicense] = useState("");

  // which model answers; persisted like the writer's composer choice
  const [model, pickModel] = usePrivateModelPreference("six:dataset-model");

  const { data: dataset, isLoading, error: loadError } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => api.dataset(datasetId),
  });
  const { data: history } = useQuery({
    queryKey: ["dataset-chat", datasetId],
    queryFn: () => api.datasetChatHistory(datasetId),
  });
  const { data: versions } = useQuery({
    queryKey: ["dataset-versions", datasetId],
    queryFn: () => api.datasetVersions(datasetId),
  });
  const { data: writerDocs } = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
  });

  useEffect(() => {
    if (dataset) {
      if (dataset.project_id) setActiveProjectId(dataset.project_id);
      setEditDescription(dataset.description);
      setEditProvenance(dataset.provenance);
      setEditLicense(dataset.license);
    }
  }, [dataset, setActiveProjectId]);
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, pendingQuestion]);
  useEffect(() => {
    if (!focusedAnalysisId) return;
    document
      .getElementById(`analysis-${focusedAnalysisId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusedAnalysisId, history]);

  const chat = useMutation({
    mutationFn: (question: string) => {
      const turnId = createSpecialistTurnId();
      return api.datasetChatStream(
        datasetId,
        question,
        model,
        recordAgentEvent,
        beginLocalTurn(turnId),
      );
    },
    onMutate: (question) => {
      setAgentWorking(true);
      startAgentTurn();
      setPendingQuestion(question);
    },
    onSuccess: async (result) => {
      queryClient.setQueryData(["dataset", datasetId], result.dataset);
      const refreshes = [
        queryClient.invalidateQueries({ queryKey: ["dataset-chat", datasetId] }),
        queryClient.invalidateQueries({ queryKey: ["datasets"] }),
      ];
      if (result.actions.some((action) => action.applied && action.operation === "create_chart")) {
        refreshes.push(queryClient.invalidateQueries({ queryKey: ["figures"] }));
      }
      await Promise.allSettled(refreshes);
      handoffAgentTurn();
      finishTurn();
      setAgentWorking(false);
      setPendingQuestion(null);
    },
    onError: async (error) => {
      await queryClient.refetchQueries({ queryKey: ["dataset-chat", datasetId] });
      handoffAgentTurn();
      finishTurn();
      setAgentWorking(false);
      setPendingQuestion(null);
      if (!(error instanceof SpecialistStreamError && error.kind === "cancelled")) {
        toast.error(error instanceof Error ? error.message : t("Analyse fehlgeschlagen.", "Analysis failed."));
      }
    },
  });
  const renderVisual = useMutation({
    mutationFn: (prompt: string) =>
      api.figureCreate(prompt, null, "auto", {
        kind: "concept",
        resolution: "2k",
        aspect_ratio: "4:3",
        review_passes: 1,
        dataset_id: datasetId,
      }),
    onSuccess: (figure, prompt) => {
      setRenderedVisuals((current) => ({
        ...current,
        [prompt]: figure.public_id,
      }));
      toast.success(t("Rendering gestartet. Der Visual-Lab-Arbeitsbereich ist bereit.", "Rendering started. The Visual Lab workspace is ready."));
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Das hat nicht funktioniert.", "That didn't work.")),
  });

  const metaChart = useMutation({
    mutationFn: (input: { kind: "forest" | "funnel"; definition: Record<string, unknown> }) =>
      api.datasetMetaChart(datasetId, {
        kind: input.kind,
        label_column: String(input.definition.label_column ?? ""),
        effect_column: String(input.definition.effect_column ?? ""),
        se_column: String(input.definition.se_column ?? ""),
        title: `${dataset?.name ?? "Dataset"} meta-analysis`,
      }),
    onSuccess: () => {
      toast.success(t("Exakter Plot im Visual Lab erstellt.", "Exact plot created in Visual Lab."));
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Plot konnte nicht erstellt werden.", "Plot creation failed.")),
  });

  const copyLatex = async (analysisId: string) => {
    try {
      const result = await api.analysisLatex(analysisId);
      await navigator.clipboard.writeText(result.latex);
      toast.success(t("LaTeX mit Live-Update-Markern kopiert.", "LaTeX copied, with live-update markers."));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("Kopieren fehlgeschlagen.", "Copy failed."));
    }
  };

  const linkToDoc = useMutation({
    mutationFn: async (docId: string) => {
      const doc = (writerDocs ?? []).find(
        (item) => (item.public_id ?? String(item.id)) === docId,
      );
      if (!doc) throw new Error(t("Dokument nicht gefunden.", "Document not found."));
      const linked = doc.dataset_ids ?? [];
      if (!linked.includes(datasetId)) {
        await api.writerPatch(doc.public_id ?? String(doc.id), {
          dataset_ids: [...linked, datasetId],
        });
      }
      return doc.title;
    },
    onSuccess: (title) => {
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      toast.success(t(`Analysen sind jetzt Live-Forschungsobjekte in „${title}“.`, `Analyses are live research objects in “${title}”.`));
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Verknüpfen fehlgeschlagen.", "Linking failed.")),
  });

  const profileDirty =
    dataset !== undefined &&
    (editDescription !== dataset.description ||
      editProvenance !== dataset.provenance ||
      editLicense !== dataset.license);
  const renameDataset = useMutation({
    mutationFn: (name: string) => api.datasetUpdate(datasetId, { name }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["dataset", datasetId], updated);
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Speichern fehlgeschlagen.", "Save failed.")),
  });
  const saveProfile = useMutation({
    mutationFn: () =>
      api.datasetUpdate(datasetId, {
        description: editDescription.trim(),
        provenance: editProvenance.trim(),
        license: editLicense.trim(),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["dataset", datasetId], updated);
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      setProfileEditing(false);
      toast.success(t("Datensatzdetails gespeichert.", "Dataset details saved."));
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Speichern fehlgeschlagen.", "Save failed.")),
  });

  const addVersion = useMutation({
    mutationFn: async (file: File) =>
      api.datasetVersionAdd(datasetId, file.name, await fileToBase64(file)),
    onSuccess: (result) => {
      toast.success(t(
        `Version ${result.version} hinzugefügt (${result.row_count.toLocaleString("de-DE")} Zeilen).`,
        `Version ${result.version} added (${result.row_count.toLocaleString("en-US")} rows).`,
      ));
      void queryClient.invalidateQueries({ queryKey: ["dataset", datasetId] });
      void queryClient.invalidateQueries({ queryKey: ["dataset-versions", datasetId] });
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Version konnte nicht hochgeladen werden.", "Version upload failed.")),
  });

  // manual exact chart controls
  const numeric = (dataset?.columns ?? []).filter((column) => column.type === "number");
  const [chartKind, setChartKind] = useState("bar");
  const [chartX, setChartX] = useState("");
  const [chartY, setChartY] = useState("");
  const [chartTitle, setChartTitle] = useState("");
  useEffect(() => {
    if (dataset && !chartX) {
      const firstY = numeric[0]?.name ?? "";
      setChartX(dataset.columns.find((column) => column.name !== firstY)?.name ?? "");
      setChartY(firstY);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset]);
  const chart = useMutation({
    mutationFn: (input?: { x: string; y: string; kind: string; title: string }) =>
      api.datasetChart(datasetId, {
        x_column: input?.x ?? chartX,
        y_column: input?.y ?? chartY,
        kind: input?.kind ?? chartKind,
        title: input?.title ?? chartTitle,
      }),
    onSuccess: () => {
      toast.success(t("Exakter Chart im Visual Lab erstellt.", "Exact chart created in Visual Lab."));
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : t("Chart konnte nicht erstellt werden.", "Chart creation failed.")),
  });

  const chatMessages: DatasetMessage[] = history ?? [];
  const persistedAgentTimelines = chatMessages
    .filter((message) => message.role === "assistant")
    .map((message) => message.payload.agent_events ?? []);
  const { checking: checkingAgentTurn, recovering: recoveringAgentTurn } =
    useDurableSpecialistTurn<DatasetChatReply>({
    resourceKind: "dataset",
    resourceId: datasetId,
    enabled: history !== undefined,
    persistedTurnIds: agentTurnIdsFromTimelines(persistedAgentTimelines),
    onStarted: (turn) => {
      recoverTurn(turn.turn_id);
      startAgentTurn();
      setAgentWorking(true);
      setPendingQuestion(null);
    },
    onEvent: recordAgentEvent,
    onTerminal: async (result, turn) => {
      if (result?.dataset) {
        queryClient.setQueryData(["dataset", datasetId], result.dataset);
      }
      await Promise.allSettled([
        queryClient.refetchQueries({ queryKey: ["dataset-chat", datasetId] }),
        queryClient.invalidateQueries({ queryKey: ["dataset", datasetId] }),
        queryClient.invalidateQueries({ queryKey: ["datasets"] }),
        queryClient.invalidateQueries({ queryKey: ["figures"] }),
      ]);
      handoffAgentTurn();
      finishTurn(turn.turn_id);
      setAgentWorking(false);
      setPendingQuestion(null);
    },
    onError: (error) => {
      setAgentWorking(false);
      toast.error(
        error instanceof Error
          ? error.message
          : t("Die laufende Analyse konnte noch nicht wiederhergestellt werden.", "The running analysis could not be recovered yet."),
      );
    },
    });
  const specialistBusy =
    agentWorking
    || Boolean(activeTurnId)
    || history === undefined
    || checkingAgentTurn
    || recoveringAgentTurn;
  const queuedChat = useAgentTurnQueue<string>({
    working: specialistBusy,
    run: (question) => chat.mutate(question),
  });
  const {
    clearChat,
    clearing,
    confirmationOpen,
    setConfirmationOpen,
    confirmClearChat,
    clearChatTriggerRef,
  } = useSpecialistChatReset({
    resourceKind: "dataset",
    resourceId: datasetId,
    queryKey: ["dataset-chat", datasetId],
    hasHistory: chatMessages.length > 0,
    onCleared: () => {
      setChatInput("");
      setPendingQuestion(null);
      queuedChat.clear();
      resetAgentTimeline();
    },
  });
  const stopAgentTurn = async () => {
    try {
      await stopTurn();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("Der Agent konnte nicht gestoppt werden.", "The agent could not be stopped."));
    }
  };

  function actionCard(action: DatasetChatAction, key: string) {
    if (action.operation === "run_analysis") {
      return (
        <div
          key={key}
          id={action.analysis_id ? `analysis-${action.analysis_id}` : undefined}
          data-analysis-id={action.analysis_id}
          className={cn(
            "rounded-2xl border px-3.5 py-3",
            action.applied
              ? "border-moss/25 bg-accent/40"
              : "border-amber-500/30 bg-amber-50 dark:bg-amber-300/10",
            action.analysis_id === focusedAnalysisId && "ring-2 ring-moss/45 ring-offset-2 ring-offset-background",
          )}
        >
          <p className="mb-1.5 flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
            <BarChart3 className="size-3 text-moss" /> {action.label}
          </p>
          {action.applied && action.result ? (
            <>
              <AnalysisResultView result={action.result} isGerman={isGerman} />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {action.kind === "meta_analysis" && action.definition && (["forest", "funnel"] as const).map((plotKind) => (
                  <Button
                    key={plotKind}
                    variant="outline"
                    size="sm"
                    className="h-7 rounded-full px-3 text-[0.6875rem]"
                    disabled={metaChart.isPending}
                    onClick={() =>
                      metaChart.mutate({
                        kind: plotKind,
                        definition: action.definition ?? {},
                      })
                    }
                  >
                    {metaChart.isPending ? <Loader2 className="size-3 animate-spin" /> : <BarChart3 className="size-3" />}
                    {plotKind === "forest" ? "Forest plot" : "Funnel plot"}
                  </Button>
                ))}
                {action.analysis_id && (
                  <>
                    <Button asChild variant="outline" size="sm" className="h-7 rounded-full px-3 text-[0.6875rem]">
                      <Link href={`/data/${datasetId}?analysis=${encodeURIComponent(action.analysis_id)}`}>
                        {t("Tabelle öffnen", "Open table")} <ArrowUpRight className="size-3" />
                      </Link>
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 rounded-full px-3 text-[0.6875rem]"
                      onClick={() => void copyLatex(action.analysis_id ?? "")}
                    >
                      <Clipboard className="size-3" /> {t("LaTeX kopieren", "Copy LaTeX")}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="outline" size="sm" className="h-7 rounded-full px-3 text-[0.6875rem]">
                          <PenLine className="size-3" /> {t("Zum Manuskript", "To manuscript")} <ChevronDown className="size-3" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-[16rem]">
                        <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                          {t("Live-Forschungsobjekt in", "Live research object in")}
                        </DropdownMenuLabel>
                        {(writerDocs ?? []).length === 0 ? (
                          <p className="px-2 py-3 text-[0.75rem] leading-relaxed text-muted-foreground">
                            {t("Noch keine Manuskripte. Erstelle zuerst eines im Writer.", "No manuscripts yet. Create one in the Writer first.")}
                          </p>
                        ) : (
                          (writerDocs ?? []).slice(0, 10).map((doc) => (
                            <DropdownMenuItem
                              key={doc.id}
                              disabled={linkToDoc.isPending}
                              onSelect={() => linkToDoc.mutate(doc.public_id ?? String(doc.id))}
                            >
                              <span className="truncate">{doc.title}</span>
                            </DropdownMenuItem>
                          ))
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </>
                )}
              </div>
            </>
          ) : (
            <p className="text-[0.6875rem] text-amber-800">{action.detail}</p>
          )}
        </div>
      );
    }
    if (action.operation === "create_chart") {
      return (
        <div key={key} className={cn("rounded-2xl border px-3.5 py-3", action.applied ? "border-moss/25 bg-accent/40" : "border-amber-500/30 bg-amber-50 dark:bg-amber-300/10")}>
          <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
            <BarChart3 className="size-3 text-moss" /> {action.label}
          </p>
          {action.applied ? (
            <Button asChild variant="outline" size="sm" className="mt-2 h-7 rounded-full px-3 text-[0.6875rem]">
              <Link
                href={
                  action.figure_id
                    ? `/figures?figure=${encodeURIComponent(action.figure_id)}`
                    : "/figures"
                }
              >
                {t("Chart öffnen", "Open chart")} <ArrowUpRight className="size-3" />
              </Link>
            </Button>
          ) : (
            <p className="mt-1 text-[0.6875rem] text-amber-800">{action.detail}</p>
          )}
        </div>
      );
    }
    if (action.operation === "render_visual") {
      const visualPrompt = action.prompt ?? "";
      const renderedVisualId = renderedVisuals[visualPrompt];
      return (
        <div key={key} className="rounded-2xl border border-border bg-secondary/35 px-3.5 py-3">
          <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
            <ImageIcon className="size-3 text-moss" /> {t("Visual-Briefing", "Visual brief")}
          </p>
          <p className="mt-1 line-clamp-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
            {action.prompt}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              className="h-7 rounded-full px-3 text-[0.6875rem]"
              disabled={renderVisual.isPending}
              onClick={() => renderVisual.mutate(visualPrompt)}
            >
              {renderVisual.isPending ? <Loader2 className="size-3 animate-spin" /> : <Shapes className="size-3" />}
              {renderedVisualId ? t("Weiteres rendern", "Render another") : t("Rendering bestätigen", "Confirm render")}
            </Button>
            {renderedVisualId ? (
              <Button asChild variant="outline" size="sm" className="h-7 rounded-full px-3 text-[0.6875rem]">
                <Link href={`/figures?focus=${encodeURIComponent(renderedVisualId)}`}>
                  {t("Öffnen und fortfahren", "Open and continue")}
                  <ArrowUpRight className="size-3" />
                </Link>
              </Button>
            ) : null}
          </div>
        </div>
      );
    }
    if (action.operation === "set_profile") {
      return (
        <div key={key} className="rounded-2xl border border-moss/25 bg-accent/40 px-3.5 py-2 text-[0.6875rem] text-foreground">
          <span className="flex items-center gap-1.5">
            <Check className="size-3 text-moss" /> {action.label}
          </span>
        </div>
      );
    }
    return null;
  }

  if (loadError) {
    return (
      <DetailError
        title={t("Datensatz nicht verfügbar", "Dataset unavailable")}
        error={loadError}
        fallback={t("Dieser Datensatz konnte nicht geladen werden.", "This dataset could not be loaded.")}
        backHref="/data"
        backLabel={t("Zurück zum Data Hub", "Back to Data Hub")}
      />
    );
  }
  if (isLoading || !dataset) {
    return <div className="grid min-h-0 flex-1 place-items-center"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background md:rounded-t-2xl">
      <SpecialistChatResetDialog
        open={confirmationOpen}
        clearing={clearing}
        returnFocusRef={clearChatTriggerRef}
        onOpenChange={setConfirmationOpen}
        onConfirm={() => void confirmClearChat()}
      />
      <header className="flex min-h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2 sm:gap-4 sm:px-5 lg:px-7">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Button asChild variant="ghost" size="icon" className="size-8 rounded-full">
            <Link href="/data" aria-label={t("Zurück zum Data Hub", "Back to Data Hub")}>
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
          <div className="min-w-0 flex-1">
            <InlineTitle
              value={dataset.name}
              onCommit={(next) => renameDataset.mutate(next)}
              ariaLabel={t("Name des Datensatzes", "Dataset name")}
              placeholder={t("Unbenannter Datensatz", "Untitled dataset")}
            />
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
              {dataset.format || t("leer", "empty")} · {dataset.row_count.toLocaleString(isGerman ? "de-DE" : "en-US")} {t("Zeilen", "rows")} · {dataset.column_count} {t("Spalten", "columns")}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={versionInputRef}
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                if (file.size > MAX_FILE_BYTES) toast.error(t("Versionen sind auf 100 MB pro Datei begrenzt.", "Versions are limited to 100 MB per file."));
                else addVersion.mutate(file);
              }
              event.target.value = "";
            }}
          />
          <Button
            variant="outline"
            className="h-9 rounded-full px-3"
            disabled={addVersion.isPending}
            onClick={() => versionInputRef.current?.click()}
            aria-label={t("Datensatzversion hinzufügen", "Add dataset version")}
          >
            {addVersion.isPending ? <Loader2 className="size-4 animate-spin" /> : <FileUp className="size-4" />}
            <span className="hidden sm:inline">{t("Version hinzufügen", "Add version")}</span>
          </Button>
        </div>
      </header>

      <ResizableWorkspaceSplit
        storageKey="six:data-workspace-split"
        label={t("Datenassistent und Datenbereich skalieren", "Resize data assistant and data workspace")}
        mobileSwitch={(
          <MobileWorkspaceSwitch
            value={mobilePane}
            onChange={setMobilePane}
            workspaceLabel={t("Datenbereich", "Data workspace")}
          />
        )}
      >
        {/* chat rail: the agent answers from the exact profile and acts through
            deterministic recipes; the user steers */}
        <aside
          className={cn(
            "min-h-0 flex-col border-b border-border bg-card/45 group-data-[workspace-layout=split]/workspace:!flex group-data-[workspace-layout=split]/workspace:border-b-0",
            mobilePane === "agent" ? "flex" : "hidden",
          )}
        >
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
            {chatMessages.length === 0 && !specialistBusy && (
              <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
                <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  {t("Dein Data Agent", "Your Data Agent")}
                </p>
                <p className="max-w-[17rem] text-[0.78125rem] leading-relaxed text-muted-foreground">
                  {t(
                    "Frage nach Zusammenfassungen, Qualitätsprüfungen, exakten Analysen oder Charts. Jede Zahl stammt aus deterministischen Berechnungen über deine Zeilen, nie aus der Arithmetik des Modells.",
                    "Ask for summaries, quality checks, exact analyses or charts. Every number comes from deterministic calculations over your rows, never from the model's arithmetic.",
                  )}
                </p>
              </div>
            )}
            {chatMessages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[85%] overflow-hidden rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                    <div className="whitespace-pre-wrap">{message.content}</div>
                  </div>
                </div>
              ) : (
                <div key={message.id} className="min-w-0 max-w-full space-y-2.5">
                  <SpecialistCompletedTurn
                    kind="dataset"
                    events={message.payload.agent_events}
                    answer={message.content}
                    artifacts={message.payload.artifacts}
                  >
                    {message.payload.actions?.length ? (
                      <div className="space-y-2">
                        {message.payload.actions.map((action, actionIndex) =>
                          actionCard(action, `${message.id}-${actionIndex}`),
                        )}
                      </div>
                    ) : null}
                    <WorkspaceActionList
                      actions={message.payload.workspace_actions}
                      compact
                    />
                  </SpecialistCompletedTurn>
                </div>
              ),
            )}
            <AgentTimelineHandoffs
              handoffs={agentTimelineHandoffs}
              persistedTimelines={persistedAgentTimelines}
            />
            {pendingQuestion &&
              !chatMessages
                .slice(-2)
                .some((message) => message.role === "user" && message.content === pendingQuestion) && (
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                  <p className="whitespace-pre-wrap">{pendingQuestion}</p>
                </div>
              </div>
            )}
            {specialistBusy && (
              <div className="min-w-0 max-w-full">
                <AgentTurnElapsed className="mb-2" />
                <AgentActivityTimeline events={agentEvents} running />
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
<div className="shrink-0 border-t border-border/60 p-3">
            <AiInteractionNotice />
            {chatMessages.length === 0 && !specialistBusy && (
              <div className="mb-2 flex max-w-full gap-1.5 overflow-x-auto pb-0.5">
                {STARTERS.map((starter) => (
                  <button
                    key={starter.label[1]}
                    type="button"
                    onClick={() => chat.mutate(starter.prompt[isGerman ? 0 : 1])}
                    title={starter.prompt[isGerman ? 0 : 1]}
                    className="shrink-0 cursor-pointer rounded-full border border-border bg-card px-2.5 py-1 text-[0.65625rem] text-muted-foreground transition-colors hover:border-moss/40 hover:text-moss"
                  >
                    {starter.label[isGerman ? 0 : 1]}
                  </button>
                ))}
              </div>
            )}
          <AgentTurnQueue items={queuedChat.queue} onRemove={queuedChat.remove} />
          <form
              method="post"
              className="flex items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                const question = chatInput.trim();
                if (!question) return;
                if (isClearChatCommand(question)) {
                  void clearChat();
                  return;
                }
                if (specialistBusy) return;
                setChatInput("");
                queuedChat.submit(question, question);
              }}
            >
              <SpecialistChatResetButton
                onClick={() => void clearChat()}
                clearing={clearing}
                triggerRef={clearChatTriggerRef}
                disabled={specialistBusy}
              />
              <div className="flex shrink-0 items-center gap-1 self-end pb-0.5">
                <ModelPicker value={model} onChange={pickModel} compact />
              </div>
              <Textarea
                value={chatInput}
                onChange={(event) => setChatInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    const question = chatInput.trim();
                    if (!question) return;
                    if (isClearChatCommand(question)) {
                      void clearChat();
                      return;
                    }
                    if (specialistBusy) return;
                    setChatInput("");
                    queuedChat.submit(question, question);
                  }
                }}
                placeholder={t("Zusammenfassen, Qualität prüfen, analysieren, visualisieren…", "Summarise, check quality, analyse, chart…")}
                rows={1}
                className="min-h-9 min-w-0 flex-1 resize-none rounded-xl bg-transparent py-2 text-[0.8125rem] dark:bg-transparent"
              />
              <Button
                type={activeTurnId ? "button" : "submit"}
                size="icon"
                className="size-9 shrink-0 rounded-full"
                disabled={activeTurnId ? !turnAccepted || stopping : !chatInput.trim() || specialistBusy}
                aria-label={activeTurnId ? t("Agentenlauf stoppen", "Stop agent turn") : t("Senden", "Send")}
                onClick={activeTurnId ? () => void stopAgentTurn() : undefined}
              >
                {stopping ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : activeTurnId ? (
                  <Square className="size-3.5 fill-current" />
                ) : (
                  <SendHorizontal className="size-4" />
                )}
              </Button>
            </form>
          </div>
        </aside>

        <main
          className={cn(
            "min-h-0 overflow-y-auto group-data-[workspace-layout=split]/workspace:!block",
            mobilePane === "workspace" ? "block" : "hidden",
          )}
        >
          <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-5 sm:px-5 sm:py-6 lg:px-7 lg:py-8">
            <section aria-labelledby="dataset-overview-heading" className="space-y-4">
              <div>
                <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-moss">
                  {t("Aktueller Datensatz", "Current dataset")}
                </p>
                <h2 id="dataset-overview-heading" className="mt-1 font-display text-2xl text-foreground">
                  {t("Daten prüfen und weiterverwenden", "Inspect and use your data")}
                </h2>
                <p className="mt-1 max-w-2xl text-[0.75rem] leading-relaxed text-muted-foreground">
                  {t(
                    "Schema, Vorschau, Herkunft und Versionen bleiben gemeinsam sichtbar. Der Data Agent links führt darauf reproduzierbare Analysen aus.",
                    "Schema, preview, provenance and versions stay visible together. The Data Agent on the left runs reproducible analyses over them.",
                  )}
                </p>
              </div>

              <div className="grid gap-2 sm:grid-cols-3">
                {[
                  [dataset.row_count.toLocaleString(isGerman ? "de-DE" : "en-US"), t("Zeilen", "rows")],
                  [dataset.column_count.toLocaleString(isGerman ? "de-DE" : "en-US"), t("Spalten", "columns")],
                  [formatBytes(dataset.byte_size), dataset.format ? dataset.format.toUpperCase() : t("LEER", "EMPTY")],
                ].map(([value, label]) => (
                  <div key={label} className="rounded-2xl border border-border bg-secondary/35 px-4 py-3">
                    <p className="font-mono text-base font-medium text-foreground">{value}</p>
                    <p className="mt-0.5 text-[0.6875rem] text-muted-foreground">{label}</p>
                  </div>
                ))}
              </div>
            </section>

            <section aria-labelledby="schema-heading" className="overflow-hidden rounded-2xl border border-border bg-card">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-secondary/35 px-4 py-3">
                <div>
                  <h3 id="schema-heading" className="text-[0.8125rem] font-medium text-foreground">
                    {t("Schema und Datenqualität", "Schema and data quality")}
                  </h3>
                  {dataset.columns.length > 0 && (
                    <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">
                      {dataset.columns.filter((column) => column.type === "number").length} {t("numerische Spalten", "numeric columns")} · {dataset.columns.reduce((sum, column) => sum + column.missing, 0).toLocaleString(isGerman ? "de-DE" : "en-US")} {t("fehlende Zellen", "missing cells")}
                    </p>
                  )}
                </div>
                <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
                  {t("Profilierte Daten", "Profiled data")}
                </span>
              </div>
              {dataset.columns.length === 0 ? (
                <div className="px-4 py-8 text-center">
                  <p className="text-[0.8125rem] font-medium text-foreground">
                    {t("Noch keine Datenversion", "No data version yet")}
                  </p>
                  <p className="mx-auto mt-1 max-w-md text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {t(
                      "Füge oben eine CSV-, TSV-, JSON- oder Excel-Datei hinzu. Die erste Datei wird als Version 1 profiliert.",
                      "Add a CSV, TSV, JSON or Excel file above. The first file is profiled as version 1.",
                    )}
                  </p>
                </div>
              ) : (
                <div className="max-h-72 overflow-auto">
                  <table className="w-full text-left text-[0.75rem]">
                    <caption className="sr-only">{t("Spaltenschema und Datenqualität", "Column schema and data quality")}</caption>
                    <thead className="sticky top-0 bg-card text-muted-foreground">
                      <tr>
                        <th scope="col" className="px-4 py-2 font-medium">{t("Spalte", "Column")}</th>
                        <th scope="col" className="px-3 py-2 font-medium">{t("Typ", "Type")}</th>
                        <th scope="col" className="px-3 py-2 text-right font-medium">{t("Fehlend", "Missing")}</th>
                        <th scope="col" className="px-4 py-2 text-right font-medium">{t("Eindeutig", "Unique")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dataset.columns.map((column) => (
                        <tr key={column.name} className="border-t border-border/70">
                          <th scope="row" className="max-w-56 truncate px-4 py-2 font-mono font-normal text-foreground">{column.name}</th>
                          <td className="px-3 py-2 text-muted-foreground">{column.type === "number" ? t("Zahl", "number") : t("Text", "text")}</td>
                          <td className="px-3 py-2 text-right font-mono">{column.missing}</td>
                          <td className="px-4 py-2 text-right font-mono">{column.unique}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {dataset.preview.length > 0 && (
              <section aria-labelledby="preview-heading" className="overflow-hidden rounded-2xl border border-border bg-card">
                <div className="border-b border-border bg-secondary/35 px-4 py-3">
                  <h3 id="preview-heading" className="text-[0.8125rem] font-medium text-foreground">
                    {t("Exakte Vorschau", "Exact preview")}
                  </h3>
                  <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">
                    {t("Die ersten 12 profilierten Zeilen; die Originaldatei bleibt unverändert.", "The first 12 profiled rows; the original file remains unchanged.")}
                  </p>
                </div>
                <div className="max-h-80 overflow-auto">
                  <table className="w-full whitespace-nowrap text-left font-mono text-[0.6875rem]">
                    <caption className="sr-only">{t("Vorschau der Datensatzwerte", "Dataset value preview")}</caption>
                    <thead className="sticky top-0 bg-card text-muted-foreground">
                      <tr>{dataset.columns.map((column) => <th scope="col" key={column.name} className="px-3 py-2 font-medium">{column.name}</th>)}</tr>
                    </thead>
                    <tbody>
                      {dataset.preview.slice(0, 12).map((row, index) => (
                        <tr key={index} className="border-t border-border/70">
                          {dataset.columns.map((column) => <td key={column.name} className="max-w-52 truncate px-3 py-2">{String(row[column.name] ?? "")}</td>)}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(18rem,0.8fr)]">
              <section aria-labelledby="context-heading" className="rounded-2xl border border-border bg-card p-4 sm:p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 id="context-heading" className="text-[0.8125rem] font-medium text-foreground">
                      {t("Forschungskontext", "Research context")}
                    </h3>
                    <p className="mt-0.5 text-[0.65625rem] leading-relaxed text-muted-foreground">
                      {t("Dokumentiert, was die Zeilen bedeuten und woher sie stammen.", "Document what the rows mean and where they came from.")}
                    </p>
                  </div>
                  {!profileEditing && (
                    <Button variant="outline" size="sm" className="h-8 shrink-0 rounded-full px-3 text-[0.6875rem]" onClick={() => setProfileEditing(true)}>
                      <Pencil className="size-3.5" /> {t("Bearbeiten", "Edit details")}
                    </Button>
                  )}
                </div>

                {profileEditing ? (
                  <form
                    method="post"
                    className="mt-4 grid gap-3 sm:grid-cols-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      if (profileDirty && !saveProfile.isPending) saveProfile.mutate();
                    }}
                  >
                    <div className="space-y-1.5 sm:col-span-2">
                      <Label htmlFor="dataset-description" className="text-[0.6875rem]">{t("Was stellen die Zeilen dar?", "What do these rows represent?")}</Label>
                      <Textarea id="dataset-description" value={editDescription} onChange={(event) => setEditDescription(event.target.value)} className="min-h-20 resize-y" />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="dataset-provenance" className="text-[0.6875rem]">{t("Herkunft", "Provenance")}</Label>
                      <Input id="dataset-provenance" value={editProvenance} onChange={(event) => setEditProvenance(event.target.value)} placeholder={t("Erhoben in Laborstudie S-04", "Collected in lab study S-04")} className="h-9" />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="dataset-license" className="text-[0.6875rem]">{t("Lizenz / Zugriff", "License / access")}</Label>
                      <Input id="dataset-license" value={editLicense} onChange={(event) => setEditLicense(event.target.value)} className="h-9" />
                    </div>
                    <div className="flex flex-wrap justify-end gap-2 sm:col-span-2">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="rounded-full"
                        onClick={() => {
                          setEditDescription(dataset.description);
                          setEditProvenance(dataset.provenance);
                          setEditLicense(dataset.license);
                          setProfileEditing(false);
                        }}
                      >
                        {t("Abbrechen", "Cancel")}
                      </Button>
                      <Button type="submit" size="sm" className="rounded-full" disabled={!profileDirty || saveProfile.isPending}>
                        {saveProfile.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                        {t("Details speichern", "Save details")}
                      </Button>
                    </div>
                  </form>
                ) : (
                  <dl className="mt-4 grid gap-4 text-[0.75rem] sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <dt className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">{t("Bedeutung", "Description")}</dt>
                      <dd className="mt-1 leading-relaxed text-foreground">{dataset.description || t("Noch nicht dokumentiert.", "Not documented yet.")}</dd>
                    </div>
                    <div>
                      <dt className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">{t("Herkunft", "Provenance")}</dt>
                      <dd className="mt-1 leading-relaxed text-foreground">{dataset.provenance || t("Noch nicht dokumentiert.", "Not documented yet.")}</dd>
                    </div>
                    <div>
                      <dt className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">{t("Lizenz / Zugriff", "License / access")}</dt>
                      <dd className="mt-1 leading-relaxed text-foreground">{dataset.license || t("Nicht angegeben", "Not specified")}</dd>
                    </div>
                  </dl>
                )}
              </section>

              <section aria-labelledby="versions-heading" className="rounded-2xl border border-border bg-card p-4 sm:p-5">
                <div className="flex items-center gap-2">
                  <History className="size-4 text-moss" />
                  <div>
                    <h3 id="versions-heading" className="text-[0.8125rem] font-medium text-foreground">{t("Versionsverlauf", "Version history")}</h3>
                    <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">{(versions ?? []).length} {t("gespeicherte Versionen", "saved versions")}</p>
                  </div>
                </div>
                <div className="mt-4 max-h-64 space-y-2 overflow-y-auto pr-1">
                  {(versions ?? []).map((version) => (
                    <div key={version.id} className="rounded-xl border border-border/75 bg-secondary/25 px-3 py-2.5">
                      <div className="flex items-baseline justify-between gap-3 text-[0.6875rem]">
                        <span className="min-w-0 truncate font-mono text-foreground" title={version.filename}>v{version.version} · {version.filename}</span>
                        <span className="shrink-0 text-muted-foreground">{formatBytes(version.byte_size)}</span>
                      </div>
                      <p className="mt-1 text-[0.625rem] text-muted-foreground">{version.row_count.toLocaleString(isGerman ? "de-DE" : "en-US")} {t("Zeilen", "rows")}</p>
                    </div>
                  ))}
                  {(versions ?? []).length === 0 && (
                    <p className="rounded-xl border border-dashed border-border px-3 py-5 text-center text-[0.6875rem] leading-relaxed text-muted-foreground">
                      {t("Die erste Datendatei wird Version 1.", "The first data file becomes version 1.")}
                    </p>
                  )}
                </div>
              </section>
            </div>

            <section aria-labelledby="chart-heading" className="rounded-2xl border border-border bg-card p-4 sm:p-5">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <BarChart3 className="size-4 text-moss" />
                    <h3 id="chart-heading" className="text-[0.8125rem] font-medium text-foreground">{t("Exakten Chart erstellen", "Create an exact chart")}</h3>
                  </div>
                  <p className="mt-1 max-w-2xl text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {t(
                      "Wähle die Variablen bewusst. Der Chart verwendet ausschließlich profilierte Werte und wird im Visual Lab weitergeführt.",
                      "Choose the variables deliberately. The chart uses profiled values only and continues in Visual Lab.",
                    )}
                  </p>
                </div>
                <Button asChild variant="outline" size="sm" className="h-8 w-fit shrink-0 rounded-full px-3 text-[0.6875rem]">
                  <Link href={`/figures?dataset=${dataset.public_id}`}>
                    {t("Visual Lab öffnen", "Open Visual Lab")} <ArrowUpRight className="size-3.5" />
                  </Link>
                </Button>
              </div>

              {dataset.columns.length >= 2 && numeric.length > 0 ? (
                <div className="mt-4 space-y-4">
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="chart-kind" className="text-[0.6875rem]">{t("Charttyp", "Chart type")}</Label>
                      <Select value={chartKind} onValueChange={setChartKind}>
                        <SelectTrigger id="chart-kind" className="h-9 w-full"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="bar">{t("Balken", "Bar")}</SelectItem>
                          <SelectItem value="line">{t("Linie", "Line")}</SelectItem>
                          <SelectItem value="scatter">{t("Streudiagramm", "Scatter")}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="chart-x" className="text-[0.6875rem]">{t("X-Achse", "X axis")}</Label>
                      <Select value={chartX} onValueChange={setChartX}>
                        <SelectTrigger id="chart-x" className="h-9 w-full"><SelectValue placeholder={t("Spalte auswählen", "Choose column")} /></SelectTrigger>
                        <SelectContent>{dataset.columns.map((column) => <SelectItem key={column.name} value={column.name}>{column.name}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="chart-y" className="text-[0.6875rem]">{t("Y-Achse (numerisch)", "Y axis (numeric)")}</Label>
                      <Select value={chartY} onValueChange={setChartY}>
                        <SelectTrigger id="chart-y" className="h-9 w-full"><SelectValue placeholder={t("Numerische Spalte auswählen", "Choose numeric column")} /></SelectTrigger>
                        <SelectContent>{numeric.map((column) => <SelectItem key={column.name} value={column.name}>{column.name}</SelectItem>)}</SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                    <div className="space-y-1.5">
                      <Label htmlFor="chart-title" className="text-[0.6875rem]">{t("Abbildungstitel", "Figure title")}</Label>
                      <Input id="chart-title" value={chartTitle} onChange={(event) => setChartTitle(event.target.value)} placeholder={chartX && chartY ? `${chartY} ${t("nach", "by")} ${chartX}` : t("Optionaler Abbildungstitel", "Optional figure title")} />
                    </div>
                    <Button
                      className="rounded-full sm:min-w-40"
                      disabled={!chartX || !chartY || chartX === chartY || chart.isPending}
                      onClick={() =>
                        chart.mutate({
                          x: chartX,
                          y: chartY,
                          kind: chartKind,
                          title: chartTitle.trim() || `${chartY} by ${chartX}`,
                        })
                      }
                    >
                      {chart.isPending ? <Loader2 className="size-4 animate-spin" /> : <BarChart3 className="size-4" />}
                      {t("Chart erstellen", "Create chart")}
                    </Button>
                  </div>
                  {chartX && chartY && chartX === chartY && (
                    <p role="status" className="text-[0.6875rem] text-amber-700 dark:text-amber-300">
                      {t("Wähle für X- und Y-Achse unterschiedliche Spalten.", "Choose different columns for the X and Y axes.")}
                    </p>
                  )}
                </div>
              ) : (
                <div className="mt-4 rounded-xl border border-dashed border-border bg-secondary/20 px-4 py-6 text-center">
                  <p className="text-[0.75rem] font-medium text-foreground">{t("Für Charts fehlen passende Spalten", "This dataset is not ready for charts")}</p>
                  <p className="mx-auto mt-1 max-w-md text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {t("Ein exakter Chart benötigt mindestens zwei Spalten, davon eine numerische.", "An exact chart needs at least two columns, including one numeric column.")}
                  </p>
                </div>
              )}
            </section>

            <p className="flex items-start gap-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
              <FileSpreadsheet className="mt-0.5 size-3.5 shrink-0 text-moss" />
              {t(
                "Analysen laufen über das profilierte Datenfenster (bis zu 2.000 Zeilen) und bleiben reproduzierbar; der Data Agent weist darauf hin, wenn dieses Fenster eine Schlussfolgerung begrenzt.",
                "Analyses run over the profiled record window (up to 2,000 rows) and stay reproducible; the Data Agent tells you when that window limits a conclusion.",
              )}
            </p>
          </div>
        </main>
      </ResizableWorkspaceSplit>
    </div>
  );
}
