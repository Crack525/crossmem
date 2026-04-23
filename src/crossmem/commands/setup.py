"""Setup command — one-time setup for all integrations."""

import click


@click.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """One-time setup: hook + instructions + ingest.

    Runs install-hook (Claude Code), install-hook --tool copilot (workspace),
    install-instructions (Gemini), and ingest (pull existing memories).
    """
    from crossmem.commands.core import ingest
    from crossmem.commands.hooks import install_hook, install_instructions

    click.echo("Setting up crossmem...\n")

    click.echo("1. Claude Code hook")
    ctx.invoke(install_hook, tool="claude", uninstall=False, dry_run=False,
               global_=False, project=None)
    click.echo()

    click.echo("2. Copilot instructions (workspace)")
    ctx.invoke(install_hook, tool="copilot", uninstall=False, dry_run=False,
               global_=False, project=None)
    click.echo()

    click.echo("3. Gemini instructions")
    ctx.invoke(install_instructions, uninstall=False, dry_run=False)
    click.echo()

    click.echo("4. Ingesting existing memories")
    ctx.invoke(ingest)
    click.echo()

    click.echo("Done. Memories will load automatically in all tools.")
