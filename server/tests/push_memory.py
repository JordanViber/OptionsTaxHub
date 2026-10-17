"""In-memory stand-in for the PostgREST calls push subscriptions make.

Offset pagination is rejected. Keyset filters use the cursor string the
server sends: created_at less than the cursor, or the same created_at and a
smaller id.
"""

import re
import uuid
from datetime import datetime, timezone


_KEYSET_RE = re.compile(
    r'^created_at\.lt\."([^"]+)",and\(created_at\.eq\."([^"]+)",id\.lt\."([^"]+)"\)$'
)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store):
        self.store = store
        self.op = "select"
        self.eqs = []
        self.or_expr = None
        self.orders = []
        self.limit_n = None
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, row):
        self.op = "insert"
        self.payload = dict(row)
        return self

    def update(self, row):
        self.op = "update"
        self.payload = dict(row)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, column, value):
        self.eqs.append((column, value))
        return self

    def order(self, column, desc=False, **_kwargs):
        self.orders.append((column, bool(desc)))
        return self

    def limit(self, count):
        self.limit_n = count
        self.store.limits.append(count)
        return self

    def range(self, *_args, **_kwargs):
        raise AssertionError("offset pagination is not safe for push fan-out")

    def or_(self, expr):
        self.or_expr = expr
        self.store.or_filters.append(expr)
        return self

    def execute(self):
        return _Result(self.store.execute(self))


class MemorySupabase:
    def __init__(self):
        self.rows = []
        self.limits = []
        self.or_filters = []
        self.insert_returns_empty = False
        self.after_first_page = None
        self._pages = 0

    def table(self, name):
        if name != "push_subscriptions":
            raise AssertionError(f"unexpected table {name}")
        return _Query(self)

    def execute(self, query):
        if query.op == "insert":
            return self._insert(query)
        if query.op == "update":
            return self._update(query)
        if query.op == "delete":
            return self._delete(query)
        return self._select(query)

    def _matches(self, query, row):
        if any(row.get(column) != value for column, value in query.eqs):
            return False
        if query.or_expr is None:
            return True
        return _keyset_matches(row, query.or_expr)

    def _filtered(self, query):
        matched = [row for row in self.rows if self._matches(query, row)]
        for column, descending in reversed(query.orders):
            matched.sort(
                key=lambda row, column=column: "" if row.get(column) is None else str(row.get(column)),
                reverse=descending,
            )
        if query.limit_n is None:
            return matched
        return matched[: query.limit_n]

    def _select(self, query):
        page = [dict(row) for row in self._filtered(query)]
        self._pages += 1
        if self._pages == 1 and self.after_first_page is not None and query.limit_n:
            self.after_first_page(self)
        return page

    def _insert(self, query):
        endpoint = query.payload.get("endpoint")
        if any(row.get("endpoint") == endpoint for row in self.rows):
            raise Exception("duplicate key value violates unique constraint")
        if self.insert_returns_empty:
            return []
        row = dict(query.payload)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        row.setdefault("updated_at", row["created_at"])
        self.rows.append(row)
        return [dict(row)]

    def _update(self, query):
        updated = []
        for row in self.rows:
            if self._matches(query, row):
                row.update(query.payload)
                updated.append(dict(row))
        return updated

    def _delete(self, query):
        kept = []
        deleted = []
        for row in self.rows:
            if self._matches(query, row):
                deleted.append(dict(row))
            else:
                kept.append(row)
        self.rows[:] = kept
        return deleted


def _keyset_matches(row, expr):
    match = _KEYSET_RE.fullmatch(expr)
    if match is None:
        raise AssertionError(f"unrecognized push keyset filter: {expr}")
    created_lt, created_eq, row_id = match.groups()
    created_at = str(row.get("created_at"))
    if created_at < created_lt:
        return True
    return created_at == created_eq and str(row.get("id")) < row_id
