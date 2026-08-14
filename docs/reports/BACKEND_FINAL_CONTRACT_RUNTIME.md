# Backend runtime contract — 2026-08-13

- OpenAPI version: `3.0.3`
- Canonical artifact: `docs/api/openapi.json` and `docs/api/openapi.yaml`
- SHA-256: `0659D84DE6B40EF2598DC2341A8D7323488B4FBCA995267E0D287CB9021443A3`
- Paths / operations / schemas: `184 / 272 / 222`
- Generation and validation: PASS with `config.settings.testing` and
  `--validate --fail-on-warn`.
- Drift: PASS with `python scripts/openapi_contract.py check-drift`.
- Postman coverage: PASS — `272/272` operations in the generated Dashboard and
  Mobile collections.

The additive `/api/v1/groups/{group_id}/chat-ticket/` endpoint is part of the
canonical contract. Run `python scripts/openapi_contract.py generate` after an
intentional API change; CI rejects validation failures and generated-contract
drift.
