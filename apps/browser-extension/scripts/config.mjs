import { createPublicKey } from "node:crypto";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const API_PATH = /^(?:\/[A-Za-z0-9._~-]+)*$/;
const PUBLIC_KEY = /^[A-Za-z0-9+/]+={0,2}$/;

function parseUrl(name, rawValue, { allowApiPath, allowInsecureLoopback }) {
  if (typeof rawValue !== "string" || rawValue.length === 0) {
    throw new Error(`${name} is required; no default service is selected.`);
  }
  if (rawValue !== rawValue.trim()) {
    throw new Error(`${name} must not contain surrounding whitespace.`);
  }

  let parsed;
  try {
    parsed = new URL(rawValue);
  } catch {
    throw new Error(`${name} must be an absolute URL.`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${name} must not contain credentials, a query, or a fragment.`);
  }
  if (!parsed.hostname || parsed.hostname.includes("*")) {
    throw new Error(`${name} must select one exact hostname.`);
  }
  if (parsed.protocol !== "https:") {
    const explicitLoopback = parsed.protocol === "http:"
      && allowInsecureLoopback
      && LOOPBACK_HOSTS.has(parsed.hostname.toLowerCase());
    if (!explicitLoopback) {
      throw new Error(`${name} must use HTTPS; HTTP is limited to explicitly enabled loopback development.`);
    }
  } else if (parsed.port) {
    throw new Error(`${name} must use the default HTTPS port so its host permission is exact.`);
  }

  const rawPath = parsed.pathname;
  const rawPathStart = rawValue.indexOf("/", rawValue.indexOf("//") + 2);
  const unparsedPath = rawPathStart === -1 ? "" : rawValue.slice(rawPathStart);
  if (/%(?:2e|2f|5c)/i.test(rawValue)
      || rawValue.includes("\\")
      || /(?:^|\/)\.{1,2}(?:\/|$)/.test(unparsedPath)) {
    throw new Error(`${name} contains an encoded or ambiguous path separator.`);
  }
  if (!allowApiPath && rawPath !== "/") {
    throw new Error(`${name} must be a pathless origin.`);
  }
  if (allowApiPath && !API_PATH.test(rawPath.replace(/\/$/, ""))) {
    throw new Error(`${name} contains an unsupported API path.`);
  }
  if (allowApiPath && rawPath.length > 1 && rawPath.endsWith("/")) {
    throw new Error(`${name} must not have a trailing API path separator.`);
  }

  const path = allowApiPath && rawPath !== "/" ? rawPath : "";
  return `${parsed.origin}${path}`;
}

export function validatePublicKey(rawValue) {
  if (rawValue === undefined || rawValue === "") return null;
  if (rawValue !== rawValue.trim()
      || rawValue.length < 128
      || rawValue.length > 8192
      || rawValue.length % 4 !== 0
      || !PUBLIC_KEY.test(rawValue)) {
    throw new Error("SIX_EXTENSION_PUBLIC_KEY must be unwrapped base64 SPKI DER public-key data.");
  }
  let key;
  try {
    key = createPublicKey({
      key: Buffer.from(rawValue, "base64"),
      format: "der",
      type: "spki",
    });
  } catch {
    throw new Error("SIX_EXTENSION_PUBLIC_KEY is not a valid SubjectPublicKeyInfo public key.");
  }
  if (key.asymmetricKeyType !== "rsa") {
    throw new Error("SIX_EXTENSION_PUBLIC_KEY must contain an RSA public key.");
  }
  return rawValue;
}

export function deploymentConfig(environment) {
  const allowInsecureLoopback = environment.SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK === "true";
  if (environment.SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK
      && environment.SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK !== "true"
      && environment.SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK !== "false") {
    throw new Error("SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK must be exactly true or false when set.");
  }
  return Object.freeze({
    appOrigin: parseUrl("APP_ORIGIN", environment.APP_ORIGIN, {
      allowApiPath: false,
      allowInsecureLoopback,
    }),
    apiOrigin: parseUrl("API_ORIGIN", environment.API_ORIGIN, {
      allowApiPath: true,
      allowInsecureLoopback,
    }),
    publicKey: validatePublicKey(environment.SIX_EXTENSION_PUBLIC_KEY),
  });
}

export function apiHostPermission(apiOrigin) {
  const parsed = new URL(apiOrigin);
  return `${parsed.protocol}//${parsed.hostname}/*`;
}
