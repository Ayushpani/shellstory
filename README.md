<p align="center">
  <strong>SHELLSTORY</strong>
</p>

<p align="center">
  <em>Autonomous Runbook Synthesis from Live Terminal Sessions</em>
</p>

<p align="center">
  <a href="#architecture">Architecture</a> |
  <a href="#installation">Installation</a> |
  <a href="#usage">Usage</a> |
  <a href="#agent-swarm">Agent Swarm</a> |
  <a href="#security">Security</a> |
  <a href="#configuration">Configuration</a> |
  <a href="#contributing">Contributing</a>
</p>

---

## The Problem

Every deployment, migration, and infrastructure task begins in the terminal. Engineers run dozens of commands, hit errors, fix them, and eventually arrive at a working state. The knowledge of *what worked and why* lives only in scroll-back history --- ephemeral, unstructured, and lost the moment the terminal closes.

Teams compensate by writing runbooks after the fact. These documents are invariably incomplete, out of date within weeks, and missing the critical troubleshooting steps that made the procedure actually work.

## The Solution

ShellStory eliminates the gap between execution and documentation. It captures every command, exit code, and output in real-time, then deploys a coordinated swarm of specialized LLM agents to synthesize the raw session into a structured, production-grade runbook --- complete with prerequisites, logical step grouping, error analysis, and remediation guidance.

The operator simply works. ShellStory writes the manual.

---

## Architecture

ShellStory is built on a three-tier architecture designed for zero data loss and minimal processing latency.

```
                              CAPTURE TIER
  +-----------+        +------------------+       +----------------+
  |  Terminal  | -----> |  Hook Script     | ----> |  .ndjson Log   |
  |  (User)    |        |  (PowerShell/    |       |  (Append-only  |
  |            |        |   Bash/Zsh)      |       |   event stream)|
  +-----------+        +------------------+       +-------+--------+
                                                          |
                              DAEMON TIER                  |
                       +----------------------+            |
                       |  Background Daemon   | <----------+
                       |  - 30s rolling window|
                       |  - PII pre-scan      |
                       |  - Signal extraction  |
                       +----------+-----------+
                                  |
                                  v
                       +----------+-----------+
                       |  State Checkpoint    |
                       |  (.state.json)       |
                       +----------+-----------+
                                  |
                              SWARM TIER                   
                       +----------+-----------+
                       |  Orchestrator        |
                       |  - Tail catch-up     |
                       |  - Agent lifecycle   |
                       |  - Checkpoint mgmt   |
                       +----------+-----------+
                                  |
              +-------------------+-------------------+
              |                   |                   |
     +--------v------+  +--------v------+  +---------v-----+
     | Signal Agent   |  | Failure Agent  |  | Prereq Agent  |
     | (Parallel)     |  | (Parallel)     |  | (Parallel)    |
     +--------+------+  +--------+------+  +---------+-----+
              |                   |                   |
              +-------------------+-------------------+
                                  |
                       +----------v-----------+
                       |  Sequence Agent      |
                       |  (Serial, depends    |
                       |   on Signal output)  |
                       +----------+-----------+
                                  |
                       +----------v-----------+
                       |  Merger Agent         |
                       |  (Final assembly)     |
                       +----------+-----------+
                                  |
                       +----------v-----------+
                       |  Validation Layer     |
                       |  (Repair loop)        |
                       +----------+-----------+
                                  |
                       +----------v-----------+
                       |  Markdown Export      |
                       |  - Runbook (.md)      |
                       |  - Raw Transcript     |
                       +----------------------+
```

### Capture Tier

A lightweight shell hook (generated per-session for PowerShell, Bash, or Zsh) intercepts the `PreCommandAction` and `PostCommandAction` lifecycle events. Each command, its exit code, working directory, and timing data are serialized as NDJSON and appended to an immutable event log. The hook uses low-level file I/O (`[System.IO.File]::AppendAllText` on Windows) to avoid file-lock contention with the daemon tier.

### Daemon Tier

