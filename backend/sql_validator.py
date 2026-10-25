
import sqlglot
from sqlglot import expressions as exp


ALLOWED_QUERY_TYPES = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)

BLOCKED_FUNCTIONS = {
    "PG_SLEEP",
    "PG_TERMINATE_BACKEND",
    "PG_CANCEL_BACKEND",
    "PG_READ_FILE",
    "PG_READ_BINARY_FILE",
    "PG_LS_DIR",
    "PG_STAT_FILE",
    "PG_RELOAD_CONF",
    "PG_ROTATE_LOGFILE",
    "PG_LOGFILE_ROTATE",
    "NEXTVAL",
    "SETVAL",
    "LO_IMPORT",
    "LO_EXPORT",
    "DBLINK",
    "DBLINK_EXEC",
    "PG_ADVISORY_LOCK",
    "PG_ADVISORY_XACT_LOCK",
}


def _function_name(node: exp.Func) -> str:
    """Return a normalized function name for SQLGlot function nodes."""
    name = node.sql_name()

    if name == "ANONYMOUS":
        name = getattr(node, "name", "") or ""

    return str(name).upper()


def validate_dataset_select(sql: str, table_name: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL query is empty.")

    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise ValueError("Invalid PostgreSQL query syntax.") from exc

    if len(statements) != 1 or statements[0] is None:
        raise ValueError("Only one SQL statement is allowed.")

    tree = statements[0]

    if not isinstance(tree, ALLOWED_QUERY_TYPES):
        raise ValueError("Only read-only SELECT queries are allowed.")

    # Reject SELECT INTO, which can create a table.
    if any(isinstance(node, exp.Into) for node in tree.walk()):
        raise ValueError("SELECT INTO is not allowed.")

    # Reject row-locking clauses.
    if any(isinstance(node, exp.Lock) for node in tree.walk()):
        raise ValueError("Row-locking clauses are not allowed.")

    tables = list(tree.find_all(exp.Table))

    if not tables:
        raise ValueError(
            "The query must read from the selected dataset."
        )

    for table in tables:
        if (
            table.name != table_name
            or table.db
            or table.catalog
        ):
            raise ValueError(
                "Access to other tables or schemas is not allowed."
            )

    for node in tree.walk():
        if isinstance(node, exp.Func):
            function_name = _function_name(node)

            if function_name in BLOCKED_FUNCTIONS:
                raise ValueError(
                    f"The SQL function {function_name} is not allowed."
                )

    return tree.sql(dialect="postgres")
