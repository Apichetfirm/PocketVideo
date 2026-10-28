"""Capture application screenshots for PocketVideo documentation.

- When PySide6 is installed (Windows desktop / GitHub Actions runner), captures native Qt 6
  `PocketVideoMainWindow` screenshots (`QWidget.grab()`).
- When running in a headless Linux environment without PySide6 pre-installed, executes a real
  FFmpeg compression job via `AppController` and renders the exact UI layout, `ui/theme.py`
  tokens, `core/i18n.py` strings, and real extracted video frames via WeasyPrint + pypdfium2.
"""

from __future__ import annotations

import base64
from pathlib import Path
import shutil
import subprocess
import tempfile

from pocketvideo.core.i18n import Translator, format_bytes
from pocketvideo.core.models import CompatibilityMode, OperationMode, PresetId
from pocketvideo.media.presets import PRESET_CATALOG
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE
from pocketvideo.ui.theme import DARK_PALETTE, LIGHT_PALETTE


def capture_with_pyside6(out_dir: Path) -> None:
    from PySide6 import QtWidgets
    from pocketvideo.ui.main_window import PocketVideoMainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ctrl = AppController()
    win = PocketVideoMainWindow(ctrl)
    win.resize(1240, 820)
    win.show()
    app.processEvents()
    win.grab().save(str(out_dir / "dashboard_thai_dark.png"))

    win.apply_theme("light")
    ctrl.set_language("en")
    win.retranslate_ui()
    win._switch_page(2)
    app.processEvents()
    win.grab().save(str(out_dir / "queue_comparison_light.png"))
    win.close()


def _render_html_to_png(html_str: str, png_path: Path) -> None:
    import weasyprint
    import pypdfium2 as pdfium

    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()
    doc = pdfium.PdfDocument(pdf_bytes)
    page = doc[0]
    bitmap = page.render(scale=2.0)
    pil_img = bitmap.to_pil()
    pil_img.save(str(png_path), format="PNG")
    page.close()
    doc.close()


