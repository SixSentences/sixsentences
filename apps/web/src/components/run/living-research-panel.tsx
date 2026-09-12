"use client";

import { useState, type ComponentType } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Bell,
  BookOpenCheck,
  CalendarClock,
  Check,
  CircleDot,
  FileText,
  Loader2,
  Radio,
  RefreshCcw,
  Route,
  Search,
  ShieldAlert,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import type { LivingResearchWorkspace } from "@/lib/types";
import { cn } from "@/lib/utils";

const SOURCE_LABELS: Record<string, string> = {
  openalex: "OpenAlex",
  citations: "Citation graph",
  retractions: "Retractions",
  web: "Web sources",
  imports: "Reference imports",
};

function sameMonitorSettings(
  left: LivingResearchWorkspace,
  right: LivingResearchWorkspace,
): boolean {
  return (
    left.enabled === right.enabled &&
    left.cadence === right.cadence &&
    left.auto_screen === right.auto_screen &&
    left.notify === right.notify &&
    left.watch_sources.join("|") === right.watch_sources.join("|")
  );
}

export default function LivingResearchPanel({ runId }: { runId: number }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["living-workspace", runId],
    queryFn: () => api.livingWorkspace(runId),
    refetchInterval: (query) =>
      query.state.data?.checks.some((check) =>
        ["pending", "running"].includes(check.status),
      )
        ? 3_000
        : false,
  });
  const [draft, setDraft] = useState<LivingResearchWorkspace | null>(null);
  const settings = draft ?? data;
  const latest = data?.checks[0];
  const openImpacts = data?.impacts.length ?? 0;
  const dirty = Boolean(draft);
  const persistedEnabled = Boolean(data?.enabled);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["living-workspace", runId] });
  const save = useMutation({
    mutationFn: () => {
      if (!settings) throw new Error("Living settings are unavailable.");
      return api.updateLivingSettings(runId, {
        enabled: settings.enabled,
        cadence: settings.cadence,
        auto_screen: settings.auto_screen,
        notify: settings.notify,
        watch_sources: settings.watch_sources,
      });
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<LivingResearchWorkspace>(
        ["living-workspace", runId],
        (current) =>
          current && {
            ...current,
            enabled: saved.enabled,
            cadence: saved.cadence as LivingResearchWorkspace["cadence"],
            auto_screen: saved.auto_screen,
            notify: saved.notify,
            watch_sources: saved.watch_sources,
          },
      );
      setDraft(null);
      toast.success(saved.enabled ? "Living monitor enabled." : "Living monitor saved.");
      void invalidate();
      void queryClient.invalidateQueries({ queryKey: ["run", String(runId)] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Settings could not be saved."),
  });
  const refresh = useMutation({
    mutationFn: (scope: "delta" | "full") => api.refreshLiving(runId, scope),
    onSuccess: (result) => {
      toast.success("A versioned evidence refresh has started.");
      void invalidate();
      router.push(`/r/${result.public_id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Refresh could not start."),
  });

  const update = (patch: Partial<LivingResearchWorkspace>) => {
    if (!settings || !data) return;
    const next = { ...settings, ...patch };
    setDraft(sameMonitorSettings(next, data) ? null : next);
  };
  const toggleSource = (source: string) => {
    if (!settings) return;
    const next = settings.watch_sources.includes(source)
      ? settings.watch_sources.filter((candidate) => candidate !== source)
      : [...settings.watch_sources, source];
    if (!next.length) {
      toast.error("Keep at least one monitoring source.");
      return;
    }
    update({ watch_sources: next });
  };

  if (isLoading || !data || !settings) {
    return (
      <div className="grid h-full place-items-center">
        <Loader2 className="size-5 animate-spin text-moss" />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-4 pb-8">
      <section className="rounded-3xl border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className={cn(
                "grid size-9 shrink-0 place-items-center rounded-full",
                settings.enabled ? "bg-accent text-moss" : "bg-secondary text-muted-foreground",
              )}
            >
              <Radio className="size-4" />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-display text-2xl leading-none text-foreground">
                  Living review
                </h2>
                <span
                  className={cn(
                    "rounded-full px-2.5 py-1 font-mono text-[0.53125rem] uppercase tracking-[0.14em]",
                    settings.enabled
                      ? "bg-accent text-moss"
                      : "bg-secondary text-muted-foreground",
                  )}
                >
                  {settings.enabled ? "Monitor active" : "Monitor off"}
                </span>
                {dirty && (
                  <span className="text-[0.65625rem] text-amber-700">
                    Unsaved changes
                  </span>
                )}
              </div>
              <p className="mt-1 text-[0.71875rem] text-muted-foreground">
                Keep the frozen baseline and compare every refresh as a separate,
                auditable version.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[0.6875rem] font-medium">
              {settings.enabled ? "Enabled" : "Disabled"}
            </span>
            <Switch
              checked={settings.enabled}
              onCheckedChange={(enabled) => update({ enabled })}
            />
          </div>
        </div>

        <div className="grid gap-4 border-t border-border px-5 py-4 md:grid-cols-2 md:items-end 2xl:grid-cols-[180px_minmax(560px,1fr)_minmax(300px,.7fr)_auto]">
          <label className="max-w-48">
            <span className="text-[0.6875rem] font-medium">Check cadence</span>
            <Select
              value={settings.cadence}
              onValueChange={(cadence: LivingResearchWorkspace["cadence"]) =>
                update({ cadence })
              }
            >
              <SelectTrigger className="mt-1.5">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="weekly">Weekly</SelectItem>
                <SelectItem value="monthly">Monthly</SelectItem>
                <SelectItem value="quarterly">Quarterly</SelectItem>
                <SelectItem value="manual">Manual only</SelectItem>
              </SelectContent>
            </Select>
          </label>

          <div>
            <p className="text-[0.6875rem] font-medium">Watched sources</p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {Object.entries(SOURCE_LABELS).map(([source, label]) => {
                const active = settings.watch_sources.includes(source);
                return (
                  <button
                    key={source}
                    type="button"
                    onClick={() => toggleSource(source)}
                    className={cn(
                      "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2 py-2 text-[0.6875rem] transition-colors",
                      active
                        ? "border-moss/30 bg-accent/45 text-foreground"
                        : "border-border text-muted-foreground hover:border-moss/25",
                    )}
                  >
                    <span
                      className={cn(
                        "grid size-3.5 place-items-center rounded-full border",
                        active ? "border-moss bg-moss-surface text-ivory" : "border-border",
                      )}
                    >
                      {active && <Check className="size-2" />}
                    </span>
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <CompactSetting
              icon={BookOpenCheck}
              title="Auto-screen"
              checked={settings.auto_screen}
              onCheckedChange={(auto_screen) => update({ auto_screen })}
            />
            <CompactSetting
              icon={Bell}
              title="Change digest"
              checked={settings.notify}
              onCheckedChange={(notify) => update({ notify })}
            />
          </div>

          <Button
            className="rounded-full lg:min-w-32"
            disabled={!draft || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending && <Loader2 className="size-3.5 animate-spin" />}
            {settings.enabled && !persistedEnabled ? "Enable monitor" : "Save changes"}
          </Button>
        </div>
      </section>

      <div className="mt-3 grid divide-y overflow-hidden rounded-2xl border border-border bg-card sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
        <StatusMetric
          icon={Radio}
          label="Status"
          value={persistedEnabled ? "Active" : "Off"}
          detail={persistedEnabled ? `${data.cadence} checks` : "Baseline only"}
          active={persistedEnabled}
        />
        <StatusMetric
          icon={CalendarClock}
          label="Next check"
          value={
            data.next_check_at
              ? formatDate(data.next_check_at)
              : data.cadence === "manual"
                ? "Manual"
                : "After first check"
          }
          detail={
            data.last_checked_at
              ? `Last ${formatDate(data.last_checked_at)}`
              : "No follow-up yet"
          }
        />
        <StatusMetric
          icon={Search}
          label="Latest delta"
          value={
            latest
              ? `+${latest.added_includes.length} / −${latest.removed_includes.length}`
              : "No delta"
          }
          detail={
            latest
              ? `${formatNumber(latest.new_records)} new records`
              : `${formatNumber(data.baseline.included)} baseline includes`
          }
        />
        <StatusMetric
          icon={Route}
          label="Downstream"
          value={formatNumber(openImpacts)}
          detail={openImpacts ? "Outputs need review" : "No affected output"}
          warning={openImpacts > 0}
        />
      </div>

      <main className="mt-4 grid gap-4 2xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,.65fr)]">
        <section className="rounded-3xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-muted-foreground">
                Evidence versions
              </p>
              <h2 className="mt-1 font-display text-2xl text-foreground">
                What changed since the baseline
              </h2>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-8 rounded-full"
                disabled={!persistedEnabled || dirty || refresh.isPending}
                onClick={() => refresh.mutate("full")}
              >
                Full refresh
              </Button>
              <Button
                size="sm"
                className="h-8 rounded-full"
                disabled={!persistedEnabled || dirty || refresh.isPending}
                onClick={() => refresh.mutate("delta")}
              >
                {refresh.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <RefreshCcw className="size-3.5" />
                )}
                Check now
              </Button>
            </div>
          </div>
          {dirty && (
            <p className="mt-2 text-right text-[0.65625rem] text-amber-700">
              Save the monitor changes before starting a refresh.
            </p>
          )}

          <div className="mt-4 overflow-hidden rounded-2xl border border-border/70">
            <div className="grid grid-cols-[auto_1fr_auto] items-center gap-4 border-b border-border/70 px-4 py-3">
              <span className="grid size-8 place-items-center rounded-full bg-primary text-primary-foreground">
                <CircleDot className="size-3.5" />
              </span>
              <div>
                <p className="text-[0.75rem] font-medium">Baseline review</p>
                <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">
                  {formatNumber(data.baseline.identified)} identified ·{" "}
                  {formatNumber(data.baseline.included)} included ·{" "}
                  {formatNumber(data.baseline.claims)} claims
                </p>
              </div>
              <span className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground">
                frozen
              </span>
            </div>
            {data.checks.map((check, index) => (
              <button
                key={check.id}
                type="button"
                onClick={() => router.push(`/r/${check.id}`)}
                className="grid w-full cursor-pointer grid-cols-[auto_1fr_auto] items-center gap-4 border-b border-border/70 px-4 py-3 text-left last:border-b-0 hover:bg-secondary/25"
              >
                <span
                  className={cn(
                    "grid size-8 place-items-center rounded-full",
                    ["pending", "running"].includes(check.status)
                      ? "bg-accent text-moss"
                      : "bg-secondary text-muted-foreground",
                  )}
                >
                  {["pending", "running"].includes(check.status) ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <RefreshCcw className="size-3.5" />
                  )}
                </span>
                <div>
                  <p className="text-[0.75rem] font-medium">
                    Refresh {data.checks.length - index}
                  </p>
                  <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">
                    {formatDate(check.created_at)} ·{" "}
                    {formatNumber(check.new_records)} new records · +
                    {check.added_includes.length} / −{check.removed_includes.length} includes
                  </p>
                </div>
                <ArrowRight className="size-3.5 text-muted-foreground" />
              </button>
            ))}
            {!data.checks.length && (
              <div className="px-5 py-7 text-center">
                <RefreshCcw className="mx-auto size-5 text-muted-foreground" />
                <p className="mt-2 text-[0.75rem] font-medium">No refresh version yet</p>
                <p className="mt-1 text-[0.6875rem] text-muted-foreground">
                  Enable and save the monitor, then run the first delta check.
                </p>
              </div>
            )}
          </div>

          {latest &&
            (latest.added_includes.length > 0 ||
              latest.removed_includes.length > 0) && (
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <DeltaList
                  title="Newly included"
                  tone="positive"
                  items={latest.added_includes}
                />
                <DeltaList
                  title="No longer included"
                  tone="negative"
                  items={latest.removed_includes}
                />
              </div>
            )}
        </section>

        <section className="rounded-3xl border border-border bg-card p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[0.59375rem] uppercase tracking-[0.2em] text-muted-foreground">
                Impact chain
              </p>
              <h2 className="mt-1 font-display text-2xl text-foreground">
                Conclusions to review
              </h2>
            </div>
            <span
              className={cn(
                "rounded-full px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.14em]",
                data.impacts.length
                  ? "bg-amber-50 text-amber-800 dark:bg-amber-300/10 dark:text-amber-200"
                  : "bg-accent text-moss",
              )}
            >
              {data.impacts.length ? `${data.impacts.length} open` : "stable"}
            </span>
          </div>
          <div className="mt-4 space-y-2">
            {data.impacts.map((impact) => (
              <div
                key={`${impact.kind}-${impact.id}`}
                className={cn(
                  "flex items-start gap-3 rounded-2xl border p-4",
                  impact.severity === "critical"
                    ? "border-red-300/60 bg-red-50/30"
                    : "border-amber-300/55 bg-amber-50/25 dark:border-amber-300/25 dark:bg-amber-300/10",
                )}
              >
                <span
                  className={cn(
                    "grid size-8 shrink-0 place-items-center rounded-full",
                    impact.severity === "critical"
                      ? "bg-red-100 text-red-800"
                      : "bg-amber-100 text-amber-800",
                  )}
                >
                  {impact.kind === "claim" ? (
                    <ShieldAlert className="size-3.5" />
                  ) : (
                    <FileText className="size-3.5" />
                  )}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-2 text-[0.75rem] font-medium">
                    {impact.title}
                  </p>
                  <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {impact.reason}
                  </p>
                </div>
              </div>
            ))}
            {!data.impacts.length && (
              <div className="grid min-h-40 place-items-center rounded-2xl border border-dashed border-border text-center">
                <div>
                  <Check className="mx-auto size-5 text-moss" />
                  <p className="mt-2 text-[0.75rem] font-medium">
                    No connected output is affected
                  </p>
                  <p className="mt-1 max-w-xs text-[0.6875rem] text-muted-foreground">
                    Future deltas trace through claims and manuscripts automatically.
                  </p>
                </div>
              </div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function StatusMetric({
  icon: Icon,
  label,
  value,
  detail,
  active = false,
  warning = false,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
  detail: string;
  active?: boolean;
  warning?: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-3 px-4 py-3">
      <span
        className={cn(
          "grid size-8 shrink-0 place-items-center rounded-full bg-secondary",
          warning ? "text-amber-700" : active ? "text-moss" : "text-muted-foreground",
        )}
      >
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0">
        <p className="font-mono text-[0.53125rem] uppercase tracking-[0.16em] text-muted-foreground">
          {label}
        </p>
        <div className="mt-0.5 flex min-w-0 items-baseline gap-2">
          <p className="shrink-0 text-[0.8125rem] font-medium text-foreground">{value}</p>
          <p className="truncate text-[0.625rem] text-muted-foreground">{detail}</p>
        </div>
      </div>
    </div>
  );
}

function CompactSetting({
  icon: Icon,
  title,
  checked,
  onCheckedChange,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex min-h-10 items-center gap-2 rounded-xl border border-border/70 px-3">
      <Icon className="size-3.5 shrink-0 text-moss" />
      <span className="min-w-0 flex-1 truncate text-[0.6875rem] font-medium">{title}</span>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  );
}

function DeltaList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "positive" | "negative";
  items: Array<{ work_id: string; title: string; year: number | null }>;
}) {
  return (
    <div
      className={cn(
        "rounded-2xl border p-4",
        tone === "positive"
          ? "border-moss/25 bg-accent/25"
          : "border-red-300/45 bg-red-50/20",
      )}
    >
      <p className="font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-muted-foreground">
        {title} · {items.length}
      </p>
      <div className="mt-2 space-y-2">
        {items.slice(0, 5).map((item) => (
          <div key={item.work_id} className="flex items-start gap-2">
            <span
              className={cn(
                "mt-1.5 size-1.5 shrink-0 rounded-full",
                tone === "positive" ? "bg-moss-surface" : "bg-red-700",
              )}
            />
            <p className="line-clamp-2 text-[0.6875rem] leading-snug">
              {item.title} {item.year ? `(${item.year})` : ""}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
