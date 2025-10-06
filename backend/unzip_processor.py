"""圧縮ファイルを展開する処理をまとめたモジュールです。"""

from __future__ import annotations

import csv
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class ArchiveResult:
    """1つの圧縮ファイル処理結果をわかりやすくまとめたクラスです。"""

    archive: str
    status: str
    message: str
    extracted_to: Optional[str]
    used_password: Optional[str]

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "archive": self.archive,
            "status": self.status,
            "message": self.message,
            "extracted_to": self.extracted_to,
            "used_password": self.used_password,
        }


class ZipExtractor:
    """ZIPファイルをパスワード付きでも展開するシンプルなクラスです。"""

    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.processing_config = config.get("unzip_processing", {})
        self.supported_extensions = self._load_supported_extensions()
        self.success_dir_name = self.processing_config.get("success_dir_name", "success")
        self.failed_dir_name = self.processing_config.get("failed_dir_name", "failed")

    def run(self, archive_folder: Path, password_csv: Path, output_root: Path) -> Dict[str, object]:
        archive_folder = archive_folder.expanduser().resolve()
        password_csv = password_csv.expanduser().resolve()
        output_root = output_root.expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)

        success_dir = output_root / self.success_dir_name
        failed_dir = output_root / self.failed_dir_name
        success_dir.mkdir(parents=True, exist_ok=True)
        failed_dir.mkdir(parents=True, exist_ok=True)

        passwords = self._load_passwords(password_csv)
        archives = self._collect_archives(archive_folder)

        results: List[ArchiveResult] = []
        success_count = 0

        for archive_path in archives:
            result = self._process_archive(archive_path, passwords, success_dir, failed_dir)
            results.append(result)
            if result.status == "success":
                success_count += 1

        return {
            "archive_folder": str(archive_folder),
            "password_csv": str(password_csv) if password_csv.exists() else "",
            "output_root": str(output_root),
            "success_dir": str(success_dir),
            "failed_dir": str(failed_dir),
            "processed": len(archives),
            "succeeded": success_count,
            "failed": len(archives) - success_count,
            "details": [result.to_dict() for result in results],
        }

    # ------------------------------------------------------------
    # 個別処理
    # ------------------------------------------------------------
    def _collect_archives(self, folder: Path) -> List[Path]:
        archives: List[Path] = []
        if not folder.exists():
            return archives
        for path in sorted(folder.rglob("*")):
            if path.is_file() and self._is_supported_archive(path):
                archives.append(path)
        return archives

    def _load_supported_extensions(self) -> List[str]:
        default_exts = [".zip"]
        extra_exts = self.processing_config.get("archive_extensions", [])
        for ext in extra_exts:
            text = str(ext).strip().lower()
            if not text:
                continue
            if not text.startswith("."):
                text = f".{text}"
            default_exts.append(text)
        # 重複を消し、安定した順番にします。
        seen = set()
        ordered: List[str] = []
        for ext in default_exts:
            if ext not in seen:
                seen.add(ext)
                ordered.append(ext)
        return ordered

    def _load_passwords(self, csv_path: Path) -> List[str]:
        if not csv_path.exists():
            return []
        passwords: List[str] = []
        try_encodings = ["utf-8-sig", "utf-8", "cp932"]
        for encoding in try_encodings:
            try:
                with csv_path.open("r", encoding=encoding, newline="") as file:
                    reader = csv.reader(file)
                    for row_index, row in enumerate(reader):
                        if not row:
                            continue
                        # 先頭行にヘッダーがある想定なので、1行目はスキップします。
                        if row_index == 0 and any(
                            keyword in str(cell).lower()
                            for cell in row
                            for keyword in ("password", "パスワード")
                        ):
                            continue
                        if len(row) < 5:
                            continue
                        password = str(row[4]).strip()
                        if password and password not in passwords:
                            passwords.append(password)
                break
            except UnicodeDecodeError:
                continue
        return passwords

    def _process_archive(
        self,
        archive_path: Path,
        passwords: Sequence[str],
        success_dir: Path,
        failed_dir: Path,
    ) -> ArchiveResult:
        destination = success_dir / archive_path.stem
        self._cleanup_directory(destination)
        destination.mkdir(parents=True, exist_ok=True)

        try:
            success, used_password = self._attempt_extraction(archive_path, destination, passwords)
        except Exception as exc:  # 予期しないエラーも失敗扱いにします。
            self._cleanup_directory(destination)
            moved_path = self._move_to_folder(archive_path, failed_dir)
            return ArchiveResult(
                archive=str(moved_path),
                status="failed",
                message=str(exc),
                extracted_to=None,
                used_password=None,
            )

        if success:
            self._extract_nested_archives(destination, used_password, passwords)
            moved_path = self._move_to_folder(archive_path, success_dir)
            return ArchiveResult(
                archive=str(moved_path),
                status="success",
                message="解凍しました。",
                extracted_to=str(destination),
                used_password=used_password,
            )

        # 失敗したときは、作成途中のフォルダを片付けます。
        self._cleanup_directory(destination)
        moved_path = self._move_to_folder(archive_path, failed_dir)
        return ArchiveResult(
            archive=str(moved_path),
            status="failed",
            message="パスワードが一致せず解凍できませんでした。",
            extracted_to=None,
            used_password=None,
        )

    def _attempt_extraction(
        self, archive_path: Path, destination: Path, passwords: Sequence[str]
    ) -> Tuple[bool, Optional[str]]:
        # まずパスワード無しで挑戦し、その後CSVで読み取った候補を総当たりします。
        password_candidates: List[Optional[str]] = [None]
        for password in passwords:
            if password not in password_candidates:
                password_candidates.append(password)

        for candidate in password_candidates:
            for pwd_bytes in self._iter_password_bytes(candidate):
                try:
                    self._cleanup_directory(destination)
                    destination.mkdir(parents=True, exist_ok=True)
                    self._extract_zip_archive(archive_path, destination, pwd_bytes)
                    return True, candidate
                except RuntimeError:
                    # パスワード不一致の場合はRuntimeErrorが送出されます。次の候補へ。
                    continue
                except zipfile.BadZipFile:
                    raise
                except Exception:
                    # それ以外の例外は失敗として扱い続行します。
                    continue
        return False, None

    def _extract_zip_archive(
        self, archive_path: Path, destination: Path, password: Optional[bytes]
    ) -> None:
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                target_name = self._decode_zip_name(info)
                target_path = destination / target_name
                if info.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                data = zf.read(info, pwd=password)
                target_path.write_bytes(data)

    def _decode_zip_name(self, info: zipfile.ZipInfo) -> str:
        # UTF-8フラグが立っていない場合、CP932で解釈すると日本語ファイル名の文字化けを防げます。
        name = info.filename
        if info.flag_bits & 0x800:
            return name
        try:
            return name.encode("cp437").decode("cp932")
        except UnicodeError:
            return name

    def _iter_password_bytes(self, password: Optional[str]) -> Iterable[Optional[bytes]]:
        if password is None:
            yield None
            return
        tried = set()
        for encoding in ("utf-8", "cp932"):
            try:
                encoded = password.encode(encoding)
            except UnicodeEncodeError:
                continue
            if encoded not in tried:
                tried.add(encoded)
                yield encoded

    def _extract_nested_archives(
        self, folder: Path, used_password: Optional[str], passwords: Sequence[str]
    ) -> None:
        priority_passwords: List[str] = []
        if used_password:
            priority_passwords.append(used_password)
        for password in passwords:
            if password not in priority_passwords:
                priority_passwords.append(password)

        queue = [path for path in folder.rglob("*") if path.is_file() and self._is_supported_archive(path)]
        processed = set()
        while queue:
            archive_path = queue.pop(0)
            if archive_path in processed:
                continue
            processed.add(archive_path)
            nested_destination = archive_path.parent / archive_path.stem
            self._cleanup_directory(nested_destination)
            nested_destination.mkdir(parents=True, exist_ok=True)
            success, nested_password = self._attempt_extraction(
                archive_path, nested_destination, priority_passwords
            )
            if success:
                archive_path.unlink(missing_ok=True)
                if nested_password and nested_password not in priority_passwords:
                    priority_passwords.insert(0, nested_password)
                queue.extend(
                    path
                    for path in nested_destination.rglob("*")
                    if path.is_file() and self._is_supported_archive(path)
                )
            else:
                # 展開できなければ作りかけのフォルダを削除して次へ。
                self._cleanup_directory(nested_destination)

    def _is_supported_archive(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        return suffix in self.supported_extensions

    def _cleanup_directory(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def _move_to_folder(self, source: Path, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        counter = 1
        while target.exists():
            target = folder / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        return Path(shutil.move(str(source), target))


def run_unzip_processing(
    archive_folder: str,
    password_csv: str,
    output_root: str,
    config: Dict[str, object],
) -> Dict[str, object]:
    extractor = ZipExtractor(config)
    return extractor.run(Path(archive_folder), Path(password_csv), Path(output_root))
