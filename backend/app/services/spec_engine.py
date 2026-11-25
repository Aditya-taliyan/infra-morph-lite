"""Natural language -> validated IR, with a fallback.

Tries the LLM interpreter (Ollama) first. If Ollama is unavailable, or its
output still fails validation after all its attempts, the rule-based
interpreter runs instead and the reason is recorded. The result always says
which interpreter produced the spec. Generation is a separate step, so the
user reviews the spec before any Terraform is made (human in the loop).

A requirement that asks only for unsupported services (scope.py) fails
before any interpreter runs.
"""

import logging
from dataclasses import dataclass

from app.core.logging import record_request
from app.domain.enums import Provider
from app.interpreters.base import InterpretationResult, InterpretationStatus, SpecInterpreter
from app.interpreters.ollama_interpreter import OllamaInterpreter
from app.interpreters.rule_based import RuleBasedInterpreter
from app.interpreters.scope import has_supported, unsupported_requests

logger = logging.getLogger(__name__)


@dataclass
class SpecEngineResult:
    # The interpreter whose result counts: the one that produced the spec,
    # or the last one tried when none did.
    final: InterpretationResult
    # Set when the fallback ran; says why the primary was not used.
    fallback_reason: str | None = None
    # The primary's result when the fallback ran (its errors, attempts).
    primary: InterpretationResult | None = None

    @property
    def ok(self) -> bool:
        return self.final.ok

    @property
    def errors(self) -> list[str]:
        """Errors of every interpreter tried, labelled with its name."""
        tried = [r for r in (self.primary, self.final) if r is not None]
        return [f"{r.interpreter}: {e}" for r in tried for e in r.errors]


class SpecEngine:
    def __init__(
        self,
        primary: SpecInterpreter | None = None,
        fallback: SpecInterpreter | None = None,
    ) -> None:
        self.primary = primary or OllamaInterpreter()
        self.fallback = fallback or RuleBasedInterpreter()

    def interpret(self, requirement: str, provider: Provider) -> SpecEngineResult:
        record_request(provider=str(provider))
        result = self._interpret(requirement, provider)
        record_request(
            resource_count=len(result.final.spec.resources) if result.ok else None,
            validation_status="valid" if result.ok else "invalid",
        )
        return result

    def _interpret(self, requirement: str, provider: Provider) -> SpecEngineResult:
        # Length only: the requirement itself is never logged.
        logger.info("Interpreting a %d-character requirement for %s", len(requirement), provider)
        unsupported = unsupported_requests(requirement)
        if unsupported and not has_supported(requirement):
            logger.info("Only unsupported services requested: %s", ", ".join(unsupported))
            return SpecEngineResult(final=InterpretationResult(
                "scope-check",
                InterpretationStatus.FAILED,
                errors=[f"Only unsupported services were requested: {', '.join(unsupported)}."],
                unsupported_requests=unsupported,
            ))

        primary = self.primary.interpret(requirement, provider, unsupported)
        primary.unsupported_requests = unsupported
        if primary.ok:
            return SpecEngineResult(final=primary)

        if primary.status is InterpretationStatus.UNAVAILABLE:
            last_error = primary.errors[-1] if primary.errors else "no details"
            reason = f"{primary.interpreter} is unavailable ({last_error})"
        else:
            reason = (
                f"{primary.interpreter} output failed validation after "
                f"{primary.attempts} attempts"
            )
        # Not the reason: it can hold Ollama's error text.
        logger.info("Falling back to %s: %s", self.fallback.name, primary.status)
        final = self.fallback.interpret(requirement, provider, unsupported)
        final.unsupported_requests = unsupported
        return SpecEngineResult(final=final, fallback_reason=reason, primary=primary)
