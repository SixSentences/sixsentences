/**
 * Optional client analytics are disabled for the public release.
 * An old deployment website id cannot silently re-enable measurement.
 * Necessary security accounting remains server-side.
 */
export default function Analytics() {
  return null;
}
