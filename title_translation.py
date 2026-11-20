"""Local ES -> pt-PT glossary for '[product] [brand/model]' titles.

Translate product prefixes and recognised descriptive phrases anywhere in the
title, including after a brand. Preserve unrecognised names, models and URLs.
This is a local glossary, not unrestricted machine translation.
"""
from __future__ import annotations

import re

PRODUCTS = {
    "zapatillas de deporte": "sapatilhas",
    "zapatillas deportivas": "sapatilhas desportivas",
    "zapatillas": "sapatilhas", "zapatilla": "sapatilha",
    "zapatos": "sapatos", "zapato": "sapato",
    "chanclas": "chinelos", "chancla": "chinelo",
    "sandalias": "sandálias", "sandalia": "sandália",
    "botas": "botas", "botines": "botins", "mocasines": "mocassins",
    "camisetas": "t-shirts", "camiseta": "t-shirt",
    "camisas": "camisas", "camisa": "camisa",
    "sudaderas": "sweatshirts", "sudadera": "sweatshirt",
    "jerséis": "camisolas", "jerseis": "camisolas", "jersey": "camisola",
    "chaquetas": "casacos", "chaqueta": "casaco",
    "abrigos": "casacos", "abrigo": "casaco",
    "cazadoras": "blusões", "cazadora": "blusão",
    "chalecos": "coletes", "chaleco": "colete",
    "chubasqueros": "impermeáveis", "chubasquero": "impermeável",
    "pantalones cortos": "calções", "pantalón corto": "calções",
    "pantalon corto": "calções", "pantalones vaqueros": "calças de ganga",
    "pantalones": "calças", "pantalón": "calças", "pantalon": "calças",
    "vaqueros": "calças de ganga", "chándal": "fato de treino",
    "chandal": "fato de treino", "chándales": "fatos de treino",
    "chandales": "fatos de treino", "vestidos": "vestidos", "vestido": "vestido",
    "faldas": "saias", "falda": "saia", "bañadores": "fatos de banho",
    "bañador": "fato de banho", "banador": "fato de banho",
    "bikinis": "biquínis", "bikini": "biquíni",
    "ropa interior": "roupa interior", "calzoncillos": "boxers",
    "bragas": "cuecas", "sujetadores": "soutiens", "sujetador": "soutien",
    "calcetines": "meias", "calcetín": "meia", "calcetin": "meia",
    "pack de calcetines": "pack de meias", "pack de camisetas": "pack de t-shirts",
    "bolsos": "malas", "bolso": "mala", "mochilas": "mochilas", "mochila": "mochila",
    "riñoneras": "bolsas de cintura", "riñonera": "bolsa de cintura",
    "rinonera": "bolsa de cintura", "bandoleras": "malas a tiracolo",
    "bandolera": "mala a tiracolo", "carteras": "carteiras", "cartera": "carteira",
    "monederos": "porta-moedas", "monedero": "porta-moedas",
    "gorras": "bonés", "gorra": "boné", "gorros": "gorros", "gorro": "gorro",
    "sombreros": "chapéus", "sombrero": "chapéu",
    "bufandas": "cachecóis", "bufanda": "cachecol",
    "guantes": "luvas", "cinturones": "cintos", "cinturón": "cinto",
    "cinturon": "cinto", "gafas de sol": "óculos de sol", "gafas": "óculos",
    "relojes": "relógios", "reloj": "relógio", "pulseras": "pulseiras",
    "pulsera": "pulseira", "collares": "colares", "collar": "colar",
    "pendientes": "brincos", "anillos": "anéis", "anillo": "anel",
    "auriculares": "auscultadores", "altavoces": "colunas", "altavoz": "coluna",
    "teléfonos": "telefones", "teléfono": "telefone",
    "cargadores": "carregadores", "cargador": "carregador",
    "fundas": "capas", "funda": "capa", "teclados": "teclados", "teclado": "teclado",
    "ratones": "ratos", "ratón": "rato", "raton": "rato",
    "botellas": "garrafas", "botella": "garrafa", "tazas": "canecas", "taza": "caneca",
}