A detached background process (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session=True` on POSIX) monitors the session log in 30-second rolling windows. During each window, it performs:

- **PII pre-scan** using the regex redaction engine
- **Signal extraction** via the Signal Agent (identifying "happy path" commands vs. noise)
- **Failure analysis** via the Failure Agent

Results are checkpointed to `.state.json`, enabling the Swarm Tier to skip redundant computation.

### Swarm Tier

When the operator finalizes a session, the Orchestrator performs a **tail catch-up**: it compares the daemon's `_processed_count` against the total event count and processes any unhandled tail events inline. This eliminates the race condition between the daemon's last processing window and the user's `exit` command, guaranteeing 100% event fidelity.

---

<h2 id="agent-swarm">Agent Swarm</h2>

The swarm consists of six specialized agents, each with a single responsibility. Agents that are data-independent run in parallel with staggered launch times (3-second intervals) to avoid rate-limit bursts. Sequential agents consume the outputs of the parallel tier.

| Agent | Execution | Responsibility |
|-------|-----------|----------------|
| **Signal** | Parallel | Classifies commands as *signal* (essential) or *noise* (debugging, navigation, typos). Only signal commands propagate to the Sequence Agent. |
| **Failure** | Parallel | Identifies failed commands (non-zero exit codes), correlates them with subsequent recovery attempts, and extracts the verified fix. |
| **Prereq** | Parallel | Infers environmental prerequisites: runtime versions, system packages, required ports, and environment variables. |
| **Sequence** | Serial | Receives the filtered signal commands and groups them into logical, ordered runbook steps with dependency-aware sequencing. |
| **Annotation** | Serial | Reviews draft steps for destructive operations, race conditions, or practical pitfalls and attaches contextual warnings. |
| **Merger** | Serial | Assembles outputs from all upstream agents into a unified runbook structure with professional titles, descriptions, and troubleshooting sections. |

### Anti-Hallucination Constraints

Every agent prompt includes explicit negative constraints:

> *"You MUST NOT invent, hallucinate, or add any commands that are not in the raw list provided. Use EXACTLY the commands given."*

The Merger Agent operates under a strict assembly-only mandate --- it formats and refines language but cannot introduce new commands. If any agent returns an empty or malformed response, the Orchestrator falls back to a deterministic, programmatic merge using chronological ordering.

### Graceful Degradation

Each agent implements a `_fallback_result()` method. If an LLM call fails after exhausting all retry attempts and fallback models, the agent returns a safe default (e.g., "all commands are signal") rather than crashing the pipeline. The Resilient LLM Client automatically rotates through a fallback chain of free-tier models:

```
google/gemma-4-31b-it:free -> deepseek/deepseek-v4-flash:free -> ...
```

---

<h2 id="security">Security and PII Redaction</h2>

ShellStory treats credential exposure as a first-class failure mode. The redaction engine operates in two layers, both executing **before** any data reaches the LLM swarm.

### Layer 1: Deterministic Regex Engine

A curated set of 13 pattern matchers targets known credential formats:

| Pattern | Example Match |
|---------|---------------|
| AWS Access Key | `AKIAIOSFODNN7EXAMPLE` |
| AWS Secret Key | 40-character base64 strings |
| OpenRouter / OpenAI Keys | `sk-or-v1-...`, `sk-...` |
| GitHub / GitLab Tokens | `ghp_...`, `glpat-...` |
| Bearer Tokens | `Authorization: Bearer ...` |
| Database Connection Strings | `postgres://user:pass@host/db` |
| SSH Private Key Headers | `-----BEGIN RSA PRIVATE KEY-----` |
| Embedded URL Credentials | `https://admin:secret@host` |
| CLI Password Arguments | `--password=mysecret` |

Each match is replaced with a deterministic variable (`$AWS_ACCESS_KEY`, `$OPENAI_KEY`, etc.) and recorded in a `VariableDefinition` that appears in the final runbook's "Environment Variables" section.

### Layer 2: AI-Assisted Scanner

A dedicated PII Scanner Agent reviews the full session transcript for context-dependent secrets that evade regex: internal hostnames, non-standard token formats, database names revealing business logic, and file paths containing personal information.

---

## Installation

**Requirements:** Python 3.11 or higher.

```bash
git clone https://github.com/Ayushpani/shellstory.git
cd shellstory
pip install -e .
```

This registers the `shellstory` command globally.

---

## Usage

### 1. Configure

Run the interactive setup wizard to provide your OpenRouter API key and configure output preferences.

```bash
shellstory configure
```

### 2. Record a Session

Start a new capture session. ShellStory spawns a hooked sub-shell automatically --- no manual hook activation required.

```bash
shellstory start "Kubernetes Cluster Migration"
```

### 3. Work Normally

Execute commands as you normally would. Every command, its exit code, timing, and working directory are captured transparently.

### 4. Finalize

Type `exit` to close the recording shell. The CLI confirms the session ID and prompts you to process.

### 5. Generate Documentation

Launch the Swarm Orchestrator to synthesize the runbook. The Swarm Matrix dashboard provides real-time visibility into agent execution, model selection, and processing state.

```bash
shellstory process
```

Two files are generated:
- **`[title].md`** --- The structured, AI-synthesized runbook with prerequisites, logical steps, and troubleshooting.
- **`[title]-raw.md`** --- A verbatim command transcript with pass/fail status markers.

### Additional Commands

```bash
shellstory list              # Display all recorded sessions
shellstory status            # Show the active session and event count
shellstory export <id>       # Re-export a previously processed session
shellstory stop              # Manually stop an active session (alternative to exit)
```

---

## Configuration

Configuration is stored at `~/.shellstory/config.yaml`. See `.shellstory.example.yaml` for the full schema.

```yaml
llm:
  provider: "openrouter"
  model: "anthropic/claude-sonnet-4"
  api_key: "YOUR_OPENROUTER_API_KEY"

default_connector: markdown

sessions_dir: "~/.shellstory/sessions"

connectors:
  markdown:
    output_dir: "~/runbooks"
```

### Supported LLM Providers

| Provider | Configuration |
|----------|--------------|
| OpenRouter | `provider: openrouter` with an OpenRouter API key |
| NVIDIA NIM | `provider: nvidia` with a NVIDIA Build API key |

The Resilient LLM Client handles rate limiting, exponential backoff, and automatic model fallback across provider-specific error codes.

---

## Project Structure

```
shellstory/
  agents/
    base.py           # Abstract agent with LLM call and JSON parsing
    specialists.py    # Signal, Failure, Prereq, Sequence, Annotation, Merger
    swarm.py          # Orchestrator with parallel execution and checkpointing
  llm/
    base.py           # Provider-agnostic LLM interface
    resilient.py      # Fallback chain with retry logic
    openrouter.py     # OpenRouter HTTP client
    nvidia.py         # NVIDIA NIM HTTP client
  utils/
    ndjson.py         # Event log serialization and deserialization
    retry.py          # Exponential backoff decorator and JSON response parser
  capture.py          # Shell hook generation (PowerShell, Bash, Zsh)
  cli.py              # Click-based CLI with all commands
  config.py           # YAML configuration management
  connectors.py       # Export connectors (Markdown, extensible)
  daemon.py           # Background processing daemon
  db.py               # SQLite session and runbook persistence
  models.py           # Pydantic models for the entire data pipeline
  redact.py           # Dual-layer PII redaction engine
  ui.py               # Rich-based Swarm Matrix TUI dashboard
  validate.py         # Post-synthesis validation and repair loop
```

---

## Contributing

Contributions are welcome. Areas of particular interest:

- **Additional shell hooks** (Fish, Nushell, cmd.exe)
- **Export connectors** (Notion, Confluence, Obsidian)
- **Agent prompt engineering** (improving step grouping accuracy, reducing LLM token usage)
- **Provider integrations** (Gemini, Groq, Anthropic direct, local models via Ollama)

Please ensure all contributions maintain zero-emoji aesthetics in user-facing output and adhere to the existing code conventions.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<p align="center"><em>Built by <a href="https://github.com/Ayushpani">Ayush Pani</a></em></p>
