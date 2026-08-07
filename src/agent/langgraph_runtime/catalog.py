"""Dynamic retrieval over the flat, atomic tool catalog.

The catalog deliberately has no capability groups, workflow aliases, or
domain routes.  It is a lexical first stage only; the graph asks the model to
rerank the returned descriptions for the current state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from src.tools.registry import ToolRegistry


_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_./-]*|\d+(?:\.\d+)?")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def retrieval_tokens(value: str) -> list[str]:
    """Tokenize English identifiers and Chinese text without a segmenter."""
    text = str(value or "").lower()
    tokens = [match.group(0) for match in _LATIN_TOKEN.finditer(text)]
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _field_summary(parameters: Mapping[str, Any]) -> str:
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    parts: list[str] = []
    for field_name, raw_schema in properties.items():
        schema = raw_schema if isinstance(raw_schema, Mapping) else {}
        description = str(schema.get("description") or schema.get("title") or "").strip()
        parts.append(f"{field_name}: {description}" if description else str(field_name))
    return "; ".join(parts)


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    fields: str
    effect: str
    effect_mode: str
    category: str
    retrieval_text: str
    approval_policy: str
    timeout_seconds: float | None
    max_attempts: int
    idempotent: bool

    def compact(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "fields": self.fields,
            "effect": self.effect,
            "effect_mode": self.effect_mode,
            "category": self.category,
            "approval_policy": self.approval_policy,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "idempotent": self.idempotent,
        }

    @property
    def document(self) -> str:
        return " ".join(
            item
            for item in (
                self.name,
                self.description,
                self.fields,
                self.category,
                self.retrieval_text,
            )
            if item
        )


class ToolCatalog:
    """BM25 retrieval and on-demand Schema loading for registered tools."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        descriptors: list[ToolDescriptor] = []
        for name in registry.get_tool_names():
            spec = registry.get_tool(name)
            if spec is None:
                continue
            descriptors.append(
                ToolDescriptor(
                    name=name,
                    description=str(spec.description or "").strip(),
                    fields=_field_summary(spec.model_parameters()),
                    effect=spec.effect,
                    effect_mode=spec.effect_mode,
                    category=str(spec.category or "data"),
                    retrieval_text=str(spec.retrieval_text or "").strip(),
                    approval_policy=spec.approval_policy,
                    timeout_seconds=spec.timeout_seconds,
                    max_attempts=spec.max_attempts,
                    idempotent=spec.idempotent,
                )
            )
        self._descriptors = tuple(descriptors)
        self._by_name = {descriptor.name: descriptor for descriptor in descriptors}
        self._term_frequencies = [Counter(retrieval_tokens(item.document)) for item in descriptors]
        self._lengths = [sum(frequencies.values()) for frequencies in self._term_frequencies]
        self._average_length = sum(self._lengths) / max(1, len(self._lengths))
        document_frequency: Counter[str] = Counter()
        for frequencies in self._term_frequencies:
            document_frequency.update(frequencies.keys())
        self._document_frequency = document_frequency

    @property
    def size(self) -> int:
        return len(self._descriptors)

    def compact_catalog(self) -> list[dict[str, Any]]:
        """Return descriptions only; exact schemas stay unloaded."""
        return [item.compact() for item in self._descriptors]

    def descriptor(self, name: str) -> dict[str, Any] | None:
        value = self._by_name.get(name)
        return value.compact() if value is not None else None

    def search(
        self,
        queries: Sequence[str] | str,
        *,
        limit: int = 12,
        exclude: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Return a generic lexical shortlist with an explainable score."""
        query_values = [queries] if isinstance(queries, str) else list(queries)
        query_tokens = retrieval_tokens(" ".join(str(value) for value in query_values))
        excluded = set(exclude)
        if not query_tokens:
            return [item.compact() | {"lexical_score": 0.0} for item in self._descriptors[:limit]]

        query_frequency = Counter(query_tokens)
        document_count = max(1, len(self._descriptors))
        k1 = 1.5
        b = 0.75
        ranked: list[tuple[float, ToolDescriptor]] = []
        for index, descriptor in enumerate(self._descriptors):
            if descriptor.name in excluded:
                continue
            frequencies = self._term_frequencies[index]
            length = self._lengths[index]
            score = 0.0
            for token, query_count in query_frequency.items():
                frequency = frequencies.get(token, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency.get(token, 0)
                inverse_document_frequency = math.log(
                    1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = frequency + k1 * (
                    1.0 - b + b * length / max(1.0, self._average_length)
                )
                score += query_count * inverse_document_frequency * frequency * (k1 + 1.0) / denominator
            if score > 0:
                ranked.append((score, descriptor))

        ranked.sort(key=lambda item: (-item[0], item[1].name))
        # A zero-overlap query must still be recoverable.  Fill the shortlist
        # with the catalog order so the model can spot synonyms missed by the
        # lightweight tokenizer; reflection may later request the full list.
        selected_names = {descriptor.name for _, descriptor in ranked[:limit]}
        selected = ranked[:limit]
        if len(selected) < limit:
            selected.extend(
                (0.0, descriptor)
                for descriptor in self._descriptors
                if descriptor.name not in excluded and descriptor.name not in selected_names
            )
        return [
            descriptor.compact() | {"lexical_score": round(float(score), 6)}
            for score, descriptor in selected[: max(1, min(int(limit), 50))]
        ]

    def load_schemas(self, names: Sequence[str]) -> list[dict[str, Any]]:
        """Load exact model-visible schemas for a bounded selected set."""
        loaded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in names:
            normalized = str(name or "").strip()
            if not normalized or normalized in seen:
                continue
            spec = self.registry.get_tool(normalized)
            if spec is None:
                continue
            seen.add(normalized)
            loaded.append(spec.to_openai_schema(include_server_controlled=False))
            if len(loaded) >= 8:
                break
        return loaded


__all__ = ["ToolCatalog", "ToolDescriptor", "retrieval_tokens"]
