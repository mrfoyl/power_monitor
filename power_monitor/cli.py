"""
power_monitor CLI

Usage examples:
  python -m power_monitor check 2615
  python -m power_monitor check "Storgata 1, Lillehammer"
  python -m power_monitor list
  python -m power_monitor list --provider glitre
  python -m power_monitor providers
"""

import sys
import logging
from datetime import datetime, timezone
from typing import List, Type

import click
from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text

from .collectors.base import BaseCollector
from .collectors.elvia import ElviaCollector
from .collectors.glitre import GlitreCollector
from .collectors.arva import ArvaCollector
from .collectors.vevig import VevigCollector
from .collectors.etna import EtnaCollector
from .geocoding import lookup_postnummer, lookup_address
from .models import PowerOutage

# Force UTF-8 output so Norwegian characters and box-drawing work on Windows
console = Console(highlight=False)

# Providers ordered by relevance for Innlandet
INNLANDET_PROVIDERS: List[Type[BaseCollector]] = [ElviaCollector, VevigCollector, EtnaCollector]
ALL_PROVIDERS: List[Type[BaseCollector]] = [ElviaCollector, VevigCollector, EtnaCollector, GlitreCollector, ArvaCollector]

PROVIDER_MAP = {
    "innlandet": INNLANDET_PROVIDERS,
    "elvia":     [ElviaCollector],
    "vevig":     [VevigCollector],
    "etna":      [EtnaCollector],
    "glitre":    [GlitreCollector],
    "arva":      [ArvaCollector],
    "all":       ALL_PROVIDERS,
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _time_ago(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    diff = datetime.now(timezone.utc) - dt
    total_sec = int(diff.total_seconds())
    if total_sec < 0:
        return "in the future"
    h, rem = divmod(total_sec, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m}m ago"
    return f"{m}m ago"


def _collect(providers: List[Type[BaseCollector]]) -> List[PowerOutage]:
    outages: List[PowerOutage] = []
    for Cls in providers:
        collector = Cls()
        try:
            found = collector.fetch_outages()
            outages.extend(found)
        except NotImplementedError as e:
            console.print(f"[yellow]  [SKIP] {collector.name}: {e}[/yellow]")
        except Exception as e:
            console.print(f"[red]  [ERR] {collector.name}: {e}[/red]")
    return outages


def _print_outages(outages: List[PowerOutage], location_label: str) -> None:
    if not outages:
        console.print(f"\n[green]OK  No active outages found for {location_label}.[/green]")
        return

    console.print(f"\n[bold red]!! {outage_word(outages)} for {location_label}[/bold red]\n")

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Provider", style="cyan", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Status")
    table.add_column("Municipality")
    table.add_column("Affected", justify="right")
    table.add_column("Started")
    table.add_column("Message")

    for o in outages:
        status_color = "red" if "Pågående" in o.status else "yellow"
        table.add_row(
            o.provider,
            o.outage_type,
            Text(o.status, style=status_color),
            o.municipality or "-",
            str(o.num_affected),
            _time_ago(o.start_time),
            (o.customer_message or "")[:60] or "-",
        )

    console.print(table)


def outage_word(outages: List[PowerOutage]) -> str:
    n = len(outages)
    return f"{n} outage{'s' if n != 1 else ''}"


# ── CLI commands ───────────────────────────────────────────────────────────────

@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Show debug logging.")
def cli(verbose: bool) -> None:
    """Norwegian power outage monitor -- check by postal code or address."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)


@cli.command()
@click.argument("query")
@click.option(
    "--all-providers", "-a", is_flag=True, default=False,
    help="Search all providers (default: Innlandet/Elvia only).",
)
def check(query: str, all_providers: bool) -> None:
    """
    Check outages for a postal code or address.

    \b
    Examples:
      python -m power_monitor check 2615
      python -m power_monitor check "Storgata 1, Lillehammer"
      python -m power_monitor check 2317 --all-providers
    """
    providers = ALL_PROVIDERS if all_providers else INNLANDET_PROVIDERS
    is_postnr = query.strip().isdigit() and len(query.strip()) == 4

    kind = "postal code" if is_postnr else "address"
    console.print(f"Looking up {kind} [bold]{query}[/bold] via Kartverket ...")

    location = lookup_postnummer(query.strip()) if is_postnr else lookup_address(query.strip())

    if not location:
        console.print(f"[red]Could not find location for: {query!r}[/red]")
        sys.exit(1)

    municipality = location["municipality"]
    county = location.get("county", "")
    poststed = location.get("poststed", "")

    if is_postnr:
        label = f"{query} {poststed} ({municipality}, {county})"
    else:
        label = f"{location.get('full_address', query)} ({municipality}, {county})"

    console.print(
        f"  -> [bold]{municipality}[/bold] kommune, {county}"
        + (f"  |  {location['postnummer']} {poststed}" if is_postnr else "")
    )
    console.print(
        f"Fetching from {len(providers)} provider(s): "
        + ", ".join(Cls().name for Cls in providers)
        + " ..."
    )

    all_outages = _collect(providers)
    matching = [
        o for o in all_outages
        if o.municipality.upper() == municipality.upper()
    ]

    _print_outages(matching, label)


@cli.command(name="list")
@click.option(
    "--provider", "provider_key", default="innlandet",
    type=click.Choice(sorted(PROVIDER_MAP.keys()), case_sensitive=False),
    help="Which provider(s) to query (default: innlandet).",
    show_default=True,
)
def list_outages(provider_key: str) -> None:
    """List all current outages from a provider."""
    providers = PROVIDER_MAP[provider_key]
    names = ", ".join(Cls().name for Cls in providers)
    console.print(f"Fetching all outages from: [bold]{names}[/bold] ...")

    outages = _collect(providers)

    if not outages:
        console.print("\n[green]OK  No active outages.[/green]")
        return

    console.print(f"\n[bold red]!! {outage_word(outages)} active[/bold red]\n")

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Provider", style="cyan", no_wrap=True)
    table.add_column("Municipality")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Affected", justify="right")
    table.add_column("Started")
    table.add_column("Message")

    for o in sorted(outages, key=lambda x: x.municipality):
        status_color = "red" if "Pågående" in o.status else "yellow"
        table.add_row(
            o.provider,
            o.municipality or "-",
            o.outage_type,
            Text(o.status, style=status_color),
            str(o.num_affected),
            _time_ago(o.start_time),
            (o.customer_message or "")[:60] or "-",
        )

    console.print(table)


@cli.command()
def providers() -> None:
    """List configured providers and their status."""
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Provider", style="cyan")
    table.add_column("Region")
    table.add_column("Status")
    table.add_column("Endpoints")

    for Cls in ALL_PROVIDERS:
        c = Cls()
        try:
            urls = c.query_urls if hasattr(c, "query_urls") else []
            if not urls:
                c.fetch_outages()  # trigger NotImplementedError if not configured
            status = Text("[OK] configured", style="green")
            endpoint_count = str(len(urls))
        except NotImplementedError:
            status = Text("[--] endpoint needed", style="yellow")
            endpoint_count = "0"
        except Exception as e:
            status = Text(f"[ERR] {e}", style="red")
            endpoint_count = "?"

        table.add_row(c.name, c.region, status, endpoint_count)

    console.print(table)
