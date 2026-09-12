const STORAGE_KEY = "six:landing-question";
const MAX_QUESTION_LENGTH = 600;
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

type StoredQuestion = {
  text: string;
  createdAt: number;
};

function normalizeQuestion(value: string | null): string | null {
  const normalized = value?.trim().slice(0, MAX_QUESTION_LENGTH) ?? "";
  return normalized || null;
}

function storeQuestion(question: StoredQuestion): void {
  const serialized = JSON.stringify(question);
  try {
    window.localStorage.setItem(STORAGE_KEY, serialized);
  } catch {
    window.sessionStorage.setItem(STORAGE_KEY, serialized);
  }
}

function readStoredQuestion(storage: Storage): StoredQuestion | null {
  const raw = storage.getItem(STORAGE_KEY);
  if (!raw) return null;
  storage.removeItem(STORAGE_KEY);

  try {
    const parsed = JSON.parse(raw) as Partial<StoredQuestion>;
    const text = normalizeQuestion(
      typeof parsed.text === "string" ? parsed.text : null,
    );
    const createdAt =
      typeof parsed.createdAt === "number" ? parsed.createdAt : 0;
    if (!text || Date.now() - createdAt > MAX_AGE_MS) return null;
    return { text, createdAt };
  } catch {
    return null;
  }
}

/**
 * Capture a landing-page question from the URL fragment without exposing the
 * research text to server logs, analytics query strings, or referrer headers.
 */
export function captureResearchQuestionFromHash(): void {
  if (typeof window === "undefined" || !window.location.hash) return;

  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const question = normalizeQuestion(fragment.get("question"));
  if (!question) return;

  storeQuestion({ text: question, createdAt: Date.now() });

  fragment.delete("question");
  const remainingFragment = fragment.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${
      remainingFragment ? `#${remainingFragment}` : ""
    }`,
  );
}

/**
 * Read and delete the most recent landing-page question on the first signed-in
 * home view. Drafts expire automatically after seven days.
 */
export function consumeResearchQuestionHandoff(): string | null {
  if (typeof window === "undefined") return null;

  const fromSession = readStoredQuestion(window.sessionStorage);
  if (fromSession) return fromSession.text;

  const fromLocal = readStoredQuestion(window.localStorage);
  return fromLocal?.text ?? null;
}
