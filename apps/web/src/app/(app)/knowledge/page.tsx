import { redirect } from "next/navigation";

export default async function LegacyKnowledgeRedirect({
  searchParams,
}: {
  searchParams: Promise<{ session?: string | string[] }>;
}) {
  const requested = (await searchParams).session;
  const session = Array.isArray(requested) ? requested[0] : requested;
  const suffix = session && /^[A-Za-z0-9_-]{1,128}$/.test(session)
    ? `?session=${encodeURIComponent(session)}`
    : "";
  redirect(`/brainstorming${suffix}`);
}
