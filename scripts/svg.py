"""Small, deterministic SVG helpers shared by the two generators."""
from html import escape

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
PALETTES = {
    "dark": dict(bg="#0d1117", fg="#e6edf3", muted="#8b949e", line="#30363d",
                 green="#3fb950", blue="#58a6ff", accent="#e0a45e"),
    "light": dict(bg="#ffffff", fg="#1f2328", muted="#656d76", line="#d0d7de",
                  green="#1a7f37", blue="#0969da", accent="#9a5b1f"),
}


def text(value, x, y, color, size=15, weight=400, extra=""):
    width = len(value) * size * 0.6
    return (f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
            f'font-weight="{weight}" textLength="{width:.2f}" '
            f'lengthAdjust="spacing" xml:space="preserve" {extra}>{escape(value)}</text>')


def document(width, height, title, description, parts):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc" '
            f'font-family="{MONO}">\n<title id="title">{escape(title)}</title>\n'
            f'<desc id="desc">{escape(description)}</desc>\n' + "\n".join(parts) + "\n</svg>\n")
