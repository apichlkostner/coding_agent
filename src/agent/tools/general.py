import ast
import operator
from datetime import datetime, timezone

from langchain_core.tools import tool


@tool
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression and return the result as a string.

    Supports: +, -, *, /, //, %, ** and parentheses.
    Does NOT execute arbitrary code — only numeric literals and operators
    are allowed (safe AST evaluation).

    Examples
    --------
    calculate("2 ** 10")        -> "1024"
    calculate("(3 + 4) * 6")    -> "42"
    """
    _OPERATORS: dict[type, object] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node: ast.expr) -> float:
        match node:
            case ast.Constant(value=v) if isinstance(v, int | float):
                return float(v)
            case ast.BinOp(left=left, op=op, right=right):
                op_fn = _OPERATORS.get(type(op))
                if op_fn is None:
                    raise ValueError(f"Unsupported operator: {type(op).__name__}")
                return op_fn(_eval(left), _eval(right))  # type: ignore[operator]
            case ast.UnaryOp(op=op, operand=operand):
                op_fn = _OPERATORS.get(type(op))
                if op_fn is None:
                    raise ValueError(f"Unsupported operator: {type(op).__name__}")
                return op_fn(_eval(operand))  # type: ignore[operator]
            case _:
                raise ValueError(f"Unsupported expression node: {ast.dump(node)}")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        # Return int string when result is a whole number (e.g. "42" not "42.0")
        return str(int(result)) if result == int(result) else str(result)
    except Exception as exc:
        return f"Error: {exc}"


@tool
def get_current_datetime() -> str:
    """Return the current UTC date and time as an ISO-8601 string.

    Example
    -------
    get_current_datetime() -> "2025-04-30T12:00:00+00:00"
    """
    return datetime.now(tz=timezone.utc).isoformat()
