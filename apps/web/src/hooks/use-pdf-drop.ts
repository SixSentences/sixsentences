"use client";

import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { toast } from "sonner";

/**
 * OS-file drag-and-drop for PDF uploads: spread `dropProps` on the drop zone
 * and render an overlay while `dragging`. A depth counter absorbs the
 * dragenter/dragleave flicker that child elements cause.
 */
export function usePdfDrop(onPdfs: (files: File[]) => void) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  const hasFiles = (event: DragEvent) =>
    Array.from(event.dataTransfer?.types ?? []).includes("Files");

  const dropProps = {
    onDragEnter: (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    onDragOver: (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
    },
    onDragLeave: (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop: (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      const pdfs = Array.from(event.dataTransfer.files).filter(
        (file) =>
          file.type === "application/pdf" ||
          file.type.startsWith("image/") ||
          /\.(pdf|png|jpe?g|webp|gif)$/i.test(file.name),
      );
      if (pdfs.length === 0) {
        toast.error("Only PDFs or images can be attached.");
        return;
      }
      onPdfs(pdfs);
    },
  };

  return { dragging, dropProps };
}

/** The dashed overlay a drop zone shows while a file hovers over it. */
export function dropOverlayClass(rounded = "rounded-[1.6rem]"): string {
  return `pointer-events-none absolute inset-0 z-20 grid place-items-center ${rounded} border-2 border-dashed border-moss bg-accent/85 backdrop-blur-[2px]`;
}
