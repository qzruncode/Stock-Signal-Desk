"""Shared, source-preserving AKShare evidence products.

The chat Agent consumes these semantic products instead of knowing individual
AKShare endpoint names.  Retrieval remains separate from model judgment: this
package normalizes provenance, coverage and freshness, but does not turn raw
rows into buy/sell conclusions.
"""

from .company import get_company_evidence

__all__ = ["get_company_evidence"]
