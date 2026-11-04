"""Outbound email through SES. Every mail is Hebrew and carries its body as plain text beside
right-to-left HTML, since a text-only mail leaves direction to the reader's client, which strands a
trailing colon or a number on the wrong edge. Mailer sends raw MIME because SES's simple send has
no attachments, base64 encoded because SES takes raw messages only as 7-bit; recipients come from
the To header. It raises on failure and logs a receipt on success."""

import html
from email import policy
from email.message import EmailMessage

from common import ses_identity
from common.log import get_logger

logger = get_logger(__name__)


def rtl_html(body: str) -> str:
    """One message body as the right-to-left HTML its email carries."""
    lines = "<br>".join(html.escape(line) for line in body.split("\n"))
    return f'<div dir="rtl" style="font-family: Arial, sans-serif; font-size: 15px">{lines}</div>'


class Mailer:
    """Sends one Hebrew message with a PDF attached from the deployment's verified sender, and
    answers whether a recipient is a verified identity."""

    def __init__(self, ses, sender: str):
        self._ses = ses
        self._sender = sender

    def verified(self, address) -> bool:
        """Whether the address is a verified SES identity, which the SES sandbox requires of
        every recipient."""
        return ses_identity.verification_status(self._ses, address) == ses_identity.VERIFIED

    def send(self, recipient, subject, body, pdf: bytes, filename):
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body, cte="base64")
        message.add_alternative(rtl_html(body), subtype="html", cte="base64")
        message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
        raw = message.as_bytes(policy=policy.SMTP)
        self._ses.send_raw_email(Source=self._sender, RawMessage={"Data": raw})
        logger.info("email sent to=%s subject=%s", recipient, subject)
