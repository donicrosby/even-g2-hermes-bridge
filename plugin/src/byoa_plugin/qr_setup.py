"""QR code generator for even_g2 plugin bootstrap.

Generates a QR encoding `wss://<host>?token=<token>` so the user can scan
it from the phone camera to populate the glasses-app's bridge URL + token
fields without manual entry.

Three render forms:
  - Terminal ASCII/Unicode blocks (printed to stdout)
  - PNG file (written to ~/.hermes/even_g2_qr.png by default)
  - HTTP endpoint `GET /qr` (served by the WS server's port)
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import qrcode
from qrcode.constants import ERROR_CORRECT_M

if TYPE_CHECKING:
    from byoa_plugin.config import BridgeConfig

LOG = logging.getLogger("byoa_plugin.qr_setup")


def build_payload(advertised_url: str, token: str) -> str:
    """Build the QR payload URL: `wss://...?token=...`"""
    sep = "&" if "?" in advertised_url else "?"
    return f"{advertised_url}{sep}token={token}"


def generate_png(payload: str, *, box_size: int = 8, border: int = 2) -> bytes:
    """Return PNG bytes encoding the QR code for `payload`."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_terminal(payload: str, *, invert: bool = False) -> str:
    """Render the QR as ASCII art suitable for terminal printing.

    Returns a multi-line string. Caller should `print(render_terminal(...))`.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.StringIO()
    # Use the library's own terminal renderer (compact ASCII).
    matrix = qr.get_matrix()
    full_block = "█" if not invert else " "
    empty_block = " " if not invert else "█"
    for row in matrix:
        line = "".join(full_block if cell else empty_block for cell in row)
        # Double each row horizontally for square-ish aspect ratio in most terminals.
        buf.write(line)
        buf.write("\n")
        buf.write(line)
        buf.write("\n")
    return buf.getvalue()


def write_png(
    payload: str, path: Path, *, box_size: int = 8, border: int = 2,
) -> Path:
    """Write the QR as a PNG to `path`. Returns the path."""
    png = generate_png(payload, box_size=box_size, border=border)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    LOG.info("wrote QR PNG to %s", path)
    return path


def print_qr(cfg: BridgeConfig, *, out: Path | None = None) -> str:
    """Print QR to terminal + write PNG. Returns the payload string.

    Convenience entry point for the `hermes even-g2 qr` CLI command.
    """
    advertised = cfg.advertised_url
    payload = build_payload(advertised, cfg.token)

    print()
    print("  Even G2 — scan with your phone camera or glasses-app QR reader:")
    print(f"  {payload}")
    print()
    print(render_terminal(payload))

    png_path = out or (Path.home() / ".hermes" / "even_g2_qr.png")
    try:
        write_png(payload, png_path)
        print(f"  PNG written to: {png_path}")
    except OSError as e:
        LOG.warning("failed to write PNG: %s", e)
    print()
    return payload
