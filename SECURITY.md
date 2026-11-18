# Security

PaperJay holds people's most private paperwork, so security reports are very welcome.

**Please report vulnerabilities privately**, through GitHub's
[private vulnerability reporting](https://github.com/3g15h1/paperjay/security/advisories/new) for this
repository, not in a public issue. Include what you found, how to reproduce it, and which version. You'll
get a reply within a few days, and credit in the release notes if you'd like.

## How PaperJay protects documents

- Passwords hashed with scrypt; sessions and API tokens stored only as hashes; sign-in slows down after
  repeated wrong passwords.
- Every request is checked against the account's role (admin, member, read-only); browser requests that
  change something must come from PaperJay's own pages.
- Uploaded files are recognised by their content, never by their name; OCR runs as an unprivileged user,
  with time and size limits, in its own process group.
- Files are served with headers that stop the browser from running anything in them.
- No telemetry; nothing leaves your server unless you configure a remote AI server.

PaperJay is meant for a home network. Put it behind a VPN or an HTTPS reverse proxy before reaching it
from the internet.
