"use client";

import { useParams } from "next/navigation";

import RunView from "@/components/run/run-view";

export default function RunPage() {
  const params = useParams<{ id: string }>();
  // the URL carries the run's opaque public id (older links with the
  // integer key keep working — the API resolves both)
  return <RunView runId={params.id} />;
}
