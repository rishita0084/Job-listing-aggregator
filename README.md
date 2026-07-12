# Job Alert MCP Server

An AI agent that searches, filters, and emails real job listings, demonstrating the complete Model Context Protocol (MCP) architecture: prompts, resources, and tools.

## Overview

This project implements a job alert assistant using the full MCP architecture, unlike many demonstrations that only showcase the tools primitive. A job alert assistant legitimately requires all three components to function effectively.

## Functionality

Users can submit requests in natural language such as:

> "Send me an email of the top 10 job listings posted within the last 24 hours for software engineer roles with 3+ years of experience."

The system will:
1. Retrieve live listings from RemoteOK's official JSON API and WeWorkRemotely's official RSS feeds
2. Filter by actual posting timestamps (using authoritative epoch/RFC-822 timestamps)
3. Classify each job's experience level as confirmed, excluded, or unspecified using a transparent text heuristic
4. Deduplicate results, limit to 10 items, and email a clean, clickable digest via Gmail SMTP

The system provides honest feedback when no matches are found rather than generating fabricated data.

## MCP Architecture Components

| Component | Description | Purpose |
|-----------|-------------|---------|
| **Prompt** | `job_alert_assistant(role, min_experience)` | Configures the AI's persona and workflow for job searching. Prompts shape model behavior as instructions, not actions. |
| **Resources** | `jobs://criteria`, `jobs://latest-digest` | Provides read-only reference data: current search criteria and the last saved digest. Resources represent state the model can check before acting. |
| **Tools** | `search_jobs`, `send_email` | Implements actions: real scraping/API calls and actual SMTP delivery. Tools perform work in the real world. |

## System Architecture

```
User prompt (natural language)
          │
          ▼
Claude Code  ──stdio──▶  job_alert_server.py (custom MCP server)
          │                        │
          │                        ├── search_jobs ──▶ RemoteOK JSON API
          │                        │                └─▶ WeWorkRemotely RSS feeds
          │                        │
          │                        └── send_email ──▶ Gmail SMTP (SSL)
          │
          ▼
     User's email inbox
```

The system operates locally. Claude Code launches the server as a subprocess over stdio transport—no ports or network exposure—using stdin/stdout, which is the appropriate transport for a locally-managed, short-lived MCP server.

## Data Sources