# Only whole qualifiers immediately after the recognised product name.
QUALIFIERS = {
    "de hombre": "de homem", "para hombre": "para homem",
    "de mujer": "de senhora", "para mujer": "para senhora",
    "de niño": "de criança", "de niña": "de criança",
    "para niños": "para crianças", "para niñas": "para crianças",
    "para niño": "para criança", "para niña": "para criança",
    "de bebé": "de bebé", "para bebé": "para bebé",
    "de manga corta": "de manga curta", "de manga larga": "de manga comprida",
    "con capucha": "com capuz", "sin capucha": "sem capuz",
    "sin mangas": "sem mangas", "con cremallera": "com fecho de correr",
    "de fútbol": "de futebol", "de futbol": "de futebol",
    "de baloncesto": "de basquetebol", "de entrenamiento": "de treino",
    "de piel": "de pele", "de cuero": "de couro", "de algodón": "de algodão",
    "de algodon": "de algodão", "de lana": "de lã",
    "inalámbricos": "sem fios", "inalambricos": "sem fios",
    "inalámbrico": "sem fios", "inalambrico": "sem fios",
}

# Multi-word Spanish descriptions are safe to recognise without translating
# isolated words such as model names (Air, Spezial, Originals, etc.). Longest
# matches win so 'muchos colores disponibles' is translated as a complete phrase.
DESCRIPTIONS = {
    "muchos colores disponibles": "muitas cores disponíveis",
    "muchos colores y tallas disponibles": "muitas cores e tamanhos disponíveis",
    "varios colores y tallas disponibles": "várias cores e tamanhos disponíveis",
    "colores y tallas disponibles": "cores e tamanhos disponíveis",
    "colores y tallas": "cores e tamanhos",
    "muchos colores": "muitas cores",
    "varios colores disponibles": "várias cores disponíveis",
    "varios colores": "várias cores",
    "diferentes colores disponibles": "diferentes cores disponíveis",
    "diferentes colores": "diferentes cores",
    "todos los colores": "todas as cores",
    "más colores disponibles": "mais cores disponíveis",
    "mas colores disponibles": "mais cores disponíveis",
    "nuevos colores disponibles": "novas cores disponíveis",
    "nuevos colores": "novas cores",
    "colores disponibles": "cores disponíveis",
    "colores especiales": "cores especiais",
    "disponible en varios colores": "disponível em várias cores",
    "disponibles en varios colores": "disponíveis em várias cores",
    "disponible en diferentes colores": "disponível em diferentes cores",
    "disponibles en diferentes colores": "disponíveis em diferentes cores",
    "disponible en muchos colores": "disponível em muitas cores",
    "disponibles en muchos colores": "disponíveis em muitas cores",
    "disponible en varios tamaños": "disponível em vários tamanhos",
    "disponible en varias tallas": "disponível em vários tamanhos",
    "disponibles en varias tallas": "disponíveis em vários tamanhos",
    "todas las tallas disponibles": "todos os tamanhos disponíveis",
    "todas las tallas": "todos os tamanhos",
    "muchas tallas disponibles": "muitos tamanhos disponíveis",
    "varias tallas disponibles": "vários tamanhos disponíveis",
    "tallas disponibles": "tamanhos disponíveis",
    "tallas grandes": "tamanhos grandes",
    "talla única": "tamanho único", "talla unica": "tamanho único",
    "talla ajustable": "tamanho ajustável",
    "últimas unidades disponibles": "últimas unidades disponíveis",
    "ultimas unidades disponibles": "últimas unidades disponíveis",
    "últimas unidades": "últimas unidades", "ultimas unidades": "últimas unidades",
    "nueva colección": "nova coleção", "nueva coleccion": "nova coleção",
    "nueva temporada": "nova estação", "edición limitada": "edição limitada",
    "edicion limitada": "edição limitada",
    "oferta limitada": "oferta limitada", "oferta especial": "oferta especial",
    "envío gratis": "envio grátis", "envio gratis": "envio grátis",
    "envío gratuito": "envio gratuito", "envio gratuito": "envio gratuito",
    "envío rápido": "envio rápido", "envio rapido": "envio rápido",
    "para hombre y mujer": "para homem e senhora",
    "para mujer y hombre": "para senhora e homem",
}


