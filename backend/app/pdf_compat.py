"""Data-only adapters from publisher syntax to PaperPilot's trusted templates."""
import re


def args(text, start, count):
    values = []
    cursor = start
    for _ in range(count):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text) or text[cursor] != '{':
            raise ValueError('模板命令缺少完整参数')
        begin = cursor + 1
        cursor += 1
        depth = 1
        while cursor < len(text) and depth:
            if text[cursor] == '\\':
                cursor += 2
                continue
            depth += (text[cursor] == '{') - (text[cursor] == '}')
            cursor += 1
        if depth:
            raise ValueError('模板命令参数未闭合')
        values.append(text[begin:cursor-1])
    return values, cursor


def box_headers(text):
    headers = []
    for match in re.finditer(r'\\begin\{tcolorbox\}\s*\[', text):
        cursor, brackets, braces = match.end(), 1, 0
        while cursor < len(text) and brackets:
            char = text[cursor]
            if char == '\\':
                cursor += 2
                continue
            if char == '{':
                braces += 1
            elif char == '}':
                braces -= 1
            elif braces == 0:
                brackets += (char == '[') - (char == ']')
            cursor += 1
        if brackets or braces:
            raise ValueError('提示框排版选项未闭合')
        headers.append((match.start(), cursor, 'tcolorbox'))
    return headers


def restore_box_headers(source, translated):
    originals, targets = box_headers(source), box_headers(translated)
    if len(originals) != len(targets):
        raise ValueError('提示框选项数量与原文不一致，不能恢复排版参数')
    for (left, right, _), (start, end, _) in reversed(list(zip(originals, targets))):
        translated = translated[:start] + source[left:right] + translated[end:]
    return translated


def table_headers(text):
    """Locate complete nested column specifications; these are layout, never prose."""
    headers = []
    for match in re.finditer(r'\\begin\{(tabular\*?|array)\}', text):
        cursor = match.end()
        if match[1] == 'tabular*':
            _, cursor = args(text, cursor, 1)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor < len(text) and text[cursor] == '[':
            end = text.find(']', cursor)
            if end < 0:
                raise ValueError('表格对齐参数未闭合')
            cursor = end + 1
        _, cursor = args(text, cursor, 1)
        headers.append((match.start(), cursor, match[1]))
    return headers


def restore_table_headers(source, translated):
    originals, targets = table_headers(source), table_headers(translated)
    if [item[2] for item in originals] != [item[2] for item in targets]:
        raise ValueError('表格环境数量或类型与原文不一致，不能恢复排版参数')
    for (left, right, _), (start, end, _) in reversed(list(zip(originals, targets))):
        translated = translated[:start] + source[left:right] + translated[end:]
    return translated


def calls(text, command, count):
    for match in re.finditer(r'\\' + re.escape(command) + r'(?![A-Za-z@])', text):
        values, end = args(text, match.end(), count)
        yield match.start(), end, values


