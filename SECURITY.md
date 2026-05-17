# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 1.x (latest) | ✅ |
| < 1.0 | ❌ |

## Reporting a Vulnerability

**Please do not file a public GitHub issue for security vulnerabilities.**

Report security issues by emailing **miryalas@gmail.com** (replace with a real address before publishing).

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes

You will receive an acknowledgement within **48 hours** and a substantive response within **7 days**.

## Scope

### In scope
- Remote code execution via agent tools or sandbox escape
- Path traversal in `read_file` / `write_file` tools
- Injection attacks through prompt construction
- Credential exposure (API keys in logs, state files, or responses)
- Download integrity bypass (GGUF magic check circumvention)
- SSRF via `fetch()` tool or web scraper targeting internal addresses

### Out of scope
- Vulnerabilities in third-party models (report to the model provider)
- Denial-of-service via very large model downloads (expected behaviour)
- Attacks requiring physical access to the device

## Security Design Notes

**No data leaves the device by default.**
All inference runs locally unless an API key (`OPENAI_API_KEY`, `GROQ_API_KEY`, etc.)
is explicitly set by the user, in which case the prompt is sent to that provider.

**Agent sandbox.**
The `python()` tool executes code in a separate subprocess with a 30-second timeout.
Compound shell operators (`;`, `&&`, `||`) are blocked in the `shell()` tool.
The agent cannot invoke `sudo` or modify system files outside the working directory.

**Download integrity.**
Every downloaded GGUF file is verified against its magic bytes and approximate size.
MLX snapshots are checked for completeness before being used.

**API keys are never logged or stored.**
Keys are read from environment variables at inference time and never persisted.

---

## Binary verification

Every pre-built binary on GitHub Releases is accompanied by a `.sha256` checksum file
generated during CI. **Always verify the checksum before running a downloaded binary.**

```bash
# macOS arm64 example
curl -Lo pai https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64
curl -Lo pai.sha256 https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64.sha256

# Verify (macOS)
shasum -a 256 -c pai.sha256
# Verify (Linux)
sha256sum -c pai.sha256

# Only run after verification passes
chmod +x pai && ./pai --version
```

The `install.sh` one-liner performs this verification automatically and refuses to
install a binary that does not match its published checksum.

## macOS Gatekeeper

Pre-built binaries are signed with an Apple Developer ID when an `APPLE_DEVELOPER_ID`
is available in CI. If you downloaded an unsigned build (e.g. from a fork or PR):

```bash
# Remove quarantine flag after verifying the checksum yourself
xattr -d com.apple.quarantine pai
```

You can also build from source to avoid Gatekeeper entirely:

```bash
git clone https://github.com/miryala3/linus-pai.git
cd linus-pai
./pai          # source launcher — never blocked by Gatekeeper
```

## Supply-chain integrity

- All CI builds run on GitHub-hosted runners with ephemeral environments.
- The `release.yml` workflow is pinned to specific action versions (`@v4`, `@v2`).
- No third-party build tools are used beyond PyInstaller and Python stdlib.
- `build/launcher.py` (the binary entry point) is minimal — read it in 5 minutes.
- `pai.py` (the main runtime) is a single auditable Python file.