def _pattern(glossary: dict[str, str]) -> re.Pattern:
    alternatives = (r"\s+".join(re.escape(word) for word in key.split())
                    for key in sorted(glossary, key=len, reverse=True))
    # Do not match part of a reference, compound name, hashtag or URL.
    return re.compile(r"(?:" + "|".join(alternatives) + r")(?=$|[\s\[\](),:;.!?])", re.I)


_PRODUCT = _pattern(PRODUCTS)
_QUALIFIER = _pattern(QUALIFIERS)

_DESCRIPTION = re.compile(
    r"(?<![\w/@#-])(?:" + "|".join(
        r"\s+".join(re.escape(word) for word in key.split())
        for key in sorted(DESCRIPTIONS, key=len, reverse=True)
    ) + r")(?![\w/@#-])", re.I,
)
_SIZE_RANGE = re.compile(
    r"(?<![\w/@#-])(?P<label>Tallas?)(?P<gap>\s+)"
    r"(?:(?:del?|desde)\s+)?(?P<first>\d{1,3}|[2-6]?X{0,3}[SML])"
    r"(?P<separator>\s+(?:al?|hasta)\s+|\s*[-–/]\s*)"
    r"(?P<last>\d{1,3}|[2-6]?X{0,3}[SML])(?![\w/-])", re.I,
)
_LITERAL = re.compile(r"https?://[^\s<>]+|(?<!\w)[@#]\w+", re.I)


def _replacement(match: re.Match, glossary: dict[str, str]) -> str:
    original = match.group()
    translated = glossary.get(" ".join(original.casefold().split()), original)
    if original.isupper():
        return translated.upper()
    if original[0].isupper():
        return translated[0].upper() + translated[1:]
    return translated


def _translate_descriptions(title: str) -> str:
    def translate(text: str) -> str:
        text = _DESCRIPTION.sub(lambda m: _replacement(m, DESCRIPTIONS), text)

        def size_range(m: re.Match) -> str:
            label = "Tamanhos" if m["label"][0].isupper() else "tamanhos"
            if m["label"].isupper():
                label = label.upper()
            separator = m["separator"]
            if re.search(r"[A-Za-z]", separator):
                separator = " a "
            return label + m["gap"] + m["first"] + separator + m["last"]

        return _SIZE_RANGE.sub(size_range, text)

    # Never apply prose replacements to URL contents, hashtags or mentions.
    parts, end = [], 0
    for literal in _LITERAL.finditer(title):
        parts.extend((translate(title[end:literal.start()]), literal.group()))
        end = literal.end()
    parts.append(translate(title[end:]))
    return "".join(parts)


def translate_product_title(title: str) -> str:
    # Keep leading emoji/brackets/whitespace without skipping words or numbers.
    prefix = re.match(r"[^\w#@/]*", title).end()
    match = _PRODUCT.match(title, prefix)
    if not match:
        return _translate_descriptions(title)
    parts = [title[:prefix], _replacement(match, PRODUCTS)]
    end = match.end()
    while True:
        gap = re.match(r"\s+", title[end:])
        if not gap:
            break
        qualifier = _QUALIFIER.match(title, end + gap.end())
        if not qualifier:
            break
        parts.extend((gap.group(), _replacement(qualifier, QUALIFIERS)))
        end = qualifier.end()
    return _translate_descriptions("".join(parts) + title[end:])
