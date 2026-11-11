# Optional RSSHub Settings

The user approved the short design: RSSHub remains optional; local/remote/container URLs are configurable in the workbench; explicit read-only connection/feed tests diagnose failures without collection or delivery.

## Approaches

- Environment-only documentation is smaller, but does not solve invisible configuration or diagnosis in the workbench.
- A global `rsshub.base_url` in `sources.yaml`, with CLI-backed edits and schema-generated fields, reuses atomic writes and the existing hash. This is the approved approach; full feed URLs remain independent.
- Automatic local discovery/deployment is rejected: a local container is not evidence of the configured remote endpoint being usable.

## Behavior

Explicit config wins over legacy `RSSHub_BASE`, then the existing local default `http://127.0.0.1:1200`. Empty config means inheritance. Runtime URL resolution is not frozen at module import. Normalize trailing slashes and preserve reverse-proxy path prefixes. Validate HTTP(S), hostname/port, and reject embedded credentials, base queries/fragments and traversal routes without echoing unsafe input. Absolute feed URLs bypass the hub, including when an unused environment URL is invalid.

The CLI exposes `rsshub show`, `rsshub set --base-url <url>` (empty clears the override; dry-run/base-hash supported), and `rsshub probe [--base-url <candidate>] [--feed <url-or-route>]`. Every result retains the existing envelope. The workbench translates guarded POST forms to these same handlers. No second writer.

Connection tests make an explicit short, direct HTTP GET to the chosen endpoint and report reachability only, not proof of RSSHub identity or every route's health. Feed tests validate RSS 2.0 (including a valid empty channel); other XML feed formats are reported as unsupported, never as a successful empty collection. Bound response size is 1 MiB, one request with a 5-second socket timeout and no automatic retries; overall wall-clock duration may include DNS/TLS/redirect overhead. Errors distinguish network, authentication, missing route, HTTP rejection and invalid/unsupported content. Do not expose response bodies, raw exception strings, URL credentials or query values.

`doctor` and view building only describe the effective address, origin and enabled dependent sources. They do not probe the network or block unrelated sources. Actual testing is explicit. The UI displays stored/inherited/effective configuration and dependence; save remains dry-run by default, while testing preserves drafts and hashes. No-JS uses existing PRG memory, never query-string raw input or repeated POSTs.

RSS directory copy describes historical verification, not current machine availability. The adapter remains named `rsshub` for compatibility but is labeled as generic RSS / RSSHub in the workbench. Container configuration passes the optional legacy environment override explicitly; documents distinguish container localhost from host/remote URLs.

## Boundaries

No real providers, credentials, collection, notification, WS, queue acknowledgment, automatic installation, deployment, merge or push. No new runtime dependencies or client renderer. Existing guarded offline fixtures must also guard the new probe transport. Use staged commits and inline execution with one fresh final reviewer. Only restart the offline preview at completion.
