from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


class EmailConfigurationError(RuntimeError):
    pass


def configured_recipients() -> list[str]:
    return [item.strip() for item in os.getenv("SPOA_RECIPIENTS", "").split(",") if item.strip()]


def send_report(subject: str, html: str, pdf_path: Path, recipients: Iterable[str]) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or user
    targets = list(recipients)
    if not host or not user or not password or not sender or not targets:
        raise EmailConfigurationError("Configuración SMTP o destinatarios incompletos")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(targets)
    message.set_content("Boletín Fiscalía SPOA V3 adjunto. Use un cliente compatible con HTML.")
    message.add_alternative(html, subtype="html")
    message.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)