def icml_frontmatter(items, blocks):
    """Read authors and affiliations from immutable source, never execute the style file."""
    originals = {b['id']: b['text'] for b in blocks}
    start = next((i for i, item in enumerate(items) if r'\icmltitle' in originals.get(item['id'], '')), None)
    if start is None:
        return items
    end = next((i for i in range(start, len(items)) if r'\printAffiliationsAndNotice' in originals.get(items[i]['id'], '')), None)
    if end is None:
        raise ValueError('ICML 标题区未找到完整结束标记，不能省略作者信息')
    source = '\n'.join(originals[item['id']] for item in items[start:end+1])
    authors = [a for _, _, a in calls(source, 'icmlauthor', 2)]
    affiliations = {a[0]: a[1] for _, _, a in calls(source, 'icmlaffiliation', 2)}
    symbols = {a[0]: a[1] for _, _, a in calls(source, 'icmlsetsymbol', 2)}
    correspondents = [a for _, _, a in calls(source, 'icmlcorrespondingauthor', 2)]
    if not authors or any(not re.fullmatch(r'[\w-]+', key) for key in affiliations):
        raise ValueError('ICML 作者或单位标识无法可靠解析')
    if any(not re.fullmatch(r'[*+†‡]+', value) for value in symbols.values()):
        raise ValueError('ICML 作者符号包含不支持的命令')
    labels = {key: str(i+1) for i, key in enumerate(affiliations)} | symbols
    names = []
    for name, keys in authors:
        keys = [k.strip() for k in keys.split(',')]
        if any(k not in labels for k in keys):
            raise ValueError('ICML 作者对应单位缺失')
        marks = [labels[k] for k in keys]
        if any(name == person for person, _ in correspondents):
            marks.append('*')
        names.append(name + r'\textsuperscript{' + ','.join(marks) + '}')
    author_text = r' \quad '.join(names)
    for key, institution in affiliations.items():
        author_text += r' \\ \textsuperscript{' + labels[key] + '}' + institution
    for name, email in correspondents:
        if not re.fullmatch(r'[A-Za-z0-9_.+@-]+', email):
            raise ValueError('ICML 通讯作者邮箱格式不支持')
        author_text += r' \\ \textsuperscript{*}通讯作者：' + name + ' (' + email.replace('_', r'\_') + ')'
    additions = [{'id':'authors', 'kind':'author', 'text':author_text, 'translate':True}]
    for _, _, value in calls(source, 'icmlkeywords', 1):
        additions.append({'id':'icml-keywords', 'kind':'metadata', 'text':'关键词：' + value[0], 'translate':True})
    # Reject unexpected source controls before replacing the publisher-only header.
    known = {'twocolumn','icmltitle','texorpdfstring','icmlsetsymbol','begin','end','icmlauthor',
             'icmlaffiliation','icmlcorrespondingauthor','icmlkeywords','vskip','hspace','printAffiliationsAndNotice'}
    if set(re.findall(r'\\([A-Za-z]+)', source)) - known:
        raise ValueError('ICML 标题区包含尚未适配的命令')
    remainder = source
    arities = {'icmltitle':1, 'texorpdfstring':2, 'icmlsetsymbol':2, 'icmlauthor':2,
               'icmlaffiliation':2, 'icmlcorrespondingauthor':2, 'icmlkeywords':1,
               'printAffiliationsAndNotice':1, 'hspace':1}
    for command, count in arities.items():
        for left, right, _ in reversed(list(calls(remainder, command, count))):
            remainder = remainder[:left] + remainder[right:]
    remainder = re.sub(r'\\(?:begin|end)\{icmlauthorlist\}|\\twocolumn\s*\[|\\vskip\s*[+-]?\d+(?:\.\d+)?(?:in|pt|cm|mm|em|ex)', '', remainder)
    if correspondents:
        remainder = re.sub(r'\$\^\{\*\}\$', '', remainder)
    if re.sub(r'[\s\[\]$*{}]', '', remainder):
        raise ValueError('ICML 标题区含未识别内容，不能静默删除')
    return items[:start] + additions + items[end+1:]


def inline_verbatim(text):
    from .chinese_pdf import escape
    return re.sub(r'\\verb\*?([^A-Za-z\s])(.*?)\1', lambda m: r'\texttt{' + escape(m[2]) + '}', text)


def literal_listing(text):
    """Separate declarative listing options from literal source, without executing either."""
    listing = re.fullmatch(r'\s*\\begin\{lstlisting\}([\s\S]*?)\\end\{lstlisting\}\s*', text)
    if not listing:
        return None
    body = listing[1]
    cursor = len(body) - len(body.lstrip())
    if cursor < len(body) and body[cursor] == '[':
        depth, braces, end = 1, 0, cursor + 1
        while end < len(body) and depth:
            char = body[end]
            if char == '\\':
                end += 2
                continue
            if char == '{':
                braces += 1
            elif char == '}':
                braces -= 1
            elif not braces and char in '[]':
                depth += 1 if char == '[' else -1
            end += 1
        if depth or braces:
            raise ValueError('lstlisting 排版参数未闭合')
        body = body[end:]
    # Remove only the opening separator, retaining indentation of actual code lines.
    body = re.sub(r'^[ \t]*\r?\n', '', body, count=1)
    from .chinese_pdf import escape
    lines = []
    for line in body.splitlines():
        line = line.expandtabs(4)
        indent = len(line) - len(line.lstrip(' '))
        prefix = r'\hspace*{' + str(indent * 0.5) + 'em}' if indent else ''
        lines.append(prefix + escape(line[indent:]) + r'\par')
    return r'{\ttfamily\small\raggedright ' + '\n'.join(lines) + '}'


