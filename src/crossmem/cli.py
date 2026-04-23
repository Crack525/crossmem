"""CLI interface for crossmem."""

import click

from crossmem.commands.core import forget, graph, ingest, init, save, search, serve, stats, update
from crossmem.commands.doctor import doctor
from crossmem.commands.hooks import install_hook, install_instructions, prompt_search, recall
from crossmem.commands.setup import setup


@click.group()
@click.version_option()
def main() -> None:
    """Cross-project memory for AI coding agents."""


# Register all commands
main.add_command(ingest)
main.add_command(search)
main.add_command(forget)
main.add_command(update)
main.add_command(save)
main.add_command(graph)
main.add_command(setup)
main.add_command(serve)
main.add_command(stats)
main.add_command(init)
main.add_command(recall)
main.add_command(prompt_search)
main.add_command(install_instructions)
main.add_command(install_hook)
main.add_command(doctor)
