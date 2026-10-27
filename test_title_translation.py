import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.main import Job
from app.parser import Offer
from app.store import Store
from app.title_translation import translate_product_title
from test_force import make_app


@pytest.mark.parametrize(("source", "expected"), [
    ("Zapatillas Nike Air Max 90", "Sapatilhas Nike Air Max 90"),
    ("Camiseta Adidas Originals", "T-shirt Adidas Originals"),
    ("Bolso de mujer Michael Kors", "Mala de senhora Michael Kors"),
    ("Sudadera con capucha Nike Tech Fleece", "Sweatshirt com capuz Nike Tech Fleece"),
    ("Pantalones cortos Jordan Dri-FIT 23", "Calções Jordan Dri-FIT 23"),
    ("Camiseta de manga larga Lacoste L1212", "T-shirt de manga comprida Lacoste L1212"),
    ("Zapatillas de deporte New Balance 9060", "Sapatilhas New Balance 9060"),
    ("Zapatillas de fútbol Adidas F50", "Sapatilhas de futebol Adidas F50"),
    ("Gafas de sol Ray-Ban RB2140", "Óculos de sol Ray-Ban RB2140"),
    ("Riñonera The North Face", "Bolsa de cintura The North Face"),
    ("Chándal de hombre Puma", "Fato de treino de homem Puma"),
    ("Auriculares inalámbricos Sony WH-1000XM5", "Auscultadores sem fios Sony WH-1000XM5"),
    ("🔥 [Zapatillas] [NIKE Air Max 90]", "🔥 [Sapatilhas] [NIKE Air Max 90]"),
    ("  👟 zapatillas  Nike  Air-Max 90 / ref. DZ1234-001", "  👟 sapatilhas  Nike  Air-Max 90 / ref. DZ1234-001"),
    ("CAMISETA NIKE AIR MAX 90", "T-SHIRT NIKE AIR MAX 90"),
    ("bolso de mujer Michael Kors", "mala de senhora Michael Kors"),
    ("Bolso de   mujer Michael Kors", "Mala de senhora Michael Kors"),
    ("Camiseta de algodón de mujer Adidas", "T-shirt de algodão de senhora Adidas"),
    ("Zapatillas MarcaDesconocida Camiseta 2000", "Sapatilhas MarcaDesconocida Camiseta 2000"),
    ("Bolso Nike de mujer Air 2000", "Mala Nike de mujer Air 2000"),
    ("Camiseta Nike / ABC14 / 25€ / https://example.test/Camiseta", "T-shirt Nike / ABC14 / 25€ / https://example.test/Camiseta"),
])
def test_product_prefix_translation_preserves_brand_model_and_suffix(source, expected):
    assert translate_product_title(source) == expected


@pytest.mark.parametrize("title", [
    "Nike Air Max 90", "Adidas Spezial - colores especiales", "Sapatilhas Nike Air Max 90",
    "T-shirt Adidas Originals", "Vestido Nike Air", "Producto desconocido Nike",
    "Camisetazo Nike", "Camiseta-123 Nike", "Camiseta123 Nike", "ABC12 Camiseta Nike",
    "#Camiseta Nike", "@Camiseta Nike", "https://example.test/Camiseta", "", "🔥", "25€ Nike",
])
def test_unknown_non_product_and_already_portuguese_titles_remain_unchanged(title):
    assert translate_product_title(title) == title


@pytest.mark.parametrize("flow", ["manual", "auto", "example", "nolink"])
def test_translation_in_all_offer_flows(tmp_path, monkeypatch, flow):
    app = make_app(tmp_path, monkeypatch)
    app.store.set("mode", "auto" if flow in ("auto", "example") else "manual")
    app.store.set("template", "{titulo}\n{preco}\n{codigo}\n{link}")
    original = Offer("Zapatillas Nike Air Max 90", "25€", ["https://www.hacoo.pl/p/1201?f=OTHER"])
    job = Job(0, 201, original, web=True, origin="https://t.me/canal/201")

    async def run():
        await app.process(job, force=flow in ("example", "nolink"), nolink=flow == "nolink")
        assert original.title == "Zapatillas Nike Air Max 90"  # Parsing/source stays untouched.
        if flow == "auto":
            assert app.store.recent()[0]["title"] == "Sapatilhas Nike Air Max 90"
            assert app.store.count_pending() == 0
        else:
            payload = app.store.pop_pending(1)
            assert payload["values"]["titulo"] == "Sapatilhas Nike Air Max 90"
            assert payload["values"]["preco"] == "25€"
            assert payload["values"]["codigo"] == "COD14"
            expected_link = None if flow == "nolink" else "https://www.hacoo.pl/p/1201?f=p_aff.o_influxmcn.g_affiliate.m_tiago.c_x.t_1.v_1"
            assert payload["values"]["link"] == expected_link
        assert "Sapatilhas Nike Air Max 90" in app.bot.sent[-1][1]
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()


def test_translation_command_authorization_toggle_and_persistence(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)

    def event(text, private=True, owner=5):
        return SimpleNamespace(raw_text=text, is_private=private, sender_id=owner, respond=AsyncMock())

    async def run():
        assert app.product_title("Camiseta Nike") == "T-shirt Nike"
        await app.on_command(event("/traducao desligar", owner=99))
        await app.on_command(event("/traducao desligar", private=False))
        assert app.s("translate_titles", True) is True
        await app.on_command(event("/traducao desligar"))
        assert app.product_title("Camiseta Nike") == "Camiseta Nike"
        persisted = Store(app.cfg.db_path)
        assert persisted.get("translate_titles") is False
        persisted.db.close()
        invalid = event("/traducao xxx")
        await app.on_command(invalid)
        assert "Uso:" in invalid.respond.call_args.args[0]
        assert app.s("translate_titles") is False
        await app.on_command(event("/traducao ligar"))
        assert app.product_title("Camiseta Nike") == "T-shirt Nike"
        status = event("/traducao")
        await app.on_command(status)
        assert "ligada" in status.respond.call_args.args[0]
        await app.http.aclose()

    asyncio.run(run())
    app.store.db.close()
