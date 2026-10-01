```
 __      __            _     ____            _     _       
 \ \    / /           | |   |  _ \          | |   | |      
  \ \  / /__  _ __ ___ | |__ | |_) |_   _  __| | __| |_   _ 
   \ \/ / _ \| '__/ __|| '_ \|  _ <| | | |/ _` |/ _` | | | |
    \  / (_) | |  \__ \| |_) | |_) | |_| | (_| | (_| | |_| |
     \/ \___/|_|  |___/|_.__/|____/ \__,_|\__,_|\__,_|\__, |
                                                        __/ |
                                                       |___/ 
```

# WorkBuddy

**Your AI coworker.**

WorkBuddy is a real software product, not a chatbot and not a wrapper around
an LLM. It is a system built to understand *work* — the way a capable
coworker would — and to act on that understanding on your behalf.

---

## Meet Buddy

Buddy is the assistant at the center of WorkBuddy. Buddy doesn't ask you to
learn features. You just describe what you need, in your own words, the way
you'd ask a colleague:

> "Buddy, I have a meeting tomorrow. Can you make this ready?"

Buddy figures out what "ready" means for your specific situation and does the
work — instead of asking you to find the right button, template, or menu.

Buddy has a warm personality with a bit of light humor, but stays honest and
professional. Buddy never pretends to be human.

## Product Philosophy

Users should never have to think about features. Users should only have to
think about their work. Every design decision in WorkBuddy is filtered
through that lens: does this make Buddy more like a coworker, or more like a
tool?

## How Buddy Thinks

Buddy is architected like a human, not like a request handler. A single
piece of work flows through eight stages, all of which read from and write
to one shared object — `BuddyState` — representing Buddy's current
understanding of the user's work:

| Stage | Role |
|---|---|
| **Observe** | Perceives raw input from the user and the workspace |
| **Understand** | Interprets observations into user intent |
| **Think** | Reasons about intent to determine what needs to happen |
| **Plan** | Turns reasoning into a concrete sequence of steps |
| **Delegate** | Assigns steps to the right skill |
| **Execute** | Carries out the delegated work |
| **Verify** | Checks the result against the original intent |
| **Speak** | Communicates the outcome back to the user |

These aren't utility functions bolted together — they're treated as Buddy's
internal organs, each with a single responsibility, wired together by
`buddy/engine_factory.py` and run end-to-end by `core/engine.py`.

## Project Structure

```
WorkBuddy/
├── README.md
├── LICENSE
├── .gitignore
├── pyproject.toml
├── requirements.txt
│
├── docs/                  # Architecture notes and product documentation
├── backend/                # API layer (introduced in a later sprint)
├── frontend/                # Client application (introduced in a later sprint)
│
├── buddy/                  # Buddy's cognitive cycle
│   ├── engine_factory.py    # Wires the stages into a runnable Engine
│   ├── state.py              # BuddyState — Buddy's understanding of the work
│   ├── eyes.py
│   ├── brain.py
│   ├── think.py
│   ├── planner.py
│   ├── delegate.py
│   ├── hands.py
│   ├── inspector.py
│   ├── voice.py
│   └── personality.py       # Buddy's tone and conversational style
│
├── core/                   # Engine and infrastructure
│   ├── engine.py             # Runs the cognitive cycle end-to-end
│   ├── workflow.py            # Defines and sequences cycle stages
│   ├── dispatcher.py           # Routes delegated work to the right skill
│   └── events.py              # Event types exchanged across core
│
├── skills/                 # Pluggable capabilities Buddy can delegate to
│   ├── base.py                # Skill interface every skill implements
│   ├── registry.py             # Skill discovery and lookup
│   ├── dummy/                  # Reference skill for validating the interface
│   ├── excel/                  # Reserved — not implemented yet
│   ├── pdf/                    # Reserved — not implemented yet
│   └── word/                   # Reserved — not implemented yet
│
├── workspace/               # Runtime data
│   ├── uploads/
│   ├── outputs/
│   ├── sessions/
│   └── memory/
│
└── tests/
```

## Engineering Standards

- Python 3.11 or newer (`requires-python = ">=3.11"`)
- PEP 8, enforced via `ruff` and `black`
- Type hints everywhere, checked with `mypy --strict`
- Dataclasses and enums where they clarify intent
- Docstrings on every module and public interface
- Structured logging over print statements
- Modular architecture — no placeholder spaghetti, no unnecessary code

## Development Roadmap

WorkBuddy is built one sprint at a time. Each sprint has an explicit,
bounded scope, and implementation does not begin until the architecture for
that sprint is approved.

**Sprint 1 (current): Buddy's Core**
Scaffolding for the cognitive cycle (`buddy/`), the engine that runs it
(`core/`), and the skill interface (`skills/`). No LLM integration, no file
processing, no API, no UI — architecture only.

Future sprints will introduce, in order to be determined: skill
implementations (starting with Excel), LLM-backed reasoning, the FastAPI
backend, and the React frontend.

## Getting Started

```bash
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install development dependencies
pip install -r requirements.txt
```

Sprint 1 contains architecture only — there is no runnable application yet.

## License

See [LICENSE](./LICENSE). WorkBuddy is currently proprietary and unreleased;
the license will be finalized before any public distribution.
