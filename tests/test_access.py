"""Resolving a signed-in address to its account and role, including a first sign-in that races
another request for the same address."""

from common.access import User, resolve


class LostFirstRead:
    """Hides the first access read, as a concurrent first sign-in does, before the row is
    written."""

    def __init__(self, store):
        self._store = store
        self._hidden = False

    def access_get(self, email):
        if not self._hidden:
            self._hidden = True
            return None
        return self._store.access_get(email)

    def access_put_if_absent(self, email, account, role):
        return self._store.access_put_if_absent(email, account, role)


def test_new_address_becomes_owner(store):
    user = resolve(store, "owner@x.com")
    assert user == User(email="owner@x.com", account="owner@x.com", role="owner")
    assert store.access_get("owner@x.com") == {
        "email": "owner@x.com",
        "account": "owner@x.com",
        "role": "owner",
    }


def test_delegate_resolves_to_owner_account(store):
    store.access_put_if_absent("delegate@x.com", "owner@x.com", "delegate")
    user = resolve(store, "delegate@x.com")
    assert user.account == "owner@x.com"
    assert user.role == "delegate"


def test_email_is_lowercased(store):
    user = resolve(store, "Owner@X.com")
    assert user.email == "owner@x.com"
    assert user.account == "owner@x.com"
    assert store.access_get("Owner@X.com") is None


def test_mixed_case_delegate_resolves_to_existing_row(store):
    store.access_put_if_absent("delegate@x.com", "owner@x.com", "delegate")
    user = resolve(store, "Delegate@X.com")
    assert user == User(email="delegate@x.com", account="owner@x.com", role="delegate")


def test_lost_put_rereads_the_row_written_concurrently(store):
    store.access_put_if_absent("delegate@x.com", "owner@x.com", "delegate")
    user = resolve(LostFirstRead(store), "delegate@x.com")
    assert user == User(email="delegate@x.com", account="owner@x.com", role="delegate")
