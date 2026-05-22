"""Typer CLI entrypoint."""

import typer

app = typer.Typer(help="Payroll CLI")


@app.callback()
def main() -> None:
    """Payroll CLI."""


@app.command()
def health() -> None:
    typer.echo("ok")


if __name__ == "__main__":
    app()
