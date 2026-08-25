
## M10 production boundary
The web/API tier now has an operational governance layer: optional authentication/RBAC, request IDs and audit events, readiness checks, background-job state, and connector configuration. All industrial connectors are constrained to `read_only`. PostgreSQL is the target durable relational store; the portfolio fixture retains immutable file artifacts for deterministic reproducibility while the pilot data model is still being validated.
