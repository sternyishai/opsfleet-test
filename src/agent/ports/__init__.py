"""Every external dependency sits behind one of these ``Protocol``s (ADR 0006).

The graph, the tools and ``agent.core`` import only from here, never a provider SDK.
Adapters live in ``agent.adapters`` and are wired in ``agent.composition``.
"""

from agent.ports.channel import ChannelAdapter
from agent.ports.delivery import Delivery
from agent.ports.llm import LLMFactory, ModelStep
from agent.ports.persona import PersonaStore
from agent.ports.pii import PiiFilter, PiiScan, PiiType
from agent.ports.profile import ProfileStore
from agent.ports.query import QueryGuard, QueryRunner
from agent.ports.reports import ReportRepository
from agent.ports.scorecards import ScorecardSink
from agent.ports.tracer import Span, Tracer
from agent.ports.trios import TrioRetriever
from agent.ports.users import UserDirectory

__all__ = [
    "ChannelAdapter",
    "Delivery",
    "LLMFactory",
    "ModelStep",
    "PersonaStore",
    "PiiFilter",
    "PiiScan",
    "PiiType",
    "ProfileStore",
    "QueryGuard",
    "QueryRunner",
    "ReportRepository",
    "ScorecardSink",
    "Span",
    "Tracer",
    "TrioRetriever",
    "UserDirectory",
]
