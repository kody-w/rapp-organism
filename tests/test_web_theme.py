import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ThemeMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.policy = None
        self.blocks = {"script": [], "style": []}
        self.active = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("http-equiv") == "Content-Security-Policy":
            self.policy = attributes["content"]
        if tag in self.blocks and "src" not in attributes:
            self.blocks[tag].append("")
            self.active = tag

    def handle_data(self, data):
        if self.active:
            self.blocks[self.active][-1] += data

    def handle_endtag(self, tag):
        if tag == self.active:
            self.active = None


class WebThemeTests(unittest.TestCase):
    def test_exact_theme_blocks_are_hash_pinned_without_unsafe_inline(self):
        markup = ThemeMarkup()
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        markup.feed(html)
        self.assertIsNotNone(markup.policy)
        self.assertNotIn("unsafe-inline", markup.policy)
        self.assertNotIn("unsafe-eval", markup.policy)
        self.assertIn("connect-src 'self'", markup.policy)
        for tag in ("script", "style"):
            self.assertEqual(len(markup.blocks[tag]), 1)
            content = markup.blocks[tag][0].encode()
            digest = base64.b64encode(hashlib.sha256(content).digest()).decode()
            self.assertIn(f"'sha256-{digest}'", markup.policy)
        self.assertLess(html.index('<script>'), html.index('<script src="app.js"'))
        self.assertIn('get("scoutTheme")', markup.blocks["script"][0])
        self.assertIn('prefers-color-scheme: dark', markup.blocks["script"][0])

    def test_component_colors_and_fonts_use_the_shared_theme(self):
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        css = (ROOT / "web/style.css").read_text(encoding="utf-8")
        tokens = set(re.findall(r"(--cp-[a-z-]+)\s*:", html))
        referenced = set(re.findall(r"var\((--[a-z-]+)\)", css))
        self.assertTrue(referenced)
        self.assertTrue(referenced <= tokens)
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")
        self.assertIn('"Segoe UI", Aptos, Calibri', css)
        self.assertIn('Consolas, "Courier New", Courier, monospace', css)
        self.assertIn('html[data-theme="dark"]', html)
        self.assertIn('https://kody-w.github.io/dogg/', html)


if __name__ == "__main__":
    unittest.main()
