"""Отправка уведомлений по SMTP."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from helpdesk.notifications.base import Notification


class EmailNotifier:
    def __init__(self, host: str, port: int, sender: str) -> None:
        self.host, self.port, self.sender = host, port, sender

    def send(self, notification: Notification) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, notification.to, notification.subject
        msg.set_content(notification.body)
        with smtplib.SMTP(self.host, self.port) as smtp:
            smtp.send_message(msg)
