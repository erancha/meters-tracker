"""DynamoDB access across the nine app tables: access, properties, apartments, meters, periods,
readings, tariffs, pending and messages. Each table holds one kind of record as plain attributes
beside the pk/sk keys (access has no sk). Numbers cross the boundary as float/int in app code
and as Decimal inside DynamoDB; the conversion lives here and nowhere else.

Core pieces:
- Store.from_env: binds each table from its <NAME>_TABLE environment variable
- _put / _update / _get / _query: shared write and read paths, stripping keys from returned
  records; every read is strongly consistent, because a flow reads back within one turn what it
  has just written
- one method family per table, constructing the pk/sk each record is stored under
- row_key: the pk/sk a record is stored under, from its table, account and scope (the apartment
  for a period, the meter for a reading); the per-record methods and apply share it
- apply: a set of puts and deletes across tables in one all-or-nothing transaction, refused with
  TooLarge before any write when it exceeds MAX_TRANSACTION_ITEMS; access rows are written under
  conditions, and AccessChanged says one failed
- failed_photo_readings: the one read that spans accounts, a filtered scan of the
  readings table
- photo_messages: the messages holding a photo's reading tag, across every day
- delegates: an account's delegate rows, a filtered scan of the access table, which has the
  address alone as its key
"""

import os
import secrets
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

from common.access import DELEGATE, OWNER
from common.paging import query_all, scan_all

TABLE_NAMES = (
    "access",
    "properties",
    "apartments",
    "meters",
    "periods",
    "readings",
    "tariffs",
    "pending",
    "messages",
)


def _to_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(item) for item in value]
    return value


