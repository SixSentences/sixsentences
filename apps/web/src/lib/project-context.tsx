"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { useProjects } from "@/hooks/queries";

const ACTIVE_PROJECT_KEY = "six_active_project";

type ProjectState = {
  /** null means "All projects" (no filter, runs go to the first project). */
  activeProjectId: number | null;
  setActiveProjectId: (id: number | null) => void;
};

const ProjectContext = createContext<ProjectState | null>(null);

/** The sidebar picks the active project; the composer files new runs into it. */
export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [activeProjectId, setActive] = useState<number | null>(null);
  const projects = useProjects();

  useEffect(() => {
    const stored = window.localStorage.getItem(ACTIVE_PROJECT_KEY);
    if (stored && stored !== "all") setActive(Number(stored));
  }, []);

  const setActiveProjectId = useCallback((id: number | null) => {
    setActive(id);
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, id === null ? "all" : String(id));
  }, []);

  useEffect(() => {
    if (
      activeProjectId !== null &&
      projects.isSuccess &&
      !projects.data.some((project) => project.id === activeProjectId)
    ) {
      setActiveProjectId(null);
    }
  }, [activeProjectId, projects.data, projects.isSuccess, setActiveProjectId]);

  const value = useMemo(
    () => ({ activeProjectId, setActiveProjectId }),
    [activeProjectId, setActiveProjectId],
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useActiveProject(): ProjectState {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useActiveProject must be used inside <ProjectProvider>");
  return ctx;
}
