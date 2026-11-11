"""Escaped, code-owned progressive editor primitives. No configuration writers."""
import hashlib
import html
import re

E = lambda value: html.escape(str(value), quote=True)


def help_tip(text, ident, label="说明"):
    return (f'<span class="help" data-help><button type="button" aria-label="{E(label)}" '
            f'aria-describedby="{E(ident)}" popovertarget="{E(ident)}">?</button>'
            f'<span popover role="tooltip" id="{E(ident)}">{E(text)}</span></span>')


def editor_link(href, label, *, primary=False):
    return f'<a class="btn{" primary" if primary else ""}" href="{E(href)}" data-editor-open>{E(label)}</a>'


def editor_panel(kind, title, body, route, key):
    prefix = "editor-" + hashlib.sha256(str(key).encode()).hexdigest()[:12] + "-"
    # Cached sibling forms need distinct label/error/help IDs too.
    body = re.sub(r'\b(id|for|aria-describedby|aria-labelledby|aria-errormessage|popovertarget)="([^"]*)"',
                  lambda m: m[1] + '="' + " ".join(prefix + ident for ident in m[2].split()) + '"', body)
    title_id = prefix + "title"
    return (f'<section class="editor-panel" data-editor-panel="{E(kind)}" data-editor-key="{E(key)}" '
            f'aria-labelledby="{title_id}"><header class="editor-heading"><h2 id="{title_id}">{E(title)}</h2>'
            f'<a class="btn" href="{E(route)}" data-editor-close aria-label="关闭编辑">关闭</a></header>'
            + body + f'<a class="btn editor-cancel" href="{E(route)}" data-editor-close>取消</a></section>')


def editor_dialog():
    return ('<dialog id="editor-dialog"><div data-editor-host></div></dialog>'
            '<div id="editor-cache" hidden></div>')
