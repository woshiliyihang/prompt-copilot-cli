import re
from pathlib import Path
p=Path(r'D:\prompt-copilot-cli\main.py')
s=p.read_text(encoding='utf-8',errors='replace')
# extract ZH_SYSTEM_PROMPT if present
zh=''
m=re.search(r'ZH_SYSTEM_PROMPT\s*=\s*"""(.*?)"""',s,flags=re.S)
if m:
    zh=m.group(1)
# find TRANSLATIONS block by matching braces
start=s.find('TRANSLATIONS =')
if start==-1:
    print('No TRANSLATIONS found')
    raise SystemExit(0)
brace_idx=s.find('{', start)
# find matching closing brace
i=brace_idx
level=0
while i<len(s):
    if s[i]=='{': level+=1
    elif s[i]=='}':
        level-=1
        if level==0:
            end=i
            break
    i+=1
translations_text=s[brace_idx:end+1]
# prepare env to eval
globals_dict={'ZH_SYSTEM_PROMPT': zh}
# replace any bare triple-quoted strings inside translations with proper Python repr safety
# Evaluate the dict by exec
try:
    exec('TRANSLATIONS = ' + translations_text, globals_dict)
except Exception as e:
    print('exec failed', e)
    raise
TRANSLATIONS=globals_dict.get('TRANSLATIONS', {})
# perform replacements: for each key, replace t('key', ...)
new_s=s
# remove TRANSLATIONS block
new_s = new_s[:start] + new_s[end+1:]
# remove def t(...) block
m=re.search(r"def t\(.*?:\n(?:\s+.*\n)+\n", new_s)
if m:
    new_s = new_s[:m.start()] + new_s[m.end():]
# For stable replacements, sort keys by length desc to avoid substr collisions
for key, val in sorted(TRANSLATIONS.items(), key=lambda kv: -len(kv[0])):
    lit = val.replace('"', '\\"')
    # prepare replacement for calls with kwargs or args
    # pattern: t(<quote>key<quote>\s*(, (?P<args>[^)]*))?)\)
    pattern = re.compile(r"t\(\s*([\'\"])"+re.escape(key)+r"\1\s*(?:,\s*([^)]*))?\)")
    def repl(m):
        args = m.group(2)
        if args:
            return '"' + lit + '".format(' + args + ')'
        else:
            return '"' + lit + '"'
    new_s, n = pattern.subn(repl, new_s)
    if n>0:
        print(f'Replaced {n} occurrences of {key}')
# write back
p.write_text(new_s, encoding='utf-8')
print('done')
