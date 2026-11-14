"""The Persona Quality Gate (HLD 09 § The quality gate). Stub.

A deep module: one method, five checks behind it. Its internal seams (the red-team cases and
the stored sample results) are fixtures loaded from the eval suite, so tests can swap them.
"""

from agent.domain.persona import Persona, PersonaGateResult
from agent.ports.llm import LLMFactory
from agent.ports.pii import PiiFilter


class PersonaQualityGate:
    """Interface:

    - ``check`` runs the five checks in order and stops at the first failure:
      1 field limits (code), 2 injection scan (the input guard's rules), 3 safety still holds
      (red-team cases through the answer-writing step, ``ModelStep.PERSONA_GATE``, with
      the candidate), 4 report sections present (sample reports re-written from stored results),
      5 figures unchanged (code compares every figure with the stored result).
    - Never queries BigQuery.
    - Fails closed: if a model call fails, returns ``could_not_run=True, passed=False``.
    - Never raises for a bad Persona; the reason is in the result.
    """

    def __init__(self, llm: LLMFactory, pii: PiiFilter) -> None:
        self._llm = llm
        self._pii = pii

    def check(self, candidate: Persona) -> PersonaGateResult:
        raise NotImplementedError
