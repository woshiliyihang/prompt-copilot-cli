from pathlib import Path
p=Path(r'D:\prompt-copilot-cli\main.py')
data=p.read_bytes()
text=data.decode('utf-8','replace')
idx=text.find('TRANSLATIONS =')
if idx==-1:
    print('NOTFOUND')
else:
    start=text.rfind('\n',0,idx)+1
    end=text.find('\n\n', idx)
    if end==-1:
        end=idx+5000
    print(text[start:end+5000])
