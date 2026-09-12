function normalizedLegalVersion(value: string | undefined): string | null {
  const version = value?.trim() ?? "";
  return version && version.length <= 64 && !/[\u0000-\u001f\u007f]/.test(version)
    ? version
    : null;
}

export interface SignupLegalVersions {
  terms: string;
  privacy: string;
  dpa: string;
}

/**
 * Legal versions are deployment-owned public configuration. Registration
 * fails closed when an operator has not supplied the complete set.
 */
export const SIGNUP_LEGAL_VERSIONS: SignupLegalVersions | null = (() => {
  const terms = normalizedLegalVersion(process.env.NEXT_PUBLIC_TERMS_VERSION);
  const privacy = normalizedLegalVersion(process.env.NEXT_PUBLIC_PRIVACY_VERSION);
  const dpa = normalizedLegalVersion(process.env.NEXT_PUBLIC_DPA_VERSION);
  return terms && privacy && dpa ? { terms, privacy, dpa } : null;
})();

export interface SignupLegalAcceptance {
  age_requirement_confirmed: boolean;
  terms_accepted: boolean;
  terms_version: string;
  privacy_acknowledged: boolean;
  privacy_version: string;
  dpa_accepted: boolean;
  dpa_version: string;
  /** Compatibility-only. The public client never opts users into operator marketing. */
  marketing_consent: boolean;
}
