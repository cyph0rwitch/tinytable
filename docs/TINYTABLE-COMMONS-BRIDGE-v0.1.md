# TinyTable–Intelligence Commons integration notes

TinyTable is the source-side provenance and consent boundary for the proposed
Intelligence Commons bridge. The first integration slice is a deterministic,
local export of an explicitly selected message or bounded message bundle.

It must preserve the existing distinctions between author, transport identity,
recipient-visible speaker, provider observation, model inference, assistance,
approval, disclosure, visibility, and consent. It must never treat ordinary
conversation or model output as Commons consent.

The canonical cross-project contract is
[`../../swarm_intelligence/TINYTABLE-COMMONS-BRIDGE-v0.1.md`](../../swarm_intelligence/TINYTABLE-COMMONS-BRIDGE-v0.1.md).

The initial implementation should be read-only with respect to TinyTable's
history and should write only a local export/report. It should not contact a
Commons node, provider channel, federation peer, or external identity service.
