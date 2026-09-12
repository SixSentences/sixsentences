export type PasswordStrengthContext = {
  email?: string;
  name?: string;
};

export type PasswordStrengthResult = {
  criteria: {
    length: boolean;
    variety: boolean;
    notCommon: boolean;
    notPersonal: boolean;
  };
  score: number;
  strong: boolean;
  label: "Weak" | "Fair" | "Good" | "Strong";
};

const COMMON_PASSWORDS = new Set([
  "admin",
  "adminadmin",
  "changeme",
  "letmein",
  "password",
  "password1",
  "password123",
  "qwerty",
  "qwerty123",
  "sixsentences",
  "welcome",
  "welcome123",
]);

const OBVIOUS_SEQUENCES = ["123456", "abcdef", "qwerty"];

function normalizePasswordText(value: string): string {
  return Array.from(value.normalize("NFKC").toLocaleLowerCase())
    .filter((character) => /[\p{L}\p{N}]/u.test(character))
    .join("");
}

export function evaluatePasswordStrength(
  password: string,
  context: PasswordStrengthContext = {},
): PasswordStrengthResult {
  const characters = Array.from(password);
  const characterClasses = [
    /\p{Ll}/u.test(password),
    /\p{Lu}/u.test(password),
    /\p{N}/u.test(password),
    /[^\p{L}\p{N}]/u.test(password),
  ].filter(Boolean).length;
  const normalized = normalizePasswordText(password);
  const personalTokens = [
    context.email?.split("@", 1)[0] ?? "",
    context.name ?? "",
  ]
    .map(normalizePasswordText)
    .filter((token) => token.length >= 4);

  const criteria = {
    length: characters.length >= 12,
    variety: characters.length >= 16 || characterClasses >= 3,
    notCommon:
      characters.length > 0 &&
      !COMMON_PASSWORDS.has(normalized) &&
      !OBVIOUS_SEQUENCES.some((sequence) => normalized.includes(sequence)) &&
      !(normalized.length >= 8 && new Set(Array.from(normalized)).size === 1),
    notPersonal:
      characters.length > 0 &&
      !personalTokens.some((token) => normalized.includes(token)),
  };
  const strong = Object.values(criteria).every(Boolean);
  const score =
    characters.length === 0
      ? 0
      : strong
        ? 4
        : criteria.length && criteria.variety
          ? 3
          : characters.length >= 8
            ? 2
            : 1;

  return {
    criteria,
    score,
    strong,
    label: score <= 1 ? "Weak" : score === 2 ? "Fair" : score === 3 ? "Good" : "Strong",
  };
}
