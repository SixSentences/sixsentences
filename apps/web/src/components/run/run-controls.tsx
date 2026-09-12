"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pause, Play, Square } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { isTerminal } from "@/lib/status";
import type { RunDetail } from "@/lib/types";

/** Pause / resume / cancel controls for a moving run. */
export default function RunControls({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const refresh = () => {
    // Routes may cache the run by its public ID or its numeric API ID.
    void queryClient.invalidateQueries({ queryKey: ["run"] });
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
  };

  const pause = useMutation({
    mutationFn: () => api.pauseRun(run.id),
    onSuccess: () => {
      toast.success("Pausing at the next checkpoint. Nothing is lost.");
      refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Pause failed."),
  });

  const resume = useMutation({
    mutationFn: () => api.resumeRun(run.id),
    onSuccess: () => {
      toast.success("Resuming. Already screened works are skipped.");
      refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Resume failed."),
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(run.id),
    onSuccess: (result) => {
      // Only acknowledge a terminal state after the server has committed it.
      // While the request waits for an active checkpoint, show "Stopping".
      queryClient.setQueriesData<RunDetail>({ queryKey: ["run"] }, (current) =>
        current?.id === run.id
          ? {
              ...current,
              status: result.status,
            }
          : current,
      );
      toast.success("Run cancelled. Completed work is saved.");
      refresh();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Cancel failed.");
      refresh();
    },
  });

  if (isTerminal(run.status)) return null;

  return (
    <div className="flex items-center gap-1">
      {cancel.isPending && (
        <span role="status" className="text-xs text-muted-foreground">
          Stopping…
        </span>
      )}
      {run.status === "running" && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground hover:text-foreground"
              onClick={() => pause.mutate()}
              disabled={pause.isPending || cancel.isPending}
              aria-label="Pause at the next checkpoint"
            >
              {pause.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Pause className="size-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Pause at the next checkpoint</TooltipContent>
        </Tooltip>
      )}

      {run.status === "paused" && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 text-muted-foreground hover:text-foreground"
              onClick={() => resume.mutate()}
              disabled={resume.isPending || cancel.isPending}
              aria-label="Resume the run"
            >
              {resume.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Play className="size-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Resume the run</TooltipContent>
        </Tooltip>
      )}

      <AlertDialog>
        <Tooltip>
          <TooltipTrigger asChild>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-muted-foreground hover:text-destructive"
                disabled={cancel.isPending}
                aria-label="Cancel the run"
              >
                {cancel.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Square className="size-4" />
                )}
              </Button>
            </AlertDialogTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">Cancel the run</TooltipContent>
        </Tooltip>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel this run?</AlertDialogTitle>
            <AlertDialogDescription>
              We request the worker to stop. An in-flight step may take a moment
              to finish before cancellation is confirmed.
              Work done so far (retrieved works, screening decisions and the
              audit log) is kept, but the run cannot be resumed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep running</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => cancel.mutate()}
              variant="destructive"
            >
              Cancel run
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
