from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ManagedRecipient(BaseModel):
    email: str
    name: str | None = None
    active: bool = True
    notes: str | None = None


class RecipientSet(BaseModel):
    name: str
    description: str | None = None
    recipients: list[ManagedRecipient] = Field(default_factory=list)


class RecipientConfig(BaseModel):
    version: int = 1
    recipient_sets: dict[str, RecipientSet] = Field(default_factory=dict)


def load_recipient_config(path: Path) -> RecipientConfig:
    if not path.exists():
        return RecipientConfig()
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Recipient config must be a mapping: {path}")
    config = RecipientConfig.model_validate(payload)
    _validate_recipient_config(config)
    return config


def active_recipient_emails(path: Path, set_id: str, only: list[str] | None = None) -> list[str]:
    config = load_recipient_config(path)
    recipient_set = config.recipient_sets.get(set_id)
    if recipient_set is None:
        available = ", ".join(sorted(config.recipient_sets)) or "none"
        raise KeyError(f"Recipient set not found: {set_id}; available: {available}")

    only_normalized = {email.strip().lower() for email in only or [] if email.strip()}
    emails: list[str] = []
    for recipient in recipient_set.recipients:
        email = recipient.email.strip()
        if only_normalized:
            if email.lower() not in only_normalized:
                continue
        elif not recipient.active:
            continue
        emails.append(email)
    return _dedupe_emails(emails)


def parse_email_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        candidates = value
    else:
        candidates = re.split(r"[,;]", value)
    return _dedupe_emails([candidate.strip() for candidate in candidates if candidate.strip()])


def _validate_recipient_config(config: RecipientConfig) -> None:
    for set_id, recipient_set in config.recipient_sets.items():
        if not recipient_set.recipients:
            raise ValueError(f"Recipient set has no recipients: {set_id}")
        for recipient in recipient_set.recipients:
            if not EMAIL_PATTERN.fullmatch(recipient.email.strip()):
                raise ValueError(f"Invalid recipient email in {set_id}: {recipient.email}")


def _dedupe_emails(emails: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for email in emails:
        normalized = email.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(email)
    return deduped
