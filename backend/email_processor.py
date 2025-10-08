"""メール処理のメインロジックをまとめたモジュールです。"""

from __future__ import annotations

import csv
import string
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# .msgファイルを読むためのライブラリは環境に無い可能性があるので、
# try / except で安全に読み込みます。
try:  # pragma: no cover - 単純な import のためテスト対象外
    import extract_msg  # type: ignore
except ImportError:  # pragma: no cover
    extract_msg = None  # type: ignore


@dataclass
class MailInfo:
    """1通のメールから取り出したい情報をまとめるシンプルなデータクラスです。"""

    received: str
    sender: str
    recipients: str
    body: str
    password_hint: str


def _to_email_str_list(values):
    """extract_msgの Recipient オブジェクトを安全に文字列へ変換します。"""

    if not values:
        return []

    output: List[str] = []
    for value in values:
        if isinstance(value, str):
            output.append(value)
        elif hasattr(value, "email"):
            output.append(getattr(value, "email"))
        elif hasattr(value, "address"):
            output.append(getattr(value, "address"))
        else:
            output.append(str(value))
    return output


class EmailProcessor:
    """メールを読み取って添付ファイルとCSVを作成するクラスです。"""

    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.processing_config = config.get("email_processing", {})
        half_symbols = str(self.processing_config.get("password_symbols_half", ""))
        full_symbols = str(self.processing_config.get("password_symbols_full", ""))
        # 半角英数字と記号を一通り許可し、JSONで設定できる記号も加えます。
        base_ascii = set(string.ascii_letters + string.digits + string.punctuation)
        base_ascii.update(half_symbols)
        self._direct_allowed_chars = base_ascii.union(full_symbols)
        self._normalized_allowed_chars = set()
        for char in self._direct_allowed_chars:
            normalized = unicodedata.normalize("NFKC", char)
            self._normalized_allowed_chars.update(normalized)
        self._separator_chars = {
            " ",
            "\t",
            "\r",
            "\n",
            "\u3000",
            ":",
            "：",
            "=",
            "＝",
            "[",
            "]",
            "［",
            "］",
            "（",
            "）",
            "<",
            ">",
            "＜",
            "＞",
            "《",
            "》",
            "«",
            "»",
            "「",
            "」",
            "『",
            "』",
            "【",
            "】",
            "〔",
            "〕",
            "〈",
            "〉",
            "‹",
            "›",
            "{",
            "}",
            "｛",
            "｝",
        }
        ignored_words = {
            "for",
            "is",
            "below",
            "below.",
            "below:",
            "below,",
            "below;",
            "here",
            "please",
            "this",
            "that",
            "password",
            "you",
            "information",
            "info",
            "detail",
            "details",
            "案内",
            "情報",
        }
        self._ignored_candidates = {word.lower() for word in ignored_words}
        self._min_password_length = int(self.processing_config.get("password_min_length", 3))
        self._require_digit_or_symbol = bool(
            self.processing_config.get("password_require_digit_or_symbol", True)
        )


    # ------------------------------------------------------------
    # 公開メソッド
    # ------------------------------------------------------------
    def run(self, mail_folder: Path, download_folder: Path, csv_path: Path) -> Dict[str, object]:
        """指定フォルダを読み取り、添付ダウンロードとCSV作成を行います。"""
        mail_folder = mail_folder.expanduser().resolve()
        download_folder = download_folder.expanduser().resolve()
        csv_path = csv_path.expanduser().resolve()
        download_folder.mkdir(parents=True, exist_ok=True)
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        mail_files = self._collect_mail_files(mail_folder)
        mail_infos: List[MailInfo] = []
        attachment_counter = 0

        for mail_file in mail_files:
            if mail_file.suffix.lower() == ".eml":
                mail_data, attachments = self._parse_eml(mail_file)
            else:
                mail_data, attachments = self._parse_msg(mail_file)

            # 添付ファイルを保存するときに 001_ のような番号を付けます。
            for attachment in attachments:
                attachment_counter += 1
                padded_number = str(attachment_counter).zfill(
                    int(self.processing_config.get("number_padding", 3))
                )
                new_name = f"{padded_number}_{attachment['name']}"
                target_path = download_folder / new_name
                target_path.write_bytes(attachment["content"])

            if not attachments:
                # 添付が無いメールだけCSVに書き出します。パスワードの案内メールを想定しています。
                mail_infos.append(mail_data)

        self._write_csv(csv_path, mail_infos)

        return {
            "mail_count": len(mail_files),
            "password_mail_count": len(mail_infos),
            "attachment_count": attachment_counter,
            "csv_path": str(csv_path),
            "download_folder": str(download_folder),
        }

    # ------------------------------------------------------------
    # 内部処理
    # ------------------------------------------------------------
    def _collect_mail_files(self, folder: Path) -> List[Path]:
        """フォルダから .eml と .msg ファイルを探します。"""
        files = sorted(folder.glob("*.eml")) + sorted(folder.glob("*.msg"))
        return files

    def _parse_eml(self, mail_file: Path) -> tuple[MailInfo, List[Dict[str, object]]]:
        """.eml ファイルを解析して本文と添付を取り出します。"""
        with mail_file.open("rb") as file:
            message: EmailMessage = BytesParser(policy=policy.default).parse(file)

        received_date = self._format_date(message.get("date"))
        sender = message.get("from", "")
        recipients = message.get("to", "")
        body = self._get_body_text(message)
        password_hint = self._extract_password(body)

        attachments = []
        for part in message.iter_attachments():
            filename = part.get_filename() or "attachment"
            content = part.get_payload(decode=True) or b""
            attachments.append({"name": filename, "content": content})

        return MailInfo(received_date, sender, recipients, body, password_hint), attachments

    def _parse_msg(self, mail_file: Path) -> tuple[MailInfo, List[Dict[str, object]]]:
        """.msg ファイルを解析します。extract_msg が無い場合は空の結果にします。"""
        if extract_msg is None:
            # ライブラリが無いときは簡単な情報だけ返します。
            return MailInfo("", "", "", "ライブラリが必要です。", ""), []

        message = extract_msg.Message(str(mail_file))
        received_date = self._format_date(str(message.date))
        sender = message.sender or ""
        recipients = ", ".join(_to_email_str_list(message.recipients)) if message.recipients else ""
        body = message.body or ""
        password_hint = self._extract_password(body)

        attachments = []
        for attachment in message.attachments:
            filename = attachment.longFilename or attachment.shortFilename or "attachment"
            content = attachment.data
            attachments.append({"name": filename, "content": content})

        return MailInfo(received_date, sender, recipients, body, password_hint), attachments

    def _format_date(self, date_value: Optional[str]) -> str:
        """日時文字列を読みやすい形式に整えます。"""
        if not date_value:
            return ""
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(date_value, fmt)
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        return date_value

    def _get_body_text(self, message: EmailMessage) -> str:
        """メール本文(テキスト部分)だけを取り出します。"""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    charset = part.get_content_charset() or "utf-8"
                    return part.get_content().strip()
        charset = message.get_content_charset() or "utf-8"
        return message.get_content().strip()

    def _extract_password(self, body: str) -> str:
        """本文からパスワードらしき文字列を探します。"""
        if not body:
            return ""

        keywords: Iterable[str] = self.processing_config.get("password_keywords", [])
        keyword_items: List[tuple[str, str]] = []
        for keyword in keywords:
            keyword_norm = unicodedata.normalize("NFKC", str(keyword)).lower()
            if keyword_norm:
                keyword_items.append((str(keyword), keyword_norm))
        # 長いキーワードを優先して検索すると、「password is」のようなケースで精度が上がります。
        keyword_items.sort(key=lambda item: len(item[1]), reverse=True)
        normalized_body, index_map = self._normalize_with_index(body)
        lowered = normalized_body.lower()

        for _, keyword_norm in keyword_items:
            start = 0
            while True:
                index = lowered.find(keyword_norm, start)
                if index == -1:
                    break
                cursor = index + len(keyword_norm)
                cursor = self._skip_separators(normalized_body, cursor)
                candidate = self._collect_password_candidate(
                    body, normalized_body, index_map, cursor
                )
                if candidate:
                    return candidate
                start = index + len(keyword_norm)

        return ""

    def _collect_password_candidate(
        self, original: str, normalized: str, index_map: List[int], start: int
    ) -> str:
        """正規化済み文字列からパスワード候補を切り出します。"""

        position = start
        skipped = 0
        max_skip = 80
        while position < len(normalized):
            orig_index = index_map[position]
            char = original[orig_index]
            if self._is_allowed_password_char(char):
                break
            position += 1
            skipped += 1
            if skipped > max_skip:
                return ""

        start = position
        end = start
        while end < len(normalized):
            orig_index = index_map[end]
            char = original[orig_index]
            if not self._is_allowed_password_char(char):
                break
            end += 1

        if end == start:
            return ""

        candidate_chars: List[str] = []
        last_index = -1
        for position in range(start, end):
            orig_index = index_map[position]
            if orig_index == last_index:
                continue
            candidate_chars.append(original[orig_index])
            last_index = orig_index

        candidate = "".join(candidate_chars).strip()
        candidate_norm = unicodedata.normalize("NFKC", candidate)
        if len(candidate_norm) < self._min_password_length:
            return ""
        if candidate_norm.lower() in self._ignored_candidates:
            return ""
        if candidate_norm.isalpha() and len(candidate_norm) < 6:
            return ""
        if self._require_digit_or_symbol:
            has_digit = any(ch.isdigit() for ch in candidate_norm)
            has_symbol = any(not ch.isalnum() for ch in candidate_norm)
            has_mixed_case = any(ch.islower() for ch in candidate_norm) and any(
                ch.isupper() for ch in candidate_norm
            )
            if not (has_digit or has_symbol or has_mixed_case):
                return ""
        return candidate

    def _is_allowed_password_char(self, char: str) -> bool:
        """パスワードに含めたい文字かどうかを判定します。"""

        if not char:
            return False
        if char in self._direct_allowed_chars:
            return True
        normalized = unicodedata.normalize("NFKC", char)
        if not normalized:
            return False
        return all(ch in self._normalized_allowed_chars for ch in normalized)

    def _skip_separators(self, normalized: str, index: int) -> int:
        """コロンや改行などの区切り文字を飛ばして、パスワード開始位置を探します。"""

        while index < len(normalized):
            char = normalized[index]
            if char in self._separator_chars:
                index += 1
                continue
            break
        return index

    def _normalize_with_index(self, text: str) -> tuple[str, List[int]]:
        """NFKC正規化した文字列と、元の文字インデックスの対応表を作ります。"""

        normalized_chars: List[str] = []
        index_map: List[int] = []
        combining_marks = {"゙", "゚"}
        for index, char in enumerate(text):
            normalized = unicodedata.normalize("NFKC", char)
            if not normalized:
                continue
            for norm_char in normalized:
                if norm_char in combining_marks and normalized_chars:
                    # 直前の文字と結合して濁点・半濁点を表現します。
                    previous_char = normalized_chars.pop()
                    previous_index = index_map.pop()
                    combined = unicodedata.normalize("NFKC", previous_char + norm_char)
                    for combined_char in combined:
                        normalized_chars.append(combined_char)
                        index_map.append(previous_index)
                    continue
                normalized_chars.append(norm_char)
                index_map.append(index)
        return "".join(normalized_chars), index_map

    def _write_csv(self, csv_path: Path, mail_infos: List[MailInfo]) -> None:
        """メール情報をCSVに書き出します。"""
        headers = self.processing_config.get("csv_headers", [])
        with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            if headers:
                writer.writerow(headers)
            for info in mail_infos:
                writer.writerow(
                    [info.received, info.sender, info.recipients, info.body, info.password_hint]
                )


# 外部から簡単に使える関数を用意しておくと分かりやすくなります。
def run_email_processing(
    mail_folder: str,
    download_folder: str,
    csv_output: str,
    config: Dict[str, object],
) -> Dict[str, object]:
    """EmailProcessor を生成して処理を実行するラッパー関数です。"""
    processor = EmailProcessor(config)
    return processor.run(Path(mail_folder), Path(download_folder), Path(csv_output))