def _from_dynamo(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


def _record(item) -> dict:
    return _from_dynamo({key: value for key, value in item.items() if key not in ("pk", "sk")})


def _scoped(account, scope) -> str:
    return f"{account}#{scope}"


def reading_key(date, seq) -> str:
    return f"{date}#{seq}"


# Per table, the pk and sk a record is stored under, given its account and its scope: the
# apartment id for a period, the meter serial for a reading, the user's address for a pending
# item, None elsewhere.
_ROW_KEYS = {
    "access": lambda account, scope, record: (record["email"], None),
    "properties": lambda account, scope, record: (account, record["id"]),
    "apartments": lambda account, scope, record: (account, record["id"]),
    "meters": lambda account, scope, record: (account, record["serial"]),
    "periods": lambda account, scope, record: (_scoped(account, scope), record["start"]),
    "readings": lambda account, scope, record: (
        _scoped(account, scope),
        reading_key(record["date"], record["seq"]),
    ),
    "tariffs": lambda account, scope, record: (account, record["effective_date"]),
    "pending": lambda account, scope, record: (scope, record["seq"]),
}

# DynamoDB refuses a transaction of more items.
MAX_TRANSACTION_ITEMS = 100


class TooLarge(Exception):
    """A set of writes exceeds what one transaction holds."""


class AccessChanged(Exception):
    """An access row the writes rest on changed since they were planned; nothing was written."""


def row_key(table, account, scope, record) -> tuple:
    """The (pk, sk) the record is stored under in the table."""
    return _ROW_KEYS[table](account, scope, record)


def _key(table, account, scope, **identity) -> dict:
    """The DynamoDB Key of the record named by the fields its key is built from."""
    pk, sk = row_key(table, account, scope, identity)
    return {"pk": pk, "sk": sk}


class Store:
    """One handle per app table; each method builds the pk/sk its record lives under and hands
    back plain dicts without those keys."""

    def __init__(self, tables: dict, dynamodb):
        self._tables = {name: dynamodb.Table(tables[name]) for name in TABLE_NAMES}
        self._client = dynamodb.meta.client

    @classmethod
    def from_env(cls):
        return cls(
            {name: os.environ[f"{name.upper()}_TABLE"] for name in TABLE_NAMES},
            boto3.resource("dynamodb"),
        )

    def _access_put(self, record) -> dict:
        """An address takes a delegate row only while it has no row or an owner's, which a delegate
        row replaces; one holding a delegate row for anyone stops the transaction."""
        return {
            "TableName": self._tables["access"].name,
            "Item": {"pk": record["email"], **record},
            "ConditionExpression": "attribute_not_exists(pk) OR #role = :owner",
            "ExpressionAttributeNames": {"#role": "role"},
            "ExpressionAttributeValues": {":owner": OWNER},
        }

    def _access_delete(self, account_id, record) -> dict:
        """A delegate row goes only while it still names this account."""
        return {
            "TableName": self._tables["access"].name,
            "Key": {"pk": record["email"]},
            "ConditionExpression": "#account = :account",
            "ExpressionAttributeNames": {"#account": "account"},
            "ExpressionAttributeValues": {":account": account_id},
        }

    def _put_request(self, table, account_id, scope, record) -> dict:
        if table == "access":
            return self._access_put(record)
        pk, sk = row_key(table, account_id, scope, record)
        return {
            "TableName": self._tables[table].name,
            "Item": {"pk": pk, "sk": sk, **_to_dynamo(record)},
        }

    def _delete_request(self, table, account_id, scope, record) -> dict:
        if table == "access":
            return self._access_delete(account_id, record)
        pk, sk = row_key(table, account_id, scope, record)
        return {"TableName": self._tables[table].name, "Key": {"pk": pk, "sk": sk}}

    def apply(self, account_id, writes):
        """Writes the puts and deletes of an account's writes in one transaction, all or none.
        Raises TooLarge, before any write, when they exceed MAX_TRANSACTION_ITEMS, and
        AccessChanged when a condition on an access row fails, the only conditions it holds."""
        items = [
            {"Put": self._put_request(table, account_id, scope, record)}
            for table, rows in writes.puts.items()
            for scope, record in rows
        ]
        items.extend(
            {"Delete": self._delete_request(table, account_id, scope, record)}
            for table, rows in writes.deletes.items()
            for scope, record in rows
        )
        if not items:
            return
        if len(items) > MAX_TRANSACTION_ITEMS:
            raise TooLarge(f"{len(items)} items exceed the {MAX_TRANSACTION_ITEMS} one holds")
        try:
            # The resource's client serializes plain Python values, as the table calls do.
            self._client.transact_write_items(TransactItems=items)
        except self._client.exceptions.TransactionCanceledException as error:
            reasons = error.response["CancellationReasons"]
            if any(reason["Code"] == "ConditionalCheckFailed" for reason in reasons):
                raise AccessChanged from error
            raise

    def _put(self, table, pk, sk, record):
        self._tables[table].put_item(Item={"pk": pk, "sk": sk, **_to_dynamo(record)})

    def _put_row(self, table, account, scope, record):
        self._put(table, *row_key(table, account, scope, record), record)

    def _update_row(self, table, account, scope, fields, remove=(), **identity):
        """Updates the existing record named by the fields its key is built from."""
        key = _key(table, account, scope, **identity)
        self._update(table, key["pk"], key["sk"], fields, remove)

    def _update(self, table, pk, sk, fields, remove=()):
        names = {f"#f{i}": name for i, name in enumerate(fields)}
        names.update({f"#r{i}": name for i, name in enumerate(remove)})
        values = {f":v{i}": _to_dynamo(value) for i, value in enumerate(fields.values())}
        expression = "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields)))
        if remove:
            expression += " REMOVE " + ", ".join(f"#r{i}" for i in range(len(remove)))
        self._tables[table].update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression=expression,
            ConditionExpression="attribute_exists(pk)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def _get(self, table, key):
        item = self._tables[table].get_item(Key=key, ConsistentRead=True).get("Item")
        return None if item is None else _record(item)

    @staticmethod
    def _by_pk(pk) -> dict:
        return {"KeyConditionExpression": Key("pk").eq(pk), "ConsistentRead": True}

    def _query(self, table, pk) -> list:
        return [_record(item) for item in query_all(self._tables[table], **self._by_pk(pk))]

    def access_get(self, email):
        return self._get("access", {"pk": email})

    def _if_condition_holds(self, table, write, **request) -> bool:
        """Runs a conditional write; False when its condition did not hold."""
        try:
            getattr(self._tables[table], write)(**request)
        except self._tables[table].meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def access_put_if_absent(self, email, account, role) -> bool:
        return self._if_condition_holds(
            "access",
            "put_item",
            Item={"pk": email, "email": email, "account": account, "role": role},
            ConditionExpression="attribute_not_exists(pk)",
        )

    def delegates(self, account) -> list:
        """The account's delegate rows, by address."""
        items = scan_all(
            self._tables["access"],
            FilterExpression=Attr("account").eq(account) & Attr("role").eq(DELEGATE),
            ConsistentRead=True,
        )
        return sorted((_record(item) for item in items), key=lambda row: row["email"])

    def properties(self, account) -> list:
        return self._query("properties", account)

    def put_property(self, account, name) -> dict:
        record = {"id": secrets.token_hex(4), "name": name}
        self._put_row("properties", account, None, record)
        return record

    def apartments(self, account) -> list:
        return self._query("apartments", account)

    def put_apartment(self, account, property_id, name, period_start, period_months) -> dict:
        record = {
            "id": secrets.token_hex(4),
            "property_id": property_id,
            "name": name,
            "period_start": period_start,
            "period_months": period_months,
        }
        self._put_row("apartments", account, None, record)
        return record

    def meter(self, account, serial):
        return self._get("meters", _key("meters", account, None, serial=serial))

    def meters(self, account) -> list:
        return self._query("meters", account)

    def put_meter(self, account, meter):
        self._put_row("meters", account, None, meter)

    def update_meter(self, account, serial, **fields):
        self._update_row("meters", account, None, fields, serial=serial)

    def periods(self, account, apartment_id) -> list:
        return self._query("periods", _scoped(account, apartment_id))

    def put_period(self, account, apartment_id, period):
        self._put_row("periods", account, apartment_id, period)

    def update_period(self, account, apartment_id, start, **fields):
        self._update_row("periods", account, apartment_id, fields, start=start)

    def delete_period(self, account, apartment_id, start):
        self._tables["periods"].delete_item(Key=_key("periods", account, apartment_id, start=start))

    def readings(self, account, serial) -> list:
        return self._query("readings", _scoped(account, serial))

    def failed_photo_readings(self) -> list:
        """Every reading, across all accounts, that carries failed photos, with its account and
        meter serial; ordered by account, serial and reading key."""
        items = scan_all(
            self._tables["readings"],
            FilterExpression=Attr("failed_photos").exists(),
            ConsistentRead=True,
        )
        rows = []
        for item in sorted(items, key=lambda item: (item["pk"], item["sk"])):
            account, serial = item["pk"].split("#", 1)
            rows.append({**_record(item), "account": account, "serial": serial})
        return rows

    def put_reading(self, account, serial, reading):
        self._put_row("readings", account, serial, reading)

    def update_reading(self, account, serial, date, seq, remove=(), **fields):
        self._update_row("readings", account, serial, fields, remove, date=date, seq=seq)

    def tariffs(self, account) -> list:
        return self._query("tariffs", account)

    def put_tariff(self, account, tariff):
        self._put_row("tariffs", account, None, tariff)

    def pending(self, user) -> list:
        return self._query("pending", user)

    def put_pending(self, user, item):
        self._put_row("pending", None, user, item)

    def claim_pending(self, user, seq, version, status) -> bool:
        """Advances a pending item's version only while it still has this version and status;
        False when another request changed or removed it first."""
        return self._if_condition_holds(
            "pending",
            "update_item",
            Key={"pk": user, "sk": seq},
            UpdateExpression="SET #version = :next",
            ConditionExpression="#version = :version AND #status = :status",
            ExpressionAttributeNames={"#version": "version", "#status": "status"},
            ExpressionAttributeValues={
                ":version": version,
                ":next": version + 1,
                ":status": status,
            },
        )

    def delete_pending(self, user, seq):
        self._tables["pending"].delete_item(Key={"pk": user, "sk": seq})

    def messages(self, user, day, limit=200) -> list:
        """The latest limit messages whose stamp starts with day, oldest first."""
        page = self._tables["messages"].query(
            KeyConditionExpression=Key("pk").eq(user) & Key("sk").begins_with(day),
            ConsistentRead=True,
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_record(item) for item in reversed(page["Items"])]

    def photo_messages(self, user) -> list:
        """Every message of the user, on any day, that carries a photo's reading tag."""
        items = query_all(
            self._tables["messages"], **self._by_pk(user), FilterExpression=Attr("reading").exists()
        )
        return [_record(item) for item in items]

    def has_messages(self, user) -> bool:
        page = self._tables["messages"].query(**self._by_pk(user), Limit=1, Select="COUNT")
        return page["Count"] > 0

    def append_message(self, user, message):
        self._put("messages", user, message["at"], message)

    def message(self, user, at) -> dict:
        return self._get("messages", {"pk": user, "sk": at})

    def set_message_reading(self, user, at, reading):
        self._update("messages", user, at, {"reading": reading})

    def mark_message_failed(self, user, at):
        self._update("messages", user, at, {"failed": True})

    def claim_failed_message(self, user, at, retried_at) -> bool:
        """Clears a message's failed mark and records when it was retried, only while the mark is
        there; False when another request retried it first or the message never failed."""
        return self._if_condition_holds(
            "messages",
            "update_item",
            Key={"pk": user, "sk": at},
            UpdateExpression="SET retried_at = :retried_at REMOVE #failed",
            ConditionExpression="#failed = :failed",
            ExpressionAttributeNames={"#failed": "failed"},
            ExpressionAttributeValues={":retried_at": retried_at, ":failed": True},
        )
