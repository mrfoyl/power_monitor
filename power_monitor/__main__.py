import sys

# Ensure UTF-8 output on Windows (Norwegian characters, box-drawing glyphs)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from .cli import cli

if __name__ == "__main__":
    cli()
