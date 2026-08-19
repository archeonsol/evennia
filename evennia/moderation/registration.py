"""
Registration signals: what an address at signup is worth knowing.

Three questions, all about the email, all answered after the account exists:

**Is it a second name for an address already here?** ``player+2@gmail.com`` and
``pl.ayer@gmail.com`` deliver to the same inbox as ``player@gmail.com``. The
provider's own aliasing rules are public, so collapsing them is arithmetic, not
inference.

**Is the domain disposable?** A denylist, refreshed by the same scheduled job
that maintains the network lists.

**Does the domain accept mail at all?** A domain with no MX cannot receive the
verification message, which usually means the address was typed to get past a
form rather than to be read.

None of this blocks a signup. Every answer becomes a ``ModerationFlag`` and
nothing else: an account created here is created, and a person decides whether
it is worth anything further. The screening also runs off the request path
entirely, because a DNS lookup on the signup path makes registration as
reliable as somebody else's resolver.
"""

from __future__ import annotations

from evennia.moderation.flags import make_dedupe_key, raise_flag
from evennia.server.models import ModerationFlag

# Providers that ignore dots in the local part. Everyone honours "+tag", so that
# is stripped everywhere; dot-collapsing is provider-specific and applied only
# where it is documented behaviour.
DOTLESS_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
    }
)

SEVERITY = {
    ModerationFlag.KIND_EMAIL_ALIAS: 2,
    ModerationFlag.KIND_DISPOSABLE_EMAIL: 2,
    ModerationFlag.KIND_UNDELIVERABLE_EMAIL: 1,
}


def email_domain(email) -> str:
    """The domain part of an address, lowercased, or empty."""
    text = str(email or "").strip().lower()
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[-1]


def normalize_email(email) -> str:
    """
    One address in the form that identifies the inbox behind it.

    Lowercased, ``+tag`` removed, and dots removed from the local part on the
    providers that ignore them. Two addresses with the same normalized form
    reach the same person; that is the only claim made here.
    """
    text = str(email or "").strip().lower()
    if "@" not in text:
        return ""
    local, _, domain = text.rpartition("@")
    if not local or not domain:
        return ""
    local = local.split("+", 1)[0]
    if domain in DOTLESS_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else ""


def disposable_domains() -> frozenset:
    """The stored disposable-domain denylist."""
    from evennia.moderation.enrich import disposable_domain_set

    return disposable_domain_set()


def has_mx(domain: str):
    """
    Whether a domain publishes mail servers.

    ``True``/``False`` when the question was answered, ``None`` when it was not
    -- no resolver installed, or the lookup failed. ``None`` never becomes a
    flag: "we could not check" is not evidence of anything.
    """
    domain = str(domain or "").strip().lower()
    if not domain:
        return None
    try:
        import dns.resolver
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX")
    except Exception as err:
        # NXDOMAIN and NoAnswer are answers: the domain takes no mail. Any other
        # failure (timeout, no nameserver) is not.
        name = type(err).__name__
        if name in {"NXDOMAIN", "NoAnswer"}:
            return False
        return None
    return bool(list(answers))


def _other_accounts_with(normalized: str, *, exclude_id) -> list:
    """Account names whose address normalizes to the same inbox."""
    from evennia.accounts.models import AccountDB

    if not normalized:
        return []
    domain = normalized.rsplit("@", 1)[-1]
    names = []
    rows = AccountDB.objects.filter(email__iendswith=f"@{domain}").values_list(
        "id", "username", "email"
    )
    for account_id, username, email in rows[:500]:
        if account_id == exclude_id:
            continue
        if normalize_email(email) == normalized:
            names.append(str(username))
    return sorted(set(names))


def screen_email(*, account_id, account_name: str, email: str) -> list:
    """
    Every email signal for one new account. Returns the flags raised.

    Runs in a worker thread from a scheduled job, never on the signup request.
    """
    raised = []
    normalized = normalize_email(email)
    domain = email_domain(email)
    if not domain:
        return raised

    others = _other_accounts_with(normalized, exclude_id=account_id)
    if others:
        kind = ModerationFlag.KIND_EMAIL_ALIAS
        raised.append(
            raise_flag(
                kind=kind,
                dedupe_key=make_dedupe_key(kind, normalized),
                severity=SEVERITY[kind],
                account_id=account_id,
                account_name=account_name,
                summary=(f"Same inbox as {', '.join(others[:6])} after alias normalization"),
                evidence={
                    "signal": kind,
                    "accounts": sorted({account_name, *others}),
                    "normalized_email": normalized,
                },
            )
        )

    if domain in disposable_domains():
        kind = ModerationFlag.KIND_DISPOSABLE_EMAIL
        raised.append(
            raise_flag(
                kind=kind,
                dedupe_key=make_dedupe_key(kind, account_name, domain),
                severity=SEVERITY[kind],
                account_id=account_id,
                account_name=account_name,
                summary=f"Registered with a disposable email domain: {domain}",
                evidence={"signal": kind, "domain": domain},
            )
        )

    deliverable = has_mx(domain)
    if deliverable is False:
        kind = ModerationFlag.KIND_UNDELIVERABLE_EMAIL
        raised.append(
            raise_flag(
                kind=kind,
                dedupe_key=make_dedupe_key(kind, account_name, domain),
                severity=SEVERITY[kind],
                account_id=account_id,
                account_name=account_name,
                summary=f"{domain} has no MX records. Verification email cannot be delivered.",
                evidence={"signal": kind, "domain": domain},
            )
        )

    return [flag for flag in raised if flag is not None]
