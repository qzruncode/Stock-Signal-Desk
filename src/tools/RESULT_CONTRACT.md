# Tool result contract

Every registered Stock Agent tool returns an object with the same acquisition
envelope. Domain fields remain tool-specific.

- `success: boolean` — the requested source acquisition or deterministic
  computation completed. An authoritative empty query may still be successful.
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

`ToolRegistry.execute` enforces types and impossible state combinations at the
single Agent boundary. Individual tools still own the business meaning of
success, partial coverage, and freshness.
