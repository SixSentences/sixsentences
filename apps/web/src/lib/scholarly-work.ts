const OPENALEX_WORK_ID = /^W[1-9]\d*$/i;
const PUBMED_WORK_ID = /^pubmed:([1-9]\d{0,11})$/i;
const DOI = /^10\.\d{4,9}\/\S+$/i;

export function normalizeScholarlyWorkId(value: string): string {
  const trimmed = value.trim();
  const pubmed = PUBMED_WORK_ID.exec(trimmed);
  if (pubmed) return `pubmed:${pubmed[1]}`;
  if (OPENALEX_WORK_ID.test(trimmed)) return `W${trimmed.slice(1)}`;
  return trimmed;
}

export function scholarlyDoiUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const doi = value
    .trim()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/^doi:\s*/i, "");
  return DOI.test(doi) ? `https://doi.org/${doi}` : null;
}

export function scholarlyWorkUrl(
  value: string,
  doi?: string | null,
): string | null {
  const workId = normalizeScholarlyWorkId(value);
  const pubmed = PUBMED_WORK_ID.exec(workId);
  if (pubmed) return `https://pubmed.ncbi.nlm.nih.gov/${pubmed[1]}/`;
  if (OPENALEX_WORK_ID.test(workId)) return `https://openalex.org/${workId}`;
  return scholarlyDoiUrl(doi ?? workId);
}
