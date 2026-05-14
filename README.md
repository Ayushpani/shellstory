# ShellStory

**Automated Production Runbook Generation from Live Shell Sessions**

ShellStory is a high-performance terminal orchestration tool designed to transform raw, chaotic shell sessions into structured, production-ready documentation. By leveraging a daemonized capture system and a multi-agent swarm architecture, ShellStory observes your workflow, redacts sensitive information, and synthesizes a professional runbook in real-time.

---

## Core Components

### 1. The Capture Daemon
ShellStory utilizes a non-intrusive background daemon that tails session logs in 30-second rolling windows. This ensures that heavy processing tasks—such as PII scanning and initial signal analysis—are pre-computed asynchronously, minimizing latency during the final documentation assembly.

### 2. The Swarm Orchestrator
When a session is finalized, the Orchestrator performs a "tail-catchup" operation to synchronize any remaining events. It then coordinates a specialized swarm of LLM agents:
- **Signal Agent**: Distills the "Happy Path" from the noise of trial-and-error.
- **Failure Agent**: Detects command failures, extracts error logs, and documents the verified resolution.
- **Prereq Agent**: Automatically identifies environmental dependencies and system requirements.
- **Sequence Agent**: Groups atomic commands into logical, high-level operational steps.
- **Merger Agent**: Assembles the disparate agent outputs into a cohesive, technical document.

### 3. Majestic TUI Matrix
The processing phase features a futuristic Swarm Matrix dashboard built with the Rich library. This interface provides real-time visualization of parallel agent execution, model status, and system orchestration logs, offering complete transparency into the documentation synthesis process.

---

## Security and PII Redaction

Security is a first-class citizen in ShellStory. The system employs a dual-layer redaction engine:
- **Layer 1 (Regex)**: Instant local scanning for common patterns such as AWS Access Keys, private keys, and standard environment variables.
- **Layer 2 (AI Scanner)**: A secondary LLM pass that identifies context-dependent secrets (e.g., custom database credentials, API tokens, and internal endpoints) that regex might miss.

All redaction occurs before data is transmitted to the synthesis agents, ensuring zero exposure of sensitive credentials to the LLM swarm.

---

## Installation

ShellStory requires Python 3.11 or higher.

```bash
# Clone the repository
git clone https://github.com/Ayushpani/shellstory.git
cd shellstory

# Install the package and dependencies
pip install -e .
```

---

## Usage Guide

### Initialization
Configure your LLM provider and API credentials. ShellStory currently supports OpenRouter and NVIDIA NIM as primary backends.
```bash
shellstory configure
```

### Recording a Session
Start a new session to drop into a recorded sub-shell. All commands within this sub-shell are captured with high fidelity.
```bash
shellstory start "Kubernetes Cluster Migration"
```

### Documentation Synthesis
Once you exit the recording shell, initiate the Swarm Matrix to generate your documentation.
```bash
shellstory process
```

---

## Configuration

ShellStory stores its configuration in `~/.shellstory/config.yaml`.

```yaml
llm:
  provider: "openrouter"
  model: "anthropic/claude-3-sonnet"
  api_key: "sk-or-v1-..."

connectors:
  markdown:
    output_dir: "~/runbooks"
```

---

## Technical Architecture

```text
[ Terminal ] <--> [ Hook Script ] --> [ .ndjson Log ]
                                           |
                                           v
[ CLI Agent ] <------------------- [ Capture Daemon ]
      |                                    |
      | (Final Process)                    | (PII Scan)
      v                                    v
[ Orchestrator ] <----------------- [ State Checkpoint ]
      |
      +--> [ Signal Expert ]
      +--> [ Failure Expert ]
      +--> [ Prereq Expert ]
      +--> [ Sequence Expert ]
      |
      v
[ Markdown Connector ] --> [ Final Runbook.md ]
```

---

## Contributing

ShellStory is an open-source project designed for the technical elite. Contributions to the agent prompts, TUI aesthetics, and capture hooks are welcome. Please ensure all contributions adhere to the zero-emoji aesthetic and maintain the technical rigor of the documentation.

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.

---
*Maintained by Ayush Pani.*
