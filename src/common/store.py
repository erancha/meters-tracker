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
- failed_photo_readings: the one read that spans accounts, a filtered scan of the
  readings table
"""

import os
import secrets
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

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


def _reading_key(date, seq) -> str:
    return f"{date}#{seq}"


class Store:
    """One handle per app table; each method builds the pk/sk its record lives under and hands
    back plain dicts without those keys."""

    def __init__(self, tables: dict, dynamodb):
        self._tables = {name: dynamodb.Table(tables[name]) for name in TABLE_NAMES}

    @classmethod
    def from_env(cls):
        return cls(
            {name: os.environ[f"{name.upper()}_TABLE"] for name in TABLE_NAMES},
            boto3.resource("dynamodb"),
        )

    def _put(self, table, pk, sk, record):
        self._tables[table].put_item(Item={"pk": pk, "sk": sk, **_to_dynamo(record)})

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

    def properties(self, account) -> list:
        return self._query("properties", account)

    def put_property(self, account, name) -> dict:
        record = {"id": secrets.token_hex(4), "name": name}
        self._put("properties", account, record["id"], record)
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
        self._put("apartments", account, record["id"], record)
        return record

    def meter(self, account, serial):
        return self._get("meters", {"pk": account, "sk": serial})

    def meters(self, account) -> list:
        return self._query("meters", account)

    def put_meter(self, account, meter):
        self._put("meters", account, meter["serial"], meter)

    def update_meter(self, account, serial, **fields):
        self._update("meters", account, serial, fields)

    def periods(self, account, apartment_id) -> list:
        return self._query("periods", f"{account}#{apartment_id}")

    def put_period(self, account, apartment_id, period):
        self._put("periods", f"{account}#{apartment_id}", period["start"], period)

    def update_period(self, account, apartment_id, start, **fields):
        self._update("periods", f"{account}#{apartment_id}", start, fields)

    def delete_period(self, account, apartment_id, start):
        self._tables["periods"].delete_item(Key={"pk": f"{account}#{apartment_id}", "sk": start})

    def readings(self, account, serial) -> list:
        return self._query("readings", f"{account}#{serial}")

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
        self._put(
            "readings",
            f"{account}#{serial}",
            _reading_key(reading["date"], reading["seq"]),
            reading,
        )

    def update_reading(self, account, serial, date, seq, remove=(), **fields):
        self._update("readings", f"{account}#{serial}", _reading_key(date, seq), fields, remove)

    def tariffs(self, account) -> list:
        return self._query("tariffs", account)

    def put_tariff(self, account, tariff):
        self._put("tariffs", account, tariff["effective_date"], tariff)

    def pending(self, user) -> list:
        return self._query("pending", user)

    def put_pending(self, user, item):
        self._put("pending", user, item["seq"], item)

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
