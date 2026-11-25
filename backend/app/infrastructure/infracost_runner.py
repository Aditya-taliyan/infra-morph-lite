"""Run Infracost on a generated run directory (docs/PROJECT_SPEC.md §17).

Every failure gives status "unavailable" with a fixed reason: never an
exception, never a guessed number. Infracost's own output is not shown to the
user or logged: it could echo the API key or quote the generated Terraform.
Infracost parses the .tf files itself and needs no cloud login, but it sends
resource types and pricing attributes to the Infracost pricing API.
"""

import json
import logging
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.config import Settings, settings
from app.domain.cost import CostEstimate, CostStatus, Pricing, ResourceCost
from app.infrastructure.child_env import stripped_env

ALLOWED_COMMANDS = frozenset({("infracost", "breakdown")})

logger = logging.getLogger(__name__)


class InfracostRunner:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    def estimate(self, run_dir: Path) -> CostEstimate:
        if shutil.which("infracost") is None:
            return CostEstimate.unavailable("Infracost is not installed (not found on PATH).")
        if not self.config.infracost_api_key:
            return CostEstimate.unavailable("INFRACOST_API_KEY is not set.")

        timeout = self.config.infracost_timeout_seconds
        try:
            completed = self._run(
                "infracost",
                "breakdown",
                # Absolute: a relative path would be resolved against cwd.
                ["--path", str(run_dir.resolve()), "--format", "json"],
                run_dir,
                timeout,
            )
        except subprocess.TimeoutExpired:
            return CostEstimate.unavailable(f"Infracost timed out after {timeout:g} seconds.")
        except OSError:
            return CostEstimate.unavailable("Infracost could not be started.")

        if completed.returncode != 0:
            return CostEstimate.unavailable(
                f"Infracost failed (exit code {completed.returncode})."
            )
        try:
            return parse_breakdown(json.loads(completed.stdout))
        except (ValueError, KeyError, TypeError, AttributeError):
            return CostEstimate.unavailable("Infracost output could not be read.")

    def _run(
        self, executable: str, subcommand: str, args: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if (executable, subcommand) not in ALLOWED_COMMANDS:
            raise ValueError(f"{executable} {subcommand} is not allowed")

        key = self.config.infracost_api_key.get_secret_value()
        start = time.monotonic()
        completed = subprocess.run(
            [shutil.which(executable) or executable, subcommand, *args],
            cwd=cwd,
            env={**stripped_env(), "INFRACOST_API_KEY": key},
            capture_output=True,
            text=True,
            encoding="utf-8",  # Windows would decode with the ANSI code page
            errors="replace",
            timeout=timeout,
        )
        logger.info(
            "%s %s: exit code %s in %.2f s",
            executable,
            subcommand,
            completed.returncode,
            time.monotonic() - start,
        )
        return completed


def parse_breakdown(document: dict[str, Any]) -> CostEstimate:
    """`infracost breakdown --format json` -> CostEstimate. Raises on bad shapes."""
    summary = document.get("summary") or {}
    unsupported = summary.get("unsupportedResourceCounts") or {}
    no_price = summary.get("noPriceResourceCounts") or {}

    resources: list[ResourceCost] = []
    listed: Counter[str] = Counter()
    for project in document["projects"]:
        if (project.get("metadata") or {}).get("errors"):
            # Infracost exits 0 here, with a total that misses resources.
            return CostEstimate.unavailable("Infracost could not evaluate the Terraform files.")
        breakdown = project["breakdown"]
        resources.extend(_resource_cost(r) for r in breakdown["resources"])
        # Despite its name, freeResources also holds the unsupported resources;
        # only the summary counts (by type) tell the two apart.
        for resource in breakdown.get("freeResources") or []:
            kind = resource["resourceType"]
            listed[kind] += 1
            free = kind in no_price and kind not in unsupported
            resources.append(_without_price(resource["name"], free))
    # freeResources leaves some types out (e.g. associations); show those by count.
    for counts, free in ((no_price, True), (unsupported, False)):
        for kind, count in sorted(counts.items()):
            if count > listed[kind]:
                resources.append(_without_price(f"{kind} ({count - listed[kind]})", free))
    resources.sort(key=lambda r: list(Pricing).index(r.pricing))

    # Infracost reports a "0" total when nothing has a price.
    priced = any(r.monthly_cost is not None for r in resources)
    return CostEstimate(
        status=CostStatus.AVAILABLE,
        currency=document["currency"],
        monthly_total=document["totalMonthlyCost"] if priced else None,
        resources=resources,
        unsupported_count=summary.get("totalUnsupportedResources") or sum(unsupported.values()),
        message="" if priced else "Infracost returned no prices for these resources.",
    )


def _without_price(address: str, free: bool) -> ResourceCost:
    return ResourceCost(
        address=address,
        pricing=Pricing.NO_CHARGE if free else Pricing.NOT_PRICED,
        monthly_cost=None,
        note="" if free else "Not supported by Infracost; no price known.",
    )


def _resource_cost(resource: dict[str, Any]) -> ResourceCost:
    cost = ResourceCost(
        address=resource["name"],
        pricing=Pricing.PRICED,
        # Infracost leaves the key out for usage-based resources.
        monthly_cost=resource.get("monthlyCost"),
    )
    if _price_not_found(resource):
        # Infracost counts a missing price as 0.
        if not cost.monthly_cost:
            cost.monthly_cost = None
            cost.pricing = Pricing.NOT_PRICED
            cost.note = "Price not found."
        else:
            cost.note = "Partial: some prices were not found."
    elif cost.monthly_cost is None:
        cost.pricing = Pricing.USAGE_BASED
        cost.note = "No fixed monthly price; the cost depends on usage."
    return cost


def _price_not_found(resource: dict[str, Any]) -> bool:
    return any(c.get("priceNotFound") for c in resource.get("costComponents") or []) or any(
        _price_not_found(sub) for sub in resource.get("subresources") or []
    )
