"""メール処理のメインロジックをまとめたモジュールです。"""

from __future__ import annotations

import csv
import string
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


class EmailProcessor:
    """メールを読み取って添付ファイルとCSVを作成するクラスです。"""

    def __init__(self, config: Dict[str, object]):
        self.config = config
        self.processing_config = config.get("email_processing", {})

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

            mail_infos.append(mail_data)

        self._write_csv(csv_path, mail_infos)

        return {
            "mail_count": len(mail_infos),
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
        recipients = ", ".join(message.recipients) if message.recipients else ""
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
        half_symbols: str = self.processing_config.get("password_symbols_half", "")
        full_symbols: str = self.processing_config.get("password_symbols_full", "")
        allowed_chars = set(string.ascii_letters + string.digits + half_symbols + full_symbols)

        body_lower = body.lower()
        candidates: List[str] = []

        for keyword in keywords:
            keyword_lower = str(keyword).lower()
            start = 0
            while True:
                index = body_lower.find(keyword_lower, start)
                if index == -1:
                    break
                start = index + len(keyword_lower)

                # キーワードの後ろの30文字を確認します。
                snippet_after = body[start : start + 40]
                extracted_after = self._collect_allowed(snippet_after, allowed_chars)
                if extracted_after:
                    candidates.append(extracted_after)

                # キーワードの前の30文字も確認してみます。
                snippet_before = body[max(0, index - 40) : index]
                extracted_before = self._collect_allowed(snippet_before[::-1], allowed_chars)
                if extracted_before:
                    candidates.append(extracted_before[::-1])

        # 最初に見つかった候補を返します。見つからなければ空文字です。
        return candidates[0] if candidates else ""

    def _collect_allowed(self, text: str, allowed_chars: set[str]) -> str:
        """指定された文字集合に含まれる文字だけを順番に取り出します。"""
        filtered = "".join(ch for ch in text if ch in allowed_chars)
        return filtered.strip()

    def _write_csv(self, csv_path: Path, mail_infos: List[MailInfo]) -> None:
        """メール情報をCSVに書き出します。"""
        headers = self.processing_config.get("csv_headers", [])
        with csv_path.open("w", encoding="utf-8", newline="") as file:
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
