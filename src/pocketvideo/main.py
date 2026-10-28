"""Main entry point for PocketVideo desktop GUI and headless CLI verification."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional

from pocketvideo import __app_name__, __version__
from pocketvideo.core.config import configure_logging
from pocketvideo.core.i18n import format_bytes
from pocketvideo.core.models import CompatibilityMode, OperationMode, PresetId
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pocketvideo",
        description="PocketVideo — Open-Source Windows Video Compressor & Converter",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument("--cli-compress", nargs="+", help="Run batch compression in headless CLI mode")
    parser.add_argument(
        "--preset",
        choices=[p.value for p in PresetId],
        default=PresetId.BEST_BALANCE.value,
        help="Compression preset ID",
    )
    parser.add_argument("--target-size-mb", type=float, default=250.0, help="Target size in MB for Preset E")
    parser.add_argument("--format", default="mp4", choices=["mp4", "mkv", "mov", "webm", "avi"])
    parser.add_argument("--output-dir", type=str, default="", help="Destination directory for output files")
    parser.add_argument(
        "--compat",
        choices=[c.value for c in CompatibilityMode],
        default=CompatibilityMode.MODERN_ANDROID.value,
    )
    return parser.parse_args(argv)


def run_cli_batch(args: argparse.Namespace) -> int:
    """Execute real video compression from CLI arguments and print comparison results."""
    configure_logging()
    controller = AppController()
    controller.active_preset = PresetId(args.preset)
    controller.target_size_mb = args.target_size_mb
    controller.target_container = args.format
    controller.compatibility_mode = CompatibilityMode(args.compat)
    if args.output_dir:
        controller.output_dir = Path(args.output_dir).resolve()

    added, errors = controller.add_files(args.cli_compress)
    for err in errors:
        print(f"[ERROR] {err}", file=sys.stderr)
    if not added:
        return 1

    items = controller.enqueue_current_batch(operation_mode=OperationMode.COMPRESS, auto_start=True)
    assert controller.queue_manager is not None
    controller.queue_manager.wait_for_all(timeout=600.0)

    exit_code = 0
    for item in items:
        if item.result:
            r = item.result
            print(
                f"[COMPLETED] {r.input_path.name} -> {r.output_path.name} | "
                f"Original: {format_bytes(r.original_size_bytes)} | "
                f"Compressed: {format_bytes(r.output_size_bytes)} | "
                f"Saved: {format_bytes(r.space_saved_bytes)} ({r.reduction_percent:.1f}%)"
            )
        else:
            print(f"[FAILED] {item.spec.input_path.name}: {item.error_message}", file=sys.stderr)
            exit_code = 2
    return exit_code


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.cli_compress:
        return run_cli_batch(args)

    configure_logging()
    if not PYSIDE6_AVAILABLE:
        print(
            "PySide6 is required to launch the desktop GUI. Install with: pip install PySide6",
            file=sys.stderr,
        )
        return 1

    from PySide6 import QtCore, QtWidgets
    from pocketvideo.ui.main_window import PocketVideoMainWindow

    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)

    controller = AppController()
    window = PocketVideoMainWindow(controller)
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
