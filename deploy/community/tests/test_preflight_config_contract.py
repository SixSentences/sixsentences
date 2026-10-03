"""Verify effective preflight configuration using synthetic files and a Docker stub."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

COMMUNITY = Path(__file__).resolve().parents[1]
PREFLIGHT_SOURCE = (COMMUNITY / "preflight.sh").read_text(encoding="utf-8")
GUARDED_KEYS = (
    "SIX_DEPLOYMENT_MODE",
    "SIX_ALLOW_INSECURE_LOCAL_HTTP",
    "SIX_SITE_ADDRESS",
    "SIX_PUBLIC_ORIGIN",
    "SIX_PUBLIC_API_URL",
    "SIX_LEGAL_BASE_URL",
    "POSTGRES_PASSWORD",
    "SIX_CONNECTOR_ENCRYPTION_KEY",
    "SIX_ERASURE_LEDGER_HMAC_KEY",
    "SIX_BACKUP_DIR",
    "SIX_SELF_SIGNUP",
    "SIX_REQUIRE_EMAIL_VERIFICATION",
    "SIX_SMTP_HOST",
    "SIX_MAIL_FROM",
    "SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED",
    "SIX_GEMINI_DATA_PROCESSING_CONFIRMED",
    "SIX_GEMINI_API_KEY",
    "SIX_PUBMED_ENABLED",
    "SIX_PUBMED_EMAIL",
    "SIX_PUBMED_API_KEY",
)


class PreflightConfigurationContractTests(unittest.TestCase):
    """Run real preflight validation without Docker, private configuration, or services."""

    def setUp(self) -> None:
        """Create a deterministic, isolated local-mode configuration and command stubs."""
        temporary = tempfile.TemporaryDirectory(prefix="six-preflight-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        scripts = self.root / "checkout" / "deploy" / "community"
        scripts.mkdir(parents=True)
        self.checkout = scripts.parents[1]
        self.script = scripts / "preflight.sh"
        self.script.write_text(PREFLIGHT_SOURCE, encoding="utf-8")
        (self.checkout / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        self.target = self.root / "synthetic deployment.env"
        self.calls = self.root / "docker-calls"
        self.image_overrides = self.root / "docker-image-overrides"
        self.values = {
            "SIX_DEPLOYMENT_MODE": "local",
            "SIX_ALLOW_INSECURE_LOCAL_HTTP": "true",
            "SIX_SITE_ADDRESS": "http://127.0.0.1:8080",
            "SIX_PUBLIC_ORIGIN": "http://127.0.0.1:8080",
            "SIX_PUBLIC_API_URL": "http://127.0.0.1:8080/api",
            "SIX_LEGAL_BASE_URL": "http://127.0.0.1:8080/legal",
            "POSTGRES_PASSWORD": "42" * 32,
            "SIX_CONNECTOR_ENCRYPTION_KEY": "A" * 43,
            "SIX_ERASURE_LEDGER_HMAC_KEY": "24" * 32,
            "SIX_BACKUP_DIR": str(self.root / "synthetic backups"),
            "SIX_SELF_SIGNUP": "false",
            "SIX_REQUIRE_EMAIL_VERIFICATION": "true",
            "SIX_SMTP_HOST": "",
            "SIX_MAIL_FROM": "Research Lab <noreply@research.example.org>",
            "SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED": "false",
            "SIX_GEMINI_DATA_PROCESSING_CONFIRMED": "false",
            "SIX_GEMINI_API_KEY": "",
            "SIX_PUBMED_ENABLED": "false",
            "SIX_PUBMED_EMAIL": "",
            "SIX_PUBMED_API_KEY": "",
        }
        self.assertEqual(set(self.values), set(GUARDED_KEYS))
        self.bash = shutil.which("bash")
        self.assertIsNotNone(self.bash)
        commands = self.root / "commands"
        commands.mkdir()
        for name in (
            "dirname",
            "stat",
            "awk",
            "tr",
            "grep",
            "printenv",
            "cmp",
        ):
            binary = shutil.which(name)
            self.assertIsNotNone(binary, name)
            (commands / name).symlink_to(str(binary))
        docker = commands / "docker"
        docker.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            '{ printf "%s\\0" "$@"; printf "\\n"; } >> "$PREFLIGHT_TEST_CALLS"\n'
            'if [[ "$*" == "compose version" || "$*" == "compose version --short" ]]; then\n'
            '  printf "%s\\n" "${PREFLIGHT_TEST_VERSION:-2.33.1}"\n'
            'elif [[ "$1" == compose && "$2" == --env-file && "$4" == --file '
            '&& "$6" == config && "$7" == --quiet && $# -eq 7 ]]; then\n'
            '  printf "%s\\0%s\\0" "${SIX_API_IMAGE-}" "${SIX_WEB_IMAGE-}" '
            '> "$PREFLIGHT_TEST_IMAGES"\n'
            '  if [[ "${PREFLIGHT_TEST_CONFIG_FAIL:-}" == 1 ]]; then\n'
            '    printf "SYNTHETIC_CONFIG_STDOUT_CANARY\\n"\n'
            '    printf "SYNTHETIC_CONFIG_STDERR_CANARY\\n" >&2\n'
            "    exit 17\n"
            "  fi\n"
            "else\n"
            '  printf "Unexpected Docker operation in test fixture\\n" >&2\n'
            "  exit 99\n"
            "fi\n",
            encoding="utf-8",
        )
        docker.chmod(0o700)
        # Do not inherit shell startup hooks, actual configuration, or an operator's home.
        self.environment = {
            "PATH": str(commands),
            "LC_ALL": "C",
            "PREFLIGHT_TEST_CALLS": str(self.calls),
            "PREFLIGHT_TEST_IMAGES": str(self.image_overrides),
        }
        self._write()

    def _write(self, source: str | None = None, *, crlf: bool = False) -> None:
        source = source if source is not None else self._canonical()
        self.target.write_bytes(source.replace("\n", "\r\n").encode() if crlf else source.encode())
        self.target.chmod(0o600)

    def _canonical(self) -> str:
        return "".join(f"{key}={value}\n" for key, value in self.values.items())

    def _run(
        self, *, arguments: list[str] | None = None, exports: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.unlink(missing_ok=True)
        return subprocess.run(
            [
                str(self.bash),
                str(self.script),
                *(arguments if arguments is not None else [str(self.target)]),
            ],
            cwd=self.root,
            env={**self.environment, **(exports or {})},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _assert_rejected(
        self, result: subprocess.CompletedProcess[str], *, key: str | None = None, hidden: str = ""
    ) -> None:
        self.assertNotEqual(result.returncode, 0, "The guarded configuration was accepted.")
        self.assertFalse(self.calls.exists(), "Invalid configuration reached Docker.")
        self.assertIn("Self-host preflight failed:", result.stderr)
        if key is not None:
            self.assertIn(key, result.stderr)
        if hidden:
            self.assertNotIn(hidden, result.stdout + result.stderr)

    def _assert_configured(self, result: subprocess.CompletedProcess[str], target: Path) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [line.split(b"\0")[:-1] for line in self.calls.read_bytes().splitlines()]
        self.assertEqual(
            calls,
            [
                [b"compose", b"version"],
                [b"compose", b"version", b"--short"],
                [
                    b"compose",
                    b"--env-file",
                    str(target).encode(),
                    b"--file",
                    str(self.checkout / "compose.yaml").encode(),
                    b"config",
                    b"--quiet",
                ],
            ],
        )

    def test_canonical_values_allow_internal_spaces_and_empty_optionals(self) -> None:
        """Literal values and disabled-feature empty fields still reach Compose validation."""
        self._assert_configured(self._run(), self.target)

    def test_crlf_configuration_is_supported(self) -> None:
        """Windows line endings preserve the same guarded values."""
        self._write(crlf=True)
        self._assert_configured(self._run(), self.target)

    def test_all_same_value_exports_are_accepted(self) -> None:
        """An exported value, including an empty optional value, may exactly match the file."""
        self._assert_configured(self._run(exports=self.values), self.target)

    def test_enabled_feature_configuration_and_identical_exports_are_accepted(self) -> None:
        """Explicitly enabled gates with matching synthetic prerequisites remain valid."""
        self.values.update(
            {
                "SIX_SELF_SIGNUP": "true",
                "SIX_SMTP_HOST": "mail.research.example.org",
                "SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED": "true",
                "SIX_GEMINI_DATA_PROCESSING_CONFIRMED": "true",
                "SIX_GEMINI_API_KEY": "synthetic-provider-value",
                "SIX_PUBMED_ENABLED": "true",
                "SIX_PUBMED_EMAIL": "researcher@research.example.org",
            }
        )
        self._write()
        self._assert_configured(self._run(exports=self.values), self.target)

    def test_each_guarded_export_must_match_even_for_inactive_features(self) -> None:
        """Every guarded environment override is checked before any Docker invocation."""
        for key in GUARDED_KEYS:
            with self.subTest(key=key):
                hidden = "SYNTHETIC_EXPORTED_MISMATCH"
                self._assert_rejected(self._run(exports={key: hidden}), key=key, hidden=hidden)

    def test_empty_export_cannot_replace_a_nonempty_guarded_value(self) -> None:
        """Unset and explicitly empty exported values are not interchangeable."""
        self._assert_rejected(self._run(exports={"SIX_MAIL_FROM": ""}), key="SIX_MAIL_FROM")

    def test_export_comparison_keeps_trailing_newline_bytes(self) -> None:
        """Command substitution must not normalize a distinct exported value to a match."""
        key = "SIX_MAIL_FROM"
        for ending in ("\n", "\n\n", "\r", " "):
            with self.subTest(ending=repr(ending)):
                self._assert_rejected(
                    self._run(exports={key: self.values[key] + ending}),
                    key=key,
                    hidden=self.values[key],
                )

    def test_each_guarded_key_requires_one_canonical_declaration(self) -> None:
        """Missing guarded fields are rejected even when their feature is disabled."""
        for key in GUARDED_KEYS:
            with self.subTest(key=key):
                self._write(
                    "".join(
                        f"{name}={value}\n" for name, value in self.values.items() if name != key
                    )
                )
                self._assert_rejected(self._run(), key=key)

    def test_each_guarded_key_rejects_duplicate_canonical_declarations(self) -> None:
        """Repeated identical values remain ambiguous declarations, including inactive fields."""
        for key in GUARDED_KEYS:
            with self.subTest(key=key):
                self._write(self._canonical() + f"{key}={self.values[key]}\n")
                self._assert_rejected(self._run(), key=key)

    def test_alternate_declarations_cannot_hide_behind_a_canonical_value(self) -> None:
        """Dotenv declaration variants cannot silently override a validated guarded key."""
        key = "SIX_GEMINI_API_KEY"
        hidden = "SYNTHETIC_ALTERNATE_VALUE"
        for alternative in (
            f" {key}={hidden}",
            f"\t{key}={hidden}",
            f"{key} ={hidden}",
            f"{key}\t={hidden}",
            f"export {key}={hidden}",
            f"export\t{key}={hidden}",
            f"{key}: {hidden}",
            f"{key}:{hidden}",
            key,
            f" {key} ",
        ):
            with self.subTest(alternative=alternative.split(hidden)[0]):
                self._write(self._canonical() + alternative + "\n")
                self._assert_rejected(self._run(), hidden=hidden)

    def test_unicode_prefixed_declarations_cannot_override_guarded_keys(self) -> None:
        """Unicode whitespace and BOM prefixes cannot conceal a second declaration."""
        for prefix in ("\u00a0", "\u2003", "\ufeff"):
            with self.subTest(prefix=repr(prefix)):
                self._write(self._canonical() + f"{prefix}SIX_SELF_SIGNUP=true\n")
                self._assert_rejected(self._run(), hidden=f"{prefix}SIX_SELF_SIGNUP=true")

    def test_unguarded_multiline_quotes_cannot_swallow_an_enabled_guard(self) -> None:
        """A line inside a quoted password is not a separate Compose assignment."""
        self.values["SIX_PUBMED_ENABLED"] = "true"
        self.values["SIX_PUBMED_EMAIL"] = "researcher@research.example.org"
        for quote in ('"', "'"):
            with self.subTest(quote=quote):
                source = self._canonical().replace(
                    "SIX_PUBMED_ENABLED=true\n",
                    f"SIX_SMTP_PASSWORD={quote}SYNTHETIC_MULTILINE\n"
                    "SIX_PUBMED_ENABLED=true\n"
                    f"SIX_SMTP_USERNAME=SYNTHETIC_END{quote}\n",
                )
                self._write(source)
                self._assert_rejected(self._run(), hidden="SYNTHETIC")

    def test_guarded_values_reject_nonliteral_dotenv_syntax(self) -> None:
        """Quotes, escapes, interpolation and inline comments cannot change guarded values."""
        key = "SIX_GEMINI_API_KEY"
        for value in (
            '"SYNTHETIC_QUOTED_VALUE"',
            "'SYNTHETIC_QUOTED_VALUE'",
            "SYNTHETIC\\ESCAPED_VALUE",
            "$SYNTHETIC_REFERENCE",
            "${SYNTHETIC_REFERENCE:-value}",
            "$(printf SYNTHETIC_REFERENCE)",
            "SYNTHETIC_VALUE # SYNTHETIC_COMMENT",
        ):
            with self.subTest(value=value):
                self._write(self._canonical().replace(f"{key}=\n", f"{key}={value}\n"))
                self._assert_rejected(self._run(), key=key, hidden="SYNTHETIC")

    def test_guarded_values_reject_outer_whitespace_and_control_characters(self) -> None:
        """Only a line-ending CR may be normalized; embedded control bytes remain invalid."""
        key = "SIX_GEMINI_API_KEY"
        for value in (
            " SYNTHETIC_VALUE",
            "SYNTHETIC_VALUE ",
            "\tSYNTHETIC_VALUE",
            "SYNTHETIC_VALUE\t",
            "SYNTHETIC\tVALUE",
            "SYNTHETIC\rVALUE",
            "SYNTHETIC\x00VALUE",
            "SYNTHETIC\x1bVALUE",
            "SYNTHETIC\x7fVALUE",
        ):
            with self.subTest(value=repr(value)):
                self._write(self._canonical().replace(f"{key}=\n", f"{key}={value}\n"))
                self._assert_rejected(self._run(), hidden="SYNTHETIC")

    def test_guarded_values_reject_unicode_whitespace(self) -> None:
        """Guarded values stay literal printable ASCII rather than Unicode-trimmed input."""
        key = "SIX_GEMINI_API_KEY"
        for value in ("\u00a0SYNTHETIC", "SYNTHETIC\u2003", "SYNTHETIC\u00a0VALUE"):
            with self.subTest(value=repr(value)):
                self._write(self._canonical().replace(f"{key}=\n", f"{key}={value}\n"))
                self._assert_rejected(self._run(), hidden="SYNTHETIC")

    def test_plain_hashes_and_commented_guarded_examples_are_not_declarations(self) -> None:
        """An internal literal hash and comments remain compatible with canonical values."""
        source = self._canonical().replace(
            "SIX_GEMINI_API_KEY=\n", "SIX_GEMINI_API_KEY=synthetic#literal\n"
        )
        self._write(source + "# SIX_SELF_SIGNUP=true\n  # export SIX_SMTP_HOST=ignored\n")
        self._assert_configured(self._run(), self.target)

    def test_unguarded_image_overrides_and_ordinary_dotenv_remain_compatible(self) -> None:
        """This guard does not become a generic dotenv parser or prohibit image selection."""
        self._write(
            self._canonical() + 'SIX_SMTP_PASSWORD="synthetic ${SMTP_TEST_VALUE}\\n text"\n'
        )
        self._assert_configured(
            self._run(
                exports={
                    "SIX_API_IMAGE": "example.invalid/synthetic/api:test",
                    "SIX_WEB_IMAGE": "example.invalid/synthetic/web:test",
                    "SIX_SMTP_PASSWORD": "synthetic environment override",
                }
            ),
            self.target,
        )
        self.assertEqual(
            self.image_overrides.read_bytes(),
            b"example.invalid/synthetic/api:test\0example.invalid/synthetic/web:test\0",
        )

    def test_unguarded_quotes_escapes_and_comments_remain_single_line_values(self) -> None:
        """Ordinary quoted passwords may contain escaped quotes and literal comment markers."""
        for value in (
            '"synthetic \\"quoted\\" ${SMTP_TEST_VALUE}" # comment',
            "'synthetic \\'quoted\\' ${SMTP_TEST_VALUE}' # comment",
            '"synthetic # literal" # comment',
            "'synthetic # literal' # comment",
        ):
            with self.subTest(value=value):
                self._write(self._canonical() + f"SIX_SMTP_PASSWORD={value}\n")
                self._assert_configured(self._run(), self.target)

    def test_default_selector_uses_the_checkout_environment(self) -> None:
        """An omitted path keeps the checkout-relative default."""
        target = self.checkout / ".env.selfhost"
        shutil.copyfile(self.target, target)
        target.chmod(0o600)
        self._assert_configured(self._run(arguments=[]), target)

    def test_compose_configuration_failure_is_redacted(self) -> None:
        """A failing parser may echo values; neither output stream may reach the operator."""
        result = self._run(exports={"PREFLIGHT_TEST_CONFIG_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.calls.exists())
        self.assertNotIn("SYNTHETIC_CONFIG_STDOUT_CANARY", result.stdout + result.stderr)
        self.assertNotIn("SYNTHETIC_CONFIG_STDERR_CANARY", result.stdout + result.stderr)
        self.assertIn("config", result.stderr.lower())

    def test_malformed_compose_version_is_redacted(self) -> None:
        """An unparseable Docker response is not reflected in a preflight diagnostic."""
        hidden = "SYNTHETIC_VERSION_CANARY"
        result = self._run(exports={"PREFLIGHT_TEST_VERSION": hidden})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(hidden, result.stdout + result.stderr)
        self.assertIn("version", result.stderr.lower())
        calls = self.calls.read_bytes()
        self.assertNotIn(b"config", calls)


if __name__ == "__main__":
    unittest.main()
