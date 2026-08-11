import math
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def execute_python_calc(expression: str) -> Dict[str, Any]:
    """
    Safely evaluates math formulas, percentage calculations, or Python code expressions.
    """
    logger.info(f"Executing Python Math Calculation: '{expression}'")
    try:
        # Clean expression
        clean_expr = expression.replace("^", "**").strip()
        
        # Allowed mathematical names
        safe_dict = {
            "math": math,
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
            "sum": sum,
            "sqrt": math.sqrt,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "log": math.log,
            "pi": math.pi,
            "e": math.e
        }
        
        result = eval(clean_expr, {"__builtins__": None}, safe_dict)
        return {
            "success": True,
            "expression": expression,
            "result": result
        }
    except Exception as e:
        logger.error(f"Math expression evaluation error: {e}")
        return {
            "success": False,
            "expression": expression,
            "error": str(e),
            "result": "Calculation Error"
        }
