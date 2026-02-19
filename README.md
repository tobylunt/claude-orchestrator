# claude-orchestrator

Automated feature orchestrator using the Claude Agent SDK.

## Install

```bash
pip install -e .
```

## Usage

```bash
# Parse a spec into features
orchestrate parse-spec spec.md -o features.json

# Run the orchestration loop
orchestrate run --project /path/to/project

# Check status
orchestrate status --project /path/to/project
```

## Project Configuration

Create an `orchestrator.toml` in your project root:

```toml
features_file = "features.json"
progress_file = "progress.txt"
init_script = "scripts/dev.sh"
commit_prefix = "feat: "
model = "sonnet"

[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp@latest"]
```
