import assert from "node:assert/strict";
import { test } from "node:test";
import { api } from "../src/litmap/ui/static/shared.js";

test("GET resumes after a rebuild finishes", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "setTimeout", (resolve) => resolve());
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return calls === 1
      ? { ok: false, json: async () => ({ code: "search_rebuilding" }) }
      : { ok: true, json: async () => ({ record: "ready" }) };
  });
  assert.deepEqual(await api("/api/trail?q=pubmed:1"), { record: "ready" });
  assert.equal(calls, 2);
});

test("POST is never replayed during a rebuild", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return { ok: false, json: async () => ({ code: "search_rebuilding", error: "Rebuilding" }) };
  });
  await assert.rejects(api("/api/decisions", { label: "include" }), { code: "search_rebuilding" });
  assert.equal(calls, 1);
});

test("other GET errors are reported immediately", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return { ok: false, json: async () => ({ error: "Record missing" }) };
  });
  await assert.rejects(api("/api/trail?q=missing"), { message: "Record missing" });
  assert.equal(calls, 1);
});
