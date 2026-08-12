import os
import re
import httpx
from mcp.server import MCPServer

AEM_HOST = os.getenv("AEM_HOST", "http://localhost:4502")
AEM_USER = os.getenv("AEM_USER", "admin")
AEM_PASS = os.getenv("AEM_PASS", "admin")

mcp = MCPServer("aem")


def aem_get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET request against AEM."""
    r = httpx.get(
        f"{AEM_HOST}{path}",
        params=params,
        auth=(AEM_USER, AEM_PASS),
        timeout=15.0,
    )
    r.raise_for_status()
    return r.json()


ALLOWED_ROOTS = ("/content",)
TEXT_PROPS = ("jcr:title", "jcr:description", "text", "title", "subtitle")


def _validate_path(path: str) -> str | None:
    """What the model sends is a request, not a command. This is where we decide."""
    if not path.startswith("/"):
        return "Path must be absolute, e.g. /content/wknd/us/en"
    if ".." in path:
        return "Path traversal is not allowed"
    if not any(path == r or path.startswith(r + "/") for r in ALLOWED_ROOTS):
        return f"Only paths under /content are allowed. Got: {path}"
    return None


def _clean(s: str, limit: int = 300) -> str:
    """Strip HTML tags, collapse whitespace, cap length."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _collect_text(node: dict, out: list, depth: int = 0, max_items: int = 60):
    """Walk the component tree and pull out text-bearing properties."""
    if len(out) >= max_items:
        return
    for key, val in node.items():
        if depth == 0 and key == "jcr:title":
            continue
        if key in TEXT_PROPS and isinstance(val, str) and val.strip():
            out.append(f"{'  ' * depth}{key}: {_clean(val)}")
    for key, val in node.items():
        if isinstance(val, dict) and not key.startswith("jcr:"):
            _collect_text(val, out, depth + 1, max_items)


def _fragment_fields(variation: dict, limit_chars: int = 500) -> list:
    """Extract real field values from a CF variation node, skipping metadata."""
    out = []
    for key, val in variation.items():
        if "@" in key or key.startswith(("jcr:", "sling:", "cq:", "dam:")):
            continue
        if isinstance(val, str) and val.strip():
            out.append(f"  {key}: {_clean(val, limit_chars)}")
        elif isinstance(val, list):
            out.append(f"  {key}: {', '.join(str(x) for x in val)}")
    return out


def _search_pages(keyword: str, limit: int = 10) -> str:
    """Core logic, kept separate from the tool wrapper so it stays testable."""
    data = aem_get("/bin/querybuilder.json", {
        "path": "/content",
        "type": "cq:Page",
        "fulltext": keyword,
        "p.limit": limit,
        "p.hits": "selective",
        "p.properties": "jcr:path jcr:content/jcr:title",
    })

    hits = data.get("hits", [])
    if not hits:
        return f"No pages found for '{keyword}'."

    lines = [f"Found {len(hits)} page(s):"]
    for h in hits:
        title = h.get("jcr:content", {}).get("jcr:title", "(no title)")
        lines.append(f"  {h['jcr:path']} — {title}")
    return "\n".join(lines)


@mcp.tool()
def search_pages(keyword: str, limit: int = 10) -> str:
    """Search AEM pages by keyword in their title.

    Use this when the user asks to find, list, or locate pages
    in AEM by topic or title. Returns page paths and titles.
    """
    return _search_pages(keyword, limit)


def _get_page(path: str) -> str:
    err = _validate_path(path)
    if err:
        return f"Refused: {err}"

    path = path.rstrip("/")
    try:
        data = aem_get(f"{path}.4.json")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"No page found at {path}."
        raise

    content = data.get("jcr:content")
    if content is None:
        return f"{path} exists but has no jcr:content — this is not a page, likely a folder."

    lines = [
        f"Page: {path}",
        f"Title: {content.get('jcr:title', '(no title)')}",
    ]
    body = []
    _collect_text(content, body)
    if body:
        lines.append("Content:")
        lines.extend(body)
    return "\n".join(lines)


@mcp.tool()
def get_page(path: str) -> str:
    """Read the content of a single AEM page at a known path.

    Use this after search_pages has given you a path, or when the user
    names an exact page path. Returns the page title and the text
    content of its components. Only paths under /content are allowed.

    Args:
        path: Absolute JCR path, e.g. /content/wknd/us/en/adventures/climbing-new-zealand
    """
    return _get_page(path)


def _list_content_fragments(keyword: str = "", limit: int = 20) -> str:
    params = {
        "path": "/content/dam",
        "type": "dam:Asset",
        "property": "jcr:content/contentFragment",
        "property.value": "true",
        "p.limit": limit,
        "p.hits": "selective",
        "p.properties": "jcr:path jcr:content/jcr:title",
    }
    if keyword:
        params["fulltext"] = keyword

    data = aem_get("/bin/querybuilder.json", params)
    hits = data.get("hits", [])
    if not hits:
        return f"No content fragments found for '{keyword}'." if keyword else "No content fragments found."

    total = data.get("total", len(hits))
    lines = [f"Showing {len(hits)} of {total} content fragment(s):"]
    for h in hits:
        title = h.get("jcr:content", {}).get("jcr:title", "(no title)")
        lines.append(f"  {h['jcr:path']} — {title}")
    return "\n".join(lines)


@mcp.tool()
def list_content_fragments(keyword: str = "", limit: int = 20) -> str:
    """List AEM Content Fragments, optionally filtered by a keyword.

    Content Fragments hold structured, reusable content in the DAM,
    separate from pages. Use this when the user asks about articles,
    fragments, or structured content. Pass a keyword to narrow the
    search, or leave it empty to list all fragments. Returns fragment
    paths and titles — call get_fragment on a path to read its fields.

    Args:
        keyword: Optional full-text filter, e.g. "ski touring"
        limit: Maximum number of fragments to return
    """
    return _list_content_fragments(keyword, limit)


def _get_fragment(path: str, variation: str = "master") -> str:
    err = _validate_path(path)
    if err:
        return f"Refused: {err}"

    path = path.rstrip("/")
    try:
        data = aem_get(f"{path}.4.json")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"No asset found at {path}."
        raise

    content = data.get("jcr:content", {})
    if not content.get("contentFragment"):
        return f"{path} exists but is not a Content Fragment."

    fdata = content.get("data", {})
    available = [k for k, v in fdata.items() if isinstance(v, dict)]
    node = fdata.get(variation)
    if node is None:
        return (f"Variation '{variation}' not found at {path}. "
                f"Available: {', '.join(available) or 'none'}")

    lines = [
        f"Fragment: {path}",
        f"Title: {content.get('jcr:title', '(no title)')}",
        f"Model: {fdata.get('cq:model', '(unknown)')}",
        f"Variation: {variation}  (available: {', '.join(available)})",
        "Fields:",
    ]
    lines.extend(_fragment_fields(node))
    return "\n".join(lines)


@mcp.tool()
def get_fragment(path: str, variation: str = "master") -> str:
    """Read the field values of a single AEM Content Fragment.

    Use this after list_content_fragments has given you a path. Returns
    the fragment's model, its available variations, and the field values
    of the requested variation. Long text fields are truncated. Only
    paths under /content are allowed.

    Args:
        path: Absolute DAM path, e.g. /content/dam/wknd-shared/en/magazine/skitouring/skitouring
        variation: Which variation to read. Defaults to "master".
    """
    return _get_fragment(path, variation)


if __name__ == "__main__":
    mcp.run()