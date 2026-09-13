# botpit

botpit is a multiplayer card-game platform where real human players and
local LLM-driven agents sit at the same table. A room can be any mix of
humans and AI seats — including all-AI tables you just spectate, and
all-human tables with no AI involved at all.

Four games ship in v1: Classic UNO, UNO Flip!, UNO Show 'em No Mercy, and
Hearts. One deterministic referee and one privacy-preserving view function
run all of them, so a human's browser and an AI agent's prompt are built
from the exact same redacted view of the game — an agent is never shown
anything it couldn't legitimately infer from its own seat.

Every AI seat is backed by a local model served through Ollama (or any
Ollama-/OpenAI-compatible endpoint), and the model per seat is swappable.
Each AI player has its own persona, so table chat reads like a table full
of distinct characters, not one voice repeated four times. Any privileged
spectator or admin can watch a game live: every seat's hand, every agent's
private reasoning as it's produced, and the public chat, all as the game
progresses (currently via HTTP polling — see
[`docs/api/api-reference.md`](docs/api/api-reference.md)).

## Quickstart

Real setup instructions — Docker Compose, Postgres/Redis, and a local
Ollama install — live in
[`docs/development/setup.md`](docs/development/setup.md). This is a
local-development project: there's no hosted instance to try it on yet.

## Learn more

- **Architecture**: [`docs/architecture/latest_architecture.md`](docs/architecture/latest_architecture.md)
- **API reference**: [`docs/api/api-reference.md`](docs/api/api-reference.md)
- **Deployment**: [`docs/deployment/`](docs/deployment/)
- **Project decisions and status**: [`DECISIONS.md`](DECISIONS.md), [`PROGRESS.md`](PROGRESS.md)
- **Contributing**: [`CONTRIBUTING.md`](CONTRIBUTING.md)

## License

MIT — see [`LICENSE`](LICENSE).
