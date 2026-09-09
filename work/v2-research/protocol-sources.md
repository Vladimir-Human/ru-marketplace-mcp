# v2.0.0 MCP protocol research

Accessed 2026-09-09. These are official sources selected for the v2 design.

1. **Tools** — <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>
   The tool contract includes discovery, typed input schemas, descriptions, and
   structured results. Implication: DSH tool surface and schema cost are product
   behavior; compare/full profiles need separate contracts and wire budgets.
2. **Resources** — <https://modelcontextprotocol.io/specification/2025-06-18/server/resources>
   Resources are distinct from tools and can expose read-only context without
   pretending that every operation is an action. Implication: capability/readiness
   and provenance could become a small resource/profile instead of another costly
   tool family.
3. **Prompts** — <https://modelcontextprotocol.io/specification/2025-06-18/server/prompts>
   Prompt templates are discoverable server behavior. Implication: reusable
   comparison and verification workflows may be better represented as skills or
   prompts while keeping the tool list narrow.
4. **Progress and cancellation** —
   <https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/progress>
   Long-running calls need progress/cancellation semantics. Implication: a v2
   fan-out should expose bounded progress and cancellation rather than only a
   fixed timeout.
5. **HTTP authorization** —
   <https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization>
   HTTP deployment has an explicit authorization boundary. Implication: the
   current operator reverse-proxy assumption should become a tested posture,
   especially for CDP and MPStats access.
6. **MCP JSON schema** —
   <https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-06-18/schema.ts>
   Schema changes are protocol contracts, not implementation details. Implication:
   public snapshots and version gates must remain part of every profile migration.
7. **FastMCP migration** — <https://gofastmcp.com/getting-started/upgrading>
   Major FastMCP upgrades can alter schema/runtime behavior. The repository's
   tested `<4` cap is therefore deliberate until a contract migration is run.

## Trade-offs

Resources/prompts can reduce tool-schema cost, but they do not replace typed
tools for source calls. Progress improves long fan-out UX, but it requires client
support and cancellation tests. HTTP auth improves shared deployments, but the
default local stdio/loopback mode should remain zero-config.
