/** Published legal-text versions mirrored by the core API launch gate. */
export const CURRENT_TERMS_VERSION = "2026-09-12";
export const CURRENT_PRIVACY_VERSION = "2026-09-12";
export const CURRENT_DPA_VERSION = "1.11";
export const CURRENT_WITHDRAWAL_VERSION = "2026-08-06";

export interface SignupLegalAcceptance {
  age_requirement_confirmed: boolean;
  terms_accepted: boolean;
  terms_version: string;
  privacy_acknowledged: boolean;
  privacy_version: string;
  dpa_accepted: boolean;
  dpa_version: string;
  marketing_consent: boolean;
}

export interface CheckoutLegalDeclaration {
  terms_accepted: boolean;
  terms_version: string;
  withdrawal_acknowledged: boolean;
  withdrawal_version: string;
  early_performance_requested: boolean;
  compensation_acknowledged: boolean;
  germany_only_confirmed: boolean;
}
