"""
Deterministic safety checks for model-generated SQL.

Simplified from the production code, with names changed. Every query the model
writes passes these checks before it runs. It then runs under a read-only
database role against tables with forced row-level security, so these checks
are one layer of several, not the only defence.

Pattern: never trust the model to follow "SELECT only" because the prompt said
so. Verify it independently, with plain code.
"""

from __future__ import annotations

import re

import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import DDL, DML, Keyword


class UnsafeSQLError(Exception):
    def __init__(self, sql: str, reason: str) -> None:
        super().__init__(reason)
        self.sql = sql
        self.reason = reason


# Generated SQL may only read the tenant-scoped schema. Every table in it has
# forced row-level security. An allowlist of schemas (rather than tables) needs
# no update when a table is added, and can't misfire on CTE names.
ALLOWED_SCHEMAS: frozenset[str] = frozenset({"app"})

PROHIBITED_KEYWORDS: frozenset[str] = frozenset(
    {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE"}
)
FORBIDDEN_PATTERNS: frozenset[str] = frozenset({"INFORMATION_SCHEMA", "PG_", "INTO", "COPY"})

# A schema-qualified name in table position (after FROM or JOIN).
_TABLE_POSITION_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$]*)\s*\.\s*([A-Za-z_][\w$]*)", re.IGNORECASE
)


def validate_sql_safety(sql: str) -> None:
    """Raise UnsafeSQLError unless `sql` is exactly one read-only SELECT."""
    if not sql or not sql.strip():
        raise UnsafeSQLError(sql="", reason="Empty SQL generated")

    statements = [s for s in sqlparse.parse(sql) if s.tokens and str(s).strip()]
    if len(statements) != 1:
        raise UnsafeSQLError(sql=sql, reason=f"Expected one statement, found {len(statements)}")

    stmt: Statement = statements[0]
    stmt_type = stmt.get_type()
    if stmt_type and stmt_type.upper() != "SELECT":
        raise UnsafeSQLError(sql=sql, reason=f"Statement type is {stmt_type}; only SELECT is allowed")

    for token in stmt.flatten():
        if token.ttype not in (Keyword, DML, DDL, Keyword.DML, Keyword.DDL):
            continue
        value = token.value.upper().strip()
        if value in PROHIBITED_KEYWORDS:
            raise UnsafeSQLError(sql=sql, reason=f"Forbidden SQL keyword: {value}")
        if any(value == p or value.startswith(p) for p in FORBIDDEN_PATTERNS):
            raise UnsafeSQLError(sql=sql, reason=f"Forbidden SQL pattern: {value}")

    upper_sql = sql.upper()
    if "INFORMATION_SCHEMA" in upper_sql or re.search(r"\bPG_\w+", upper_sql):
        raise UnsafeSQLError(sql=sql, reason="System catalogs are not queryable")

    _reject_schemas_outside_allowlist(sql)


def _reject_schemas_outside_allowlist(sql: str) -> None:
    for match in _TABLE_POSITION_REF.finditer(sql):
        # EXTRACT(MONTH FROM t.created_on) also reads "FROM t.x", but there
        # `t` is a table alias, not a schema. Skip matches inside function-call
        # parentheses so ordinary date questions aren't rejected.
        if _in_function_call_parens(sql, match.start()):
            continue
        schema = match.group(1)
        if schema.lower() not in ALLOWED_SCHEMAS:
            raise UnsafeSQLError(sql=sql, reason=f"Schema {schema!r} may not be queried")


def _in_function_call_parens(sql: str, index: int) -> bool:
    """Is `index` directly inside a function call's parentheses?

    The test is the parenthesis, not a list of function names: a "(" that
    follows an identifier opens a function call, unless the next token is
    SELECT or WITH, which makes it a subquery. Only the innermost
    parenthesis matters.
    """
    stack: list[bool] = []  # True = function-call parentheses
    in_string = False
    i = 0
    while i < index and i < len(sql):
        char = sql[i]
        if in_string:
            if char == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":  # escaped quote
                    i += 2
                    continue
                in_string = False
        elif char == "'":
            in_string = True
        elif char == "(":
            following = sql[i + 1 :].lstrip()
            is_subquery = bool(re.match(r"(?:SELECT|WITH)\b", following, re.IGNORECASE))
            preceding = sql[:i].rstrip()
            follows_identifier = bool(preceding) and (preceding[-1].isalnum() or preceding[-1] == "_")
            stack.append(follows_identifier and not is_subquery)
        elif char == ")" and stack:
            stack.pop()
        i += 1
    return bool(stack) and stack[-1]
