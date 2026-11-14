"""Concrete adapters, one per technology, behind the ports in ``agent.ports``.

The only package allowed to import provider SDKs (``google.cloud``, ``langchain_google_genai``,
``sqlglot`` ...). Each slice in Phase 4 adds its adapters here; nothing is implemented yet.
"""
