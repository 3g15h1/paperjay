# AI assistants (MCP)

PaperJay has a built-in [MCP](https://modelcontextprotocol.io) server, so an AI assistant such as Claude can
work with your documents: search and read them, ask questions, add documents and notes, change their filing
and delete them. You decide what each assistant may do.

## 1. Make an access key

In PaperJay, open **Settings → AI assistants (MCP)**, give the key a name (e.g. "Claude on my laptop"),
tick what it may do, and press **Make access key**:

| Permission | The assistant may |
|---|---|
| **Read** | search, open and read documents (with their text and notes), ask questions, list tags, companies and types, see what's coming up |
| **Create** | add documents, notes, tags, companies and types |
| **Update** | change titles, dates, companies, types, categories and tags; mark documents reviewed; bring documents back from the Trash; rename labels |
| **Delete** | move documents to the Trash and delete tags, companies and types |

A key acts as the person who made it and can never do more than their account: a key made by someone who
"can only look" can only read. **Delete** only moves documents to the Trash, where they stay for 30 days;
no key can delete anything for good, empty the Trash, change settings or people, make other keys or download
an export. Remove a key on the same page and the assistant is cut off straight away.

PaperJay shows the key once, together with ready-to-paste setup for Claude Code and Claude Desktop.

## 2. Connect your assistant

The server's address is your PaperJay address followed by `/mcp`, for example
`http://paperjay.local:8000/mcp`. It needs the key in an `Authorization: Bearer pj_…` header.

**Claude Code**

```sh
claude mcp add --transport http --scope user paperjay http://paperjay.local:8000/mcp \
  --header "Authorization: Bearer pj_…"
```

**Claude Desktop** needs [Node.js](https://nodejs.org) for the small `mcp-remote` bridge. Open
Settings → Developer → Edit Config and add, inside `"mcpServers"`:

```json
"paperjay": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://paperjay.local:8000/mcp", "--allow-http",
           "--header", "Authorization:${PAPERJAY_AUTH}"],
  "env": { "PAPERJAY_AUTH": "Bearer pj_…" }
}
```

Then restart Claude Desktop. Other assistants that speak MCP over "Streamable HTTP" connect the same way.

**HTTPS:** if you use PaperJay's own HTTPS address (port 8443), the assistant has to trust PaperJay's
certificate: download `https://<your PaperJay>:8443/paperjay-ca.crt` and set
`NODE_EXTRA_CA_CERTS=/path/to/paperjay-ca.crt` for Claude Code or Claude Desktop. On your home network
the plain `http://…:8000` address avoids that.

**Your network only.** The assistant's app connects to PaperJay from your computer, so it works wherever
PaperJay is reachable from that computer. Connectors added on claude.ai run in the cloud and can't reach a
PaperJay that's only on your home network.

## Tools

| Tool | Needs | What it does |
|---|---|---|
| `search_documents` | Read | find documents by words and/or company, type, tags and dates; or list the newest |
| `get_document` | Read | one document's details, notes and text (`max_chars` limits the text) |
| `ask` | Read | answer a question with `[n]` citations, like the Ask box (`use_ai: false` skips the AI) |
| `list_labels` | Read | tags, companies (`correspondent`) or types with their ids |
| `upcoming` | Read | bills coming due and policies expiring |
| `add_document` | Create | upload a file (base64); PaperJay reads and files it in the background |
| `add_note` | Create | add a note to a document |
| `create_label` | Create | make a tag, company or type |
| `update_document` | Update | change title, date, company, type, category or tags (`add_tag_ids`, `remove_tag_ids`) |
| `mark_reviewed` | Update | take a document out of Needs review |
| `restore_document` | Update | bring a document back from the Trash |
| `rename_label` | Update | rename a tag, company or type |
| `delete_document` | Delete | move a document to the Trash |
| `delete_label` | Delete | delete a tag, company or type |

An assistant is only offered the tools its key allows. Each tool runs as an ordinary request to PaperJay's
[API](api.md) with the same key, so it's checked exactly like any other request, and it shows up in each
document's history under your name.

## Details

The server speaks MCP protocol versions 2025-06-18, 2025-03-26 and 2024-11-05 over the Streamable HTTP
transport (`POST /mcp`, JSON replies; `GET /mcp` answers 405 because it sends no notifications). It keeps
no sessions. Requests from a web page on another site are refused, and a browser's sign-in isn't accepted:
only an access key.
