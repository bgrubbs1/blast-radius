"""Blast Radius -- know what a schema change breaks before you ship it.

Reads lineage, schemas, queries and ownership out of DataHub over MCP, decides
deterministically which downstream assets break, and writes the migration
patches and the notification list.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
