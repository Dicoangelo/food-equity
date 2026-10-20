# BeforeTheCrush architecture captions

Source snapshot: [`fcb4f76`](https://github.com/niatalwalkar/food-equity/tree/fcb4f76678713c55af3873acfc21e2871f44bcc7). These figures document code, not proof of the current live deployment.

## From surplus alert to reviewed rescue plan

A reviewed surplus offer moves through candidate retrieval and feasibility checks to a coordinator handoff. Evidence informs the proposal; a person confirms the next step.

[1920px WebP](web/01-pitch/01-pitch-overview-1920.webp) · 1920 × 1072

## The coordinator stays in control

The system prepares a primary match, up to two backups and outreach drafts. The coordinator contacts recipients to confirm capacity and acceptance, then opens directions.

[1920px WebP](web/01-pitch/02-human-handoff-1920.webp) · 1920 × 1072

## Runtime and trust boundaries

The browser connects to the Cloud Run FastAPI service. The broker uses Vertex AI and loopback MCP Toolbox; the optional Live voice lane stages input for review.

[1920px WebP](web/02-technical/03-runtime-architecture-1920.webp) · 1920 × 1072

## Four read tools, explicit evidence paths

Four read tools expose recipient retrieval, exact recipient evidence, tract context and pinned FoodKeeper guidance. FoodKeeper is a local reference, separate from BigQuery.

[1920px WebP](web/02-technical/04-mcp-data-topology-1920.webp) · 1920 × 1072

## Retrieve broadly, check explicitly, rank transparently

Explicit checks cover capacity, storage, service, acceptance and the pickup clock. Missing facts are flagged for confirmation; eligible candidates are ranked, enriched with evidence and rechecked.

[1920px WebP](web/03-research/05-decision-method-1920.webp) · 1920 × 1072

## What the evidence can—and cannot—tell us

Recipient profiles, neighborhood context and FoodKeeper references support retrieval and guidance, with explicit limits on what each source establishes.

[1920px WebP](web/03-research/06-data-provenance-1920.webp) · 1920 × 1072
