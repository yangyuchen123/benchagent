"""Tool-free runtime marker for the manually repaired no-MCP environment.

The benchmark evaluates the submitted artifact offline.  It intentionally
registers no benchmark-owned Agent tools.
"""

BENCHMARK_ENVIRONMENT_TOOLS: tuple[str, ...] = ()
