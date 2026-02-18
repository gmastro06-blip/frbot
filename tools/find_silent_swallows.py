import os, re
root = 'c:\\Users\\gmast\\Documents\\GitHub\\frbot\\runtime'
silent = []
for dirpath, dirs, files in os.walk(root):
    for f in files:
        if not f.endswith('.py'):
            continue
        fp = os.path.join(dirpath, f)
        with open(fp, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()
        for i, line in enumerate(lines):
            if re.match(r"\s*except\s+Exception\s*:\s*$", line):
                indent = len(line) - len(line.lstrip(' '))
                block = []
                j = i+1
                while j < len(lines):
                    l = lines[j]
                    lstrip = l.lstrip(' ')
                    ind = len(l) - len(lstrip)
                    if lstrip and ind <= indent:
                        break
                    block.append(l)
                    j += 1
                content_lines = [ln for ln in (l.strip() for l in block) if ln and not ln.startswith('#')]
                if len(content_lines) == 1 and (content_lines[0] == 'pass' or content_lines[0] == 'return' or content_lines[0] == 'return None' or content_lines[0].startswith('return ')):
                    # ensure should_reraise not referenced
                    block_text = ''.join(block)
                    if 'should_reraise' not in block_text:
                        silent.append((fp, i+1, content_lines[0], '\n'.join(block[:5])))

if not silent:
    print('No silent broad excepts found')
else:
    for fp, ln, c, preview in silent:
        print(f"{fp}:{ln} -> {c}\n{preview}\n---")
