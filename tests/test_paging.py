"""Whole-table reads across pages: a scan returns every item even when one page holds fewer."""

from common.paging import scan_all


def test_scan_all_follows_pages_to_the_end(ddb):
    table = ddb.Table("meters")
    for i in range(5):
        table.put_item(Item={"pk": "acc", "sk": f"M{i}"})
    items = scan_all(table, Limit=2, ConsistentRead=True)
    assert sorted(item["sk"] for item in items) == [f"M{i}" for i in range(5)]
