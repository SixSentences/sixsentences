"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import SixMark from "@/components/brand/six-mark";
import { useActiveProject } from "@/lib/project-context";

/**
 * Project detail pages have been retired. Keep old bookmarks useful by
 * selecting their project as the global context before returning to Search.
 */
export default function LegacyProjectRedirect() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { setActiveProjectId } = useActiveProject();

  useEffect(() => {
    const projectId = Number(params.id);
    if (Number.isInteger(projectId) && projectId > 0) {
      setActiveProjectId(projectId);
    }
    router.replace("/");
  }, [params.id, router, setActiveProjectId]);

  return (
    <main className="grid min-h-[50vh] place-items-center" aria-live="polite">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <SixMark animated className="size-6 text-moss" title="Opening Search" />
        Opening Search…
      </div>
    </main>
  );
}