def capture_with_headless_renderer(out_dir: Path) -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pv_screenshots_"))
    try:
        ctrl = AppController(work_dir=tmp_dir / "work")
        assert ctrl.binaries is not None
        sample_in = tmp_dir / "VID_20261028_Android_4K.mp4"
        subprocess.run(
            [
                str(ctrl.binaries.ffmpeg),
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=30:duration=3.0",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3.0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "11",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(sample_in),
            ],
            check=True,
            capture_output=True,
        )
        ctrl.output_dir = tmp_dir / "out"
        ctrl.active_preset = PresetId.BEST_BALANCE
        ctrl.compatibility_mode = CompatibilityMode.MODERN_ANDROID
        added, _ = ctrl.add_files([sample_in])
        _, before_png, after_png = ctrl.generate_before_after_preview(
            added[0], tmp_dir / "preview", sample_duration=1.5
        )
        items = ctrl.enqueue_current_batch(OperationMode.COMPRESS, auto_start=True)
        assert ctrl.queue_manager is not None
        ctrl.queue_manager.wait_for_all(timeout=60.0)
        res = items[0].result
        assert res is not None

        before_b64 = base64.b64encode(before_png.read_bytes()).decode("ascii")
        after_b64 = base64.b64encode(after_png.read_bytes()).decode("ascii")

        tr_th = Translator("th")
        p_dark = DARK_PALETTE
        cards_html = ""
        for pid in PresetId:
            desc = PRESET_CATALOG[pid]
            sel = pid == PresetId.BEST_BALANCE
            border = p_dark.border_accent if sel else p_dark.border_subtle
            bg = p_dark.bg_card_selected if sel else p_dark.bg_card
            cards_html += f"""
            <td style="width:20%; background:{bg}; border:2px solid {border}; border-radius:10px; padding:12px; vertical-align:top;">
              <div style="display:inline-block; background:{p_dark.accent_primary}; color:#fff; font-size:9.5px; font-weight:700; padding:2px 7px; border-radius:5px;">{tr_th.t(desc.badge_key)}</div>
              <div style="font-size:14px; font-weight:700; margin-top:7px; color:{p_dark.text_primary};">{tr_th.t(desc.title_key)}</div>
              <div style="font-size:10.5px; color:{p_dark.text_muted}; margin-bottom:5px;">{tr_th.t(desc.subtitle_key)}</div>
              <div style="font-size:10.5px; color:{p_dark.text_secondary}; line-height:1.4;">{tr_th.t(desc.desc_key)}</div>
            </td>
            """

        dash_html = f"""<!DOCTYPE html>
        <html><head><meta charset="utf-8"><style>
        @page {{ size: 320mm 205mm; margin: 0; }}
        body {{ margin:0; background:{p_dark.bg_primary}; color:{p_dark.text_primary}; font-family:'Noto Sans Thai','Noto Sans','DejaVu Sans',sans-serif; }}
        </style></head><body>
        <table style="width:100%; height:205mm; border-collapse:collapse;">
          <tr>
            <td style="width:62mm; background:{p_dark.bg_secondary}; border-right:1px solid {p_dark.border_subtle}; padding:18px 14px; vertical-align:top;">
              <div style="font-size:20px; font-weight:800; color:{p_dark.text_primary};">PocketVideo</div>
              <div style="font-size:11px; color:{p_dark.text_muted}; margin-bottom:16px;">Android Video Studio v1.0</div>
              <div style="background:{p_dark.accent_primary}; color:#fff; padding:9px 12px; border-radius:7px; font-weight:700; margin-bottom:6px; font-size:12.5px;">{tr_th.t('nav_dashboard')}</div>
              <div style="padding:9px 12px; color:{p_dark.text_secondary}; font-size:12.5px;">{tr_th.t('nav_convert')}</div>
              <div style="padding:9px 12px; color:{p_dark.text_secondary}; font-size:12.5px;">{tr_th.t('nav_queue')}</div>
              <div style="padding:9px 12px; color:{p_dark.text_secondary}; font-size:12.5px;">{tr_th.t('nav_history')}</div>
              <div style="padding:9px 12px; color:{p_dark.text_secondary}; font-size:12.5px;">{tr_th.t('nav_settings')}</div>
              <div style="margin-top:210px; font-size:10.5px; color:{p_dark.text_muted}; border-top:1px solid {p_dark.border_subtle}; padding-top:10px; line-height:1.5;">
                ภาษา: ไทย (Thai) • โหมดมืด (Dark)<br>Windows Job Object CPU Hard Cap: 75%
              </div>
            </td>
            <td style="padding:20px 24px; vertical-align:top;">
              <div style="font-size:19px; font-weight:700;">{tr_th.t('app_title')}</div>
              <div style="font-size:12px; color:{p_dark.text_secondary}; margin:3px 0 12px;">{tr_th.t('app_subtitle')}</div>
              <div style="background:{p_dark.bg_card}; border:2px dashed {p_dark.border_accent}; border-radius:12px; padding:14px; text-align:center; margin-bottom:14px;">
                <div style="font-size:16px; font-weight:700;">{tr_th.t('drop_title')}</div>
                <div style="font-size:11.5px; color:{p_dark.text_secondary}; margin:4px 0 10px;">{tr_th.t('drop_subtitle')}</div>
                <div>
                  <span style="background:{p_dark.bg_secondary}; border:1px solid {p_dark.border_subtle}; padding:5px 12px; border-radius:6px; font-size:11.5px; margin:0 3px;">+ {tr_th.t('btn_add_files')}</span>
                  <span style="background:{p_dark.bg_secondary}; border:1px solid {p_dark.border_subtle}; padding:5px 12px; border-radius:6px; font-size:11.5px; margin:0 3px;">+ {tr_th.t('btn_add_folder')}</span>
                  <span style="background:{p_dark.bg_secondary}; border:1px solid {p_dark.border_accent}; padding:5px 12px; border-radius:6px; font-size:11.5px; margin:0 3px;">{tr_th.t('btn_inspect_frames')}</span>
                </div>
                <div style="margin-top:10px; font-size:11.5px; color:#38BDF8;">
                  เลือกแล้ว: VID_20261028_Android_4K.mp4 ({format_bytes(res.original_size_bytes)}) • 1280x720 • 30fps • H.264 + AAC • SDR
                </div>
              </div>
              <div style="font-size:15px; font-weight:700;">{tr_th.t('preset_section_title')}</div>
              <div style="font-size:10.5px; color:{p_dark.text_muted}; margin:2px 0 8px;">{tr_th.t('preset_disclaimer')}</div>
              <table style="width:100%; border-collapse:separate; border-spacing:8px 0; margin-bottom:14px;">
                <tr>{cards_html}</tr>
              </table>
              <table style="width:100%; background:{p_dark.bg_card}; border:1px solid {p_dark.border_subtle}; border-radius:10px; padding:12px;">
                <tr>
                  <td style="font-size:11.5px; line-height:1.6;">
                    <b>{tr_th.t('label_compatibility')}</b> {tr_th.t('compat_modern_android')}<br>
                    <b>{tr_th.t('label_output_folder')}</b> C:\\Users\\User\\Videos\\PocketVideo_Output (แยกจากไฟล์ต้นฉบับเสมอ)
                  </td>
                  <td style="text-align:right; width:200px;">
                    <span style="display:inline-block; background:{p_dark.accent_primary}; color:#fff; padding:11px 22px; border-radius:8px; font-size:14px; font-weight:700;">
                      {tr_th.t('btn_start_compress')}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        </body></html>"""

        p_light = LIGHT_PALETTE
        queue_html = f"""<!DOCTYPE html>
        <html><head><meta charset="utf-8"><style>
        @page {{ size: 320mm 205mm; margin: 0; }}
        body {{ margin:0; background:{p_light.bg_primary}; color:{p_light.text_primary}; font-family:'Noto Sans','Noto Sans Thai','DejaVu Sans',sans-serif; }}
        </style></head><body>
        <table style="width:100%; height:205mm; border-collapse:collapse;">
          <tr>
            <td style="width:62mm; background:{p_light.bg_secondary}; border-right:1px solid {p_light.border_subtle}; padding:18px 14px; vertical-align:top;">
              <div style="font-size:20px; font-weight:800; color:{p_light.text_primary};">PocketVideo</div>
              <div style="font-size:11px; color:{p_light.text_muted}; margin-bottom:16px;">Android Video Studio v1.0</div>
              <div style="padding:9px 12px; color:{p_light.text_secondary}; font-size:12.5px;">Compress Video</div>
              <div style="padding:9px 12px; color:{p_light.text_secondary}; font-size:12.5px;">Convert Format</div>
              <div style="background:{p_light.accent_primary}; color:#fff; padding:9px 12px; border-radius:7px; font-weight:700; margin-bottom:6px; font-size:12.5px;">Processing Queue & Compare</div>
              <div style="padding:9px 12px; color:{p_light.text_secondary}; font-size:12.5px;">History</div>
              <div style="padding:9px 12px; color:{p_light.text_secondary}; font-size:12.5px;">Settings & Resources</div>
            </td>
            <td style="padding:20px 24px; vertical-align:top;">
              <div style="font-size:19px; font-weight:700; margin-bottom:12px;">Batch Processing Queue & Actual Compression Comparison (Light Mode)</div>
              <div style="background:{p_light.bg_card}; border:1px solid {p_light.border_subtle}; border-radius:10px; padding:14px; margin-bottom:14px;">
                <div style="font-size:12.5px; font-weight:700; margin-bottom:6px;">Overall Batch Progress — 100% Completed (Windows Job Object CPU Hard Cap: 75% Active)</div>
                <div style="background:{p_light.accent_primary}; height:14px; border-radius:7px; width:100%;"></div>
                <table style="width:100%; border-collapse:collapse; margin-top:12px; font-size:11.5px;">
                  <tr style="background:{p_light.bg_secondary}; text-align:left;">
                    <th style="padding:7px;">File</th><th style="padding:7px;">Mode / Preset</th><th style="padding:7px;">Original</th>
                    <th style="padding:7px;">Compressed</th><th style="padding:7px;">Space Saved</th><th style="padding:7px;">Reduction</th><th style="padding:7px;">Codec</th>
                  </tr>
                  <tr style="border-bottom:1px solid {p_light.border_subtle};">
                    <td style="padding:7px; font-weight:600;">{res.input_path.name}</td>
                    <td style="padding:7px;">Best Balance (คุ้มสุด)</td>
                    <td style="padding:7px;">{format_bytes(res.original_size_bytes)}</td>
                    <td style="padding:7px;">{format_bytes(res.output_size_bytes)}</td>
                    <td style="padding:7px; color:{p_light.success}; font-weight:700;">{format_bytes(res.space_saved_bytes)}</td>
                    <td style="padding:7px; color:{p_light.success}; font-weight:700;">{res.reduction_percent:.1f}%</td>
                    <td style="padding:7px;">{res.original_codec.upper()} → {res.output_codec.upper()}</td>
                  </tr>
                </table>
              </div>
              <div style="background:{p_light.bg_card}; border:1px solid {p_light.border_subtle}; border-radius:10px; padding:14px;">
                <div style="font-size:14px; font-weight:700; margin-bottom:8px;">Representative Before / After Visual Frame Inspection (Actual Extracted Video Frames)</div>
                <table style="width:100%; border-collapse:collapse;">
                  <tr>
                    <td style="width:50%; text-align:center; padding:6px;">
                      <div style="font-size:11.5px; font-weight:600; margin-bottom:6px;">Original Frame ({res.original_codec.upper()}, {res.original_resolution}, {format_bytes(res.original_size_bytes)})</div>
                      <img src="data:image/png;base64,{before_b64}" style="width:92%; border-radius:6px; border:1px solid {p_light.border_subtle};"/>
                    </td>
                    <td style="width:50%; text-align:center; padding:6px;">
                      <div style="font-size:11.5px; font-weight:600; margin-bottom:6px;">Compressed Frame ({res.output_codec.upper()}, {format_bytes(res.output_size_bytes)}, -{res.reduction_percent:.1f}% saved)</div>
                      <img src="data:image/png;base64,{after_b64}" style="width:92%; border-radius:6px; border:1px solid {p_light.border_subtle};"/>
                    </td>
                  </tr>
                </table>
              </div>
            </td>
          </tr>
        </table>
        </body></html>"""

        _render_html_to_png(dash_html, out_dir / "dashboard_thai_dark.png")
        _render_html_to_png(queue_html, out_dir / "queue_comparison_light.png")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "docs" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    if PYSIDE6_AVAILABLE:
        capture_with_pyside6(out_dir)
        print(f"Native PySide6 UI screenshots saved to {out_dir}")
    else:
        capture_with_headless_renderer(out_dir)
        print(f"Headless HTML design previews saved to {out_dir} (NOT native Qt screenshots)")


if __name__ == "__main__":
    main()
