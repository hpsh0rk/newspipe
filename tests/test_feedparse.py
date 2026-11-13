"""Pure original feed evidence; no linked-page collection or inferred full text."""
import importlib
import unittest


def rss(content="", *, guid="one", date="", description="", title="Title"):
    return (f'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">'
            f'<channel><item><guid>{guid}</guid><title>{title}</title>'
            f'<link>https://publisher.example.invalid/one</link><pubDate>{date}</pubDate>'
            f'<description><![CDATA[{description}]]></description>'
            f'<content:encoded><![CDATA[{content}]]></content:encoded>'
            '</item></channel></rss>').encode()


ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://feed.example.invalid/base/">
<entry xml:base="posts/"><id>urn:post:one</id><title>One &amp; Two</title>
<link rel="self" href="entry.xml"/><link rel="enclosure" href="image.png"/>
<link rel="alternate" type="text/html" href="one"/>
<published>2026-11-10T10:00:00Z</published><updated>2026-11-13T10:00:00+00:00</updated>
<author><name>Alice</name></author><summary>Short preview</summary>
<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Original &amp; evidence</p>
<script>not evidence</script><a href="https://article.example.invalid/a">article</a></div></content>
</entry></feed>'''


class FeedParseTests(unittest.TestCase):
    def setUp(self):
        try:
            self.parser = importlib.import_module("newspipe.feedparse")
        except ModuleNotFoundError:
            self.fail("bounded original feed parser is not implemented")

    def test_rss_keeps_encoded_text_entities_and_markup_not_short_description(self):
        body = '<p>Original &amp; evidence ' + ('long material ' * 100) + '</p><script>SECRET noise</script>'
        item, = self.parser.parse(rss(body, description="Short preview"))
        self.assertGreater(len(item.source_text), 1000)
        self.assertIn("Original & evidence", item.source_text)
        self.assertNotIn("SECRET", item.source_text)
        self.assertEqual(item.source_markup, body)
        self.assertEqual(item.body_field, "content:encoded")
        self.assertFalse(item.text_truncated)

    def test_rss_publisher_source_is_bounded_and_not_the_article_or_author(self):
        body = rss().replace(b'</item>', b'<source xml:base="https://publisher.example.invalid/news/" url="../">Publisher</source></item>')
        item, = self.parser.parse(body)
        self.assertEqual(getattr(item, "publisher_name", None), "Publisher")
        self.assertEqual(item.publisher_url, "https://publisher.example.invalid/")
        self.assertEqual(item.author, "")
        self.assertEqual(item.permalink, "https://publisher.example.invalid/one")
        clipped, = self.parser.parse(body.replace(b'Publisher</source>', b'x' * 501 + b'</source>'))
        self.assertEqual(len(clipped.publisher_name), 500)
        self.assertTrue(clipped.text_truncated)

    def test_atom_source_title_and_alternate_homepage_preserve_child_base(self):
        body = ATOM.replace(b'</entry>', b'<source xml:base="https://publisher.example.invalid/base/">'
            b'<title>Publisher &amp; Co</title><link rel="self" href="feed.xml"/>'
            b'<link rel="alternate" xml:base="../" href="home"/></source></entry>')
        item, = self.parser.parse(body)
        self.assertEqual(getattr(item, "publisher_name", None), "Publisher & Co")
        self.assertEqual(item.publisher_url, "https://publisher.example.invalid/home")
        self.assertEqual(item.author, "Alice")

    def test_atom_xhtml_alternate_link_and_xml_base_are_preserved(self):
        item, = self.parser.parse(ATOM)
        self.assertEqual(item.native_id, "urn:post:one")
        self.assertEqual(item.permalink, "https://feed.example.invalid/base/posts/one")
        self.assertEqual(item.title, "One & Two")
        self.assertEqual(item.author, "Alice")
        self.assertIn("Original & evidence", item.source_text)
        self.assertNotIn("not evidence", item.source_text)
        self.assertEqual(item.body_field, "content")
        self.assertGreater(item.updated_ts, item.published_ts)
        self.assertIn({"role": "linked", "url": "https://article.example.invalid/a"}, item.references)

    def test_atom_child_and_nested_xhtml_bases_resolve_provenance(self):
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.invalid/feed/">
        <entry><link xml:base="../posts/" href="one"/>
        <content type="xhtml" xml:base="../articles/"><div xmlns="http://www.w3.org/1999/xhtml" xml:base="nested/">
        <a xml:base="../other/" href="two">Evidence</a></div></content></entry></feed>'''
        item, = self.parser.parse(body)
        self.assertEqual(item.native_id, "https://example.invalid/posts/one")
        self.assertEqual(item.permalink, "https://example.invalid/posts/one")
        self.assertEqual(item.references, ({"role": "linked", "url": "https://example.invalid/articles/other/two"},))

    def test_rss_technical_title_is_literal_character_data(self):
        item, = self.parser.parse(rss(title="Use Vec&lt;T&gt; and Option&lt;T&gt;"))
        self.assertEqual(item.title, "Use Vec<T> and Option<T>")

    def test_unknown_and_timezone_less_dates_are_not_fabricated(self):
        for value in ("", "not a date", "Tue, 10 Nov 2026 10:00:00"):
            with self.subTest(value=value):
                item, = self.parser.parse(rss(date=value))
                self.assertIsNone(item.published_ts)
        item, = self.parser.parse(rss(date="Tue, 10 Nov 2026 10:00:00 +0000"))
        self.assertEqual(item.published_ts, 1794304800)

    def test_atom_updated_without_published_remains_unknown_publication(self):
        item, = self.parser.parse(ATOM.replace(b'<published>2026-11-10T10:00:00Z</published>', b''))
        self.assertIsNone(item.published_ts)
        self.assertIsNotNone(item.updated_ts)

    def test_atom_plain_text_is_not_reinterpreted_as_html(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>one</id><content type="text">A &lt; B &amp; C &gt; D</content></entry></feed>'
        item, = self.parser.parse(body)
        self.assertEqual(item.source_text, "A < B & C > D")

    def test_external_atom_content_is_not_fetched_or_treated_as_inline_body(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>one</id><summary>Preview</summary><content src="http://127.0.0.1/private"/></entry></feed>'
        item, = self.parser.parse(body)
        self.assertEqual(item.source_text, "Preview")
        self.assertEqual(item.body_field, "summary")

    def test_limits_are_explicit_not_silent_full_text_or_identity_truncation(self):
        native = "prefix" * 50
        item, = self.parser.parse(rss("x" * 50000, guid=native, title="t" * 700))
        self.assertEqual(item.native_id, native)
        self.assertEqual(len(item.source_text), 20000)
        self.assertEqual(len(item.source_markup), 40000)
        self.assertEqual(len(item.title), 500)
        self.assertTrue(item.text_truncated)

    def test_missing_identity_and_excessive_entry_count_fail_visibly(self):
        for body in (b'<rss><channel><item><title>No id or link</title></item></channel></rss>',
                     ('<rss><channel>' + '<item><guid>one</guid></item>' * 1001 + '</channel></rss>').encode()):
            with self.subTest(body=body[:50]), self.assertRaises(self.parser.FeedError):
                self.parser.parse(body)

    def test_xml_entities_and_unsupported_formats_are_rejected_before_expansion(self):
        for body, reason in ((b'<!DOCTYPE rss [<!ENTITY x "secret">]><rss><channel/></rss>', "invalid_feed"),
                             (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>', "unsupported_feed"),
                             (b'<html>Provider error SECRET</html>', "invalid_feed")):
            with self.subTest(reason=reason), self.assertRaises(self.parser.FeedError) as error:
                self.parser.parse(body)
            self.assertEqual(error.exception.reason, reason)
            self.assertNotIn("SECRET", str(error.exception))

    def test_empty_rss_and_atom_are_successful_empty_not_unsupported(self):
        for body in (b'<rss><channel/></rss>', b'<feed xmlns="http://www.w3.org/2005/Atom"/>'):
            self.assertEqual(self.parser.parse(body), ())


if __name__ == "__main__":
    unittest.main()