def normalize(text):
    listing = literal_listing(text)
    if listing is not None:
        return listing
    text = inline_verbatim(text)
    # Trusted monochrome boxes: publisher colour definitions are not executable input.
    # Only literal colour expressions are adapted; commands and malformed options fail validation.
    text = re.sub(r'\b(colback|colframe|coltext)\s*=\s*([A-Za-z][A-Za-z0-9]*(?:![0-9]+(?:\.[0-9]+)?(?:![A-Za-z][A-Za-z0-9]*)?)*)(?=\s*[,\]])',
                  lambda m: m[1] + '=' + ('white' if m[1] == 'colback' else 'black'), text)
    text = re.sub(r'\\vskip\s*([+-]?\d+(?:\.\d+)?(?:in|pt|cm|mm|em|ex))', lambda m: r'\vspace{' + m[1] + '}', text)
    text = re.sub(r'\\newline(?![A-Za-z@])', lambda _: r'\linebreak{}', text)
    # These source aliases only change colour; the fixed export uses black text.
    text = re.sub(r'\\tcb(?![A-Za-z@])', lambda _: r'\textnormal', text)
    text = text.replace(r'\begin{promptbox}', r'\begin{tcolorbox}').replace(r'\end{promptbox}', r'\end{tcolorbox}')
    text = text.replace('frametitle=', 'title=')
    text = re.sub(r'\\begin\{adjustbox\}\{(?:max )?width=\\(?:textwidth|linewidth)\}', lambda _: r'\resizebox{\linewidth}{!}{', text)
    text = text.replace(r'\end{adjustbox}', '}')
    text = re.sub(r'\\bibliographystyle\{(?:icml20\d{2}|plain)\}', lambda _: r'\bibliographystyle{plainnat}', text)
    # Preserve explicit appendix column transitions from the original document.
    # Only declarative numbering/spacing assignments are permitted, never arbitrary macro definitions.
    for start, end, values in reversed(list(calls(text, 'renewcommand', 2))):
        name, value = values
        allowed = (name == r'\arraystretch' and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value))
        allowed = allowed or (re.fullmatch(r'\\theH?(?:equation|figure|table)', name)
                              and re.fullmatch(r'[A-Z]\.\\arabic\{(?:equation|figure|table)\}', value))
        if not allowed:
            raise ValueError('不支持重定义排版宏：' + name)
    for _, _, (name, value) in calls(text, 'setcounter', 2):
        if name not in {'equation','figure','table'} or not re.fullmatch(r'\d+', value):
            raise ValueError('不支持的计数器设置')
    for match in reversed(list(re.finditer(r'\\begin\{tabular\}', text))):
        values, end = args(text, match.end(), 1)
        spec = re.sub(r'S\[table-format=\d+\.\d+\]', 'c', values[0])
        text = text[:match.end()] + '{' + spec + '}' + text[end:]
    return text


def adapt(items, blocks, files=None):
    if files is not None and any(re.search(r"\\tcb(?![A-Za-z@])", i["text"]) for i in items):
        source = "\n".join(files.values())
        if not re.search(r"\\def\s*\\tcb\s*\{\\textcolor\{blue\}\}", source):
            raise ValueError("tcb 宏定义不是已核实的颜色别名，需核对原文")
    # A .bbl is a generated duplicate when a real .bib is referenced; regenerate from .bib.
    has_bib = files and any(re.search(r'\\bibliography\{[^}]+\}', item['text']) for item in items)
    generated = {b['id'] for b in blocks if b.get('location', '').rsplit(':', 1)[0].endswith('.bbl')}
    output = []
    for item in icml_frontmatter(items, blocks):
        if has_bib and item['id'] in generated:
            continue
        literal_code = bool(re.fullmatch(r'\s*\\begin\{lstlisting\}[\s\S]*\\end\{lstlisting\}\s*', item['text']))
        output.append({**item, 'text':normalize(item['text']), 'translate':False if literal_code else item['translate']})
    from .skills import code_literal_ranges
    ranges = code_literal_ranges([{'id':item['id'], 'text':item['text'],
                                  'translatable':item['translate']} for item in output])
    return [{**item, '_literal_ranges':ranges[item['id']]} for item in output]



CMIDRULE = re.compile(
    r'\\cmidrule(?![A-Za-z@])\s*(?:\[[^\]\n]*\]\s*)?'
    r'(?:\((?:[lr]\s*(?:\{[^{}\n]*\})?\s*)+\)\s*)?'
    r'\{\s*(\d+)\s*-\s*(\d+)\s*\}')
_TABLE_RULE_START = re.compile(r'\\cmidrule(?![A-Za-z@])')
_TRANSLATED_TRIM = re.compile(
    r'\\cmidrule(?![A-Za-z@])[ \t]*(?:\[[^\\{}\]\n]*\][ \t]*)?'
    r'(?:\([^\\{}\n]*\)|（[^\\{}\n]*）)[ \t]*\{\s*(\d+)\s*-\s*(\d+)\s*\}')


def validate_table_rules(text):
    for start in _TABLE_RULE_START.finditer(text):
        if not CMIDRULE.match(text, start.start()):
            raise ValueError('cmidrule 表格线参数无效，需按原文核对，不能翻译排版选项')


def restore_table_rules(source, translated):
    """Recover only identified rule options from immutable source, never table cells."""
    original = list(_TABLE_RULE_START.finditer(source))
    target = list(_TABLE_RULE_START.finditer(translated))
    if len(original) != len(target):
        raise ValueError('表格线数量与原文不一致，不能自动补齐或删除')
    replacements = []
    for left, right in zip(original, target):
        expected = CMIDRULE.match(source, left.start())
        found = CMIDRULE.match(translated, right.start()) or _TRANSLATED_TRIM.match(translated, right.start())
        if not expected or not found or expected.groups() != found.groups():
            raise ValueError('表格线列范围或结构与原文不一致，不能猜测修复')
        replacements.append((found.start(), found.end(), expected[0]))
    for start, end, value in reversed(replacements):
        translated = translated[:start] + value + translated[end:]
    return translated
