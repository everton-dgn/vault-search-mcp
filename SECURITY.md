# Security policy

## Supported versions

The project is alpha software. Security fixes apply to the current development
line until the first stable release exists.

## Reporting a vulnerability

Use the hosting platform's private vulnerability-reporting feature. If it is
unavailable, open an issue without technical details and ask the maintainers for
a private channel.

Do not publish a proof of concept, vault content, credentials, machine paths, or
raw logs before remediation and coordinated disclosure.

Include in the private report:

- affected version or commit;
- preconditions and exposed surface;
- minimal steps using synthetic fixtures;
- observed impact;
- temporary mitigation, when known.

## Supported trust boundary

The MCP server and daemon are designed for one operator on a controlled machine.
The daemon must remain on loopback. The project provides no authentication,
multi-user authorization, or protection for direct internet exposure.

Remote access is unsupported. That boundary requires TLS, authentication,
quotas, and a dedicated threat-model review before it can enter the public
contract.

Retrieved vault content is untrusted data. Clients must prevent note excerpts
from replacing system instructions or user authorization.

External frontmatter enrichment starts disabled. When an operator enables it,
note content sent to the external process becomes subject to that provider's
data policy.

The [threat model](docs/security/threat-model.md) describes assets, boundaries,
and assumptions in detail.

## Response expectations

A maintainer should acknowledge the report, evaluate reproducibility, and agree
on a remediation window. Timing depends on severity and availability. This
project does not promise an SLA it cannot sustain.
