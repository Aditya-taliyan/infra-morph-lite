# Infra Morph Lite - Backend Architecture

## 1. Architecture Overview

Infra Morph Lite is designed as a multi-cloud infrastructure generation system.

The backend follows a layered architecture:

Frontend
    |
    v
API Layer
    |
    v
Service Layer
    |
    v
Domain Models
    |
    v
Provider Generators
    |
    v
Terraform Configuration

The main responsibility of each layer is:

- API Layer - receives requests and returns responses.
- Service Layer - coordinates application workflows.
- Domain Models - represent infrastructure concepts independently of any cloud provider.
- Provider Generators - convert the domain model into provider-specific Terraform configuration.
- Terraform Configuration - represents the final infrastructure-as-code output.

The architecture must keep the domain model independent from specific cloud providers.

This allows the same infrastructure specification to eventually be generated for multiple providers such as AWS, Azure, and GCP.

## 2. Directory Responsibilities

### app/api

Contains the HTTP/API layer.

Responsibilities:
- Define API endpoints.
- Receive and validate API requests.
- Return API responses.
- Convert API input into service-layer calls.

The API layer must not contain Terraform generation logic.

### app/core

Contains application-wide infrastructure and configuration.

Responsibilities:
- Application configuration.
- Logging configuration.
- Shared application-level utilities when necessary.

The core package should remain small and should not become a general-purpose dumping ground.

### app/domain

Contains the domain model (the IR) representing infrastructure concepts.

Responsibilities:
- Define infrastructure specifications.
- Represent cloud-independent infrastructure concepts.

The domain must not depend on a specific cloud provider, Terraform, FastAPI, Streamlit, subprocess, httpx or Jinja2.

### app/models

Contains API request/response schemas only. See "Layer decisions" below.

### app/services

Contains application and business workflows.

Responsibilities:
- Coordinate application operations.
- Connect API requests with domain models and generators.
- Orchestrate infrastructure generation workflows.

Services should contain workflow logic, not HTTP-specific implementation details.

### app/generators

Contains provider-specific infrastructure generators.

Responsibilities:
- Convert domain models into Terraform configuration.
- Implement provider-specific generation logic.
- Keep provider-specific behavior isolated from the domain models.

Future providers may include:
- AWS
- Azure
- GCP

### app/validators

Contains validation logic beyond basic API request parsing.

Responsibilities:
- Validate infrastructure specifications.
- Validate provider-specific constraints.
- Validate generated infrastructure where appropriate.

Validation logic should be reusable outside individual API endpoints.

### tests

Contains automated tests for the backend.

Tests should verify:
- Domain models.
- Services.
- Generators.
- Validators.
- API behavior.

## 3. Dependency Rules

The backend follows a controlled dependency direction.

### API Layer

The API layer may depend on services, models, and validation components when required.

The API layer must not contain Terraform generation logic.

### Service Layer

The service layer coordinates application workflows.

Services may depend on domain models, generators, and validation components.

### Domain Models

Domain models are provider-independent.

Models must not depend on:
- AWS SDKs
- Azure SDKs
- GCP SDKs
- Terraform implementations
- HTTP frameworks

### Generator Layer

Generators may depend on domain models.

Generators contain provider-specific implementation details and Terraform generation logic.

Provider-specific logic must remain isolated within the generator layer.

### Validator Layer

Validators provide reusable validation logic.

Validators should not contain API routing logic or provider-specific Terraform generation logic.

### Core Layer

Core contains shared application configuration and infrastructure.

Core dependencies should remain minimal.

### Dependency Direction

The intended application flow is:

API
 |
 v
Services
 /     \
v       v
Models  Generators
          |
          v
      Terraform

This dependency direction should be preserved as the project grows.

## 4. Layer decisions

- `app/domain/` holds the IR and core types (Pydantic). It is the single source of truth for what infrastructure can be described. It imports nothing from other app layers.
- `app/models/` holds API request/response schemas only (the shapes FastAPI receives and returns). These are transport types: they may wrap or reference domain types, but the domain never imports from `models/`. Keeping them separate means the HTTP contract can change without touching the IR, and vice versa.
- `app/providers/` holds the `ProviderAdapter` interface and the AWS/Azure mappings. It is the only place (besides templates) where `aws_*`/`azurerm_*` names appear.
- `app/infrastructure/` holds external I/O: the terraform runner, the infracost runner and the Ollama client.
- `app/interpreters/` turns natural language into IR (LLM and rule-based fallback).
- `app/generators/templates/` holds the Jinja2 Terraform templates. Generated runs are written to `backend/generated/<run_id>/` (gitignored).
- Tests are split into `tests/unit/`, `tests/integration/` (API → service → generator → validator) and `tests/fixtures/` (sample IRs, golden files).
