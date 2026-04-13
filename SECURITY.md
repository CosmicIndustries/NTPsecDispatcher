# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | ✅        |
| < 0.1   | ❌        |

NTPsecDispatcher follows a rolling-release model. Only the latest commit on `main` is supported. If you are running an older snapshot, upgrade before filing a security report.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email the maintainer directly (see profile) or use [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability).

Include:
- A description of the vulnerability and its impact
- Steps to reproduce
- Affected file(s) and line numbers if known
- Any suggested fix (optional but appreciated)

You can expect an acknowledgement within 72 hours and a fix or workaround within 14 days for confirmed issues. You will be credited in the release notes unless you request otherwise.

## Scope

In-scope:
- Remote code execution via crafted NTP server responses
- Privilege escalation from the dispatcher process
- Log injection attacks
- Unsafe handling of the memo/cache files

Out of scope:
- Vulnerabilities in the underlying OS NTP daemons (report to chrony/ntpsec upstream)
- Social engineering
- Theoretical issues without a proof of concept
