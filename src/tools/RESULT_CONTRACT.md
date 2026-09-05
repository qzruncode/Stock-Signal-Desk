# Tool result contract

Every registered Stock Agent tool returns an object with the same acquisition
envelope. Domain fields remain tool-specific.

- `success: boolean` — the requested source acquisition or deterministic
  computation completed. This is an execution outcome, not a promise that the
  payload contains answerable data.
- `partial: boolean` — `success` is true, but at least one requested source,
  section, dimension, or coverage segment failed. It must never be true when
  `success` is false. A warning alone does not imply a partial result.
- `data_time: string | null` — newest observation/event/report time actually
  used by the result, in ISO-8601 form. It is not the retrieval time.
- `is_stale: boolean | null` — true only when `data_time` is older than that
  tool's documented update expectation; false only when freshness can be
  established; null when freshness is not applicable or cannot be determined.
- `errors: array` — acquisition failures. A successful fallback keeps the
  primary failure here and normally makes the result partial.
- `warnings: array` — limitations such as bounded samples or incomplete
  historical windows. A warning makes the result partial only when it means a
  requested coverage segment is unavailable.
- `has_data: boolean` — the normalized payload contains answerable domain data,
  including singleton results such as `item` or `calculation`; provider
  attempts, routing metadata, and an empty collection do not count.
- `data_status: string` — the normalized semantic state: `error`, `empty`,
  `stale`, `partial`, `fallback`, `freshness_unknown`, or `usable`.
- `usable: boolean` — `success` and `has_data` are both true. A successful
  empty query remains visible in the tool ledger but is not usable evidence.
- `evidence_eligible: boolean` — whether the result may enter the citation
  ledger. The executor never creates an evidence id for failed or empty data;
  stale or time-unknown data may only support claims that state that limitation,
  not an unqualified "current/latest" claim.

Source fallback is owned by the operation boundary. An operation with a safe
structured provider chain records every attempt and returns the actual source.
When failure, empty data, stale data, or missing source time remains unresolved,
the tool's declarative `web_fallback` capability or an explicit
`fallback_recommended` result flag causes the Agent middleware to require a
bounded, explicit `search_web_source(source_id=auto)` or
`read_web_source(source_id=auto)` call before accepting a terminal answer.
The web operations themselves own their HTTP/provider fallback chain; no hidden
nested call is allowed to bypass the tool ledger, budgets, or evidence audit.

`ToolRegistry.execute` enforces types and impossible state combinations at the
single Agent boundary. Individual tools still own the business meaning of
success, partial coverage, and freshness.
