"""PDFをテキストやWordファイルに変換する処理をまとめたモジュールです。"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def _load_module(module_name: str):
    """指定したモジュールを遅延ロードします。"""

    spec = importlib.util.find_spec(module_name)
    if spec is None:
        raise ModuleNotFoundError(
            f"{module_name} が見つかりません。requirements.txt を確認してください。"
        )
    return importlib.import_module(module_name)


@dataclass
class PdfConversionDetail:
    """1つのPDFを処理した結果を整理したクラスです。"""

    source: str
    status: str
    message: str
    outputs: List[str]
    engine: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "message": self.message,
            "outputs": self.outputs,
            "engine": self.engine,
        }


class PdfConverter:
    """PDFから文字を抜き出して保存するクラスです。"""

    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.processing_config = config.get("pdf_processing", {})
        self.auto_engine_order: List[str] = [
            str(engine)
            for engine in self.processing_config.get(
                "auto_engine_order", ["pdfplumber", "pymupdf", "pdfminer"]
            )
        ]

    def run(
        self,
        pdf_folder: Path,
        output_folder: Path,
        output_format: str,
        engine: str,
    ) -> Dict[str, object]:
        """フォルダ内のPDFをまとめて変換します。"""

        pdf_folder = pdf_folder.expanduser().resolve()
        output_folder = output_folder.expanduser().resolve()
        output_folder.mkdir(parents=True, exist_ok=True)

        pdf_files = self._collect_pdfs(pdf_folder)
        output_format = output_format.lower().strip() or "text"
        engine = engine.lower().strip() or "auto"

        success_count = 0
        details: List[PdfConversionDetail] = []

        for pdf_path in pdf_files:
            result = self._process_pdf(pdf_path, output_folder, output_format, engine)
            details.append(result)
            if result.status == "success":
                success_count += 1

        return {
            "pdf_folder": str(pdf_folder),
            "output_folder": str(output_folder),
            "requested_format": output_format,
            "requested_engine": engine,
            "processed": len(pdf_files),
            "succeeded": success_count,
            "failed": len(pdf_files) - success_count,
            "details": [detail.to_dict() for detail in details],
        }

    # ------------------------------------------------------------
    # 内部処理
    # ------------------------------------------------------------
    def _collect_pdfs(self, folder: Path) -> List[Path]:
        if not folder.exists():
            return []
        return [path for path in sorted(folder.rglob("*.pdf")) if path.is_file()]

    def _process_pdf(
        self,
        pdf_path: Path,
        output_root: Path,
        output_format: str,
        engine: str,
    ) -> PdfConversionDetail:
        engines_to_try: Iterable[str]
        if engine == "auto":
            engines_to_try = self.auto_engine_order
        else:
            engines_to_try = [engine]

        last_error: Optional[Exception] = None
        for selected_engine in engines_to_try:
            try:
                text = self._extract_text(pdf_path, selected_engine)
                outputs = self._write_output(text, pdf_path, output_root, output_format)
                return PdfConversionDetail(
                    source=str(pdf_path),
                    status="success",
                    message="変換に成功しました。",
                    outputs=outputs,
                    engine=selected_engine,
                )
            except Exception as exc:  # pragma: no cover - 例外は個別ファイルで報告
                last_error = exc
                continue

        message = str(last_error) if last_error else "対応するエンジンがありません。"
        return PdfConversionDetail(
            source=str(pdf_path),
            status="failed",
            message=message,
            outputs=[],
            engine=None,
        )

    def _extract_text(self, pdf_path: Path, engine: str) -> str:
        engine = engine.lower()
        if engine == "pdfplumber":
            return self._extract_with_pdfplumber(pdf_path)
        if engine == "pdfminer":
            return self._extract_with_pdfminer(pdf_path)
        if engine in {"pymupdf", "fitz"}:
            return self._extract_with_pymupdf(pdf_path)
        raise ValueError(f"未知のエンジンです: {engine}")

    def _extract_with_pdfplumber(self, pdf_path: Path) -> str:
        pdfplumber = _load_module("pdfplumber")
        texts: List[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                texts.append(page_text)
        return "\n".join(texts)

    def _extract_with_pdfminer(self, pdf_path: Path) -> str:
        high_level = _load_module("pdfminer.high_level")
        extract_text = getattr(high_level, "extract_text")
        return str(extract_text(str(pdf_path)))

    def _extract_with_pymupdf(self, pdf_path: Path) -> str:
        fitz = _load_module("fitz")
        document = fitz.open(pdf_path)
        texts: List[str] = []
        for page in document:
            texts.append(page.get_text())
        document.close()
        return "\n".join(texts)

    def _write_output(
        self,
        text: str,
        pdf_path: Path,
        output_root: Path,
        output_format: str,
    ) -> List[str]:
        outputs: List[str] = []
        if output_format == "word":
            outputs.append(self._write_docx(text, pdf_path, output_root))
        else:
            outputs.append(self._write_text(text, pdf_path, output_root))
        return outputs

    def _write_text(self, text: str, pdf_path: Path, output_root: Path) -> str:
        output_path = output_root / f"{pdf_path.stem}.txt"
        output_path.write_text(text, encoding="utf-8-sig")
        return str(output_path)

    def _write_docx(self, text: str, pdf_path: Path, output_root: Path) -> str:
        docx_module = _load_module("docx")
        document = docx_module.Document()
        lines = text.splitlines() or [text]
        if not lines:
            lines = [""]
        for line in lines:
            document.add_paragraph(line)
        output_path = output_root / f"{pdf_path.stem}.docx"
        document.save(output_path)
        return str(output_path)


def run_pdf_conversion(
    pdf_folder: str,
    output_folder: str,
    output_format: str,
    engine: str,
    config: Dict[str, object],
) -> Dict[str, object]:
    """PdfConverter を使いやすくするラッパー関数です。"""

    converter = PdfConverter(config)
    return converter.run(Path(pdf_folder), Path(output_folder), output_format, engine)
