# Milestone 10 — Production Hardening

M10 adds operational scaffolding around the already-tested M1–M9 intelligence engines without granting CIP Intelligence any plant-control authority.

## Included
- environment-driven runtime configuration;
- optional bearer API-key authentication foundation;
- viewer / engineer / QA / admin role hierarchy;
- request IDs and security headers;
- audit-event persistence;
- health and readiness endpoints;
- durable background-job metadata;
- read-only connector registry;
- connector contracts for watched-folder, historian API, SQL read-only, OPC UA read-only, LIMS, MES, and CMMS integration;
- PostgreSQL-oriented production schema;
- Docker/Docker Compose packaging;
- non-root API container configuration.

## Control boundary
All industrial connector definitions are read-only. V1 contains no route for pump commands, valve commands, recipe writes, setpoint changes, PLC writes, or HMI writes.

## Persistence boundary
SQLite remains a convenient local/demo operational metadata backend. PostgreSQL is the production target. The immutable analytical artifact stores built in earlier milestones are not falsely represented as fully migrated enterprise persistence.

## M11 hardening correction
Release validation found that the local operational database could fail if its runtime directory disappeared after application startup. `ProductionDB` now recreates its local parent/schema on connection, and a regression test simulates runtime-volume removal and recovery.

## Still required for a real plant deployment
- customer-specific identity provider / SSO;
- TLS/PKI and secrets-management integration;
- OT network zoning/firewall review;
- backup/restore tests;
- monitoring and alerting;
- vulnerability/patch management;
- vendor-specific connector qualification;
- customer change-control and cybersecurity approval.
