# Contributing to Agents-On-Hand

Thank you for your interest in contributing! Here's how to get started.

---

## Development Setup

```bash
git clone https://github.com/your-org/Agents-On-Hand.git
cd Agents-On-Hand
pip install -r requirements.txt
pip install pytest pytest-asyncio
cp .env.example .env   # fill in your credentials
```

## Running Tests

```bash
python -m pytest tests/ -v
```

All 11 tests must pass before submitting a PR.

## Project Structure

```
agents_on_hand/     Core package (bot, config, session, ACP, stream handlers)
tests/              Automated tests (pytest)
docs/               Architecture and design docs
.github/            CI workflows and issue templates
main.py             Entry point
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a detailed system overview.

## Pull Request Guidelines

1. **Branch naming**: `feature/short-description` or `fix/short-description`
2. **Tests required**: Add or update tests for any new functionality in `tests/`
3. **No secrets in commits**: Never commit `.env` or tokens
4. **Describe your PR**: Include what changed and why

## Code Style

- Follow PEP 8
- Use type hints where practical
- Keep functions focused and under ~60 lines
- Log meaningful events using `logger.info` / `logger.error`
