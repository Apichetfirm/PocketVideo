"""Bilingual localization engine (Thai default + English) for PocketVideo."""

from __future__ import annotations

from typing import Any

SUPPORTED_LANGUAGES = ("th", "en")
DEFAULT_LANGUAGE = "th"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "th": {
        "app_title": "PocketVideo — โปรแกรมบีบอัดและแปลงไฟล์วิดีโอ",
        "app_subtitle": "ลดขนาดวิดีโอจากมือถือ Android อย่างฉลาด คงความคมชัดสูงสุด ทำงานในเครื่อง 100%",
        "nav_dashboard": "บีบอัดวิดีโอ",
        "nav_convert": "แปลงนามสกุลไฟล์",
        "nav_queue": "คิวงาน",
        "nav_history": "ประวัติการทำงาน",
        "nav_settings": "ตั้งค่า & ทรัพยากร",
        "drop_title": "ลากและวางไฟล์วิดีโอที่นี่",
        "drop_subtitle": "รองรับ MP4, MOV, MKV, AVI, WebM (4K, 60fps, แนวตั้ง/แนวนอน, HDR)",
        "btn_add_files": "เพิ่มไฟล์วิดีโอ",
        "btn_add_folder": "เพิ่มทั้งโฟลเดอร์",
        "btn_clear_selection": "ล้างรายการ",
        "btn_start_compress": "เริ่มบีบอัดวิดีโอ",
        "btn_start_convert": "เริ่มแปลงไฟล์",
        "btn_inspect_frames": "เปรียบเทียบภาพก่อน/หลัง & ทดลองบีบอัดสั้นๆ",
        "btn_browse_output": "เลือกโฟลเดอร์ปลายทาง",
        "label_output_folder": "โฟลเดอร์บันทึกไฟล์ (แยกจากไฟล์ต้นฉบับเสมอ):",
        "label_output_format": "รูปแบบไฟล์ปลายทาง:",
        "label_compatibility": "โหมดความเข้ากันได้ของ Android:",
        "compat_modern_android": "Modern Android (H.265/HEVC — ประหยัดพื้นที่สูงสุด)",
        "compat_max_compatibility": "Maximum Compatibility (H.264/AAC MP4 — เปิดได้ทุกอุปกรณ์)",
        "preset_section_title": "เลือกรูปแบบการบีบอัด (5 โหมดเข้าใจง่าย)",
        "preset_disclaimer": "หมายเหตุ: โหมดด้านล่างแสดงลำดับความสำคัญของการประมวลผล ผลลัพธ์จริงขึ้นอยู่กับเนื้อหาวิดีโอ บิตเรต และโค้ดเดกต้นฉบับ",
        "preset_a_title": "เน้นภาพสวย",
        "preset_a_en": "Best Picture Quality",
        "preset_a_badge": "คุณภาพสูงสุด",
        "preset_a_desc": "รักษาความคมชัดของภาพให้ใกล้เคียงต้นฉบับมากที่สุด ใช้การเข้ารหัสด้วยซอฟต์แวร์คุณภาพสูง อาจใช้เวลานานขึ้นและลดขนาดไฟล์ได้ปานกลาง",
        "preset_b_title": "คุ้มสุด",
        "preset_b_en": "Best Balance (แนะนำ)",
        "preset_b_badge": "แนะนำสำหรับ Android",
        "preset_b_desc": "สมดุลที่สุดระหว่างความคมชัด ขนาดไฟล์ที่เล็กลงอย่างเห็นผล และเวลาประมวลผลที่เหมาะสม แนะนำสำหรับวิดีโอจากมือถือ Android (H.265/HEVC)",
        "preset_c_title": "ประหยัดพื้นที่สุด",
        "preset_c_en": "Maximum Space Saving",
        "preset_c_badge": "ไฟล์เล็กสุด",
        "preset_c_desc": "เน้นลดขนาดไฟล์ให้เล็กที่สุดเท่าที่เป็นไปได้ ใช้เวลาประมวลผลนานขึ้น และอาจมีการสูญเสียรายละเอียดของภาพบางส่วน",
        "preset_d_title": "เร็วสุด",
        "preset_d_en": "Fastest Compression",
        "preset_d_badge": "ประมวลผลไว",
        "preset_d_desc": "เน้นความเร็วสูงสุด ใช้การ์ดจอ (GPU Hardware Encoding) เมื่อรองรับ หรือซีพียูความเร็วสูง (การเน้นความเร็วอาจทำให้ขนาดไฟล์หรือคุณภาพสู้โหมดช้าไม่ได้)",
        "preset_e_title": "กำหนดขนาดเอง",
        "preset_e_en": "Custom Target File Size",
        "preset_e_badge": "2-Pass แม่นยำ",
        "preset_e_desc": "ระบุขนาดไฟล์ผลลัพธ์ที่ต้องการ (MB หรือ GB) คำนวณบิตเรตวิดีโออัตโนมัติโดยหักลบเสียงและคอนเทนเนอร์ พร้อมเข้ารหัสแบบ 2-Pass แท้ (ขนาดจริงอาจคลาดเคลื่อนเล็กน้อย)",
        "label_target_size": "ขนาดไฟล์เป้าหมาย:",
        "unit_mb": "MB",
        "unit_gb": "GB",
        "warn_already_efficient": "คำเตือน: วิดีโอต้นฉบับถูกบีบอัดมาอย่างมีประสิทธิภาพอยู่แล้ว การบีบอัดซ้ำอาจช่วยลดขนาดได้น้อยมาก",
        "warn_hdr_detected": "ตรวจพบวิดีโอ HDR ({hdr_format}): ระบบจะรักษาค่าสี 10-bit/HDR10 ให้โดยอัตโนมัติเมื่อใช้ตัวเข้ารหัสที่รองรับ",
        "warn_dolby_vision": "คำเตือน Dolby Vision: การบีบอัดใหม่ไม่สามารถรับประกันการรักษาเมทาดาทา Dolby Vision ได้ครบสมบูรณ์ แนะนำให้ใช้โหมดคงค่า HDR10 พื้นฐาน หรือคัดลอกสตรีมวิดีโอโดยตรง",
        "warn_preset_c_degradation": "คำเตือน: โหมดประหยัดพื้นที่สุดอาจทำให้เห็นความคมชัดลดลงอย่างชัดเจนในฉากที่มีการเคลื่อนไหวสูง",
        "warn_preset_d_efficiency": "หมายเหตุ: การเข้ารหัสความเร็วสูง (GPU/Fast CPU) ให้ความสำคัญกับความเร็วมากกว่าประสิทธิภาพขนาดไฟล์ต่อคุณภาพ",
        "warn_target_impossible": "ไม่สามารถบีบอัดตามขนาดที่ระบุได้: ขนาดเป้าหมายเล็กเกินไปสำหรับความยาววิดีโอและเสียง กรุณาเพิ่มขนาดเป้าหมาย",
        "warn_target_high_risk": "ความเสี่ยงคุณภาพสูง: บิตเรตวิดีโอที่คำนวณได้ ({kbps} kbps) ต่ำมาก ภาพอาจแตกหรือเบลออย่างเห็นได้ชัด",
        "warn_target_moderate_risk": "ความเสี่ยงคุณภาพปานกลาง: บิตเรตวิดีโอ ({kbps} kbps) ค่อนข้างจำกัด อาจสูญเสียความคมชัดบางส่วน",
        "warn_subtitle_dropped": "คำเตือน: คอนเทนเนอร์ปลายทาง ({container}) ไม่รองรับซับไตเติลรูปแบบ {codec} ซับไตเติลนี้จะไม่ถูกรวมในไฟล์ผลลัพธ์",
        "convert_mode_smart_remux": "เปลี่ยนคอนเทนเนอร์ความเร็วสูง (Smart Remux — ไม่มีการบีบอัดซ้ำ ภาพไม่เสียคุณภาพ 100%)",
        "convert_mode_full_transcode": "แปลงโค้ดเดกเต็มรูปแบบ (Full Transcoding — เข้ารหัสวิดีโอและเสียงใหม่เพื่อความเข้ากันได้)",
        "resource_section_title": "ควบคุมการใช้พลังงานเครื่อง (Resource Management)",
        "resource_light": "เบา (Light) — เครื่องลื่นไหล ทำงานอื่นควบคู่ได้สบาย",
        "resource_balanced": "สมดุล (Balanced) — ใช้ทรัพยากรระดับปกติ",
        "resource_maximum": "เต็มกำลัง (Maximum) — เน้นบีบอัดเสร็จไวที่สุด",
        "resource_hard_cap_note": "จำกัด CPU สูงสุดแบบบังคับจริง (Hard Limit) ด้วย Windows Job Object กับโปรเซส FFmpeg",
        "resource_best_effort_note": "จำนวนเธรด, CPU Affinity และการเลือก GPU เป็นการควบคุมแบบ Best-Effort (ไม่ใช่การล็อกเปอร์เซ็นต์แบบตายตัว)",
        "queue_pause": "พักงาน",
        "queue_resume": "ทำต่อ",
        "queue_cancel": "ยกเลิก",
        "queue_cancel_all": "ยกเลิกทั้งหมด",
        "queue_retry_failed": "ลองใหม่เฉพาะงานที่ล้มเหลว",
        "comparison_header": "สรุปผลลัพธ์การบีบอัดจริง",
        "comparison_original": "ขนาดต้นฉบับ",
        "comparison_compressed": "ขนาดหลังบีบอัด",
        "comparison_saved": "ประหยัดพื้นที่ได้",
        "comparison_reduction": "ลดลงประมาณ",
        "theme_dark": "โหมดมืด (Dark)",
        "theme_light": "โหมดสว่าง (Light)",
        "lang_label": "ภาษา / Language:",
        "advanced_toggle": "ตั้งค่าขั้นสูงสำหรับผู้เชี่ยวชาญ (Advanced Settings)",
    },
    "en": {
        "app_title": "PocketVideo — Video Compressor & Converter",
        "app_subtitle": "Smart Android video compression preserving visible quality — 100% local & offline",
        "nav_dashboard": "Compress Video",
        "nav_convert": "Convert Format",
        "nav_queue": "Processing Queue",
        "nav_history": "History",
        "nav_settings": "Settings & Resources",
        "drop_title": "Drag and drop videos here",
        "drop_subtitle": "Supports MP4, MOV, MKV, AVI, WebM (4K, 60fps, Portrait/Landscape, HDR)",
        "btn_add_files": "Add Files",
        "btn_add_folder": "Add Folder",
        "btn_clear_selection": "Clear List",
        "btn_start_compress": "Start Compression",
        "btn_start_convert": "Start Conversion",
        "btn_inspect_frames": "Before/After Frame Compare & Sample Preview",
        "btn_browse_output": "Select Output Folder",
        "label_output_folder": "Destination Folder (always separate from source files):",
        "label_output_format": "Output Container:",
        "label_compatibility": "Android Compatibility Mode:",
        "compat_modern_android": "Modern Android (H.265/HEVC — Best compression efficiency)",
        "compat_max_compatibility": "Maximum Compatibility (H.264/AAC in MP4 — Plays everywhere)",
        "preset_section_title": "Choose Compression Priority (5 Simple Modes)",
        "preset_disclaimer": "Note: Presets describe encoding priorities, not guaranteed file-size reductions. Actual savings depend on source content, codec, and bitrate.",
        "preset_a_title": "Best Picture Quality",
        "preset_a_en": "เน้นภาพสวย",
        "preset_a_badge": "Visual Priority",
        "preset_a_desc": "Preserve visual quality as much as practical using high-quality software encoding. Processing takes longer and file-size reduction may be modest.",
        "preset_b_title": "Best Balance",
        "preset_b_en": "คุ้มสุด (Recommended)",
        "preset_b_badge": "Default for Android",
        "preset_b_desc": "High visible quality with meaningful file-size reduction and reasonable processing time. Recommended for Android phone videos using H.265/HEVC.",
        "preset_c_title": "Maximum Space Saving",
        "preset_c_en": "ประหยัดพื้นที่สุด",
        "preset_c_badge": "Smallest File",
        "preset_c_desc": "Prioritize smaller output files and allow slower processing. Accepts visible quality loss when necessary.",
        "preset_d_title": "Fastest Compression",
        "preset_d_en": "เร็วสุด",
        "preset_d_badge": "High Speed",
        "preset_d_desc": "Prioritize speed using compatible GPU hardware encoding when available (with automatic CPU fallback). Faster encoding can reduce size/quality efficiency.",
        "preset_e_title": "Custom Target File Size",
        "preset_e_en": "กำหนดขนาดเอง",
        "preset_e_badge": "Genuine 2-Pass",
        "preset_e_desc": "Specify target output size in MB or GB. Calculates required average video bitrate accounting for audio and container overhead using genuine two-pass software encoding (exact size not guaranteed).",
        "label_target_size": "Target Output Size:",
        "unit_mb": "MB",
        "unit_gb": "GB",
        "warn_already_efficient": "Warning: Input video is already efficiently compressed. Recompression may offer little storage benefit.",
        "warn_hdr_detected": "HDR Video Detected ({hdr_format}): 10-bit color primaries and transfer metadata will be preserved.",
        "warn_dolby_vision": "Dolby Vision Warning: Full Dolby Vision RPU metadata preservation cannot be guaranteed when re-encoding. We recommend falling back to HDR10 compatibility or remuxing without video re-encoding.",
        "warn_preset_c_degradation": "Warning: Maximum Space Saving may cause noticeable visual degradation in high-motion scenes.",
        "warn_preset_d_efficiency": "Note: Faster encoding prioritizes speed and can produce larger files or lower quality per bit than slower software presets.",
        "warn_target_impossible": "Target size rejected: The requested size is smaller than the minimum viable audio and container budget for this duration.",
        "warn_target_high_risk": "High Quality Risk: Calculated video bitrate ({kbps} kbps) is extremely low for this resolution; visible artifacts are likely.",
        "warn_target_moderate_risk": "Moderate Quality Risk: Calculated video bitrate ({kbps} kbps) is tight; minor softening may occur.",
        "warn_subtitle_dropped": "Warning: Target container ({container}) does not support subtitle codec '{codec}'. Subtitle stream will be omitted.",
        "convert_mode_smart_remux": "Fast Smart Remux (Container swap without re-encoding — 100% lossless & instant)",
        "convert_mode_full_transcode": "Full Transcoding (Re-encodes video and audio streams for target container compatibility)",
        "resource_section_title": "System Resource Controls",
        "resource_light": "Light — Keep the computer responsive",
        "resource_balanced": "Balanced — Normal resource usage",
        "resource_maximum": "Maximum — Prioritize encoding performance",
        "resource_hard_cap_note": "Enforces a genuine CPU hard cap (%) on child FFmpeg processes via Windows Job Objects.",
        "resource_best_effort_note": "Thread count, CPU affinity, and GPU encoder selection are best-effort controls (not percentage caps).",
        "queue_pause": "Pause",
        "queue_resume": "Resume",
        "queue_cancel": "Cancel",
        "queue_cancel_all": "Cancel All",
        "queue_retry_failed": "Retry Failed Jobs",
        "comparison_header": "Actual Compression Results",
        "comparison_original": "Original Size",
        "comparison_compressed": "Output Size",
        "comparison_saved": "Space Saved",
        "comparison_reduction": "Reduction",
        "theme_dark": "Dark Mode",
        "theme_light": "Light Mode",
        "lang_label": "Language / ภาษา:",
        "advanced_toggle": "Advanced Encoding & Resource Settings",
    },
}


class Translator:
    """Thread-safe localization helper defaulting to Thai ('th')."""

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self._language = language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {language}")
        self._language = language

    def t(self, key: str, **kwargs: Any) -> str:
        table = TRANSLATIONS.get(self._language, TRANSLATIONS[DEFAULT_LANGUAGE])
        template = table.get(key) or TRANSLATIONS["en"].get(key, key)
        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError:
                return template
        return template


def format_bytes(num_bytes: int) -> str:
    """Format byte count into human-readable KB / MB / GB string."""
    sign = "-" if num_bytes < 0 else ""
    val = float(abs(num_bytes))
    if val >= 1024 ** 3:
        return f"{sign}{val / (1024 ** 3):.2f} GB"
    if val >= 1024 ** 2:
        return f"{sign}{val / (1024 ** 2):.1f} MB"
    if val >= 1024:
        return f"{sign}{val / 1024:.1f} KB"
    return f"{sign}{int(val)} B"


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"
