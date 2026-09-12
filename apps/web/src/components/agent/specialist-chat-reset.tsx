"use client";

import {
  type RefObject,
  useCallback,
  useRef,
  useState,
} from "react";
import {
  useMutation,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { Eraser, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SpecialistResourceKind } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export function isClearChatCommand(value: string) {
  return /^\/clear$/i.test(value.trim());
}

export function useSpecialistChatReset({
  resourceKind,
  resourceId,
  queryKey,
  hasHistory,
  onCleared,
}: {
  resourceKind: SpecialistResourceKind;
  resourceId: string;
  queryKey: QueryKey;
  hasHistory: boolean;
  onCleared: () => void;
}) {
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const german = me?.language === "de";
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  const clearChatTriggerRef = useRef<HTMLButtonElement>(null);
  const clearMutation = useMutation({
    mutationFn: () => api.specialistChatClear(resourceKind, resourceId),
    onSuccess: async () => {
      queryClient.setQueryData(queryKey, []);
      onCleared();
      await queryClient.invalidateQueries({ queryKey });
      toast.success(
        german
          ? "Chat geleert. Die Inhalte im Arbeitsbereich bleiben erhalten."
          : "Chat cleared. Your workspace content was kept.",
      );
    },
    onError: (error) => {
      toast.error(
        error instanceof ApiError && error.status === 409
          ? german
            ? "Der Agent arbeitet noch. Stoppe den Auftrag oder warte, bevor du den Chat leerst."
            : "The agent is still working. Stop it or wait before clearing the chat."
          : error instanceof Error
          ? error.message
          : german
            ? "Der Chat konnte nicht geleert werden."
            : "The chat could not be cleared.",
      );
    },
  });

  const performClear = useCallback(async () => {
    try {
      await clearMutation.mutateAsync();
      return true;
    } catch {
      return false;
    }
  }, [clearMutation]);

  const clearChat = useCallback(() => {
    if (hasHistory) {
      setConfirmationOpen(true);
      return;
    }
    void performClear();
  }, [hasHistory, performClear]);

  const confirmClearChat = useCallback(async () => {
    const cleared = await performClear();
    if (cleared) setConfirmationOpen(false);
    return cleared;
  }, [performClear]);

  return {
    clearChat,
    clearing: clearMutation.isPending,
    confirmationOpen,
    setConfirmationOpen,
    confirmClearChat,
    clearChatTriggerRef,
  };
}

export function SpecialistChatResetDialog({
  open,
  clearing,
  returnFocusRef,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  clearing: boolean;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { me } = useAuth();
  const german = me?.language === "de";

  return (
    <AlertDialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!clearing) onOpenChange(nextOpen);
      }}
    >
      <AlertDialogContent
        size="sm"
        className="max-w-[calc(100vw-2rem)]"
        onEscapeKeyDown={(event) => {
          if (clearing) event.preventDefault();
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          window.requestAnimationFrame(() => {
            const trigger = returnFocusRef.current;
            if (trigger?.isConnected) trigger.focus();
          });
        }}
      >
        <AlertDialogHeader>
          <AlertDialogMedia className="rounded-full bg-destructive/10 text-destructive">
            <Eraser className="size-5" />
          </AlertDialogMedia>
          <AlertDialogTitle>
            {german ? "Chat wirklich leeren?" : "Clear this chat?"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {german
              ? "Der Chatverlauf und die angezeigte Agent-Aktivität werden entfernt. Inhalte und erstellte Dateien im Arbeitsbereich bleiben erhalten."
              : "The chat history and displayed agent activity will be removed. Your workspace content and created files will stay unchanged."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={clearing} className="w-full">
            {german ? "Abbrechen" : "Keep chat"}
          </AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={clearing}
            className="w-full"
            onClick={(event) => {
              event.preventDefault();
              onConfirm();
            }}
          >
            {clearing ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {german ? "Chat leeren" : "Clear chat"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function SpecialistChatResetButton({
  onClick,
  clearing,
  triggerRef,
  disabled = false,
  className,
}: {
  onClick: () => void;
  clearing: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
  disabled?: boolean;
  className?: string;
}) {
  const { me } = useAuth();
  const german = me?.language === "de";
  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={onClick}
      disabled={disabled || clearing}
      className={cn(
        "inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border border-border px-2.5 text-[0.6875rem] font-medium text-muted-foreground transition-colors hover:border-moss/40 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45",
        className,
      )}
      aria-label={german ? "Chat leeren" : "Clear chat"}
      title={
        disabled
          ? german
            ? "Stoppe oder beende den aktuellen Agent-Auftrag, bevor du den Chat leerst"
            : "Stop or finish the current agent task before clearing the chat"
          : german
            ? "Chat und Agent-Aktivität leeren; Inhalte bleiben erhalten"
            : "Clear chat and agent activity; workspace content is kept"
      }
    >
      {clearing ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <Eraser className="size-3.5" />
      )}
      <span className="hidden sm:inline">{german ? "Chat leeren" : "Clear chat"}</span>
    </button>
  );
}
