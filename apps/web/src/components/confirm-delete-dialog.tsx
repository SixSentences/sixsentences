"use client";

import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export type ConfirmDeleteTarget = {
  title: string;
  description: string;
  action: string;
  cancel?: string;
};

/**
 * The one confirm gate for destructive deletes: open while `target` is set.
 * The dialog stays open during the mutation; close it from onSuccess.
 */
export function ConfirmDeleteDialog({
  target,
  pending,
  onCancel,
  onConfirm,
}: {
  target: ConfirmDeleteTarget | null;
  pending?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>{target?.title}</AlertDialogTitle>
          <AlertDialogDescription>{target?.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{target?.cancel ?? "Keep it"}</AlertDialogCancel>
          <AlertDialogAction
            disabled={pending}
            onClick={(event) => {
              event.preventDefault();
              onConfirm();
            }}
            variant="destructive"
          >
            {pending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {target?.action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
