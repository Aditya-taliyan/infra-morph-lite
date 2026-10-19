"""Natural language -> validated IR, with a fallback.

Tries the LLM interpreter (Ollama) first. If Ollama is unavailable, or its
output still fails validation after all its attempts, the rule-based
interpreter runs instead and the reason is recorded. The result always says
which interpreter produced the spec. Generation is a separate step, so the
user reviews the spec before any Terraform is made (human in the loop).
"""

import logging
from dataclasses import dataclass

from app.domain.enums import Provider
from app.interpreters.base import InterpretationResult, InterpretationStatus, SpecInterpreter
from app.interpreters.ollama_interpreter import OllamaInterpreter
from app.interpreters.rule_based import RuleBasedInterpreter

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
        # Length only: the requirement itself is never logged at INFO.
        logger.info("Interpreting a %d-character requirement for %s", len(requirement), provider)
        primary = self.primary.interpret(requirement, provider)
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
        logger.info("Falling back to %s: %s", self.fallback.name, reason)
        final = self.fallback.interpret(requirement, provider)
        return SpecEngineResult(final=final, fallback_reason=reason, primary=primary)
