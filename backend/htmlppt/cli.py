"""htmlppt command-line interface."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from htmlppt.analyze.segmenter import build_deck, describe
from htmlppt.brand.presets import apply_preset, list_palettes, list_presets, palette_brand
from htmlppt.extract.renderer import snapshot_html
from htmlppt.layout.engine import layout_deck
from htmlppt.render.pptx_writer import write_pptx

app = typer.Typer(help="HTML to PowerPoint Converter")


@app.command()
def convert(
    input_file: Path = typer.Argument(..., help="Path to AI-generated HTML file"),
    output_file: Path = typer.Option(Path("output.pptx"), "-o", "--output", help="Output .pptx path"),
    preset: str = typer.Option("corporate", "-p", "--preset", help="Style preset (minimal, corporate, bold, data-dense, executive)"),
    palette: str = typer.Option("ocean", "--palette", help="Built-in palette (ocean, slate, forest, burgundy, sunset, mono)"),
    dark: bool = typer.Option(False, "--dark", help="Enable dark theme"),
    explain: bool = typer.Option(False, "--explain", help="Print semantic analysis without generating PPTX"),
):
    """Convert an HTML presentation or dashboard into a native, editable PowerPoint presentation."""
    if not input_file.exists():
        typer.secho(f"Error: input file {input_file} not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"Rendering and snapshotting {input_file}...")
    snap = snapshot_html(path=input_file)

    typer.echo("Analyzing semantic structure...")
    deck = build_deck(snap)

    if explain:
        typer.echo("\n--- Semantic Deck Structure ---")
        typer.echo(describe(deck))
        return

    # Prepare Brand & Preset
    brand = palette_brand(palette_id=palette)
    brand = apply_preset(brand, preset_id=preset, dark=dark)

    typer.echo(f"Running layout engine with preset '{preset}' and palette '{palette}'...")
    deck_model = layout_deck(deck, brand)

    typer.echo(f"Writing PowerPoint presentation ({len(deck_model.slides)} slides) to {output_file}...")
    write_pptx(deck_model, output_path=output_file)

    typer.secho(f"Successfully created {output_file}!", fg=typer.colors.GREEN, bold=True)


@app.command()
def presets():
    """List available style presets."""
    typer.echo("Available Style Presets:")
    for p in list_presets():
        typer.echo(f"  • {p['id']:<12} - {p['label']}: {p['description']}")


@app.command()
def palettes():
    """List available built-in color palettes."""
    typer.echo("Available Color Palettes:")
    for p in list_palettes():
        typer.echo(f"  • {p['id']:<10} - {p['label']} ({', '.join('#' + c for c in p['colors'][:4])}...)")


@app.command()
def evaluate(
    corpus_dir: Path = typer.Option(Path("backend/tests/corpus"), "--corpus-dir", "-c", help="Directory containing corpus .html files"),
    output_html: Path = typer.Option(Path("corpus_report.html"), "-o", "--output", help="Output visual comparison HTML report"),
    preset: str = typer.Option("corporate", "-p", "--preset", help="Style preset (minimal, corporate, bold, data-dense, executive)"),
    palette: str = typer.Option("ocean", "--palette", help="Built-in palette (ocean, slate, forest, burgundy, sunset, mono)"),
    dark: bool = typer.Option(True, "--dark/--light", help="Enable dark or light theme"),
):
    """Evaluate all HTML decks in corpus_dir and generate a side-by-side Playwright/PPTX visual comparison report."""
    from htmlppt.evaluate import format_scorecard_table, generate_visual_comparison_report

    if not corpus_dir.exists():
        typer.secho(f"Error: corpus directory {corpus_dir} not found.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"Evaluating corpus in {corpus_dir} (palette={palette}, preset={preset}, dark={dark})...")
    scorecards = generate_visual_comparison_report(
        corpus_dir=corpus_dir,
        output_html=output_html,
        palette=palette,
        preset=preset,
        dark=dark,
    )
    typer.echo("\n" + format_scorecard_table(scorecards))
    typer.secho(f"\nVisual comparison report generated: {output_html.resolve()}", fg=typer.colors.GREEN, bold=True)


if __name__ == "__main__":
    app()

