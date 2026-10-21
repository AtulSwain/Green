"""Safe arithmetic. Expressions are parsed into a syntax tree and only whitelisted
operations are evaluated; nothing is passed to eval/exec."""

from __future__ import annotations

import ast
import math
import operator

from pydantic import Field

from ..services.tool_registry import Tool, ToolArgs, ToolCategory, ToolError

MAX_EXPRESSION = 300
MAX_EXPONENT = 1000
MAX_MAGNITUDE = 1e300

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp, "min": min, "max": max,
    "radians": math.radians, "degrees": math.degrees, "factorial": math.factorial,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _check(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("Only numbers are supported.")
    if isinstance(value, float) and (math.isnan(value) or abs(value) > MAX_MAGNITUDE):
        raise ToolError("The result is too large or not a number.")
    if isinstance(value, int) and abs(value) > 10**300:
        raise ToolError("The result is too large.")
    return value


def _evaluate(node):
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        return _check(node.value)
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ToolError(f"Unknown name '{node.id}'.")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _check(_UNARY[type(node.op)](_evaluate(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ToolError("The exponent is too large.")
        return _check(_BINARY[type(node.op)](left, right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS and not node.keywords:
        args = [_evaluate(arg) for arg in node.args]
        if node.func.id == "factorial" and (not args or args[0] > 1000):
            raise ToolError("factorial is limited to numbers up to 1000.")
        return _check(_FUNCTIONS[node.func.id](*args))
    raise ToolError("Only numbers, + - * / // % **, parentheses and common math functions are allowed.")


def calculate(expression: str):
    expression = expression.replace("^", "**").replace("×", "*").replace("÷", "/").strip()
    if not expression or len(expression) > MAX_EXPRESSION:
        raise ToolError("Expression is empty or too long.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError("That is not a valid arithmetic expression.") from exc
    try:
        result = _evaluate(tree)
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero.") from exc
    except (ValueError, OverflowError, TypeError) as exc:
        raise ToolError(f"Math error: {exc}") from exc
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    return result


class CalculateArgs(ToolArgs):
    expression: str = Field(..., min_length=1, max_length=MAX_EXPRESSION,
                            description="Arithmetic expression, e.g. '(1250 * 0.18) + 40' or 'sqrt(2) * pi'")


class CalculateTool(Tool):
    name = "calculate"
    description = (
        "Evaluate an arithmetic expression exactly. Use this for any calculation instead of doing math in your head. "
        "Supports + - * / // % ** ( ) and sqrt, abs, round, floor, ceil, sin, cos, tan, log, log10, exp, min, max, pi, e."
    )
    category = ToolCategory.READ
    Args = CalculateArgs

    def prepare(self, args):
        return calculate(args.expression)

    def describe(self, args, prepared):
        return f"Calculating {args.expression}"

    def run(self, args, prepared):
        return {"expression": args.expression, "result": prepared}

    def summarize(self, data):
        return f"= {data['result']}"
