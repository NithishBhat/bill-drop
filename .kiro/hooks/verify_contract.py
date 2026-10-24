"""Verify the backend <-> frontend JSON contract. Run by the Kiro hooks in .kiro/hooks/*.json.

Checks, in order:
  1. Static (stdlib only): every dict literal returned by analyze_bill / find_savings / ask, and the
     hardcoded FALLBACK_BILL / FALLBACK_SAVINGS, has exactly the contract keys. BILL_SCHEMA's
     "required" list matches its "properties".
  2. Static: every key app.py reads from bill / savings / rec / result / reply / m exists in the contract.
  3. Runtime (needs boto3): calls analyze_bill() and ask() with fake Bedrock clients (no AWS calls)
     on a success path and a failure path and checks the returned keys, including nested ones.

Exit 0 = contract holds. Exit 1 = violations, listed on stdout and stderr.
Usage:  python .kiro/hooks/verify_contract.py
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend.py"
APP = ROOT / "app.py"

# The contract. Mirrors the docstring at the top of backend.py.
RESULT_KEYS = {"bill", "savings", "is_fallback", "error"}
SAVINGS_KEYS = {"yearly_cost_now", "total_yearly_savings", "recommendations"}
REC_KEYS = {"title", "why", "yearly_savings", "link"}
ASK_KEYS = {"answer", "sources"}
HISTORY_KEYS = {"month", "kwh"}
FUNCTION_RETURNS = {"analyze_bill": RESULT_KEYS, "find_savings": SAVINGS_KEYS, "ask": ASK_KEYS}

problems = []


def dict_keys(node):
    """Keys of a dict literal whose keys are all string constants, else None."""
    if not isinstance(node, ast.Dict) or not all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                                                  for k in node.keys):
        return None
    return {k.value for k in node.keys}


def own_returns(func):
    """Return statements of func itself, not of nested functions or lambdas."""
    found, stack = [], list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def parse(path):
    """utf-8-sig: tolerate the BOM some Windows editors add."""
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def compare(where, got, expected):
    if got != expected:
        missing, extra = sorted(expected - got), sorted(got - expected)
        problems.append(f"{where}: missing {missing}, unexpected {extra}")


# ----------------------------------------------------------------------------- 1. backend.py, static
def check_backend_static():
    tree = parse(BACKEND)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assigns = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}

    for name, expected in FUNCTION_RETURNS.items():
        if name not in funcs:
            problems.append(f"backend.py: function {name}() is missing")
            continue
        returns = own_returns(funcs[name])
        if not returns:
            problems.append(f"backend.py:{funcs[name].lineno} {name}() never returns")
        for ret in returns:
            keys = dict_keys(ret.value)
            if keys is None:
                problems.append(f"backend.py:{ret.lineno} {name}() must return a dict literal "
                                "so its keys are explicit")
            else:
                compare(f"backend.py:{ret.lineno} {name}() return", keys, expected)
    if "read_bill" not in funcs:
        problems.append("backend.py: function read_bill() is missing")

    try:
        schema = ast.literal_eval(assigns["BILL_SCHEMA"])
        bill_keys = set(schema["required"])
        compare("backend.py BILL_SCHEMA required vs properties", bill_keys, set(schema["properties"]))
    except (KeyError, ValueError, TypeError) as e:
        problems.append(f"backend.py: BILL_SCHEMA must be a literal dict with 'required' ({e!r})")
        bill_keys = set()

    for const, expected in (("FALLBACK_BILL", bill_keys), ("FALLBACK_SAVINGS", SAVINGS_KEYS)):
        try:
            value = ast.literal_eval(assigns[const])
        except (KeyError, ValueError) as e:
            problems.append(f"backend.py: {const} must be a hardcoded literal dict ({e!r})")
            continue
        compare(f"backend.py {const}", set(value), expected)
        if const == "FALLBACK_SAVINGS":
            for i, rec in enumerate(value.get("recommendations", [])):
                compare(f"backend.py FALLBACK_SAVINGS recommendations[{i}]", set(rec), REC_KEYS)
    return bill_keys


# ----------------------------------------------------------------------------- 2. app.py, static
def check_app_static(bill_keys):
    allowed = {"bill": bill_keys, "savings": SAVINGS_KEYS, "rec": REC_KEYS, "result": RESULT_KEYS,
               "reply": ASK_KEYS, "m": HISTORY_KEYS}
    tree = parse(APP)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in allowed and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str) and node.slice.value not in allowed[node.value.id]):
            problems.append(f"app.py:{node.lineno} reads {node.value.id}[{node.slice.value!r}], "
                            "which the backend never returns")


# ----------------------------------------------------------------------------- 3. runtime
class _Fake:
    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, 0

    def _next(self):
        out = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return out

    converse = retrieve = lambda self, **_: self._next()


def _tool(payload):
    return {"usage": {"inputTokens": 1, "outputTokens": 1},
            "output": {"message": {"content": [{"toolUse": {"input": payload}}]}}}


def check_runtime(bill_keys):
    sys.path.insert(0, str(ROOT))
    import backend  # noqa: E402  (boto3.client() needs no credentials or network)

    kb = _Fake([{"retrievalResults": [{"content": {"text": "BCCE Basic is $0.15/kWh"},
                                       "location": {"s3Location": {"uri": "s3://kb/boston-bcce.txt"}}}]}])
    model = _Fake([
        _tool({"utility": "Eversource", "kwh_used": 600, "total_cost": 180.0,  # keys left out on purpose
               "supply_rate_per_kwh": 0.17, "monthly_kwh_history": [{"month": "2026-01", "kwh": 700}]}),
        _tool({"recommendations": [
            {"kind": "supply_switch", "title": "Switch", "why": "Cheaper", "new_supply_rate_per_kwh": 0.15,
             "estimated_yearly_savings": None, "link": "www.boston.gov"},
            {"kind": "rebate", "title": "Heat pump", "why": "Winter", "new_supply_rate_per_kwh": None,
             "estimated_yearly_savings": 300, "link": None}]}),
        {"usage": {"inputTokens": 1, "outputTokens": 1},
         "output": {"message": {"content": [{"text": "Call Mass Save."}]}}},
    ])
    backend._bedrock, backend._kb = model, kb

    def check_result(label, result, expect_fallback):
        compare(f"runtime analyze_bill ({label})", set(result), RESULT_KEYS)
        if result.get("is_fallback") is not expect_fallback:
            problems.append(f"runtime analyze_bill ({label}): is_fallback={result.get('is_fallback')!r}, "
                            f"expected {expect_fallback} (error={result.get('error')!r})")
        compare(f"runtime analyze_bill ({label}) bill", set(result.get("bill", {})), bill_keys)
        savings = result.get("savings", {})
        compare(f"runtime analyze_bill ({label}) savings", set(savings), SAVINGS_KEYS)
        for i, rec in enumerate(savings.get("recommendations", [])):
            compare(f"runtime analyze_bill ({label}) recommendations[{i}]", set(rec), REC_KEYS)

    result = backend.analyze_bill(b"\x89PNG\r\n\x1a\nfake")
    check_result("success path", result, expect_fallback=False)
    if not result["savings"]["recommendations"]:
        problems.append("runtime analyze_bill (success path): no recommendations, so their keys went unchecked")
    compare("runtime ask()", set(backend.ask("heat pump rebates?", result["bill"], result["savings"])), ASK_KEYS)

    backend._bedrock = _Fake([RuntimeError("Bedrock is down")])
    check_result("API failure", backend.analyze_bill(b"%PDF-1.7 fake"), expect_fallback=True)
    check_result("unsupported upload", backend.analyze_bill(b"not a bill"), expect_fallback=True)


def main():
    try:
        import boto3  # noqa: F401
    except ImportError:
        venv_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
            sys.exit(subprocess.call([str(venv_python), __file__]))
        boto3 = None

    unreadable = False
    try:
        bill_keys = check_backend_static()
        check_app_static(bill_keys)
    except (OSError, SyntaxError) as e:
        problems.append(f"can't read or parse source: {type(e).__name__}: {e}")
        unreadable = True

    if unreadable:
        pass  # importing a broken backend would just repeat the same error
    elif boto3 is None:
        print("verify_contract: boto3 not installed, skipped the runtime check "
              "(pip install -r requirements.txt)", file=sys.stderr)
    else:
        try:
            check_runtime(bill_keys)
        except Exception as e:
            problems.append(f"runtime check crashed: {type(e).__name__}: {e}")

    if problems:
        report = "verify_contract: FAILED, backend/frontend JSON contract broken:\n" + \
                 "\n".join(f"  - {p}" for p in problems)
        print(report)
        print(report, file=sys.stderr)
        sys.exit(1)
    print("verify_contract: OK. analyze_bill, find_savings, ask, the fallback dicts and app.py agree "
          "on every contract key.")


if __name__ == "__main__":
    main()
