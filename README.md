# Kiro

**Knowledge Interface & Response Operator**

A voice-first, always-available personal AI assistant designed as a heavy executive-function counterweight for ADHD brains.

## Quick Start

```bash
# Clone and enter
cd KiroMKIII

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"

# Copy default config (optional - defaults work fine)
mkdir -p ~/.kiro/config
cp config/default.yaml ~/.kiro/config/kiro.yaml

# Run the daemon
kirod
```

## Project Status

This is **Phase 0** — establishing foundation infrastructure. See [docs/07-development-plan.md](docs/07-development-plan.md) for the full roadmap.

## Architecture

Kiro runs as a single daemon process (`kirod`) with these subsystems:

- **Audio Pipeline** — Wake word detection, STT, TTS
- **Intent Handler** — Classifies and routes voice commands
- **Executive Function Engine** — Tasks, reminders, commitments
- **Memory System** — Episodic and semantic memory
- **Persona System** — Adaptive response personalities
- **LLM Gateway** — Provider-agnostic API abstraction

See [docs/02-system-architecture.md](docs/02-system-architecture.md) for details.

## Configuration

Kiro looks for configuration in:
1. `~/.kiro/config/kiro.yaml` (user config)
2. `./config/default.yaml` (defaults)

Environment variables override YAML values with prefix `KIRO_`:
```bash
export KIRO_LOG_LEVEL=DEBUG
export KIRO_DATABASE__PATH=/custom/path/kiro.db
```

## Development

```bash
# Run tests
pytest

# Type checking
mypy src/

# Linting
ruff check src/ tests/
ruff format src/ tests/
```

## Documentation

Full specifications live in `/docs`:

| Doc | Content |
|-----|---------|
| [00-README](docs/00-README.md) | Index and quick reference |
| [01-executive-summary](docs/01-executive-summary.md) | What Kiro is and why |
| [02-system-architecture](docs/02-system-architecture.md) | Subsystems and data flow |
| [03-executive-function-engine](docs/03-executive-function-engine.md) | Task management design |
| [04-persona-system](docs/04-persona-system.md) | Personality and tone |
| [05-memory-architecture](docs/05-memory-architecture.md) | Memory layers |
| [06-hardware-roadmap](docs/06-hardware-roadmap.md) | Deployment targets |
| [07-development-plan](docs/07-development-plan.md) | Phased implementation |
| [08-future-expansion](docs/08-future-expansion.md) | MKII parking lot |
| [09-assumptions-risks](docs/09-assumptions-risks.md) | Decisions and risks |

## License

MIT
