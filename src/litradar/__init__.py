"""litradar — a daily journal literature radar.

The engine is deterministic and headless: it fetches journal surfaces, filters by a
strict date window, deduplicates, optionally asks an LLM to rank, validates every
model claim, and renders an auditable Markdown digest.  A front-end (Obsidian
plugin, cron, plain CLI) is never on the critical path.
"""

__version__ = "2.0.0"
SCHEMA = 2
__all__ = ["__version__", "SCHEMA"]
