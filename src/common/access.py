"""Maps a signed-in Google address to the account it works in.

An address with no access row becomes an owner of its own account, keyed by its lowercase
address. A delegate row names the owner account the address works in.
"""

from dataclasses import dataclass

OWNER = "owner"


@dataclass
class User:
    email: str
    account: str
    role: str


def _user(row) -> User:
    return User(email=row["email"], account=row["account"], role=row["role"])


def resolve(store, email: str) -> User:
    address = email.lower()
    row = store.access_get(address)
    if row is not None:
        return _user(row)
    if store.access_put_if_absent(address, address, OWNER):
        return User(email=address, account=address, role=OWNER)
    return _user(store.access_get(address))
