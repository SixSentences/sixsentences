import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import test from "node:test";

import {
  apiHostPermission,
  deploymentConfig,
  validatePublicKey,
} from "../scripts/config.mjs";

test("HTTPS deployment values are normalized without a hosted fallback", () => {
  assert.deepEqual(deploymentConfig({
    APP_ORIGIN: "https://research.example.org",
    API_ORIGIN: "https://research.example.org/api",
  }), {
    appOrigin: "https://research.example.org",
    apiOrigin: "https://research.example.org/api",
    publicKey: null,
  });
});

test("configuration is mandatory", () => {
  assert.throws(() => deploymentConfig({}), /APP_ORIGIN is required/);
  assert.throws(() => deploymentConfig({ APP_ORIGIN: "https://research.example.org" }), /API_ORIGIN is required/);
});

test("HTTP is restricted to explicitly enabled loopback development", () => {
  const values = {
    APP_ORIGIN: "http://localhost:3000",
    API_ORIGIN: "http://127.0.0.1:8000",
  };
  assert.throws(() => deploymentConfig(values), /must use HTTPS/);
  assert.deepEqual(deploymentConfig({
    ...values,
    SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK: "true",
  }), {
    appOrigin: "http://localhost:3000",
    apiOrigin: "http://127.0.0.1:8000",
    publicKey: null,
  });
  assert.throws(() => deploymentConfig({
    ...values,
    APP_ORIGIN: "http://192.168.1.10:3000",
    SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK: "true",
  }), /must use HTTPS/);
  assert.throws(() => deploymentConfig({
    ...values,
    SIX_EXTENSION_ALLOW_INSECURE_LOOPBACK: "TRUE",
  }), /must be exactly true or false/);
});

test("application values are pathless and API paths are narrowly encoded", () => {
  const base = { API_ORIGIN: "https://research.example.org/api" };
  const credentialedOrigin = ["https://", "synthetic-user", "@research.example.org"].join("");
  for (const appOrigin of [
    "https://research.example.org/library",
    credentialedOrigin,
    "https://research.example.org?mode=test",
    "https://research.example.org#fragment",
    " https://research.example.org",
  ]) {
    assert.throws(() => deploymentConfig({ ...base, APP_ORIGIN: appOrigin }));
  }
  for (const apiOrigin of [
    "https://research.example.org/api/",
    "https://research.example.org/api/../private",
    "https://research.example.org/api/%2e%2e/private",
    "https://research.example.org/api%2fprivate",
    "https://research.example.org/api?token=synthetic",
    "https://research.example.org/api#fragment",
    "https://*.example.org/api",
    "https://research.example.org:8443/api",
  ]) {
    assert.throws(() => deploymentConfig({
      APP_ORIGIN: "https://research.example.org",
      API_ORIGIN: apiOrigin,
    }));
  }
});

test("host permission is limited to the configured API scheme and host", () => {
  assert.equal(apiHostPermission("https://research.example.org/api"), "https://research.example.org/*");
  assert.equal(apiHostPermission("http://127.0.0.1:8000"), "http://127.0.0.1/*");
  assert.notEqual(apiHostPermission("https://research.example.org/api"), `<all_${"urls"}>`);
});

test("an operator-owned RSA SPKI public key is optional and strictly parsed", () => {
  const { publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const encoded = publicKey.export({ type: "spki", format: "der" }).toString("base64");
  assert.equal(validatePublicKey(encoded), encoded);
  assert.equal(validatePublicKey(""), null);
  assert.throws(() => validatePublicKey("not base64"), /unwrapped base64/);
  assert.throws(() => validatePublicKey("A".repeat(128)), /not a valid/);
});
