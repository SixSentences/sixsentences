"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  ArrowUpRight,
  AudioLines,
  Blocks,
  Check,
  ChevronDown,
  ClipboardList,
  Database,
  FileText,
  FolderPlus,
  Image,
  Languages,
  Link2,
  Loader2,
  Mic2,
  Moon,
  Pencil,
  Plus,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  Telescope,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { FIGURE_PROMPT_MAX_CHARACTERS } from "@/lib/figure-limits";
import { useActiveProject } from "@/lib/project-context";
import type {
  AssistantPreferences,
  SurveyQuestion,
  SurveyQuestionType,
  VoiceGuideSection,
  WorkspaceAction,
  WorkspaceResourceType,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const TYPE_META: Record<
  WorkspaceAction["type"],
  { label: string; detail: string; icon: typeof Image }
> = {
  create_visual: {
    label: "Visual Lab",
    detail: "Render a scientific figure from this context",
    icon: Image,
  },
  create_survey: {
    label: "Survey",
    detail: "Create an editable questionnaire",
    icon: ClipboardList,
  },
  create_ai_interview: {
    label: "AI interview",
    detail: "Create a guided interview study",
    icon: Mic2,
  },
  create_manuscript: {
    label: "Manuscript",
    detail: "Open a source-connected writing project",
    icon: FileText,
  },
  start_review: {
    label: "Systematic review",
    detail: "Start an auditable literature workflow",
    icon: Telescope,
  },
  create_project: {
    label: "Project",
    detail: "Create a workspace for connected research",
    icon: FolderPlus,
  },
  open_data_hub: {
    label: "Dataset",
    detail: "Create an empty, source-connected Data Hub workspace",
    icon: Database,
  },
  open_library: {
    label: "Library",
    detail: "Collect and reuse research sources",
    icon: FileText,
  },
  upload_interview: {
    label: "Interview transcript",
    detail: "Continue with a recording or transcript",
    icon: AudioLines,
  },
  set_theme: {
    label: "Appearance",
    detail: "Switch the workspace theme everywhere",
    icon: Moon,
  },
  set_language: {
    label: "System language",
    detail: "Use this language throughout the workspace",
    icon: Languages,
  },
  update_assistant_preferences: {
    label: "AI behavior",
    detail: "Set the default response style for every assistant",
    icon: SlidersHorizontal,
  },
  open_settings: {
    label: "Settings",
    detail: "Open the relevant protected settings section",
    icon: Settings,
  },
  connect_reference_manager: {
    label: "Reference manager",
    detail: "Continue in the protected integration form",
    icon: Blocks,
  },
  manage_resource: {
    label: "Workspace action",
    detail: "Resolve and update an existing workspace resource",
    icon: Pencil,
  },
};

type DraftQuestion = Omit<SurveyQuestion, "id">;
type CreatedAction = { route?: string; label: string };
type ResourceCandidate = {
  id: string;
  resourceType: WorkspaceResourceType;
  title: string;
  subtitle: string;
  route: string;
  projectId: number | null;
};

const DEFAULT_ASSISTANT_PREFERENCES: AssistantPreferences = {
  detail: "balanced",
  tone: "academic",
  format: "adaptive",
  custom_instructions: "",
};

const CONTROL_ACTIONS = new Set<WorkspaceAction["type"]>([
  "set_theme",
  "set_language",
  "update_assistant_preferences",
  "open_settings",
  "connect_reference_manager",
]);

const PROJECT_TARGET_ACTIONS = new Set<WorkspaceAction["type"]>([
  "create_visual",
  "create_survey",
  "create_ai_interview",
  "create_manuscript",
  "start_review",
  "open_data_hub",
  "upload_interview",
]);

function FieldLabel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      className={cn(
        "font-mono text-[0.59375rem] uppercase tracking-[0.16em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </label>
  );
}

function NativeSelect({
  className,
  wrapperClassName,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & {
  wrapperClassName?: string;
}) {
  return (
    <span className={cn("relative block w-full min-w-0", wrapperClassName)}>
      <select
        {...props}
        className={cn(
          "peer h-9 w-full appearance-none rounded-xl border border-border bg-background pl-3 pr-9 text-[0.75rem] disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto",
          className,
        )}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden"
      />
    </span>
  );
}

function hasGroundedQuantitativeValues(value: string): boolean {
  const matches = value.match(
    /(?<![\w@])(?:\d{1,3}(?:[.,]\d+)?\s*%|0[.,]\d+|\d+[.,]\d+)(?![\w])|\b(?:n|sample|count|score|accuracy|precision|recall|f1|pass@k)\s*[:=]\s*\d+(?:[.,]\d+)?\b/gi,
  );
  return new Set(
    (matches ?? []).map((item) =>
      item.toLowerCase().replaceAll(",", ".").replace(/\s+/g, ""),
    ),
  ).size >= 2;
}

function conceptualEvidenceBrief(title: string): string {
  return [
    `Create a non-quantitative evidence map for “${title.trim() || "the current research question"}”.`,
    "Organize the available papers or concepts by research focus, reported method, supported finding and evidence gap.",
    "Use concise source-linked labels and one clear reading direction.",
    "Do not draw axes, bars, benchmark rankings, effect sizes or performance gaps unless exact comparable source values are added.",
  ].join(" ");
}

function normalizeSearchText(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, " ")
    .trim();
}

function candidateScore(candidate: ResourceCandidate, selector: string): number {
  const title = normalizeSearchText(candidate.title);
  const query = normalizeSearchText(selector);
  if (!query) return 0;
  if (title === query) return 1_000;
  if (query.includes(title) && title.length > 3) return 700 + title.length;
  if (title.includes(query) && query.length > 3) return 600 + query.length;
  const words = query.split(" ").filter((word) => word.length >= 3);
  return words.reduce(
    (score, word) => score + (title.includes(word) ? Math.min(word.length, 12) : 0),
    0,
  );
}

function makeQuestion(): DraftQuestion {
  return {
    title: "",
    description: "",
    type: "long_text",
    required: false,
    options: [],
    min: null,
    max: null,
  };
}

function makeSection(): VoiceGuideSection {
  return {
    title: "",
    question: "",
    probes: [],
    must_cover: true,
  };
}

export function WorkspaceActionCard({
  action,
  compact = false,
}: {
  action: WorkspaceAction;
  compact?: boolean;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useActiveProject();
  const { setTheme } = useTheme();
  const {
    me,
    setLanguage: persistLanguage,
    setAssistantPreferences,
  } = useAuth();
  const meta = TYPE_META[action.type];
  const Icon = meta.icon;
  const [dismissed, setDismissed] = useState(false);
  const [created, setCreated] = useState<CreatedAction | null>(null);
  const [title, setTitle] = useState(action.title);
  const [description, setDescription] = useState(
    action.description
      ?? action.instructions
      ?? action.objective
      ?? action.research_goal
      ?? "",
  );
  const [prompt, setPrompt] = useState(action.prompt ?? "");
  const [question, setQuestion] = useState(action.question ?? "");
  const [query, setQuery] = useState(action.query ?? "");
  const [visualKind, setVisualKind] = useState(action.kind ?? "concept");
  const [aspectRatio, setAspectRatio] = useState(action.aspect_ratio ?? "4:3");
  const [resolution, setResolution] = useState(action.resolution ?? "2k");
  const [reviewPasses, setReviewPasses] = useState(action.review_passes ?? 1);
  const [language, setLanguage] = useState(action.language ?? "en");
  const [theme, setThemeDraft] = useState(action.theme ?? "light");
  const [preferenceDraft, setPreferenceDraft] = useState<AssistantPreferences>({
    ...(me?.assistant_preferences ?? DEFAULT_ASSISTANT_PREFERENCES),
    ...(action.preferences ?? {}),
  });
  const [questions, setQuestions] = useState<DraftQuestion[]>(
    action.questions?.length ? action.questions : [makeQuestion()],
  );
  const [sections, setSections] = useState<VoiceGuideSection[]>(
    action.sections?.length ? action.sections : [makeSection()],
  );
  const [newName, setNewName] = useState(action.new_name ?? "");
  const [selectedResourceId, setSelectedResourceId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ResourceCandidate | null>(null);
  const deleteInFlightRef = useRef(false);
  const [destinationProjectId, setDestinationProjectId] = useState<number | null>(null);
  const [destinationWriterId, setDestinationWriterId] = useState("");
  const [resourceStatus, setResourceStatus] = useState(action.resource_status ?? "");
  const manageResource = action.type === "manage_resource";
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
  });
  const { data: runs = [] } = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(),
    enabled: manageResource,
  });
  const { data: writerDocuments = [] } = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
    enabled: manageResource,
  });
  const { data: surveys = [] } = useQuery({
    queryKey: ["surveys"],
    queryFn: api.surveys,
    enabled: manageResource,
  });
  const { data: datasets = [] } = useQuery({
    queryKey: ["datasets"],
    queryFn: api.datasets,
    enabled: manageResource,
  });
  const { data: interviews = [] } = useQuery({
    queryKey: ["interviews"],
    queryFn: api.interviews,
    enabled: manageResource,
  });
  const { data: interviewStudies = [] } = useQuery({
    queryKey: ["voice-studies"],
    queryFn: api.voiceStudies,
    enabled: manageResource,
  });
  const { data: figures = [] } = useQuery({
    queryKey: ["figures"],
    queryFn: api.figures,
    enabled: manageResource,
  });
  const { data: libraryDocuments = [] } = useQuery({
    queryKey: ["library-documents", ""],
    queryFn: () => api.libraryDocuments(),
    enabled: manageResource,
  });
  const [projectId, setProjectId] = useState<number | null>(
    action.context.project_id,
  );
  const resourceCandidates = useMemo<ResourceCandidate[]>(() => {
    if (!manageResource || !action.resource_type) return [];
    let values: ResourceCandidate[] = [];
    if (action.resource_type === "project") {
      values = (projects ?? []).map((item) => ({
        id: String(item.id),
        resourceType: "project",
        title: item.name,
        subtitle: item.status,
        route: "/",
        projectId: item.id,
      }));
    } else if (action.resource_type === "review") {
      values = runs.map((item) => ({
        id: item.public_id,
        resourceType: "review",
        title: item.title || item.question,
        subtitle: item.status,
        route: `/r/${item.public_id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "manuscript") {
      values = writerDocuments.map((item) => ({
        id: item.public_id,
        resourceType: "manuscript",
        title: item.title,
        subtitle: item.compile_status,
        route: `/writer/${item.public_id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "survey") {
      values = surveys.map((item) => ({
        id: item.public_id,
        resourceType: "survey",
        title: item.title,
        subtitle: `${item.status} · ${item.response_count} responses`,
        route: `/surveys/${item.public_id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "dataset") {
      values = datasets.map((item) => ({
        id: item.public_id,
        resourceType: "dataset",
        title: item.name,
        subtitle: `${item.row_count} rows`,
        route: `/data/${item.public_id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "interview") {
      values = interviews.map((item) => ({
        id: item.id,
        resourceType: "interview",
        title: item.title,
        subtitle: item.status,
        route: `/interviews/${item.id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "interview_study") {
      values = interviewStudies.map((item) => ({
        id: item.id,
        resourceType: "interview_study",
        title: item.title,
        subtitle: `${item.session_count} sessions`,
        route: `/interviews/studies/${item.id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "visual") {
      values = figures.map((item) => ({
        id: item.public_id,
        resourceType: "visual",
        title: item.config.title || item.prompt,
        subtitle: item.status,
        route: `/figures?focus=${item.public_id}`,
        projectId: item.project_id,
      }));
    } else if (action.resource_type === "library_paper") {
      values = libraryDocuments.map((item) => ({
        id: String(item.id),
        resourceType: "library_paper",
        title: item.title || item.work_id,
        subtitle: [item.year, item.project_name].filter(Boolean).join(" · "),
        route: `/library?query=${encodeURIComponent(item.title || item.work_id)}`,
        projectId: item.project_id,
      }));
    }
    return values.sort(
      (left, right) =>
        candidateScore(right, action.selector ?? "")
        - candidateScore(left, action.selector ?? ""),
    );
  }, [
    action.resource_type,
    action.selector,
    datasets,
    figures,
    interviewStudies,
    interviews,
    libraryDocuments,
    manageResource,
    projects,
    runs,
    surveys,
    writerDocuments,
  ]);
  const selectedResource =
    resourceCandidates.find((item) => item.id === selectedResourceId) ?? null;

  useEffect(() => {
    if (!manageResource || !resourceCandidates.length) return;
    setSelectedResourceId((current) =>
      resourceCandidates.some((candidate) => candidate.id === current)
        ? current
        : resourceCandidates[0]!.id,
    );
  }, [manageResource, resourceCandidates]);

  useEffect(() => {
    if (!manageResource || !action.destination) return;
    const destination = normalizeSearchText(action.destination);
    const project = (projects ?? []).find((item) =>
      normalizeSearchText(item.name).includes(destination),
    );
    if (project) setDestinationProjectId(project.id);
    const manuscript = writerDocuments.find((item) =>
      normalizeSearchText(item.title).includes(destination),
    );
    if (manuscript) setDestinationWriterId(manuscript.public_id);
  }, [action.destination, manageResource, projects, writerDocuments]);
  const missingPlotData =
    action.type === "create_visual"
    // The explicit kind selector is the source of truth. Every generated
    // visual brief also contains a safety sentence mentioning a
    // "quantitative plot"; scanning the entire prompt therefore disabled
    // perfectly valid flow and concept diagrams by accident.
    && visualKind === "plot"
    && !hasGroundedQuantitativeValues(prompt);
  const visualPromptTooLong =
    action.type === "create_visual"
    && prompt.trim().length > FIGURE_PROMPT_MAX_CHARACTERS;
  const receiptKey = me
    ? `six:workspace-action:v2:${me.org_id}:${me.user_id}:${action.id}`
    : null;
  const controlAction = CONTROL_ACTIONS.has(action.type);
  const manageActionBlocked =
    manageResource
    && (
      !selectedResource
      || (action.operation === "rename" && !newName.trim())
      || (action.operation === "move" && !destinationProjectId)
      || (action.operation === "attach_to_manuscript" && !destinationWriterId)
      || (action.operation === "update_status" && !resourceStatus)
    );

  useEffect(() => {
    setCreated(null);
    if (!receiptKey) {
      return;
    }
    if (
      action.type === "open_library"
      || action.type === "upload_interview"
      || CONTROL_ACTIONS.has(action.type)
    ) {
      return;
    }
    try {
      const raw = window.localStorage.getItem(receiptKey);
      if (!raw) return;
      const receipt = JSON.parse(raw) as Partial<CreatedAction>;
      if (
        typeof receipt.route === "string"
        && receipt.route.startsWith("/")
        && typeof receipt.label === "string"
      ) {
        setCreated({ route: receipt.route, label: receipt.label });
      }
    } catch {
      window.localStorage.removeItem(receiptKey);
    }
  }, [action.type, receiptKey]);

  const confirmLabel = useMemo(() => {
    if (action.type === "open_library" || action.type === "upload_interview") {
      return "Open workspace";
    }
    if (action.type === "open_data_hub") return "Create dataset";
    if (action.type === "create_visual") return "Confirm render";
    if (action.type === "start_review") return "Start review";
    if (action.type === "create_ai_interview") return "Create AI interview";
    if (action.type === "set_theme") return "Apply appearance";
    if (action.type === "set_language") return "Apply language";
    if (action.type === "update_assistant_preferences") return "Save AI behavior";
    if (action.type === "open_settings") return "Open settings";
    if (action.type === "connect_reference_manager") {
      return `Set up ${action.provider === "citavi" ? "Citavi" : "Zotero"}`;
    }
    if (action.type === "manage_resource") {
      if (action.operation === "delete") return "Delete selected item";
      if (action.operation === "rename") return "Rename selected item";
      if (action.operation === "move") return "Move selected item";
      if (action.operation === "attach_to_manuscript") return "Attach to manuscript";
      if (action.operation === "update_status") return "Update status";
      return "Open selected item";
    }
    return `Create ${meta.label.toLowerCase()}`;
  }, [action.operation, action.provider, action.type, meta.label]);

  const execute = useMutation({
    mutationFn: async (): Promise<CreatedAction> => {
      const cleanTitle = title.trim();
      if (
        !cleanTitle
        && action.type !== "open_library"
        && action.type !== "upload_interview"
        && action.type !== "manage_resource"
        && !CONTROL_ACTIONS.has(action.type)
      ) {
        throw new Error("Add a title before confirming.");
      }
      if (action.type === "manage_resource") {
        if (!selectedResource || !action.operation) {
          throw new Error("Select the exact workspace item first.");
        }
        const id = selectedResource.id;
        if (action.operation === "open") {
          if (selectedResource.resourceType === "project") {
            setActiveProjectId(Number(id));
          }
          return { route: selectedResource.route, label: "Open item" };
        }
        if (action.operation === "rename") {
          const name = newName.trim();
          if (!name) throw new Error("Enter the new name first.");
          if (selectedResource.resourceType === "project") {
            await api.renameProject(Number(id), name);
          } else if (selectedResource.resourceType === "review") {
            await api.renameRun(id, name);
          } else if (selectedResource.resourceType === "manuscript") {
            await api.writerPatch(id, { title: name });
          } else if (selectedResource.resourceType === "survey") {
            await api.surveyUpdate(id, { title: name });
          } else if (selectedResource.resourceType === "dataset") {
            await api.datasetUpdate(id, { name });
          } else if (selectedResource.resourceType === "interview") {
            await api.interviewUpdate(id, { title: name });
          } else if (selectedResource.resourceType === "interview_study") {
            await api.voiceStudyUpdate(id, { title: name });
          } else if (selectedResource.resourceType === "visual") {
            await api.figureRename(id, name);
          } else {
            throw new Error("This resource keeps its scholarly source title.");
          }
        } else if (action.operation === "move") {
          if (!destinationProjectId) {
            throw new Error("Select the destination project first.");
          }
          if (selectedResource.resourceType === "review") {
            await api.moveRun(id, destinationProjectId);
          } else if (selectedResource.resourceType === "manuscript") {
            await api.writerPatch(id, { project_id: destinationProjectId });
          } else if (selectedResource.resourceType === "survey") {
            await api.surveyUpdate(id, { project_id: destinationProjectId });
          } else if (selectedResource.resourceType === "dataset") {
            await api.datasetUpdate(id, { project_id: destinationProjectId });
          } else if (selectedResource.resourceType === "interview") {
            await api.interviewUpdate(id, { project_id: destinationProjectId });
          } else if (selectedResource.resourceType === "interview_study") {
            await api.voiceStudyUpdate(id, { project_id: destinationProjectId });
          } else if (selectedResource.resourceType === "visual") {
            await api.figureUpdate(id, { project_id: destinationProjectId });
          } else if (selectedResource.resourceType === "library_paper") {
            await api.updateLibraryDocument(Number(id), {
              project_id: destinationProjectId,
            });
          } else {
            throw new Error("This resource cannot be moved into another project.");
          }
        } else if (action.operation === "attach_to_manuscript") {
          if (!destinationWriterId) {
            throw new Error("Select the destination manuscript first.");
          }
          if (selectedResource.resourceType === "library_paper") {
            await api.writerLibrarySourcesAdd(destinationWriterId, [Number(id)]);
          } else if (selectedResource.resourceType === "visual") {
            await api.figureAttach(id, destinationWriterId);
          } else if (selectedResource.resourceType === "dataset") {
            const document = await api.writerGet(destinationWriterId);
            await api.writerPatch(destinationWriterId, {
              dataset_ids: [...new Set([...document.dataset_ids, id])],
            });
          } else if (selectedResource.resourceType === "review") {
            const document = await api.writerGet(destinationWriterId);
            const run = runs.find((item) => item.public_id === id);
            if (!run) throw new Error("The selected review could not be resolved.");
            await api.writerPatch(destinationWriterId, {
              run_ids: [...new Set([...document.run_ids, run.id])],
            });
          } else if (selectedResource.resourceType === "interview") {
            const catalog = await api.writerInterviewContexts(destinationWriterId);
            await api.writerInterviewContextsSet(destinationWriterId, [
              ...catalog.items
                .filter((item) => item.linked && item.interview_id !== id)
                .map((item) => ({
                  interview_id: item.interview_id,
                  mode: item.mode ?? "analysis" as const,
                  include_methodology: item.include_methodology,
                })),
              {
                interview_id: id,
                mode: "analysis",
                include_methodology: true,
              },
            ]);
          } else if (selectedResource.resourceType === "survey") {
            const catalog = await api.writerSurveyContexts(destinationWriterId);
            await api.writerSurveyContextsSet(destinationWriterId, [
              ...catalog.items
                .filter((item) => item.linked && item.survey_id !== id)
                .map((item) => ({
                  survey_id: item.survey_id,
                  mode: item.mode ?? "summary" as const,
                })),
              { survey_id: id, mode: "summary" },
            ]);
          } else {
            throw new Error("This resource cannot be attached to a manuscript.");
          }
        } else if (action.operation === "update_status") {
          if (!resourceStatus) throw new Error("Select the new status first.");
          if (selectedResource.resourceType === "project") {
            await api.updateProject(Number(id), {
              status: resourceStatus as "active" | "paused" | "complete" | "archived",
            });
          } else if (selectedResource.resourceType === "survey") {
            await api.surveyUpdate(id, {
              status: resourceStatus as "draft" | "live" | "closed",
            });
          } else {
            throw new Error("This resource does not expose a workspace status.");
          }
        } else if (action.operation === "delete") {
          if (selectedResource.resourceType === "project") {
            await api.deleteProject(Number(id));
          } else if (selectedResource.resourceType === "review") {
            await api.deleteRun(id);
          } else if (selectedResource.resourceType === "manuscript") {
            await api.writerDelete(id);
          } else if (selectedResource.resourceType === "survey") {
            await api.surveyDelete(id);
          } else if (selectedResource.resourceType === "dataset") {
            await api.datasetDelete(id);
          } else if (selectedResource.resourceType === "interview") {
            await api.interviewDelete(id);
          } else if (selectedResource.resourceType === "interview_study") {
            await api.voiceStudyDelete(id);
          } else if (selectedResource.resourceType === "visual") {
            await api.figureDelete(id);
          } else {
            await api.deleteLibraryDocument(Number(id));
          }
        }
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["projects"] }),
          queryClient.invalidateQueries({ queryKey: ["runs"] }),
          queryClient.invalidateQueries({ queryKey: ["writer-docs"] }),
          queryClient.invalidateQueries({ queryKey: ["surveys"] }),
          queryClient.invalidateQueries({ queryKey: ["datasets"] }),
          queryClient.invalidateQueries({ queryKey: ["interviews"] }),
          queryClient.invalidateQueries({ queryKey: ["voice-studies"] }),
          queryClient.invalidateQueries({ queryKey: ["figures"] }),
          queryClient.invalidateQueries({ queryKey: ["library-documents"] }),
        ]);
        return {
          ...(action.operation === "delete"
            ? {}
            : { route: selectedResource.route }),
          label:
            action.operation === "delete"
              ? `${selectedResource.title} was deleted`
              : `${selectedResource.title} was updated`,
        };
      }
      if (action.type === "set_theme") {
        setTheme(theme);
        return {
          label:
            theme === "dark"
              ? "Dark mode is active"
              : theme === "system"
                ? "Appearance follows your system"
                : "Light mode is active",
        };
      }
      if (action.type === "set_language") {
        await persistLanguage(language);
        return {
          label: language === "de" ? "Deutsch ist aktiv" : "English is active",
        };
      }
      if (action.type === "update_assistant_preferences") {
        await setAssistantPreferences(preferenceDraft);
        return { label: "AI behavior was saved" };
      }
      if (
        action.type === "open_settings"
        || action.type === "connect_reference_manager"
      ) {
        window.dispatchEvent(
          new CustomEvent("six:open-settings", {
            detail: {
              tab:
                action.type === "connect_reference_manager"
                  ? "integrations"
                  : (action.section ?? "account"),
              provider:
                action.type === "connect_reference_manager"
                  ? action.provider
                  : undefined,
            },
          }),
        );
        return {
          label:
            action.type === "connect_reference_manager"
              ? `${action.provider === "citavi" ? "Citavi" : "Zotero"} setup opened`
              : "Settings opened",
        };
      }
      if (action.type === "create_visual") {
        if (prompt.trim().length < 12) {
          throw new Error("The visual brief needs a little more detail.");
        }
        if (prompt.trim().length > FIGURE_PROMPT_MAX_CHARACTERS) {
          throw new Error("Shorten the visual brief to 16,000 characters.");
        }
        if (missingPlotData) {
          throw new Error(
            "Add at least two exact labelled values before rendering a quantitative plot.",
          );
        }
        const sourceRunId =
          action.context.source_type === "research_chat"
            ? action.context.source_numeric_id
            : null;
        const figure = await api.figureCreate(prompt.trim(), sourceRunId, "auto", {
          title: cleanTitle,
          kind: visualKind,
          resolution,
          aspect_ratio: aspectRatio,
          review_passes: reviewPasses,
          ...(projectId ? { project_id: projectId } : {}),
          ...(action.context.source_type === "dataset"
            ? { dataset_id: action.context.source_id }
            : {}),
        });
        void queryClient.invalidateQueries({ queryKey: ["figures"] });
        return { route: `/figures?focus=${figure.public_id}`, label: "Open Visual Lab" };
      }
      if (action.type === "create_survey") {
        const normalized = questions
          .filter((item) => item.title.trim())
          .map((item) => ({
            ...item,
            id: crypto.randomUUID(),
            title: item.title.trim(),
            description: item.description.trim(),
            options: [...new Set(item.options.map((option) => option.trim()).filter(Boolean))],
          }));
        if (!normalized.length) {
          throw new Error("Add at least one survey question.");
        }
        const incompleteChoice = normalized.find(
          (item) =>
            (item.type === "single_choice" || item.type === "multiple_choice")
            && item.options.length < 2,
        );
        if (incompleteChoice) {
          throw new Error(
            `"${incompleteChoice.title}" needs at least two answer options.`,
          );
        }
        const invalidScale = normalized.find(
          (item) =>
            item.type === "scale"
            && (
              typeof item.min !== "number"
              || typeof item.max !== "number"
              || !Number.isFinite(item.min)
              || !Number.isFinite(item.max)
              || item.min >= item.max
            ),
        );
        if (invalidScale) {
          throw new Error(
            `"${invalidScale.title}" needs a minimum below its maximum.`,
          );
        }
        const survey = await api.surveyCreate({
          title: cleanTitle,
          description: description.trim(),
          project_id: projectId,
          questions: normalized,
        });
        void queryClient.invalidateQueries({ queryKey: ["surveys"] });
        return { route: `/surveys/${survey.public_id}`, label: "Open survey builder" };
      }
      if (action.type === "create_ai_interview") {
        const normalized = sections
          .filter((item) => item.question.trim())
          .map((item, index) => ({
            ...item,
            title: item.title.trim() || `Topic ${index + 1}`,
            question: item.question.trim(),
            probes: item.probes.filter(Boolean),
          }));
        if (!normalized.length) {
          throw new Error("Add at least one interview question.");
        }
        const study = await api.voiceStudyCreate(
          cleanTitle,
          language,
          projectId ?? undefined,
        );
        await api.voiceStudyUpdate(study.id, { sections: normalized });
        void queryClient.invalidateQueries({ queryKey: ["voice-studies"] });
        return {
          route: `/interviews/studies/${study.id}`,
          label: "Open interview builder",
        };
      }
      if (action.type === "create_manuscript") {
        const runIds =
          action.context.source_type === "research_chat"
          && action.context.source_numeric_id
            ? [action.context.source_numeric_id]
            : [];
        const document = await api.writerCreate(
          cleanTitle,
          action.template ?? "blank",
          runIds,
          undefined,
          projectId ?? undefined,
          description.trim(),
        );
        const documentId = document.public_id ?? document.id;
        if (action.context.source_type === "dataset") {
          await api.writerPatch(documentId, {
            dataset_ids: [action.context.source_id],
          });
        } else if (action.context.source_type === "interview") {
          await api.writerInterviewContextsSet(documentId, [
            {
              interview_id: action.context.source_id,
              mode: "analysis",
              include_methodology: true,
            },
          ]);
        } else if (action.context.source_type === "survey") {
          await api.writerSurveyContextsSet(documentId, [
            {
              survey_id: action.context.source_id,
              mode: "summary",
            },
          ]);
        }
        void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
        return {
          route: `/writer/${documentId}`,
          label: "Open manuscript",
        };
      }
      if (action.type === "start_review") {
        if (!question.trim()) {
          throw new Error("Add a research question before starting.");
        }
        const run = await api.createRun(projectId, {
          question: question.trim(),
          ...(query.trim() ? { query: query.trim() } : {}),
          screen: true,
          exhaustive: true,
        });
        void queryClient.invalidateQueries({ queryKey: ["runs"] });
        return { route: `/r/${run.public_id}`, label: "Open review" };
      }
      if (action.type === "create_project") {
        const project = await api.createProject(cleanTitle);
        if (description.trim()) {
          await api.updateProject(project.id, { description: description.trim() });
        }
        void queryClient.invalidateQueries({ queryKey: ["projects"] });
        setActiveProjectId(project.id);
        return { route: "/", label: "Start a search" };
      }
      if (action.type === "open_data_hub") {
        const dataset = await api.datasetCreate("", null, {
          name: cleanTitle,
          description: description.trim(),
          ...(projectId ? { project_id: projectId } : {}),
        });
        void queryClient.invalidateQueries({ queryKey: ["datasets"] });
        return {
          route: `/data/${dataset.public_id}`,
          label: "Open dataset",
        };
      }
      if (action.type === "open_library") {
        const focus = description.trim() || title.trim();
        return {
          route: focus
            ? `/library?query=${encodeURIComponent(focus)}`
            : "/library",
          label: "Open Library",
        };
      }
      const params = new URLSearchParams();
      if (title.trim()) params.set("title", title.trim());
      if (description.trim()) params.set("guide", description.trim());
      if (projectId) params.set("project", String(projectId));
      return {
        route: `/interviews${params.size ? `?${params.toString()}` : ""}`,
        label: "Open Interviews",
      };
    },
    onSuccess: (result) => {
      if (action.type === "manage_resource" && action.operation === "delete") {
        deleteInFlightRef.current = false;
        setDeleteTarget(null);
      }
      if (
        action.type === "open_library"
        || action.type === "upload_interview"
        || (
          action.type === "manage_resource"
          && action.operation === "open"
        )
      ) {
        if (result.route) router.push(result.route);
        return;
      }
      setCreated(result);
      if (!controlAction && receiptKey) {
        try {
          window.localStorage.setItem(receiptKey, JSON.stringify(result));
        } catch {
          // The resource still exists when browser storage is unavailable.
        }
      }
      toast.success(
        controlAction || action.type === "manage_resource"
          ? result.label
          : `${meta.label} created.`,
      );
    },
    onError: (error) => {
      if (action.type === "manage_resource" && action.operation === "delete") {
        deleteInFlightRef.current = false;
      }
      toast.error(error instanceof Error ? error.message : "The action could not be completed.");
    },
  });

  if (dismissed) return null;
  if (created) {
    return (
      <div className="mt-3 rounded-2xl border border-moss/30 bg-accent/35 p-3">
        <div className="flex items-center gap-2">
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-moss-surface text-ivory">
            <Check className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[0.8125rem] font-semibold">
              {controlAction || action.type === "manage_resource"
                ? `${meta.label} updated`
                : `${meta.label} is ready`}
            </p>
            <p className="truncate text-[0.6875rem] text-muted-foreground">
              {action.type === "manage_resource"
                ? (selectedResource?.title ?? title)
                : title}
            </p>
          </div>
          {created.route ? (
            <Button
              size="sm"
              className="h-8 rounded-full"
              onClick={() => router.push(created.route!)}
            >
              {created.label}
              <ArrowUpRight className="size-3.5" />
            </Button>
          ) : (
            <span className="text-[0.6875rem] font-medium text-moss">
              {created.label}
            </span>
          )}
        </div>
      </div>
    );
  }

  const updateQuestion = (index: number, patch: Partial<DraftQuestion>) =>
    setQuestions((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  const updateSection = (index: number, patch: Partial<VoiceGuideSection>) =>
    setSections((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );

  return (
    <div className="mt-3 overflow-hidden rounded-2xl border border-moss/30 bg-card shadow-[0_14px_36px_-30px_rgba(7,38,31,0.55)]">
      <div className="flex items-start gap-3 border-b border-border/70 bg-accent/25 p-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-full border border-moss/25 bg-moss-surface/10 text-moss">
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[0.8125rem] font-semibold">{meta.label}</p>
            <span className="rounded-full border border-border bg-background/70 px-2 py-0.5 font-mono text-[0.5rem] uppercase tracking-[0.13em] text-muted-foreground">
              Preview
            </span>
          </div>
          <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
            {meta.detail}
          </p>
        </div>
      </div>

      <div className={cn("space-y-3 p-3", compact && "space-y-2.5")}>
        {action.type !== "open_library"
        && action.type !== "manage_resource"
        && !controlAction ? (
          <div className="space-y-1.5">
            <FieldLabel>
              {action.type === "upload_interview" ? "Interview title" : "Title"}
            </FieldLabel>
            <Input
              aria-label={
                action.type === "upload_interview" ? "Interview title" : "Action title"
              }
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="h-9 rounded-xl bg-background"
            />
          </div>
        ) : null}

        {action.type === "set_theme" ? (
          <div className="grid grid-cols-3 gap-2">
            {([
              { value: "light", label: "Light", icon: Sun },
              { value: "dark", label: "Dark", icon: Moon },
              { value: "system", label: "System", icon: Settings },
            ] as const).map((choice) => {
              const ChoiceIcon = choice.icon;
              return (
                <button
                  key={choice.value}
                  type="button"
                  aria-pressed={theme === choice.value}
                  onClick={() => setThemeDraft(choice.value)}
                  className={cn(
                    "flex min-w-0 items-center justify-center gap-2 rounded-xl border px-2 py-3 text-[0.75rem] transition-colors",
                    theme === choice.value
                      ? "border-moss/55 bg-accent text-foreground"
                      : "border-border bg-background text-muted-foreground hover:border-moss/35 hover:text-foreground",
                  )}
                >
                  <ChoiceIcon className="size-3.5 shrink-0" />
                  {choice.label}
                </button>
              );
            })}
          </div>
        ) : null}

        {action.type === "set_language" ? (
          <div className="space-y-1.5">
            <FieldLabel>System language</FieldLabel>
            <NativeSelect
              aria-label="System language"
              value={language}
              onChange={(event) => setLanguage(event.target.value as "de" | "en")}
            >
              <option value="en">English</option>
              <option value="de">Deutsch</option>
            </NativeSelect>
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              Navigation, labels and future assistant responses use this language.
            </p>
          </div>
        ) : null}

        {action.type === "update_assistant_preferences" ? (
          <div className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-3">
              <div className="space-y-1.5">
                <FieldLabel>Detail</FieldLabel>
                <NativeSelect
                  aria-label="Assistant response detail"
                  value={preferenceDraft.detail}
                  onChange={(event) =>
                    setPreferenceDraft((current) => ({
                      ...current,
                      detail: event.target.value as AssistantPreferences["detail"],
                    }))
                  }
                >
                  <option value="concise">Concise</option>
                  <option value="balanced">Balanced</option>
                  <option value="thorough">Thorough</option>
                </NativeSelect>
              </div>
              <div className="space-y-1.5">
                <FieldLabel>Tone</FieldLabel>
                <NativeSelect
                  aria-label="Assistant response tone"
                  value={preferenceDraft.tone}
                  onChange={(event) =>
                    setPreferenceDraft((current) => ({
                      ...current,
                      tone: event.target.value as AssistantPreferences["tone"],
                    }))
                  }
                >
                  <option value="direct">Direct</option>
                  <option value="academic">Academic</option>
                  <option value="explanatory">Explanatory</option>
                  <option value="critical">Critical reviewer</option>
                </NativeSelect>
              </div>
              <div className="space-y-1.5">
                <FieldLabel>Format</FieldLabel>
                <NativeSelect
                  aria-label="Assistant response format"
                  value={preferenceDraft.format}
                  onChange={(event) =>
                    setPreferenceDraft((current) => ({
                      ...current,
                      format: event.target.value as AssistantPreferences["format"],
                    }))
                  }
                >
                  <option value="adaptive">Adaptive</option>
                  <option value="prose">Prose</option>
                  <option value="structured">Structured</option>
                </NativeSelect>
              </div>
            </div>
            <div className="space-y-1.5">
              <FieldLabel>Persistent instruction · optional</FieldLabel>
              <Textarea
                aria-label="Persistent assistant instruction"
                value={preferenceDraft.custom_instructions}
                onChange={(event) =>
                  setPreferenceDraft((current) => ({
                    ...current,
                    custom_instructions: event.target.value,
                  }))
                }
                maxLength={800}
                className="min-h-16 rounded-xl bg-background"
              />
              <p className="text-right font-mono text-[0.5625rem] text-muted-foreground">
                {preferenceDraft.custom_instructions.length} / 800
              </p>
            </div>
          </div>
        ) : null}

        {action.type === "open_settings" ? (
          <div className="rounded-xl border border-border bg-background px-3 py-3">
            <p className="text-[0.75rem] font-medium text-foreground">
              {action.section === "api-keys"
                ? "API keys"
                : action.section === "assistant"
                  ? "AI behavior"
                  : action.section === "integrations"
                      ? "Integrations"
                      : action.section === "team"
                        ? "Team"
                        : action.section === "webhooks"
                          ? "Webhooks"
                          : action.section === "legal"
                            ? "Legal"
                            : "Account"}
            </p>
            <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
              The protected settings dialog opens directly on this section.
            </p>
          </div>
        ) : null}

        {action.type === "connect_reference_manager" ? (
          <div className="rounded-xl border border-moss/25 bg-moss-surface/10 px-3 py-3">
            <p className="text-[0.75rem] font-medium text-foreground">
              Continue with {action.provider === "citavi" ? "Citavi" : "Zotero"}
            </p>
            <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
              Credentials and library identifiers are entered only in the protected
              integration form. They are never copied into this chat.
            </p>
          </div>
        ) : null}

        {action.type === "manage_resource" ? (
          <div className="space-y-3">
            <div className="rounded-xl border border-border bg-background px-3 py-2.5">
              <div className="flex items-center gap-2">
                {action.operation === "delete" ? (
                  <Trash2 className="size-3.5 text-destructive" />
                ) : action.operation === "attach_to_manuscript" ? (
                  <Link2 className="size-3.5 text-moss" />
                ) : (
                  <Pencil className="size-3.5 text-moss" />
                )}
                <p className="text-[0.75rem] font-medium capitalize">
                  {(action.operation ?? "open").replaceAll("_", " ")}
                </p>
              </div>
              <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                The exact tenant-owned item below is the only resource this
                confirmation can change.
              </p>
            </div>

            <div className="space-y-1.5">
              <FieldLabel>Workspace item</FieldLabel>
              {resourceCandidates.length ? (
                <NativeSelect
                  aria-label="Workspace item"
                  value={selectedResourceId}
                  onChange={(event) => setSelectedResourceId(event.target.value)}
                >
                  {resourceCandidates.map((candidate) => (
                    <option key={`${candidate.resourceType}:${candidate.id}`} value={candidate.id}>
                      {candidate.title}
                      {candidate.subtitle ? ` · ${candidate.subtitle}` : ""}
                    </option>
                  ))}
                </NativeSelect>
              ) : (
                <div className="rounded-xl border border-dashed border-border px-3 py-4 text-[0.75rem] text-muted-foreground">
                  No matching {action.resource_type?.replaceAll("_", " ")} is
                  available in this workspace.
                </div>
              )}
              {action.selector ? (
                <p className="line-clamp-2 text-[0.625rem] leading-relaxed text-muted-foreground">
                  Resolved from: “{action.selector}”
                </p>
              ) : null}
            </div>

            {action.operation === "rename" ? (
              <div className="space-y-1.5">
                <FieldLabel>New name</FieldLabel>
                <Input
                  aria-label="New resource name"
                  value={newName}
                  onChange={(event) => setNewName(event.target.value)}
                  maxLength={240}
                  className="h-9 rounded-xl bg-background"
                />
              </div>
            ) : null}

            {action.operation === "move" ? (
              <div className="space-y-1.5">
                <FieldLabel>Destination project</FieldLabel>
                <NativeSelect
                  aria-label="Destination project"
                  value={destinationProjectId ?? ""}
                  onChange={(event) =>
                    setDestinationProjectId(
                      event.target.value ? Number(event.target.value) : null,
                    )}
                >
                  <option value="">Select a project</option>
                  {(projects ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            ) : null}

            {action.operation === "attach_to_manuscript" ? (
              <div className="space-y-1.5">
                <FieldLabel>Destination manuscript</FieldLabel>
                <NativeSelect
                  aria-label="Destination manuscript"
                  value={destinationWriterId}
                  onChange={(event) => setDestinationWriterId(event.target.value)}
                >
                  <option value="">Select a manuscript</option>
                  {writerDocuments.map((document) => (
                    <option key={document.public_id} value={document.public_id}>
                      {document.title}
                    </option>
                  ))}
                </NativeSelect>
                <p className="text-[0.625rem] leading-relaxed text-muted-foreground">
                  The source remains in its current workspace and becomes
                  available to the selected writing agent.
                </p>
              </div>
            ) : null}

            {action.operation === "update_status" ? (
              <div className="space-y-1.5">
                <FieldLabel>New status</FieldLabel>
                <NativeSelect
                  aria-label="New resource status"
                  value={resourceStatus}
                  onChange={(event) => setResourceStatus(event.target.value as typeof resourceStatus)}
                >
                  <option value="">Select a status</option>
                  {action.resource_type === "survey" ? (
                    <>
                      <option value="draft">Draft</option>
                      <option value="live">Live</option>
                      <option value="closed">Closed</option>
                    </>
                  ) : (
                    <>
                      <option value="active">Active</option>
                      <option value="paused">Paused</option>
                      <option value="complete">Complete</option>
                      <option value="archived">Archived</option>
                    </>
                  )}
                </NativeSelect>
              </div>
            ) : null}

            {action.operation === "delete" && selectedResource ? (
              <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2.5">
                <p className="text-[0.75rem] font-medium text-destructive">
                  Delete “{selectedResource.title}”
                </p>
                <p className="mt-1 text-[0.625rem] leading-relaxed text-muted-foreground">
                  This only runs after the confirmation below. Dependencies
                  and active jobs are still checked by the destination API.
                </p>
              </div>
            ) : null}
          </div>
        ) : null}

        {action.type === "create_visual" ? (
          <>
            <div className="space-y-1.5">
              <FieldLabel>Scientific brief</FieldLabel>
              <Textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                className="min-h-28 rounded-xl bg-background"
                maxLength={FIGURE_PROMPT_MAX_CHARACTERS}
              />
              <p
                className={cn(
                  "text-right font-mono text-[0.5625rem]",
                  visualPromptTooLong ? "text-destructive" : "text-muted-foreground",
                )}
              >
                {prompt.length.toLocaleString()} /{" "}
                {FIGURE_PROMPT_MAX_CHARACTERS.toLocaleString()}
              </p>
              {visualPromptTooLong ? (
                <p className="text-[0.625rem] text-destructive">
                  Shorten the saved visual brief to 16,000 characters before
                  rendering.
                </p>
              ) : null}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <NativeSelect
                aria-label="Visual kind"
                value={visualKind}
                onChange={(event) =>
                  setVisualKind(event.target.value as typeof visualKind)
                }
              >
                {["method", "architecture", "flow", "concept", "plot"].map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </NativeSelect>
              <NativeSelect
                aria-label="Aspect ratio"
                value={aspectRatio}
                onChange={(event) =>
                  setAspectRatio(event.target.value as typeof aspectRatio)
                }
              >
                {["1:1", "4:3", "3:2", "16:9", "2:3"].map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </NativeSelect>
              <NativeSelect
                aria-label="Resolution"
                value={resolution}
                onChange={(event) =>
                  setResolution(event.target.value as typeof resolution)
                }
              >
                {["1k", "2k", "4k"].map((item) => (
                  <option key={item} value={item}>{item.toUpperCase()}</option>
                ))}
              </NativeSelect>
              <NativeSelect
                aria-label="Review passes"
                value={reviewPasses}
                onChange={(event) =>
                  setReviewPasses(Number(event.target.value) as 0 | 1 | 2)
                }
              >
                <option value={0}>Fast</option>
                <option value={1}>Checked</option>
                <option value={2}>Strict</option>
              </NativeSelect>
            </div>
            {action.grounding_note || missingPlotData ? (
              <div
                className={cn(
                  "rounded-xl border px-3 py-2 text-[0.6875rem] leading-relaxed",
                  missingPlotData
                    ? "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-200"
                    : "border-moss/25 bg-moss-surface/10 text-muted-foreground",
                )}
              >
                {missingPlotData
                  ? (
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span>
                          A quantitative plot needs at least two exact labelled
                          values and units in the brief.
                        </span>
                        <button
                          type="button"
                          className="rounded-full border border-current/25 px-2.5 py-1 font-medium transition-colors hover:bg-amber-500/10"
                          onClick={() => {
                            setVisualKind("concept");
                            setPrompt(conceptualEvidenceBrief(title));
                          }}
                        >
                          Use evidence map
                        </button>
                      </div>
                    )
                  : action.grounding_note}
              </div>
            ) : null}
          </>
        ) : null}

        {action.type === "create_survey" ? (
          <>
            <div className="space-y-1.5">
              <FieldLabel>Description</FieldLabel>
              <Textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="min-h-16 rounded-xl bg-background"
              />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <FieldLabel>Questions</FieldLabel>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 rounded-full text-[0.6875rem]"
                  onClick={() => setQuestions((current) => [...current, makeQuestion()])}
                >
                  <Plus className="size-3" />
                  Add
                </Button>
              </div>
              {questions.map((item, index) => (
                <div
                  key={`${action.id}-question-${index}`}
                  className="space-y-2 rounded-xl border border-border bg-secondary/20 p-2.5"
                >
                  <div className="flex gap-2">
                    <Input
                      aria-label={`Question ${index + 1}`}
                      value={item.title}
                      placeholder={`Question ${index + 1}`}
                      onChange={(event) =>
                        updateQuestion(index, { title: event.target.value })
                      }
                      className="h-8 rounded-lg bg-background"
                    />
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8 shrink-0 rounded-full"
                      disabled={questions.length === 1}
                      aria-label={`Delete question ${index + 1}`}
                      onClick={() =>
                        setQuestions((current) =>
                          current.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <NativeSelect
                      aria-label={`Question ${index + 1} type`}
                      wrapperClassName="w-44 max-w-full"
                      value={item.type}
                      onChange={(event) =>
                        updateQuestion(index, {
                          type: event.target.value as SurveyQuestionType,
                        })
                      }
                      className="h-8 rounded-lg text-[0.6875rem]"
                    >
                      <option value="short_text">Short answer</option>
                      <option value="long_text">Long answer</option>
                      <option value="single_choice">Single choice</option>
                      <option value="multiple_choice">Multiple choice</option>
                      <option value="rating">Rating</option>
                      <option value="scale">Scale</option>
                    </NativeSelect>
                    <label className="flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={item.required}
                        onChange={(event) =>
                          updateQuestion(index, { required: event.target.checked })
                        }
                      />
                      Required
                    </label>
                  </div>
                  <Input
                    aria-label={`Question ${index + 1} description`}
                    value={item.description}
                    placeholder="Optional context for participants"
                    onChange={(event) =>
                      updateQuestion(index, { description: event.target.value })
                    }
                    className="h-8 rounded-lg bg-background text-[0.75rem]"
                  />
                  {item.type === "single_choice" || item.type === "multiple_choice" ? (
                    <Textarea
                      aria-label={`Question ${index + 1} options`}
                      value={item.options.join("\n")}
                      placeholder={"One option per line"}
                      onChange={(event) =>
                        updateQuestion(index, {
                          options: event.target.value
                            .split("\n")
                            .map((value) => value.trim()),
                        })
                      }
                      className="min-h-16 rounded-lg bg-background text-[0.75rem]"
                    />
                  ) : null}
                  {item.type === "scale" ? (
                    <div className="grid grid-cols-2 gap-2">
                      <Input
                        aria-label={`Question ${index + 1} minimum`}
                        type="number"
                        value={item.min ?? 1}
                        onChange={(event) =>
                          updateQuestion(index, { min: Number(event.target.value) })
                        }
                        className="h-8 rounded-lg bg-background"
                      />
                      <Input
                        aria-label={`Question ${index + 1} maximum`}
                        type="number"
                        value={item.max ?? 10}
                        onChange={(event) =>
                          updateQuestion(index, { max: Number(event.target.value) })
                        }
                        className="h-8 rounded-lg bg-background"
                      />
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : null}

        {action.type === "create_ai_interview" ? (
          <>
            <div className="grid gap-2 sm:grid-cols-[1fr_7rem]">
              <div className="space-y-1.5">
                <FieldLabel>Research goal</FieldLabel>
                <Textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  className="min-h-16 rounded-xl bg-background"
                />
              </div>
              <div className="space-y-1.5">
                <FieldLabel>Language</FieldLabel>
                <NativeSelect
                  aria-label="Interview language"
                  value={language}
                  onChange={(event) => setLanguage(event.target.value as "de" | "en")}
                >
                  <option value="en">English</option>
                  <option value="de">Deutsch</option>
                </NativeSelect>
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <FieldLabel>Interview guide</FieldLabel>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 rounded-full text-[0.6875rem]"
                  onClick={() => setSections((current) => [...current, makeSection()])}
                >
                  <Plus className="size-3" />
                  Add topic
                </Button>
              </div>
              {sections.map((item, index) => (
                <div
                  key={`${action.id}-section-${index}`}
                  className="space-y-2 rounded-xl border border-border bg-secondary/20 p-2.5"
                >
                  <div className="flex gap-2">
                    <Input
                      aria-label={`Interview topic ${index + 1} title`}
                      value={item.title}
                      placeholder={`Topic ${index + 1}`}
                      onChange={(event) =>
                        updateSection(index, { title: event.target.value })
                      }
                      className="h-8 rounded-lg bg-background"
                    />
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="size-8 shrink-0 rounded-full"
                      disabled={sections.length === 1}
                      aria-label={`Delete interview topic ${index + 1}`}
                      onClick={() =>
                        setSections((current) =>
                          current.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                  <Textarea
                    aria-label={`Interview topic ${index + 1} core question`}
                    value={item.question}
                    placeholder="Open core question"
                    onChange={(event) =>
                      updateSection(index, { question: event.target.value })
                    }
                    className="min-h-16 rounded-lg bg-background"
                  />
                  <Textarea
                    aria-label={`Interview topic ${index + 1} probes`}
                    value={item.probes.join("\n")}
                    placeholder={"Follow-up probes, one per line"}
                    onChange={(event) =>
                      updateSection(index, {
                        probes: event.target.value
                          .split("\n")
                          .map((value) => value.trim()),
                      })
                    }
                    className="min-h-14 rounded-lg bg-background text-[0.75rem]"
                  />
                  <label className="flex items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={item.must_cover}
                      onChange={(event) =>
                        updateSection(index, { must_cover: event.target.checked })
                      }
                    />
                    Must cover
                  </label>
                </div>
              ))}
            </div>
          </>
        ) : null}

        {action.type === "start_review" ? (
          <>
            <div className="space-y-1.5">
              <FieldLabel>Research question</FieldLabel>
              <Textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                className="min-h-20 rounded-xl bg-background"
              />
            </div>
            <div className="space-y-1.5">
              <FieldLabel>Boolean query · optional</FieldLabel>
              <Textarea
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="min-h-14 rounded-xl bg-background font-mono text-[0.75rem]"
              />
            </div>
          </>
        ) : null}

        {action.type === "create_manuscript" || action.type === "create_project" ? (
          <div className="space-y-1.5">
            <FieldLabel>
              {action.type === "create_project" ? "Description" : "Writing objective"}
            </FieldLabel>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="min-h-16 rounded-xl bg-background"
            />
          </div>
        ) : null}

        {action.type === "open_data_hub"
        || action.type === "open_library"
        || action.type === "upload_interview" ? (
          <div className="space-y-1.5">
            <FieldLabel>
              {action.type === "open_library"
                ? "Library focus"
                : action.type === "upload_interview"
                  ? "Interview guide or context"
                  : "Dataset description"}
            </FieldLabel>
            <Textarea
              aria-label={`${meta.label} instructions`}
              value={description}
              placeholder={meta.detail}
              onChange={(event) => setDescription(event.target.value)}
              className="min-h-16 rounded-xl bg-background"
              maxLength={4000}
            />
          </div>
        ) : null}

        {PROJECT_TARGET_ACTIONS.has(action.type)
        && action.type !== "open_library"
        && (projects?.length ?? 0) > 0 ? (
          <div className="space-y-1.5">
            <FieldLabel>Project</FieldLabel>
            <NativeSelect
              aria-label="Target project"
              value={projectId ?? ""}
              onChange={(event) =>
                setProjectId(event.target.value ? Number(event.target.value) : null)
              }
            >
              <option value="">No project</option>
              {projects?.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </NativeSelect>
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border/70 bg-secondary/20 p-3">
        <span className="mr-auto inline-flex items-center gap-1.5 text-[0.625rem] text-muted-foreground">
          <ShieldCheck className="size-3 text-moss" />
          {controlAction
            ? "Nothing changes until you confirm"
            : action.type === "manage_resource"
              ? "The selected item is rechecked when you confirm"
              : "Nothing runs until you confirm"}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 rounded-full text-[0.6875rem]"
          onClick={() => setDismissed(true)}
        >
          Dismiss
        </Button>
        <Button
          type="button"
          size="sm"
          variant={
            action.type === "manage_resource" && action.operation === "delete"
              ? "destructive"
              : "default"
          }
          className="h-8 rounded-full text-[0.6875rem]"
          disabled={
            execute.isPending
            || missingPlotData
            || visualPromptTooLong
            || manageActionBlocked
          }
          onClick={() => {
            if (
              action.type === "manage_resource"
              && action.operation === "delete"
              && selectedResource
            ) {
              setDeleteTarget({ ...selectedResource });
              return;
            }
            execute.mutate();
          }}
        >
          {execute.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {confirmLabel}
        </Button>
      </div>
      <ConfirmDeleteDialog
        target={
          deleteTarget
            ? {
                title: `Delete “${deleteTarget.title}”?`,
                description:
                  "This permanently removes the selected workspace item. Dependencies and active jobs are rechecked by the destination service before anything is deleted.",
                action: "Delete item",
                cancel: "Keep item",
              }
            : null
        }
        pending={execute.isPending}
        onCancel={() => {
          if (!execute.isPending && !deleteInFlightRef.current) setDeleteTarget(null);
        }}
        onConfirm={() => {
          if (!deleteTarget || execute.isPending || deleteInFlightRef.current) return;
          if (
            !selectedResource
            || selectedResource.id !== deleteTarget.id
            || selectedResource.resourceType !== deleteTarget.resourceType
          ) {
            setDeleteTarget(null);
            toast.error("The selected item changed. Nothing was deleted.");
            return;
          }
          deleteInFlightRef.current = true;
          execute.mutate();
        }}
      />
    </div>
  );
}

export function WorkspaceActionList({
  actions,
  compact = false,
}: {
  actions?: WorkspaceAction[];
  compact?: boolean;
}) {
  if (!actions?.length) return null;
  return (
    <div className="space-y-2">
      {actions.map((action) => (
        <WorkspaceActionCard key={action.id} action={action} compact={compact} />
      ))}
    </div>
  );
}