- **RemoteOK**: Official public JSON API (https://remoteok.com/api). No authentication, scraping, or CAPTCHAs required.
- **WeWorkRemotely**: Official public RSS feeds (https://weworkremotely.com/remote-job-rss-feed), filtered to programming categories.

Both platforms focus on remote-first, global positions, making location filtering impractical as an intentional design choice documented transparently.

## Development Journey

This project evolved through an iterative, authentic development process:

- **Initial approach**: Used free API access via a local proxy (Free Claude Code) routing traffic to NVIDIA NIM's Nemotron model instead of Anthropic's API, enabling the entire agent loop on a free/local model.
- **Indeed integration attempt**: Initial scraping worked once but subsequently returned 403 Forbidden bot-detection pages. Critically, the AI did not detect this failure and generated false mock data. This established the core principle: tools must either return authentic data or explicitly report failure.
- **Transition to official feeds**: Switched to RemoteOK and WeWorkRemotely's official public JSON/RSS feeds designed for programmatic access, eliminating scraping fragility.
- **Hallucination prevention**: Addressed instances where the model recycled previous false outputs by implementing raw, unverified tool-call verification prompts during testing.
- **Stdio issue**: Identified resolved debug `print()` statements contaminating stdout (used for JSON-RPC protocol messages between Claude Code and the MCP server), causing intermittent multi-minute delays. Established that stdout must remain pristine for stdio transport, with logging directed to stderr.
- **Credential management**: Transitioned from manual environment variable exports (`$env:GMAIL_APP_PASSWORD` per session) to secure storage in the MCP server's scoped env configuration (`.claude/settings.local.json`), enabling automatic, permanent credential injection by Claude Code.
- **Configurable time windows**: Replaced rigid 24-hour cutoffs (frequently yielding no results) with an adjustable `hours_window` parameter inferred from natural language—"last 24 hours" maps to 24, while vague requests default to a week.

For those learning MCP: the primary lesson was not any specific fix, but developing discipline to never accept an agent's success claim without independently verifying actual tool output.

## Setup

### Prerequisites
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) installed
- [Claude Code](https://code.claude.com) installed
- Gmail account with [2-Step Verification](https://myaccount.google.com/security) enabled and an [App Password](https://myaccount.google.com/apppasswords) generated

### Installation
```bash
uv sync
```

### MCP Server Registration
```bash
claude mcp add job-alerts -- uv run job_alert_server.py
```

### Credential Configuration
Add to `.claude/settings.local.json` (already git-ignored):
```json
{
  "mcpServers": {
    "job-alerts": {
      "env": {
        "GMAIL_SENDER_EMAIL": "your-email@gmail.com",
        "GMAIL_APP_PASSWORD": "your-16-char-app-password",
        "DIGEST_RECIPIENT_EMAIL": "where-to-send-digests@example.com"
      }
    }
  }
}
```

### Usage
```bash
claude
```
Then submit requests in plain English:
```
Find me software engineer jobs with 3+ years experience posted in the last 24 hours, and email them to me.
```

## Daily Automation (Optional Add-On)

The MCP server itself only runs when explicitly invoked through Claude Code—it does not schedule itself. However, since the daily use case is typically identical (same role, same time window, same recipient), the entire workflow can be automated outside the MCP server using the host operating system's native scheduler, removing the need to manually prompt Claude Code every day.

This is not part of the MCP server's core architecture. It is an operating-system-level convenience layer sitting on top of it, included here because it demonstrates a natural extension of an MCP-based agent: once a workflow is reliable enough to trust, it can move from "something you ask for" to "something that just happens."

### How it works (Windows implementation)

1. A PowerShell wrapper script (`set_env_and_run_claude.ps1`) sets the proxy environment variables Claude Code requires when running non-interactively (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`), then invokes Claude Code in headless mode:
   ```powershell
   claude -p "Find me software engineer jobs with 3+ years experience posted in the last 24 hours, and email them to me."
   ```
2. `register_task.ps1` registers this script with Windows Task Scheduler to run once daily at a fixed time (e.g., 9:00 AM).
3. Because credentials are already stored in `.claude/settings.local.json`, the scheduled task requires no manual input—it runs silently and delivers the digest to the configured inbox automatically.

### Cross-platform note

Windows Task Scheduler is platform-specific. On macOS/Linux, the equivalent is a `cron` job or a `launchd`/`systemd` timer invoking `claude -p` with the same wrapper logic.

## Future Development Ideas

- A native cross-platform automation script (single wrapper supporting Windows Task Scheduler, cron, and launchd)
- Additional job sources beyond RemoteOK and WeWorkRemotely
- Salary-range extraction where available
- A lightweight web dashboard for reviewing digest history instead of email-only delivery

## Project Structure
```
job-alert-mcp/
├── job_alert_server.py            # MCP server implementing prompt, resources, and tools
├── set_env_and_run_claude.ps1     # Wrapper script for headless daily automation
├── register_task.ps1              # Registers the wrapper with Windows Task Scheduler
├── pyproject.toml                 # Project deps (managed by uv)
├── uv.lock
├── .python-version
├── .claude/                        # Claude Code project configuration (commands, settings)
├── .gitignore
└── README.md
```

## Limitations

- **Experience assessment heuristic**: Neither source provides a structured "years required" field; matching relies on keyword/phrase detection in titles and descriptions. Jobs marked `unspecified` may or may not meet criteria—they are included rather than silently excluded.
- **Location non-filtering**: Sources focus on remote-first, global positions, yielding worldwide results rather than location-specific listings.
- **Demonstration scope**: This implementation demonstrates MCP architecture principles, emphasizing correctness and transparency over exhaustive coverage (e.g., limited to two sources and the most recent items within the configured time window).

## Educational Context

This project was developed through hands-on learning of the Model Context Protocol, beginning with Anthropic's [official reference MCP servers](https://github.com/modelcontextprotocol/servers) and public explanatory videos, then translating that into a functional three-primitive server rather than a tools-only demonstration. Individuals pursuing similar learning may adapt this foundation for personal job searches or other domains requiring an integrated prompt, resource, and tool combination.