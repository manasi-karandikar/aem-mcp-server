# AEM MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes an AEM instance to an LLM client such as Claude Desktop. It lets a model
search pages, read page content and metadata, and read Content Fragments — over
plain HTTP, using AEM's own QueryBuilder and Sling GET servlet.

It was written to understand the protocol from the inside rather than through a
framework, and it is deliberately small: five tools, one file, no mutation.

## Status

Working and tested against a local AEM as a Cloud Service SDK with the WKND
sample content:

- Five read-only tools, exercised through Claude Desktop with a real model
- An MCP client harness (`mcp_client_test.py`) that verifies schema generation
  and tool dispatch over the SDK's in-memory transport
- Unit tests covering the path guardrail, output bounds and content fencing
- Repository ACLs defined as RepoInit in the companion WKND project

Not done: mutation tools, per-user identity propagation, and a port to the
Apache Sling MCP server contributions framework. See [Limitations](#limitations).

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `search_pages` | `keyword`, `limit`, `root` | Page paths and titles, locale copies collapsed |
| `get_page` | `path` | Page title and the text content of its components |
| `get_page_properties` | `path` | Template, tags, modification and replication metadata |
| `list_content_fragments` | `keyword`, `limit` | Content Fragment paths and titles |
| `get_fragment` | `path`, `variation` | Fragment model, available variations, field values |

## Design

### Search and fetch are separate, and so are pages and fragments

Discovery tools are cheap and return identifiers. Fetch tools are expensive and
return the content behind one identifier. The model discovers first, then
fetches — and every path it supplies is validated in between.

Pages and Content Fragments are not merged into one `get_content` tool because
they are genuinely different: different node types, different retrieval
mechanisms (QueryBuilder versus the Sling GET servlet), different shapes, and
fragments have variations while pages do not. One tool covering both would have
produced a lowest-common-denominator output that served neither well.

There is deliberately no generic `run_query(jcr_query)` tool. It would have
unbounded output, would require the model to know QueryBuilder syntax, and would
leave no surface on which to validate anything. One powerful tool is worse than
three safe ones.

### Tool functions are thin wrappers

Every tool is a wrapper whose only job is to carry the schema and the docstring.
The logic lives in a `_`-prefixed function beside it:

```python
def _search_pages(keyword, limit=10, root="/content"):
    ...

@mcp.tool()
def search_pages(keyword: str, limit: int = 10, root: str = "/content") -> str:
    """Search AEM pages by keyword in their title.
    ..."""
    return _search_pages(keyword, limit, root)
```

This is what makes the server testable without MCP, without a transport and
without a client. Every tool was verified as a plain function against a real AEM
instance before it was ever called through the protocol.

### Docstrings are prompts

The model reads tool descriptions to decide what to call. They are written for
that audience, not as reference documentation:

- `get_page`: *"Use this after search_pages has given you a path"*
- `get_page_properties`: *"much cheaper than get_page… call get_page only when
  the answer requires the actual text"*
- `list_content_fragments`: *"call get_fragment on a path to read its fields"*

Chaining across tools was never coded. It emerged from these lines: given
"what does WKND say about ski touring?", the model searches, lists fragments,
reads one, and follows the `authorFragment` reference to a second fragment.

Because tool return values also enter the model's context, they are written in
the same language and register as the docstrings. They are prompts, not UI text.

### Output is bounded, and truncation is visible

A page's JCR subtree is mostly structural bookkeeping — `jcr:primaryType`,
`cq:styleIds`, version history. The problem is not size but signal-to-noise, so
the server extracts rather than compresses. Four bounds, in four places:

| Bound | Where it applies | What it prevents |
|---|---|---|
| `READ_DEPTH = 4` (Sling depth selector, never `.infinity`) | The HTTP request | A large payload never crosses the wire |
| `TEXT_PROPS` allowlist | Extraction | Only text-bearing properties are read |
| `MAX_TEXT_ITEMS = 60` | Tree walk | Checked on every recursive call |
| `PAGE_VALUE_CHARS` / `FRAGMENT_VALUE_CHARS` | Per value | Rich text fields and long-form fragment copy |

Every truncation is marked. Values are cut with `…[truncated]` rather than a
bare ellipsis, and hitting the item limit appends an explicit note. Silent
truncation is worse than slow output: a model that does not know it received a
partial page will answer confidently from it.

### Authorization comes from the repository

The server does not filter results by permission, and it should not. In JCR,
permissions are enforced on the session: open one as a restricted user and
QueryBuilder results are filtered and unreadable nodes return 404. The server's
only job is to connect as the right identity.

There is no default identity. `AEM_USER` and `AEM_PASS` are required and the
server refuses to start without them — a missing environment variable must not
silently grant admin access.

This was verified rather than assumed. Running the same search as `admin` and as
two users scoped to different locale branches produces three different result
sets, with no filtering code in this repository. See
[Repository ACLs](#repository-acls) below for the setup.

Identity propagation is a function of transport, not of MCP. The protocol
carries no notion of a current user:

- **stdio (this server)** — identity is ambient. Each person runs their own
  server process with their own AEM credentials in their own client config.
  Correct for local use, does not scale to a shared deployment.
- **Hosted over HTTP** — MCP's authorization spec applies: the client obtains a
  token, the server validates it and maps the subject to an AEM identity by
  forwarding the token, exchanging it, or impersonating.
- **Inside AEM (Sling contributions)** — the problem disappears. The servlet is
  handed a `ResourceResolver` already bound to the authenticated user.

### Read-only on purpose

A read-only server can run with far less ceremony. The moment mutation is
allowed, an approval gate is required, because a model will produce a wrong page
path with exactly as much confidence as a right one. That gate needs designing,
so version one does not cross the boundary.

The same choice contains prompt injection. Page bodies and fragment fields are
authored by many people, including external translation vendors, and reach the
model through the same channel as its instructions. Authored content is fenced
so the boundary is visible — but fencing is a partial mitigation. What actually
limits the damage is that a fully hijacked model can still only issue read
calls, only under `/content`, only as the configured AEM user, and only with the
client's per-call approval. It can cause wrong answers, not data loss.

## Running it

Requires Python 3.11+ and an AEM instance. Tested against the AEM as a Cloud
Service SDK on `localhost:4502` with WKND installed.

```bash
pip install "mcp>=2.0,<3.0" httpx
```

The `mcp` package hit a major version bump: in SDK 2.0 `FastMCP` was renamed to
`MCPServer` and `mcp.server.fastmcp` was removed rather than deprecated, so most
tutorials online no longer apply. Pin the version.

Set credentials, then run:

```bash
export AEM_HOST=http://localhost:4502   # optional, this is the default
export AEM_USER=your-user
export AEM_PASS=your-password
python server.py
```

`server.py` speaks stdio and will wait for a client. To exercise it directly,
import it instead — the `__main__` guard means importing does not start the
server:

```bash
python -c "import server; print(server._search_pages('ski touring', 3))"
```

### Claude Desktop

Add to `claude_desktop_config.json` (`%APPDATA%\Claude\` on Windows,
`~/Library/Application Support/Claude/` on macOS):

```json
{
  "mcpServers": {
    "aem": {
      "command": "/full/path/to/python",
      "args": ["/full/path/to/server.py"],
      "env": {
        "AEM_USER": "your-user",
        "AEM_PASS": "your-password"
      }
    }
  }
}
```

Quit Claude Desktop completely — from the system tray or menu bar, not just the
window — and reopen it.

Storing a password in a config file is acceptable for local development and not
for anything else.

## Testing

```bash
pip install pytest
pytest tests -q
```

The unit tests cover what a live instance cannot reach. WKND has no page with
sixty text components, so the item limit and its truncation notice are only
exercised against synthetic node structures. The same applies to path rejection:
a guardrail that has only ever seen valid input has not been tested.

`mcp_client_test.py` is a separate harness that connects through the protocol
using the SDK's in-memory transport. It prints the JSON Schema generated for
each tool, which is the clearest way to see how Python type hints and docstrings
become the contract the client validates against.

## Repository ACLs

The two test groups used to verify permission handling are defined as Sling
RepoInit in the companion WKND project, at:

```
ui.config/src/main/content/jcr_root/apps/wknd/osgiconfig/config.author/
    org.apache.sling.jcr.repoinit.RepositoryInitializer~mcp-readers.cfg.json
```

`wknd-mcp-reader-en` and `wknd-mcp-reader-fr` grant `jcr:read` on the English and
French content branches respectively. Groups and ACLs are version controlled;
user accounts are not, because passwords do not belong in a repository. Create
the users per environment and add them to the appropriate group.

One finding worth repeating: granting nothing is not the same as denying
everything. The French reader could still see two English Experience Fragments,
because `everyone` had read access on that subtree. Effective permissions are the
union of everything that applies to a principal, so verifying only your own
grants will miss things.

## Limitations

- **Local development tool.** HTTP Basic auth against a single configured user.
- **One identity for all callers.** Correct under stdio, where each person runs
  their own process; wrong for any shared deployment.
- **No mutation.** Deliberate, see above.
- **Discovery is keyword-based.** Full-text QueryBuilder search over titles. At
  ten thousand pages this becomes the weak point: the model cannot browse a tree
  it cannot see, and keyword search will not always surface the right branch.
  `root` scoping helps, and it is the natural place for a future
  `list_children` tool.
- **Locale copies are collapsed by leaf name and title.** Two genuinely distinct
  pages sharing both would be merged. Collapsing keeps the first hit, which is
  usually the `language-masters` source — the canonical path, but not
  necessarily the one a reader of a specific locale wants.

## Where this fits

Adobe ships a hosted MCP server for AEM as a Cloud Service with full CRUD over
pages, fragments and assets. For generic content operations on AEMaaCS, that is
what you would use.

This server earns its place elsewhere: environments the hosted service cannot
reach (local SDKs, on-premise, air-gapped), project-specific tools that a generic
server will never have — governance checks, deprecated component audits,
translation coverage — and output shaping and guardrails a team wants to control
itself.

The natural next step is the Apache Sling MCP server contributions framework,
which Adobe packages for AEM. Custom tools plug in by implementing
`org.apache.sling.mcp.server.spi.McpServerContribution` and registering it as an
OSGi service; the servlet binds contributions with `0..n` cardinality, so any
bundle can add tools. Running inside AEM would replace HTTP with native JCR
access and replace configured credentials with the authenticated user's own
session — a strictly better security model, which is the real argument for the
port.
