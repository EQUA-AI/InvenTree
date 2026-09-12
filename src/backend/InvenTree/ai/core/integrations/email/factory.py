"""Explicit mailbox adapter construction without notification fallbacks."""

from importlib import import_module

from .contracts import AccountConfig, MailboxError, MailboxProvider


def get_provider(config: AccountConfig, *, allow_recording: bool = False) -> MailboxProvider:
    """Load only the selected adapter; callers resolve secrets and authority."""
    if not config.account_id or not config.address:
        raise MailboxError("account_required")
    modules = {
        "smtp_imap": ("smtp_imap", "SMTPIMAPProvider"),
        "graph": ("graph", "GraphProvider"),
        "google": ("google", "GoogleProvider"),
    }
    if config.provider == "recording" and allow_recording:
        modules["recording"] = ("recording", "RecordingProvider")
    if config.provider not in modules:
        raise MailboxError("unsupported_provider")
    module, name = modules[config.provider]
    return getattr(import_module(f"{__package__}.{module}"), name)(config)
