import asyncio

import httpx
import pytest

from app import parser, webfeed

HTML = """
<html><body>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="ofertasx/101">
  <a class="tgme_widget_message_photo_wrap 123"
     href="https://t.me/ofertasx/101"
     style="width:800px;background-image:url('https://cdn4.telesco.pe/file/abc.jpg')"></a>
  <div class="tgme_widget_message_text js-message_text" dir="auto">Adidas Spezial - colores especiales<br/><br/>💰 25€<br/><br/>📲 <a href="https://c.onlyaff.app/cW5Uak" target="_blank">https://c.onlyaff.app/cW5Uak</a></div>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message js-widget_message" data-post="ofertasx/102">
  <div class="tgme_widget_message_text js-message_text">Bom dia a todos</div>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message js-widget_message" data-post="outrocanal/7">
  <div class="tgme_widget_message_text">reencaminhada</div>
 </div>
</div>
</body></html>
"""


def test_normalize_channel():
    n = webfeed.normalize_channel
    assert n("https://t.me/ofertasx") == "ofertasx"
    assert n("t.me/s/ofertasx") == "ofertasx"
    assert n("@ofertasx") == "ofertasx"
    assert n("ofertasx") == "ofertasx"
    assert n("https://t.me/+AbCdEf123") is None  # convite privado
    assert n("https://t.me/joinchat/AAAA") is None
    assert n("") is None


def test_parse_html():
    posts = webfeed.parse_html("ofertasx", HTML)
    assert [p.id for p in posts] == [101, 102]  # ignora o de outro canal
    p = posts[0]
    assert p.photo_url == "https://cdn4.telesco.pe/file/abc.jpg"
    assert p.urls == ["https://c.onlyaff.app/cW5Uak"]
    assert "\n" in p.text and p.text.startswith("Adidas Spezial")
    o = parser.parse(p.text, p.urls)
    assert o.title == "Adidas Spezial - colores especiales" and o.price == "25€"
    assert posts[1].photo_url is None and parser.parse(posts[1].text, posts[1].urls) is None


def test_recent_offers():
    def post(i, with_photo):
        return webfeed.WebPost(
            "c", i, f"Produto {i}\n💰 {i}€\nhttps://x.com/p/{i}", [f"https://x.com/p/{i}"],
            f"https://img/{i}.jpg" if with_photo else None,
        )

    posts = [post(i, i % 2 == 1) for i in range(1, 13)] + [webfeed.WebPost("c", 13, "bom dia", [], None)]
    picked, skipped = webfeed.recent_offers(posts, 5, True)
    assert [p.id for p, _ in picked] == [11, 9, 7, 5, 3]  # só com foto, mais novas primeiro
    assert skipped == 6  # 13, 12, 10, 8, 6, 4
    assert picked[0][1].title == "Produto 11" and picked[0][1].price == "11€"
    picked2, _ = webfeed.recent_offers(posts, 3, False)
    assert [p.id for p, _ in picked2] == [12, 11, 10]  # 13 não é oferta
    assert webfeed.recent_offers([], 5, True) == ([], 0)


def _client(status, body=""):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, text=body))
    )


def test_fetch_ok_and_errors():
    assert len(asyncio.run(webfeed.fetch(_client(200, HTML), "ofertasx"))) == 2
    with pytest.raises(webfeed.WebFeedError):
        asyncio.run(webfeed.fetch(_client(302), "privado"))
    with pytest.raises(webfeed.WebFeedError):
        asyncio.run(webfeed.fetch(_client(200, "<html></html>"), "vazio"))
