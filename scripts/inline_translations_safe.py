"""Safe inline translations tool
- Generates a unified diff (scripts/translation_patch.diff) with inlined TRANSLATIONS replacements
- Writes a temp file scripts/temp_main_inlined.py for syntax checking and smoke-run
- DOES NOT overwrite main.py
"""
import ast
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / 'main.py'
OUT_DIFF = ROOT / 'scripts' / 'translation_patch.diff'
OUT_TEMP = ROOT / 'scripts' / 'temp_main_inlined.py'

src = MAIN_PY.read_text(encoding='utf-8')
mod = ast.parse(src)

# collect simple constant assignments
consts = {}
for node in mod.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
        try:
            val = ast.literal_eval(node.value)
            consts[name] = val
        except Exception:
            pass

# find TRANSLATIONS value node
trans_node = None
for node in mod.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == 'TRANSLATIONS':
                trans_node = node.value
                trans_assign_node = node
                break
    if trans_node:
        break

if trans_node is None:
    print('No TRANSLATIONS assignment found. Exiting.')
    sys.exit(1)

# helper to evaluate AST nodes with substitution from consts
def eval_node(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Str):
        return node.s
    if isinstance(node, ast.Dict):
        keys = [eval_node(k) for k in node.keys]
        values = [eval_node(v) for v in node.values]
        return dict(zip(keys, values))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = eval_node(node.left)
        right = eval_node(node.right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    if isinstance(node, ast.Name):
        if node.id in consts:
            return consts[node.id]
    raise ValueError(f'Unsupported node for safe eval: {ast.dump(node)}')

try:
    translations = eval_node(trans_node)
except Exception as e:
    print('Failed to evaluate TRANSLATIONS safely:', e)
    sys.exit(1)

# find all call sites of t(...)
calls = []
for node in ast.walk(mod):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == 't' and node.args:
            first = node.args[0]
            if isinstance(first, (ast.Constant, ast.Str)) and isinstance(getattr(first, 'value', getattr(first, 's', None)), str):
                calls.append(node)

# helper to map (lineno,col) to absolute index
lines = src.splitlines(keepends=True)
line_offsets = [0]
for i, ln in enumerate(lines):
    line_offsets.append(line_offsets[-1] + len(ln))

def pos_to_index(lineno, col):
    return line_offsets[lineno-1] + col

replacements = []
for c in calls:
    try:
        key_node = c.args[0]
        key = key_node.s if isinstance(key_node, ast.Str) else key_node.value
        if key not in translations:
            print(f'Key {key!r} not in TRANSLATIONS; skipping')
            continue
        translation = translations[key]
        call_src = ast.get_source_segment(src, c)
        if call_src is None:
            print('Could not get source for call, skipping:', ast.dump(c))
            continue
        # build args source excluding first arg
        inner = call_src[call_src.find('(')+1:call_src.rfind(')')]
        # remove the first arg occurrence from inner; robust approach: use positions of child nodes
        other_parts = []
        for a in c.args[1:]:
            seg = ast.get_source_segment(src, a)
            if seg is None:
                seg = ''
            other_parts.append(seg)
        for kw in c.keywords:
            if kw.arg is None:
                # **kwargs - include full text
                seg = ast.get_source_segment(src, kw.value)
                other_parts.append('**' + seg)
            else:
                seg = ast.get_source_segment(src, kw.value)
                other_parts.append(f"{kw.arg}={seg}")
        args_source = ', '.join(p for p in other_parts if p)
        # select language-specific text if translation is a mapping
        def choose_text(v):
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                for lang in ('en', 'zh', 'zh_CN', 'zh-cn', 'zh_cn'):
                    if lang in v and isinstance(v[lang], str):
                        return v[lang]
                for val in v.values():
                    if isinstance(val, str):
                        return val
            raise ValueError('No string available for this translation value')
        try:
            chosen = choose_text(translation)
        except Exception as e:
            print(f'Could not choose language for key {key!r}:', e)
            continue
        new_literal = repr(chosen)
        if args_source:
            new_text = f"{new_literal}.format({args_source})"
        else:
            new_text = new_literal
        start = pos_to_index(c.lineno, c.col_offset)
        end = pos_to_index(c.end_lineno, c.end_col_offset)
        replacements.append((start, end, new_text, key))
    except Exception as e:
        print('Skipping a call due to error:', e)

# remove TRANSLATIONS assignment and def t(...) if present
# get source ranges
trans_start = pos_to_index(trans_assign_node.lineno, trans_assign_node.col_offset)
trans_end = pos_to_index(trans_assign_node.end_lineno, trans_assign_node.end_col_offset)
remove_ranges = [(trans_start, trans_end, '', 'TRANSLATIONS assign')]
# find def t
for node in mod.body:
    if isinstance(node, ast.FunctionDef) and node.name == 't':
        s = pos_to_index(node.lineno, node.col_offset)
        e = pos_to_index(node.end_lineno, node.end_col_offset)
        remove_ranges.append((s, e, '', 'def t'))
        break

# apply replacements in order from end to start
all_edits = []
for (s,e,txt,key) in replacements:
    all_edits.append((s,e,txt))
for (s,e,txt,desc) in remove_ranges:
    all_edits.append((s,e,txt))
all_edits.sort(key=lambda x: x[0], reverse=True)
new_src = src
for s,e,txt in all_edits:
    new_src = new_src[:s] + txt + new_src[e:]

# write diff
orig_lines = src.splitlines(keepends=True)
new_lines = new_src.splitlines(keepends=True)
ud = list(difflib.unified_diff(orig_lines, new_lines, fromfile='main.py', tofile='main.inlined.py'))
if not ud:
    print('No changes generated.')
    sys.exit(0)
OUT_DIFF.write_text(''.join(ud), encoding='utf-8')
OUT_TEMP.write_text(new_src, encoding='utf-8')
print(f'Wrote diff to {OUT_DIFF} and temp file to {OUT_TEMP}.')

# run syntax check on temp file
import py_compile
try:
    py_compile.compile(str(OUT_TEMP), doraise=True)
    print('Syntax check OK for inlined temp file.')
except py_compile.PyCompileError as e:
    print('Syntax check FAILED for inlined temp file:')
    print(e)
    sys.exit(2)

# smoke-run temp file --help
import subprocess
res = subprocess.run([sys.executable, str(OUT_TEMP), '--help'], capture_output=True, text=True)
print('Smoke run exit code:', res.returncode)
if res.stdout:
    print('--- help output (first 200 chars) ---')
    print(res.stdout[:200])
if res.returncode != 0:
    print('Smoke run failed; check the diff at', OUT_DIFF)
    sys.exit(3)

print('Smoke run succeeded. Review the diff at', OUT_DIFF, 'then apply it if acceptable.')
